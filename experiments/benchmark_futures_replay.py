from __future__ import annotations

import argparse
import time
import tracemalloc
from pathlib import Path
from typing import Dict

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import DepthUpdateEvent, SnapshotEvent, SymbolSpec
from lob_sim.config import load_config
from lob_sim.replay.reader import iter_records
from lob_sim.replay.runner import parse_symbol_spec_from_record


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = rank - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def benchmark_replay(path: Path, env_path: str, progress_every: int = 0) -> int:
    cfg = load_config(env_path)
    symbols: Dict[str, SymbolSpec] = {}
    syncers: Dict[str, BookSynchronizer] = {}

    total_events = 0
    snapshot_events = 0
    depth_events = 0
    trade_events = 0
    gap_count = 0
    loop_latencies_us: list[float] = []

    tracemalloc.start()
    wall_start = time.perf_counter()

    for rec in iter_records(path):
        loop_start_ns = time.perf_counter_ns()
        total_events += 1

        if rec.type == "exchangeInfo":
            parsed = parse_symbol_spec_from_record(rec)
            if parsed is not None:
                symbol, tick_size, step_size = parsed
                spec = SymbolSpec(symbol=symbol, tick_size=tick_size, step_size=step_size)
                symbols[symbol] = spec
                syncers.setdefault(
                    symbol,
                    BookSynchronizer(
                        LocalOrderBook(symbol=symbol, spec=spec, top_n=cfg.book_top_n),
                        resync_on_gap=cfg.resync_on_gap,
                    ),
                )
        elif rec.symbol in symbols:
            spec = symbols[rec.symbol]
            syncer = syncers.setdefault(
                rec.symbol,
                BookSynchronizer(
                    LocalOrderBook(symbol=rec.symbol, spec=spec, top_n=cfg.book_top_n),
                    resync_on_gap=cfg.resync_on_gap,
                ),
            )

            if rec.type == "snapshot":
                snapshot_events += 1
                syncer.on_snapshot(
                    SnapshotEvent(
                        symbol=rec.symbol,
                        last_update_id=int(rec.data["lastUpdateId"]),
                        bids=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("bids", [])],
                        asks=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("asks", [])],
                    )
                )
            elif rec.type == "depthUpdate":
                depth_events += 1
                try:
                    syncer.on_depth_update(
                        DepthUpdateEvent(
                            symbol=rec.symbol,
                            first_update_id=int(rec.data["U"]),
                            final_update_id=int(rec.data["u"]),
                            prev_update_id=int(rec.data.get("pu", rec.data.get("U", 0))),
                            bids=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("b", [])],
                            asks=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("a", [])],
                            ts_local=float(rec.ts_local),
                        )
                    )
                except BookSyncGapError:
                    gap_count += 1
            elif rec.type == "aggTrade":
                trade_events += 1
                # Benchmark the same parse path used elsewhere even though replay itself
                # does not mutate the book on public trade prints.
                spec.price_to_tick(rec.data["p"])
                spec.qty_to_lot(rec.data["q"])
                bool(rec.data["m"])

        loop_latencies_us.append((time.perf_counter_ns() - loop_start_ns) / 1_000.0)

        if progress_every > 0 and total_events % progress_every == 0:
            print(
                f"[benchmark] events={total_events} snapshots={snapshot_events} depth={depth_events} "
                f"trades={trade_events} gaps={gap_count}",
                flush=True,
            )

    wall_time = time.perf_counter() - wall_start
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    latencies_sorted = sorted(loop_latencies_us)
    events_per_sec = total_events / wall_time if wall_time > 0 else 0.0

    print(f"Replay benchmark file: {path}")
    print(f"Total events: {total_events}")
    print(f"Snapshot events: {snapshot_events}")
    print(f"Depth events: {depth_events}")
    print(f"AggTrade events: {trade_events}")
    print(f"Gap count: {gap_count}")
    print(f"Wall time: {wall_time:.6f}s")
    print(f"Events/sec: {events_per_sec:.2f}")
    print(f"Loop latency p50: {_percentile(latencies_sorted, 0.50):.2f}us")
    print(f"Loop latency p99: {_percentile(latencies_sorted, 0.99):.2f}us")
    print(f"Peak traced memory: {peak_bytes / (1024 * 1024):.2f} MiB")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark deterministic futures replay throughput and loop timing")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--progress-every", type=int, default=0, help="Optional progress print interval")
    args = parser.parse_args()

    return benchmark_replay(Path(args.file), args.env, progress_every=args.progress_every)


if __name__ == "__main__":
    raise SystemExit(main())
