from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import DepthUpdateEvent, SnapshotEvent, SymbolSpec
from lob_sim.config import load_config
from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import iter_records
from lob_sim.replay.runner import parse_symbol_spec_from_record
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


BENCHMARK_SCHEMA_VERSION = "lob_sim.replay_benchmark.v1"


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


def benchmark_replay(path: Path, env_path: str, progress_every: int = 0) -> dict[str, Any]:
    cfg = load_config(env_path)
    cfg_snapshot = config_snapshot(cfg)
    metadata = {
        "benchmark_created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_file": path.as_posix(),
        "input_sha256": file_sha256(path),
        "config_digest": config_digest(cfg_snapshot),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "source": source_state(),
    }
    symbols: Dict[str, SymbolSpec] = {}
    syncers: Dict[str, BookSynchronizer] = {}

    total_events = 0
    exchange_info_events = 0
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
            exchange_info_events += 1
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

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "metadata": metadata,
        "event_counts": {
            "total_events": total_events,
            "exchange_info_events": exchange_info_events,
            "snapshot_events": snapshot_events,
            "depth_events": depth_events,
            "agg_trade_events": trade_events,
            "gap_count": gap_count,
        },
        "timing": {
            "wall_time_seconds": wall_time,
            "events_per_second": events_per_sec,
            "loop_latency_p50_us": _percentile(latencies_sorted, 0.50),
            "loop_latency_p99_us": _percentile(latencies_sorted, 0.99),
        },
        "memory": {
            "peak_traced_bytes": peak_bytes,
            "peak_traced_mib": peak_bytes / (1024 * 1024),
        },
    }


def write_benchmark_json(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_benchmark(result: dict[str, Any]) -> None:
    metadata = result["metadata"]
    event_counts = result["event_counts"]
    timing = result["timing"]
    memory = result["memory"]

    print(f"Replay benchmark file: {metadata['input_file']}")
    print(f"Input SHA-256: {metadata['input_sha256']}")
    print(f"Config digest: {metadata['config_digest']}")
    print(f"Python: {metadata['python_version']}")
    print(f"Platform: {metadata['platform']}")
    print(f"Git commit: {metadata['source']['git_commit']}")
    print(f"Git branch: {metadata['source']['git_branch']}")
    print(f"Git dirty: {metadata['source']['git_dirty']}")
    print(f"Total events: {event_counts['total_events']}")
    print(f"ExchangeInfo events: {event_counts['exchange_info_events']}")
    print(f"Snapshot events: {event_counts['snapshot_events']}")
    print(f"Depth events: {event_counts['depth_events']}")
    print(f"AggTrade events: {event_counts['agg_trade_events']}")
    print(f"Gap count: {event_counts['gap_count']}")
    print(f"Wall time: {timing['wall_time_seconds']:.6f}s")
    print(f"Events/sec: {timing['events_per_second']:.2f}")
    print(f"Loop latency p50: {timing['loop_latency_p50_us']:.2f}us")
    print(f"Loop latency p99: {timing['loop_latency_p99_us']:.2f}us")
    print(f"Peak traced memory: {memory['peak_traced_mib']:.2f} MiB")
    print("Benchmark JSON:")
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark deterministic futures replay throughput and loop timing")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--progress-every", type=int, default=0, help="Optional progress print interval")
    parser.add_argument("--json-out", help="Optional path for a machine-readable benchmark JSON artifact")
    args = parser.parse_args()

    result = benchmark_replay(Path(args.file), args.env, progress_every=args.progress_every)
    print_benchmark(result)
    if args.json_out:
        write_benchmark_json(result, Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
