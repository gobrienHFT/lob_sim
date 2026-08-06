from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from itertools import count

from .binance.rest import BinanceRESTClient
from .binance.symbols import parse_exchange_info_for_symbol
from .binance.ws import run_depth_stream, run_trade_stream
from .book.local_book import LocalOrderBook
from .book.sync import BookSyncGapError, BookSynchronizer
from .book.types import SnapshotEvent, SymbolSpec
from .config import Config, load_config
from .record.format import NDJSONRecord, snapshot_payload
from .record.writer import NDJSONWriter
from .replay.runner import replay
from .sim.engine import SimulationEngine

logger = logging.getLogger(__name__)

CAPTURE_SCHEMA_VERSION = 2


def _capture_metadata() -> dict:
    return {
        "schemaVersion": CAPTURE_SCHEMA_VERSION,
        "clock": "receive_time",
        "timestampUnit": "seconds",
        "exchangeTimestampUnit": "milliseconds",
        "eventMetadataField": "_capture",
        "routes": {"depth": "public", "aggTrade": "market"},
    }


def _snapshot_level_payload(spec: SymbolSpec, entries: list[tuple[int, int]]) -> list[tuple[str, str]]:
    return [(str(spec.tick_to_price(tick)), str(spec.lot_to_qty(qty))) for tick, qty in entries]


async def _write_snapshot(
    symbol: str,
    spec: SymbolSpec,
    snapshot_data: dict,
    writer: NDJSONWriter,
    *,
    sync_epoch: int = 0,
    reason: str = "bootstrap",
    accepted: bool,
    validation_error: str | None = None,
    next_receive_seq: Callable[[], int] | None = None,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    bids = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
        for level in snapshot_data.get("bids", [])
    ]
    asks = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
        for level in snapshot_data.get("asks", [])
    ]
    received_ts = time.time()
    payload = snapshot_payload(
        int(snapshot_data["lastUpdateId"]),
        _snapshot_level_payload(spec, bids),
        _snapshot_level_payload(spec, asks),
    )
    payload["_capture"] = {
        "recvSeq": next_receive_seq() if next_receive_seq is not None else None,
        "recvMonotonicNs": time.monotonic_ns(),
        "syncEpoch": sync_epoch,
        "reason": reason,
        "snapshotAccepted": accepted,
    }
    if validation_error is not None:
        payload["_capture"]["validationError"] = validation_error
    writer.write(
        NDJSONRecord(
            ts_local=received_ts,
            symbol=symbol,
            type="snapshot",
            data=payload,
        )
    )
    return bids, asks


def _add_capture_metadata(
    raw: dict,
    *,
    recv_seq: int,
    sync_epoch: int,
    default_route: str,
) -> dict:
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
    writer: NDJSONWriter,
    stop_event: asyncio.Event,
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

    async def on_depth_connect(stream_epoch: int) -> None:
        nonlocal last_depth_stream_epoch, snapshot_reason
        if last_depth_stream_epoch is None:
            snapshot_reason = "bootstrap"
        elif stream_epoch != last_depth_stream_epoch:
            sync.begin_resync("depth_stream_reconnect")
            snapshot_reason = "reconnect"
        last_depth_stream_epoch = stream_epoch
        # This is deliberately set only after the public websocket is connected:
        # events can now buffer while the REST snapshot is in flight.
        snapshot_requested.set()

    async def on_depth(evt, raw):
        nonlocal snapshot_reason
        previous_epoch = sync.epoch
        try:
            sync.on_depth_update(evt)
        except BookSyncGapError as exc:
            snapshot_reason = sync.invalid_reason or "gap"
            if config.resync_on_gap:
                snapshot_requested.set()
            logger.warning("Book sync invalidated for %s: %s", symbol, exc)
            if not config.resync_on_gap:
                raise
        finally:
            if sync.epoch != previous_epoch and config.resync_on_gap:
                snapshot_requested.set()
            payload = _add_capture_metadata(
                raw,
                recv_seq=receive_sequence(),
                sync_epoch=sync.epoch,
                default_route="public",
            )
            writer.write(
                NDJSONRecord(
                    ts_local=evt.ts_local,
                    symbol=evt.symbol,
                    type="depthUpdate",
                    data=payload,
                )
            )

    async def on_trade(evt, raw):
        payload = _add_capture_metadata(
            raw,
            recv_seq=receive_sequence(),
            sync_epoch=sync.epoch,
            default_route="market",
        )
        writer.write(NDJSONRecord(ts_local=evt.ts_local, symbol=evt.symbol, type="aggTrade", data=payload))

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
                except RuntimeError as exc:
                    logger.warning(
                        "Snapshot request failed for %s in sync epoch %s; retrying: %s",
                        symbol,
                        requested_epoch,
                        exc,
                    )
                    await _wait_for_retry(
                        stop_event,
                        min(config.ws_reconnect_max_sec, retry_delay),
                    )
                    retry_delay = min(config.ws_reconnect_max_sec, retry_delay * 2.0)
                    continue

                # A reconnect can invalidate an in-flight REST response. Never
                # install a snapshot requested for an older stream/sync epoch.
                if requested_epoch != sync.epoch:
                    continue

                bids = [
                    (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
                    for level in snapshot_data.get("bids", [])
                ]
                asks = [
                    (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
                    for level in snapshot_data.get("asks", [])
                ]
                try:
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
                        reason=requested_reason,
                        accepted=False,
                        validation_error=str(exc),
                        next_receive_seq=receive_sequence,
                    )
                    snapshot_reason = "snapshot_retry"
                    logger.warning(
                        "Snapshot did not bridge buffered depth for %s in sync epoch %s; retrying: %s",
                        symbol,
                        requested_epoch,
                        exc,
                    )
                    await _wait_for_retry(
                        stop_event,
                        min(config.ws_reconnect_max_sec, retry_delay),
                    )
                    retry_delay = min(config.ws_reconnect_max_sec, retry_delay * 2.0)
                    continue
                await _write_snapshot(
                    symbol,
                    spec,
                    snapshot_data,
                    writer,
                    sync_epoch=requested_epoch,
                    reason=requested_reason,
                    accepted=True,
                    next_receive_seq=receive_sequence,
                )
                break

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(
            run_depth_stream(
                symbol,
                spec,
                config,
                on_depth,
                stop_event,
                on_connect=on_depth_connect,
            )
        )
        task_group.create_task(run_trade_stream(symbol, spec, config, on_trade, stop_event))
        task_group.create_task(snapshot_worker())


async def cmd_collect(config: Config) -> None:
    config.record_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.time_ns()
    filename = f"raw_{timestamp}.ndjson.gz" if config.record_gzip else f"raw_{timestamp}.ndjson"
    path = config.record_dir / filename

    async with BinanceRESTClient(config) as rest:
        exchange = await rest.get_exchange_info()
        symbols = {symbol: parse_exchange_info_for_symbol(exchange, symbol) for symbol in config.symbols}

        stop = asyncio.Event()
        receive_counter = count(1)

        def next_receive_seq() -> int:
            return next(receive_counter)

        with NDJSONWriter(path, flush_every=config.record_flush_every) as writer:
            capture_started_ts = time.time()
            writer.write(
                NDJSONRecord(
                    ts_local=capture_started_ts,
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
                        },
                    )
                )
            try:
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
                                next_receive_seq,
                            )
                        )
                    await asyncio.sleep(config.collect_seconds)
                    stop.set()
            finally:
                stop.set()


def cmd_replay(config: Config, file: str) -> None:
    result = replay(file, config)
    payload = asdict(result)
    payload["integrity_ok"] = result.integrity_ok
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not result.integrity_ok:
        raise SystemExit(2)


def cmd_simulate(config: Config, file: str) -> None:
    engine = SimulationEngine(config)
    metrics = engine.run(file)
    _, _, summary = engine.write_outputs(file, metrics)
    print(json.dumps(summary, indent=2))


def main() -> None:
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(prog="lob_sim")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("replay")
    r.add_argument("--file", required=True)
    r.set_defaults(func=cmd_replay)

    s = sub.add_parser("simulate")
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_simulate)

    args = parser.parse_args()
    cfg = load_config(args.env)
    logging.basicConfig(level=cfg.log_level)
    if args.command == "collect":
        asyncio.run(args.func(cfg))
    else:
        args.func(cfg, args.file)


if __name__ == "__main__":
    main()
