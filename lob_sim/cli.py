from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

from .binance.rest import BinanceRESTClient
from .binance.symbols import parse_exchange_info_for_symbol
from .binance.ws import run_symbol_stream
from .book.local_book import LocalOrderBook
from .book.sync import BookSyncGapError, BookSynchronizer
from .book.types import SnapshotEvent, SymbolSpec
from .config import Config, load_config
from .options.demo import (
    DEFAULT_OPTIONS_SCENARIO,
    OptionsMarketMakerDemo,
    build_options_config,
    format_interview_summary,
    options_scenarios,
)
from .record.format import NDJSONRecord, snapshot_payload
from .record.writer import NDJSONWriter
from .replay.runner import replay
from .sim.engine import SimulationEngine


logger = logging.getLogger(__name__)


def _snapshot_level_payload(spec: SymbolSpec, entries: list[tuple[int, int]]) -> list[tuple[str, str]]:
    return [(str(spec.tick_to_price(tick)), str(spec.lot_to_qty(qty))) for tick, qty in entries]


async def _write_snapshot(
    symbol: str,
    spec: SymbolSpec,
    snapshot_data: dict,
    writer: NDJSONWriter,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    bids = [(spec.price_to_tick(level[0]), spec.qty_to_lot(level[1])) for level in snapshot_data.get("bids", [])]
    asks = [(spec.price_to_tick(level[0]), spec.qty_to_lot(level[1])) for level in snapshot_data.get("asks", [])]
    writer.write(
        NDJSONRecord(
            ts_local=time.time(),
            symbol=symbol,
            type="snapshot",
            data=snapshot_payload(
                int(snapshot_data["lastUpdateId"]),
                _snapshot_level_payload(spec, bids),
                _snapshot_level_payload(spec, asks),
            ),
        )
    )
    return bids, asks


async def _collect_symbol(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    rest: BinanceRESTClient,
    writer: NDJSONWriter,
    initial_snapshot_id: int,
    initial_bids: list[tuple[int, int]],
    initial_asks: list[tuple[int, int]],
    stop_event: asyncio.Event,
    verbose: bool = False,
) -> None:
    book = LocalOrderBook(symbol=symbol, spec=spec, top_n=config.book_top_n)
    sync = BookSynchronizer(book=book, resync_on_gap=config.resync_on_gap)
    sync.on_snapshot(
        SnapshotEvent(
            symbol=symbol,
            last_update_id=initial_snapshot_id,
            bids=initial_bids,
            asks=initial_asks,
        )
    )

    async def on_depth(evt, raw):
        writer.write(NDJSONRecord(ts_local=evt.ts_local, symbol=evt.symbol, type="depthUpdate", data=raw))
        try:
            sync.on_depth_update(evt)
        except BookSyncGapError:
            if not config.resync_on_gap:
                return
            sync.reset()
            snapshot_data = await rest.get_depth_snapshot(symbol, config.snapshot_limit)
            bids, asks = await _write_snapshot(symbol, spec, snapshot_data, writer)
            if verbose:
                print(
                    f"[collect] resynced snapshot for {symbol} last_update_id={snapshot_data['lastUpdateId']}",
                    flush=True,
                )
            sync.on_snapshot(
                SnapshotEvent(
                    symbol=symbol,
                    last_update_id=int(snapshot_data["lastUpdateId"]),
                    bids=bids,
                    asks=asks,
                )
            )

    async def on_trade(evt, raw):
        writer.write(NDJSONRecord(ts_local=evt.ts_local, symbol=evt.symbol, type="aggTrade", data=raw))

    while not stop_event.is_set():
        await run_symbol_stream(
            symbol=symbol,
            spec=spec,
            config=config,
            on_depth=on_depth,
            on_trade=on_trade,
            stop_event=stop_event,
        )


async def cmd_collect(config: Config, verbose: bool = False) -> None:
    random.seed(config.sim_seed)
    config.record_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    filename = f"raw_{timestamp}.ndjson.gz" if config.record_gzip else f"raw_{timestamp}.ndjson"
    path = config.record_dir / filename
    if verbose:
        print(
            f"[collect] recording {', '.join(config.symbols)} for {config.collect_seconds}s into {path}",
            flush=True,
        )

    async with BinanceRESTClient(config) as rest:
        exchange = await rest.get_exchange_info()
        symbols = {symbol: parse_exchange_info_for_symbol(exchange, symbol) for symbol in config.symbols}

        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        with NDJSONWriter(path, flush_every=config.record_flush_every) as writer:
            for symbol, spec in symbols.items():
                writer.write(
                    NDJSONRecord(
                        ts_local=time.time(),
                        symbol=symbol,
                        type="exchangeInfo",
                        data={"symbol": symbol, "tickSize": str(spec.tick_size), "stepSize": str(spec.step_size)},
                    )
                )
                snapshot_data = await rest.get_depth_snapshot(symbol, config.snapshot_limit)
                bids, asks = await _write_snapshot(symbol, spec, snapshot_data, writer)
                if verbose:
                    print(
                        f"[collect] seeded {symbol} snapshot last_update_id={snapshot_data['lastUpdateId']} "
                        f"bids={len(bids)} asks={len(asks)}",
                        flush=True,
                    )
                tasks.append(
                    asyncio.create_task(
                        _collect_symbol(
                            symbol,
                            spec,
                            config,
                            rest,
                            writer,
                            int(snapshot_data["lastUpdateId"]),
                            bids,
                            asks,
                            stop,
                            verbose,
                        )
                    )
                )

            await asyncio.sleep(config.collect_seconds)
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    if verbose:
        print(f"[collect] completed recording to {path}", flush=True)


def cmd_replay(config: Config, file: str, verbose: bool = False, progress_every: int = 5000) -> None:
    res = replay(file, config, verbose=verbose, progress_every=progress_every)
    print("Replay complete")
    print(f"Events: {res.events_processed}, depth events: {res.depth_events}, gap count: {res.gap_count}")
    print(f"Rate: {res.events_per_sec:.2f} events/sec")


def cmd_simulate(config: Config, file: str, verbose: bool = False, progress_every: int = 5000) -> None:
    engine = SimulationEngine(config)
    metrics = engine.run(file, verbose=verbose, progress_every=progress_every)
    output_files, summary = engine.write_outputs(file, metrics)
    if verbose:
        print(f"[simulate] summary written to {output_files['summary']}", flush=True)
        print(f"[simulate] summary CSV written to {output_files['summary_csv']}", flush=True)
        print(f"[simulate] trades written to {output_files['trades']}", flush=True)
    print(json.dumps(summary, indent=2))


def cmd_options_demo(
    out_dir: str,
    steps: int,
    seed: int,
    scenario: str,
    verbose: bool = False,
    progress_every: int = 25,
    brief: bool = False,
) -> None:
    options_cfg = build_options_config(steps=steps, seed=seed, scenario=scenario)
    summary = OptionsMarketMakerDemo(options_cfg).run(
        Path(out_dir),
        verbose=verbose,
        progress_every=progress_every,
    )
    if brief:
        print(format_interview_summary(summary))
        return
    if verbose:
        return
    print(json.dumps(summary, indent=2))


def main() -> None:
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(prog="lob_sim")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("replay")
    r.add_argument("--file", required=True)
    r.add_argument("--verbose", action="store_true")
    r.add_argument("--progress-every", type=int, default=5000)
    r.set_defaults(func=cmd_replay)

    s = sub.add_parser("simulate")
    s.add_argument("--file", required=True)
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--progress-every", type=int, default=5000)
    s.set_defaults(func=cmd_simulate)

    o = sub.add_parser("options-demo")
    o.add_argument("--out-dir", default="outputs")
    o.add_argument("--steps", type=int, default=450)
    o.add_argument("--seed", type=int, default=7)
    o.add_argument("--scenario", choices=options_scenarios(), default=DEFAULT_OPTIONS_SCENARIO)
    o.add_argument("--verbose", action="store_true")
    o.add_argument("--progress-every", type=int, default=25)
    o.add_argument("--brief", action="store_true")
    o.set_defaults(func=cmd_options_demo)

    args = parser.parse_args()
    if args.command == "options-demo":
        args.func(args.out_dir, args.steps, args.seed, args.scenario, args.verbose, args.progress_every, args.brief)
        return

    cfg = load_config(args.env)
    if args.command == "collect":
        asyncio.run(args.func(cfg, args.verbose))
    elif args.command == "replay":
        args.func(cfg, args.file, args.verbose, args.progress_every)
    elif args.command == "simulate":
        args.func(cfg, args.file, args.verbose, args.progress_every)
    else:
        args.func(cfg, args.file)


if __name__ == "__main__":
    main()
