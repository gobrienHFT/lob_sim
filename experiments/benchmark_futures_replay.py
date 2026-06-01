from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import SymbolSpec
from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import iter_records
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, instrument_specs_snapshot, source_state

from scripts.audit_futures_pack import audit_futures_pack


BENCHMARK_SCHEMA_VERSION = "lob_sim.replay_benchmark.v2"
REVIEWER_BENCHMARK_SCHEMA_VERSION = "lob_sim.reviewer_benchmark.v1"


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


def benchmark_replay(
    path: Path,
    env_path: str,
    progress_every: int = 0,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> dict[str, Any]:
    cfg = load_config(env_path)
    cfg_snapshot = config_snapshot(cfg)
    metadata = {
        "benchmark_created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_file": path.as_posix(),
        "input_sha256": file_sha256(path),
        "config": cfg_snapshot,
        "config_digest": config_digest(cfg_snapshot),
        "feed_adapter": adapter_metadata(adapter),
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
            spec = adapter.instrument_spec_from_record(rec)
            if spec is not None:
                symbols[spec.symbol] = spec
                syncers.setdefault(
                    spec.symbol,
                    BookSynchronizer(
                        LocalOrderBook(symbol=spec.symbol, spec=spec, top_n=cfg.book_top_n),
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
                syncer.on_snapshot(adapter.snapshot_from_record(rec, spec))
            elif rec.type == "depthUpdate":
                depth_events += 1
                try:
                    syncer.on_depth_update(adapter.depth_update_from_record(rec, spec))
                except BookSyncGapError:
                    gap_count += 1
            elif rec.type == "aggTrade":
                trade_events += 1
                # Benchmark the same parse path used elsewhere even though replay itself
                # does not mutate the book on public trade prints.
                adapter.agg_trade_from_record(rec, spec)

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
    metadata["instrument_specs"] = instrument_specs_snapshot(symbols)

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


def _phase_timing(wall_times: list[float]) -> dict[str, float]:
    sorted_times = sorted(wall_times)
    return {
        "runs": len(wall_times),
        "wall_time_seconds": sum(wall_times),
        "last_wall_time_seconds": wall_times[-1] if wall_times else 0.0,
        "wall_time_p50_seconds": _percentile(sorted_times, 0.50),
        "wall_time_p99_seconds": _percentile(sorted_times, 0.99),
    }


def _run_with_memory(operation: Callable[[], Any]) -> tuple[Any, float, int]:
    tracemalloc.start()
    wall_start = time.perf_counter()
    result = operation()
    wall_time = time.perf_counter() - wall_start
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, wall_time, peak_bytes


def _benchmark_simulation_no_export(path: Path, env_path: str, runs: int) -> dict[str, Any]:
    wall_times: list[float] = []
    peak_bytes = 0
    last_summary: dict[str, Any] = {}

    for _ in range(runs):

        def operation() -> dict[str, Any]:
            cfg = load_config(env_path)
            engine = SimulationEngine(cfg)
            metrics = engine.run(path)
            summary = metrics.get_summary(engine._books)  # noqa: SLF001
            summary["event_trace_count"] = len(engine.event_trace)
            return summary

        last_summary, wall_time, peak = _run_with_memory(operation)
        wall_times.append(wall_time)
        peak_bytes = max(peak_bytes, peak)

    timing = _phase_timing(wall_times)
    records = int(last_summary.get("event_counts", {}).get("records_processed", 0))
    timing["events_per_second"] = (
        (records * len(wall_times)) / timing["wall_time_seconds"] if timing["wall_time_seconds"] > 0 else 0.0
    )
    return {
        "timing": timing,
        "memory": {
            "peak_traced_bytes": peak_bytes,
            "peak_traced_mib": peak_bytes / (1024 * 1024),
        },
        "event_counts": last_summary.get("event_counts", {}),
        "fill_count": last_summary.get("fill_count", 0),
        "event_trace_count": last_summary.get("event_trace_count", 0),
    }


def _benchmark_simulation_with_export(path: Path, env_path: str, runs: int) -> dict[str, Any]:
    wall_times: list[float] = []
    peak_bytes = 0
    last_summary: dict[str, Any] = {}
    last_artifacts: dict[str, str] = {}

    for _ in range(runs):
        with TemporaryDirectory(prefix="lob_sim_benchmark_export_") as temp_dir:
            previous_record_dir = os.environ.get("RECORD_DIR")
            os.environ["RECORD_DIR"] = temp_dir
            try:

                def operation() -> tuple[dict[str, Any], dict[str, str]]:
                    cfg = load_config(env_path)
                    engine = SimulationEngine(cfg)
                    metrics = engine.run(path)
                    generated_paths, summary = engine.write_outputs(str(path), metrics)
                    return summary, {name: generated.as_posix() for name, generated in generated_paths.items()}

                (last_summary, last_artifacts), wall_time, peak = _run_with_memory(operation)
            finally:
                if previous_record_dir is None:
                    os.environ.pop("RECORD_DIR", None)
                else:
                    os.environ["RECORD_DIR"] = previous_record_dir
            wall_times.append(wall_time)
            peak_bytes = max(peak_bytes, peak)

    timing = _phase_timing(wall_times)
    records = int(last_summary.get("event_counts", {}).get("records_processed", 0))
    timing["events_per_second"] = (
        (records * len(wall_times)) / timing["wall_time_seconds"] if timing["wall_time_seconds"] > 0 else 0.0
    )
    return {
        "timing": timing,
        "memory": {
            "peak_traced_bytes": peak_bytes,
            "peak_traced_mib": peak_bytes / (1024 * 1024),
        },
        "event_counts": last_summary.get("event_counts", {}),
        "fill_count": last_summary.get("fill_count", 0),
        "event_trace_count": last_summary.get("event_trace_count", 0),
        "artifact_labels": sorted(last_artifacts),
    }


def _benchmark_pack_audit(pack_dir: Path, runs: int) -> dict[str, Any]:
    wall_times: list[float] = []
    peak_bytes = 0
    last_result: dict[str, Any] = {}

    for _ in range(runs):
        last_result, wall_time, peak = _run_with_memory(lambda: audit_futures_pack(pack_dir))
        wall_times.append(wall_time)
        peak_bytes = max(peak_bytes, peak)

    return {
        "timing": _phase_timing(wall_times),
        "memory": {
            "peak_traced_bytes": peak_bytes,
            "peak_traced_mib": peak_bytes / (1024 * 1024),
        },
        "pack_dir": pack_dir.as_posix(),
        "audit_ok": last_result.get("ok", False),
        "audit_issue_count": len(last_result.get("issues", [])),
        "counts": last_result.get("counts", {}),
    }


def benchmark_reviewer_modes(
    path: Path,
    env_path: str,
    *,
    runs: int = 1,
    pack_dir: Path | None = None,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> dict[str, Any]:
    runs = max(1, runs)
    replay = benchmark_replay(path, env_path, adapter=adapter)
    metadata = dict(replay["metadata"])
    modes = {
        "replay_only": {
            "timing": replay["timing"],
            "memory": replay["memory"],
            "event_counts": replay["event_counts"],
        },
        "simulation_no_export": _benchmark_simulation_no_export(path, env_path, runs),
        "simulation_with_event_trace_export": _benchmark_simulation_with_export(path, env_path, runs),
    }
    if pack_dir is not None:
        modes["pack_audit"] = _benchmark_pack_audit(pack_dir, runs)
    metadata.setdefault("instrument_specs", replay["metadata"].get("instrument_specs", {}))
    return {
        "schema_version": REVIEWER_BENCHMARK_SCHEMA_VERSION,
        "metadata": metadata,
        "mode_runs": runs,
        "modes": modes,
        "fixture_note": (
            "Replay and simulation modes use the selected input file; pack_audit times the selected committed pack "
            "when --pack is supplied. Synthetic fixtures are labeled in their committed pack documentation."
        ),
    }


def write_benchmark_json(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def print_benchmark(result: dict[str, Any]) -> None:
    if "modes" in result:
        metadata = result["metadata"]
        print(f"Reviewer benchmark file: {metadata['input_file']}")
        print(f"Input SHA-256: {metadata['input_sha256']}")
        print(f"Config digest: {metadata['config_digest']}")
        print(f"Feed adapter: {metadata['feed_adapter']['name']} ({metadata['feed_adapter']['venue_label']})")
        print(f"Instrument specs: {', '.join(sorted(metadata['instrument_specs'])) or '<none>'}")
        print(f"Python: {metadata['python_version']}")
        print(f"Platform: {metadata['platform']}")
        print(f"Git commit: {metadata['source']['git_commit']}")
        print(f"Git branch: {metadata['source']['git_branch']}")
        print(f"Git dirty: {metadata['source']['git_dirty']}")
        for name, mode in result["modes"].items():
            timing = mode.get("timing", {})
            memory = mode.get("memory", {})
            print(
                f"{name}: wall={timing.get('wall_time_seconds', 0.0):.6f}s "
                f"p50={timing.get('wall_time_p50_seconds', timing.get('loop_latency_p50_us', 0.0))} "
                f"p99={timing.get('wall_time_p99_seconds', timing.get('loop_latency_p99_us', 0.0))} "
                f"peak={memory.get('peak_traced_mib', 0.0):.2f} MiB"
            )
        print("Benchmark JSON:")
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    metadata = result["metadata"]
    event_counts = result["event_counts"]
    timing = result["timing"]
    memory = result["memory"]

    print(f"Replay benchmark file: {metadata['input_file']}")
    print(f"Input SHA-256: {metadata['input_sha256']}")
    print(f"Config digest: {metadata['config_digest']}")
    print(f"Feed adapter: {metadata['feed_adapter']['name']} ({metadata['feed_adapter']['venue_label']})")
    print(f"Instrument specs: {', '.join(sorted(metadata['instrument_specs'])) or '<none>'}")
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
    parser = argparse.ArgumentParser(description="Benchmark deterministic futures replay and reviewer evidence paths")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--progress-every", type=int, default=0, help="Optional progress print interval")
    parser.add_argument("--json-out", help="Optional path for a machine-readable benchmark JSON artifact")
    parser.add_argument(
        "--mode",
        choices=("replay", "all"),
        default="replay",
        help="Benchmark replay only or all reviewer modes",
    )
    parser.add_argument("--runs", type=int, default=1, help="Runs per non-replay reviewer benchmark mode")
    parser.add_argument("--pack", help="Optional futures pack directory for pack-audit timing in --mode all")
    args = parser.parse_args()

    if args.mode == "all":
        result = benchmark_reviewer_modes(
            Path(args.file),
            args.env,
            runs=args.runs,
            pack_dir=Path(args.pack) if args.pack else None,
        )
    else:
        result = benchmark_replay(Path(args.file), args.env, progress_every=args.progress_every)
    print_benchmark(result)
    if args.json_out:
        write_benchmark_json(result, Path(args.json_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
