from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from itertools import count
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .binance.rest import BinanceRESTClient
from .binance.symbols import parse_exchange_info_for_symbol
from .binance.ws import run_depth_stream, run_trade_stream
from .book.local_book import LocalOrderBook
from .book.sync import BookSyncGapError, BookSynchronizer
from .book.types import SnapshotEvent, SymbolSpec
from .config import Config, FILL_ASSUMPTION_PROFILES, fill_assumption_config_for_profile, load_config
from .options.demo import (
    DEFAULT_OPTIONS_SCENARIO,
    OptionsMarketMakerDemo,
    build_options_config,
    format_artifact_paths,
    format_brief_summary,
    format_run_intro,
    format_scenario_card,
    format_terminal_summary,
    scenario_card,
    options_scenarios,
)
from .record.format import NDJSONRecord, snapshot_payload
from .record.envelope import EventEnvelope, SCHEMA_V3
from .record.segmented import SegmentedCaptureWriter
from .record.writer import NDJSONWriter
from .record.schema import RECORD_SCHEMA_VERSION
from .replay.inspection import inspect_stream
from .replay.arrow_store import normalize_to_arrow
from .replay.reader import iter_records
from .replay.runner import replay
from .sim.engine import SimulationEngine
from .sim.sinks import NullSink
from .sim.run_manifest import config_snapshot
from . import __version__


logger = logging.getLogger(__name__)


CAPTURE_SCHEMA_VERSION = 3


class _RecordWriter(Protocol):
    def write(self, record: NDJSONRecord) -> None: ...


class _EnvelopeRecordWriter:
    """Adapt the existing collector callbacks to schema-v3 envelopes."""

    def __init__(
        self,
        writer: SegmentedCaptureWriter,
        capture_id: str,
        next_receive_seq: Callable[[], int],
    ) -> None:
        self.writer = writer
        self.capture_id = capture_id
        self.next_receive_seq = next_receive_seq

    def write(self, record: NDJSONRecord) -> None:
        capture_value = record.data.get("_capture")
        if isinstance(capture_value, Mapping):
            capture = capture_value
        elif record.type == "captureEvent":
            # Early schema-v3 prototypes placed connection metadata directly
            # on captureEvent payloads.  Preserve those receipt identities
            # rather than silently assigning a second sequence and routing the
            # event as generic control traffic.
            capture = {
                key: record.data[key]
                for key in ("recvSeq", "recvMonotonicNs", "streamEpoch", "syncEpoch", "route")
                if key in record.data
            }
        else:
            capture = {}
        recv_seq = int(capture["recvSeq"]) if capture.get("recvSeq") is not None else self.next_receive_seq()
        event_ms = record.data.get("E")
        transaction_ms = record.data.get("T")
        self.writer.write(
            EventEnvelope(
                capture_id=self.capture_id,
                schema_version=SCHEMA_V3,
                venue=str(record.data.get("venue", "BINANCE_USDM")),
                instrument=record.symbol,
                event_kind=record.type,
                route=str(capture.get("route", "control")),
                recv_seq=recv_seq,
                recv_wall_ns=max(0, int(record.ts_local * 1_000_000_000)),
                recv_monotonic_ns=int(capture.get("recvMonotonicNs", time.monotonic_ns())),
                exchange_event_ns=int(event_ms) * 1_000_000 if event_ms is not None else None,
                exchange_transaction_ns=(int(transaction_ms) * 1_000_000 if transaction_ms is not None else None),
                stream_epoch=int(capture.get("streamEpoch", 0)),
                sync_epoch=int(capture.get("syncEpoch", 0)),
                payload=record.data,
            )
        )


def _capture_metadata() -> dict[str, object]:
    return {
        "schemaVersion": CAPTURE_SCHEMA_VERSION,
        "clock": "receive_time",
        "timestampUnit": "seconds",
        "exchangeTimestampUnit": "milliseconds",
        "eventMetadataField": "_capture",
        "routes": {"depth": "public", "aggTrade": "market"},
        "validity": "book AND trade_stream AND clock AND capture",
    }


def _snapshot_level_payload(spec: SymbolSpec, entries: list[tuple[int, int]]) -> list[tuple[str, str]]:
    return [(str(spec.tick_to_price(tick)), str(spec.lot_to_qty(qty))) for tick, qty in entries]


async def _write_snapshot(
    symbol: str,
    spec: SymbolSpec,
    snapshot_data: dict,
    writer: _RecordWriter,
    *,
    sync_epoch: int = 0,
    stream_epoch: int = 0,
    reason: str = "bootstrap",
    accepted: bool = True,
    validation_error: str | None = None,
    next_receive_seq: Callable[[], int] | None = None,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    bids = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1])) for level in snapshot_data.get("bids", [])
    ]
    asks = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1])) for level in snapshot_data.get("asks", [])
    ]
    capture = {
        "recvSeq": next_receive_seq() if next_receive_seq is not None else None,
        "recvMonotonicNs": time.monotonic_ns(),
        "streamEpoch": stream_epoch,
        "syncEpoch": sync_epoch,
        "route": "public",
        "reason": reason,
        "snapshotAccepted": accepted,
    }
    if validation_error is not None:
        capture["validationError"] = validation_error
    payload = snapshot_payload(
        int(snapshot_data["lastUpdateId"]),
        _snapshot_level_payload(spec, bids),
        _snapshot_level_payload(spec, asks),
    )
    payload["_capture"] = capture
    writer.write(
        NDJSONRecord(
            ts_local=time.time(),
            symbol=symbol,
            type="snapshot",
            data=payload,
        )
    )
    return bids, asks


def _add_capture_metadata(raw: dict, *, recv_seq: int, sync_epoch: int, default_route: str) -> dict:
    payload = dict(raw)
    capture = dict(payload.get("_capture", {}))
    capture.setdefault("recvMonotonicNs", time.monotonic_ns())
    capture.setdefault("streamEpoch", 0)
    capture.setdefault("route", default_route)
    capture["recvSeq"] = recv_seq
    capture["syncEpoch"] = sync_epoch
    payload["_capture"] = capture
    return payload


async def _wait_for_signal_or_stop(signal: asyncio.Event, stop_event: asyncio.Event) -> bool:
    signal_task = asyncio.create_task(signal.wait())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait({signal_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        return signal_task in done and not stop_event.is_set()
    finally:
        for task in (signal_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(signal_task, stop_task, return_exceptions=True)


async def _wait_for_retry(stop_event: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        await asyncio.sleep(0)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return


async def _collect_symbol(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    rest: BinanceRESTClient,
    writer: _RecordWriter,
    stop_event: asyncio.Event,
    verbose: bool = False,
    next_receive_seq: Callable[[], int] | None = None,
) -> None:
    book = LocalOrderBook(symbol=symbol, spec=spec, top_n=config.book_top_n)
    sync = BookSynchronizer(book=book, resync_on_gap=config.resync_on_gap)
    sync.begin_resync("bootstrap")
    local_sequence = count(1)
    receive_sequence = next_receive_seq or (lambda: next(local_sequence))
    snapshot_requested = asyncio.Event()
    snapshot_reason = "bootstrap"
    last_depth_stream_epoch: int | None = None

    async def _record_connection(route: str, stream_epoch: int) -> None:
        recv_seq = receive_sequence()
        recv_monotonic_ns = time.monotonic_ns()
        writer.write(
            NDJSONRecord(
                ts_local=time.time(),
                symbol=symbol,
                type="captureEvent",
                data={
                    "event": "connect",
                    "route": route,
                    "streamEpoch": stream_epoch,
                    "syncEpoch": sync.epoch,
                    "recvSeq": recv_seq,
                    "recvMonotonicNs": recv_monotonic_ns,
                    "_capture": {
                        "route": route,
                        "streamEpoch": stream_epoch,
                        "syncEpoch": sync.epoch,
                        "recvSeq": recv_seq,
                        "recvMonotonicNs": recv_monotonic_ns,
                    },
                },
            )
        )

    async def on_depth_connect(stream_epoch: int) -> None:
        nonlocal last_depth_stream_epoch, snapshot_reason
        if last_depth_stream_epoch is not None and stream_epoch != last_depth_stream_epoch:
            sync.begin_resync("depth_stream_reconnect")
            snapshot_reason = "reconnect"
        last_depth_stream_epoch = stream_epoch
        await _record_connection("public", stream_epoch)
        snapshot_requested.set()

    async def on_trade_connect(stream_epoch: int) -> None:
        await _record_connection("market", stream_epoch)

    async def on_depth(evt, raw):
        nonlocal snapshot_reason
        try:
            sync.on_depth_update(evt)
        except BookSyncGapError as exc:
            snapshot_reason = sync.invalid_reason or "gap"
            if config.resync_on_gap:
                snapshot_requested.set()
            logger.warning("Book sync invalidated for %s: %s", symbol, exc)
        finally:
            writer.write(
                NDJSONRecord(
                    ts_local=evt.ts_local,
                    symbol=evt.symbol,
                    type="depthUpdate",
                    data=_add_capture_metadata(
                        raw,
                        recv_seq=receive_sequence(),
                        sync_epoch=sync.epoch,
                        default_route="public",
                    ),
                )
            )

    async def on_trade(evt, raw):
        writer.write(
            NDJSONRecord(
                ts_local=evt.ts_local,
                symbol=evt.symbol,
                type="aggTrade",
                data=_add_capture_metadata(
                    raw,
                    recv_seq=receive_sequence(),
                    sync_epoch=sync.epoch,
                    default_route="market",
                ),
            )
        )

    async def snapshot_worker() -> None:
        nonlocal snapshot_reason
        while not stop_event.is_set():
            if not await _wait_for_signal_or_stop(snapshot_requested, stop_event):
                return
            snapshot_requested.clear()
            retry_delay = 0.25
            while not stop_event.is_set() and not sync.ready:
                requested_epoch = sync.epoch
                requested_reason = snapshot_reason
                try:
                    snapshot_data = await rest.get_depth_snapshot(symbol, config.snapshot_limit)
                    bids = [
                        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
                        for level in snapshot_data.get("bids", [])
                    ]
                    asks = [
                        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
                        for level in snapshot_data.get("asks", [])
                    ]
                    if requested_epoch != sync.epoch:
                        continue
                    sync.on_snapshot(
                        SnapshotEvent(
                            symbol=symbol,
                            last_update_id=int(snapshot_data["lastUpdateId"]),
                            bids=bids,
                            asks=asks,
                        )
                    )
                except BookSyncGapError as exc:
                    await _write_snapshot(
                        symbol,
                        spec,
                        snapshot_data,
                        writer,
                        sync_epoch=requested_epoch,
                        stream_epoch=last_depth_stream_epoch or 0,
                        reason=requested_reason,
                        accepted=False,
                        validation_error=str(exc),
                        next_receive_seq=receive_sequence,
                    )
                    snapshot_reason = "snapshot_retry"
                    await _wait_for_retry(stop_event, min(config.ws_reconnect_max_sec, retry_delay))
                    retry_delay = min(config.ws_reconnect_max_sec, retry_delay * 2.0)
                    continue
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Snapshot request/validation failed for %s: %s", symbol, exc)
                    await _wait_for_retry(stop_event, min(config.ws_reconnect_max_sec, retry_delay))
                    retry_delay = min(config.ws_reconnect_max_sec, retry_delay * 2.0)
                    continue
                await _write_snapshot(
                    symbol,
                    spec,
                    snapshot_data,
                    writer,
                    sync_epoch=requested_epoch,
                    stream_epoch=last_depth_stream_epoch or 0,
                    reason=requested_reason,
                    accepted=True,
                    next_receive_seq=receive_sequence,
                )
                if verbose:
                    print(
                        f"[collect] synced {symbol} epoch={requested_epoch} "
                        f"last_update_id={snapshot_data['lastUpdateId']}",
                        flush=True,
                    )
                break

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(
            run_depth_stream(symbol, spec, config, on_depth, stop_event, on_connect=on_depth_connect)
        )
        task_group.create_task(
            run_trade_stream(symbol, spec, config, on_trade, stop_event, on_connect=on_trade_connect)
        )
        task_group.create_task(snapshot_worker())


async def cmd_collect(config: Config, verbose: bool = False) -> None:
    random.seed(config.sim_seed)
    config.record_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    capture_id = f"capture_{timestamp}"
    filename = f"raw_{timestamp}.ndjson.gz" if config.record_gzip else f"raw_{timestamp}.ndjson"
    legacy_path = config.record_dir / filename
    display_path = (
        config.record_dir / f"{capture_id}.manifest.json" if config.capture_schema_version >= 3 else legacy_path
    )
    if verbose:
        print(
            f"[capture] recording {', '.join(config.symbols)} for {config.collect_seconds}s into {display_path}",
            flush=True,
        )

    async with BinanceRESTClient(config) as rest:
        exchange = await rest.get_exchange_info()
        symbols = {symbol: parse_exchange_info_for_symbol(exchange, symbol) for symbol in config.symbols}

        stop = asyncio.Event()
        receive_counter = count(1)

        def next_receive_seq() -> int:
            return next(receive_counter)

        with ExitStack() as stack:
            if config.capture_schema_version >= 3:
                segmented = stack.enter_context(
                    SegmentedCaptureWriter(
                        config.record_dir,
                        capture_id,
                        compression="zstd" if config.record_gzip else "none",
                    )
                )
                writer: _RecordWriter = _EnvelopeRecordWriter(segmented, capture_id, next_receive_seq)
            else:
                writer = stack.enter_context(NDJSONWriter(legacy_path, flush_every=config.record_flush_every))
            writer.write(
                NDJSONRecord(
                    ts_local=time.time(),
                    symbol="*",
                    type="captureMeta",
                    data=_capture_metadata(),
                )
            )
            for symbol, spec in symbols.items():
                writer.write(
                    NDJSONRecord(
                        ts_local=time.time(),
                        symbol=symbol,
                        type="exchangeInfo",
                        data={
                            "symbol": symbol,
                            "tickSize": str(spec.tick_size),
                            "stepSize": str(spec.step_size),
                            "baseAsset": spec.quantity_unit,
                            "quoteAsset": spec.price_currency,
                            "venue": spec.venue,
                        },
                    )
                )
            async with asyncio.TaskGroup() as task_group:
                for symbol, spec in symbols.items():
                    task_group.create_task(
                        _collect_symbol(
                            symbol,
                            spec,
                            config,
                            rest,
                            writer,
                            stop,
                            verbose,
                            next_receive_seq,
                        )
                    )
                await asyncio.sleep(config.collect_seconds)
                stop.set()
    if verbose:
        print(f"[capture] completed recording to {display_path}", flush=True)


def cmd_replay(config: Config, file: str, verbose: bool = False, progress_every: int = 5000) -> None:
    res = replay(file, config, verbose=verbose, progress_every=progress_every)
    print("Replay complete")
    print(
        f"Events: {res.events_processed}, depth events: {res.depth_events}, "
        f"gap count: {res.gap_count}, elapsed: {res.elapsed_seconds:.2f}s"
    )
    print(f"Rate: {res.events_per_sec:.2f} events/sec")
    for symbol, result in res.symbols.items():
        print(
            f"{symbol}: snapshot={'yes' if result.snapshot_seen else 'no'}, "
            f"synced={'yes' if result.synced else 'no'}, gaps={result.gap_count}, "
            f"levels={result.total_levels}, last_update_id={result.last_update_id}"
        )


def cmd_inspect(file: str) -> None:
    print(json.dumps(inspect_stream(file).as_dict(), indent=2))


def cmd_validate(file: str) -> None:
    inspection = inspect_stream(file)
    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": "lob_sim.validation_report.v1",
                "inspection": inspection.as_dict(),
            },
            indent=2,
        )
    )


def cmd_normalize(file: str, out: str, batch_size: int = 65_536) -> None:
    print(json.dumps(normalize_to_arrow(file, out, batch_size=batch_size), indent=2))


def cmd_doctor(config: Config) -> None:
    payload = {
        "ok": True,
        "lob_sim_version": __version__,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "record_dir": str(config.record_dir),
        "output_dir": str(config.output_dir),
        "symbols": list(config.symbols),
        "config": config_snapshot(config),
    }
    print(json.dumps(payload, indent=2))


def cmd_simulate(config: Config, file: str, verbose: bool = False, progress_every: int = 5000) -> None:
    engine = SimulationEngine(config)
    metrics = engine.run(file, verbose=verbose, progress_every=progress_every)
    output_files, summary = engine.write_outputs(file, metrics)
    if verbose:
        print(f"[simulate] summary written to {output_files['summary']}", flush=True)
        print(f"[simulate] summary CSV written to {output_files['summary_csv']}", flush=True)
        print(f"[simulate] trades written to {output_files['trades']}", flush=True)
    print(json.dumps(summary, indent=2))


def _deterministic_run(config: Config, file: str) -> dict[str, object]:
    engine = SimulationEngine(config, event_sink=NullSink(), retain_event_trace=False)
    metrics = engine.run(file)
    summary = metrics.get_summary(engine._books)
    return {
        "state_sha256": engine.state_sha256(),
        "fill_count": summary["fill_count"],
        "total_pnl": summary["total_pnl"],
        "valuation_complete": summary["valuation_complete"],
    }


def cmd_compare(config: Config, file: str, repetitions: int = 10) -> None:
    if repetitions < 2:
        raise ValueError("compare repetitions must be at least 2")
    runs = [_deterministic_run(config, file) for _ in range(repetitions)]
    hashes = [str(run["state_sha256"]) for run in runs]
    result: dict[str, object] = {
        "schema_version": "lob_sim.determinism_comparison.v1",
        "ok": len(set(hashes)) == 1,
        "repetitions": repetitions,
        "state_sha256": hashes[0],
        "python_repeat_parity": len(set(hashes)) == 1,
        "runs": runs,
        "rust_differential": "not_run_extension_unavailable",
    }
    try:
        import lob_core  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        result["rust_differential"] = {
            "extension_available": True,
            "logical_time_smoke": list(lob_core.logical_time_key(1, 2)),
            "full_event_parity": "not_yet_implemented",
        }
    print(json.dumps(result, indent=2))


def cmd_audit(config: Config, file: str) -> None:
    inspection = inspect_stream(file)
    replay_result = replay(file, config)
    deterministic = _deterministic_run(config, file)
    ok = replay_result.gap_count == 0 and all(symbol.synced for symbol in replay_result.symbols.values())
    print(
        json.dumps(
            {
                "schema_version": "lob_sim.capture_audit.v1",
                "ok": ok,
                "inspection": inspection.as_dict(),
                "replay": {
                    "events_processed": replay_result.events_processed,
                    "gap_count": replay_result.gap_count,
                    "symbols": {
                        symbol: {
                            "synced": result.synced,
                            "gaps": result.gap_count,
                            "last_update_id": result.last_update_id,
                        }
                        for symbol, result in sorted(replay_result.symbols.items())
                    },
                },
                "deterministic_state": deterministic,
            },
            indent=2,
        )
    )


def cmd_bench(config: Config, file: str, runs: int = 3) -> None:
    if runs <= 0:
        raise ValueError("benchmark runs must be positive")
    record_count = sum(1 for _ in iter_records(file))
    samples: list[float] = []
    state_hashes: list[str] = []
    for _ in range(runs):
        start = time.perf_counter()
        result = _deterministic_run(config, file)
        samples.append(time.perf_counter() - start)
        state_hashes.append(str(result["state_sha256"]))
    rates = [record_count / seconds if seconds else 0.0 for seconds in samples]
    print(
        json.dumps(
            {
                "schema_version": "lob_sim.short_benchmark.v1",
                "claim": "offline replay throughput; not trading latency",
                "records": record_count,
                "runs": runs,
                "wall_seconds": samples,
                "events_per_second": rates,
                "median_events_per_second": sorted(rates)[len(rates) // 2],
                "deterministic": len(set(state_hashes)) == 1,
                "sink": "null",
            },
            indent=2,
        )
    )


def cmd_demo(config: Config, file: str | None = None) -> None:
    target = (
        Path(file)
        if file
        else Path(__file__).resolve().parents[1]
        / "docs"
        / "sample_outputs"
        / "futures_replay_walkthrough"
        / "input_fixture.ndjson"
    )
    inspection = inspect_stream(target)
    run = _deterministic_run(config, str(target))
    print(
        json.dumps(
            {
                "schema_version": "lob_sim.reviewer_demo.v1",
                "input": inspection.as_dict(),
                "deterministic_run": run,
                "next_commands": [
                    f"python -m lob_sim.cli --env .env.example validate --file {target}",
                    f"python -m lob_sim.cli --env .env.example compare --file {target}",
                    "python scripts/reviewer_gate.py",
                ],
                "non_claim": "public L2 results are execution scenarios, not historical private FIFO fill truth",
            },
            indent=2,
        )
    )


def cmd_options_demo(
    out_dir: str,
    steps: int,
    seed: int,
    scenario: str,
    verbose: bool = False,
    progress_every: int = 25,
    brief: bool = False,
    log_mode: str = "compact",
    walkthrough_mode: bool = False,
) -> None:
    options_cfg = build_options_config(steps=steps, seed=seed, scenario=scenario)
    out_path = Path(out_dir)
    if not brief:
        print(format_run_intro(options_cfg, out_path, log_mode))
        print()
        print(format_scenario_card(scenario_card(scenario)))
        print()
    summary = OptionsMarketMakerDemo(options_cfg).run(
        out_path,
        verbose=verbose,
        progress_every=progress_every,
        log_mode=log_mode,
        walkthrough_mode=walkthrough_mode,
    )
    if brief:
        print(format_brief_summary(summary))
        print()
        print(format_artifact_paths(summary))
        return
    print(format_terminal_summary(summary))
    print()
    print(format_artifact_paths(summary))


def main() -> None:
    if os.name == "nt":
        windows_policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if windows_policy_factory is not None:
            asyncio.set_event_loop_policy(windows_policy_factory())

    parser = argparse.ArgumentParser(prog="lob_sim")
    parser.add_argument("--env", default=".env", help="Path to .env file (falls back to .env.example)")
    sub = parser.add_subparsers(dest="command", required=True)

    for capture_command in ("capture", "collect"):
        c = sub.add_parser(capture_command)
        c.add_argument("--verbose", action="store_true")
        c.set_defaults(func=cmd_collect)

    d = sub.add_parser("doctor")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("replay")
    r.add_argument("--file", required=True)
    r.add_argument("--verbose", action="store_true")
    r.add_argument("--progress-every", type=int, default=5000)
    r.set_defaults(func=cmd_replay)

    i = sub.add_parser("inspect")
    i.add_argument("--file", required=True)
    i.set_defaults(func=cmd_inspect)

    v = sub.add_parser("validate")
    v.add_argument("--file", required=True)
    v.set_defaults(func=cmd_validate)

    n = sub.add_parser("normalize")
    n.add_argument("--file", required=True)
    n.add_argument("--out", required=True)
    n.add_argument("--batch-size", type=int, default=65_536)
    n.set_defaults(func=cmd_normalize)

    s = sub.add_parser("simulate")
    s.add_argument("--file", required=True)
    s.add_argument(
        "--fill-profile",
        choices=FILL_ASSUMPTION_PROFILES,
        help="Passive-fill assumption profile (default: FILL_PROFILE from env, otherwise base)",
    )
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--progress-every", type=int, default=5000)
    s.set_defaults(func=cmd_simulate)

    compare = sub.add_parser("compare")
    compare.add_argument("--file", required=True)
    compare.add_argument("--repetitions", type=int, default=10)
    compare.set_defaults(func=cmd_compare)

    audit = sub.add_parser("audit")
    audit.add_argument("--file", required=True)
    audit.set_defaults(func=cmd_audit)

    bench = sub.add_parser("bench")
    bench.add_argument("--file", required=True)
    bench.add_argument("--runs", type=int, default=3)
    bench.set_defaults(func=cmd_bench)

    demo = sub.add_parser("demo")
    demo.add_argument("--file")
    demo.set_defaults(func=cmd_demo)

    o = sub.add_parser("options-demo")
    o.add_argument("--out-dir", default="outputs")
    o.add_argument("--steps", type=int, default=450)
    o.add_argument("--seed", type=int, default=7)
    o.add_argument("--scenario", choices=options_scenarios(), default=DEFAULT_OPTIONS_SCENARIO)
    o.add_argument("--verbose", action="store_true")
    o.add_argument("--progress-every", type=int, default=25)
    o.add_argument("--brief", action="store_true")
    o.add_argument("--log-mode", choices=("compact", "verbose"), default="compact")
    o.add_argument("--walkthrough-mode", dest="walkthrough_mode", action="store_true")
    o.add_argument("--interview-mode", dest="walkthrough_mode", action="store_true", help=argparse.SUPPRESS)
    o.set_defaults(func=cmd_options_demo)

    args = parser.parse_args()
    if args.command == "options-demo":
        args.func(
            args.out_dir,
            args.steps,
            args.seed,
            args.scenario,
            args.verbose,
            args.progress_every,
            args.brief,
            args.log_mode,
            args.walkthrough_mode,
        )
        return

    if args.command in {"inspect", "validate"}:
        args.func(args.file)
        return

    if args.command == "normalize":
        args.func(args.file, args.out, args.batch_size)
        return

    cfg = load_config(args.env)
    if args.command in {"capture", "collect"}:
        asyncio.run(args.func(cfg, args.verbose))
    elif args.command == "doctor":
        args.func(cfg)
    elif args.command == "replay":
        args.func(cfg, args.file, args.verbose, args.progress_every)
    elif args.command == "simulate":
        if args.fill_profile is not None:
            cfg = replace(cfg, fill_assumption=fill_assumption_config_for_profile(args.fill_profile))
        args.func(cfg, args.file, args.verbose, args.progress_every)
    elif args.command == "compare":
        args.func(cfg, args.file, args.repetitions)
    elif args.command == "audit":
        args.func(cfg, args.file)
    elif args.command == "bench":
        args.func(cfg, args.file, args.runs)
    elif args.command == "demo":
        args.func(cfg, args.file)
    else:
        args.func(cfg, args.file)


if __name__ == "__main__":
    main()
