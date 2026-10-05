"""Matched-tape HMM overhead: offline Python replay, never exchange latency."""

from __future__ import annotations

import argparse
import gc
import itertools
import os
import platform
import sys
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.config import Config, load_config
from lob_sim.regime.artifact import load_model
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import identity, integer
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_snapshot, source_state

MODES = ("baseline", "observe", "policy")


def percentiles(values: Sequence[int | float]) -> dict[str, float]:
    """Linearly interpolated empirical quantiles, retaining all raw samples."""
    if not values:
        raise ValueError("benchmark samples cannot be empty")
    ordered = sorted(values)

    def quantile(probability: float) -> float:
        position = (len(ordered) - 1) * probability
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))

    return {"median": quantile(0.5), "p95": quantile(0.95), "p99": quantile(0.99)}


def replay_probe(engine: SimulationEngine) -> dict[str, Any]:
    """Bounded sufficient-statistic identities, not a full event-by-event oracle."""
    summary = engine.metrics.get_summary(engine._books, engine._specs)
    if not summary["audit_retention"]["memory_bounded_by_tape_duration"]:
        raise ValueError("benchmark requires aggregate-only bounded audit sinks")
    retention = engine.event_trace_retention()
    if retention["rows_retained"] != 0 or not retention["memory_bounded_by_tape_duration"]:
        raise ValueError("benchmark cannot retain event traces")
    hmm = engine.regime.summary() if engine.regime is not None else None
    return {
        "core_sha256": identity(
            {
                "summary": summary,
                "books": {
                    symbol: {"bids": sorted(book.bids.items()), "asks": sorted(book.asks.items())}
                    for symbol, book in sorted(engine._books.items())
                },
                "latency_sampler": engine.latency_model.sampler_state(),
            }
        ),
        "records_processed": summary["event_counts"]["records_processed"],
        "quote_count": summary["quote_count"],
        "cancel_count": summary["cancel_count"],
        "fill_count": summary["fill_count"],
        "fill_audit_sha256": summary["audit_retention"]["fill_audit_sha256"],
        "markout_audit_sha256": summary["audit_retention"]["markout_audit_sha256"],
        "event_trace_retention": retention,
        "audit_retention": summary["audit_retention"],
        "hmm": hmm,
    }


def benchmark_hmm(
    path: Path,
    model_path: Path,
    cfg: Config,
    *,
    symbol: str,
    warmups: int = 3,
    repetitions: int = 30,
    memory_runs: int = 1,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fresh engines, common tape/config, balanced order, separate tracing runs.

    Policy changes work performed (quotes, fills, pending horizons). Its ratio
    is end-to-end cost, not the isolated cost of posterior inference. Timing
    includes construction, raw input/hash/decode, scheduling and EOF draining;
    model loading, GC preconditioning and diagnostic reduction are outside.
    """
    for name, count, maximum in (
        ("warmups", warmups, 30),
        ("repetitions", repetitions, 1000),
        ("memory_runs", memory_runs, 10),
    ):
        integer(count, name, minimum=1)
        if count > maximum:
            raise ValueError(f"{name} exceeds explicit benchmark cap")
    if tracemalloc.is_tracing():
        raise ValueError("timing requires tracemalloc disabled on entry")
    if cfg.hmm is not None or cfg.sim_latency_mode != "fixed":
        raise ValueError("benchmark requires HMM disabled and fixed latency")
    if not cfg.mm_enabled or cfg.mm_strategy_profile != "research_mm" or cfg.symbols != (symbol,):
        raise ValueError("benchmark requires enabled single-symbol research_mm reference")
    model_file_sha256 = file_sha256(model_path)
    model = load_model(model_path)
    if file_sha256(model_path) != model_file_sha256:
        raise ValueError("model artifact changed during loading")
    observe = HMMSettings(model, symbol)
    policy = HMMSettings(model, symbol, mode="policy")
    configurations = {
        "baseline": cfg,
        "observe": replace(cfg, hmm=observe),
        "policy": replace(cfg, hmm=policy, mm_strategy_profile="hmm_regime_mm"),
    }
    parents = {
        "input_sha256": file_sha256(path),
        "model_file_sha256": model_file_sha256,
        "package": checkpoint_code_identity(),
        "benchmark_script_sha256": file_sha256(Path(__file__)),
    }
    prototypes: dict[str, dict[str, Any]] = {}
    measurements: dict[str, list[int]] = {mode: [] for mode in MODES}
    peaks: dict[str, list[int]] = {mode: [] for mode in MODES}
    orders = tuple(itertools.permutations(MODES))
    measured_order = []

    def run(mode: str, *, trace_memory: bool = False) -> tuple[int, int]:
        gc.collect()  # Outside timing; do not disable ordinary runtime GC.
        if trace_memory:
            tracemalloc.start()
        try:
            start = time.perf_counter_ns()
            engine = SimulationEngine(configurations[mode], retain_event_trace=False, retain_audit_rows=False)
            engine.run(path)
            duration = time.perf_counter_ns() - start
            peak = tracemalloc.get_traced_memory()[1] if trace_memory else 0
        finally:
            if trace_memory:
                tracemalloc.stop()
        if duration <= 0:
            raise ValueError("nonpositive benchmark timing")
        probe = replay_probe(engine)
        if mode in prototypes and probe != prototypes[mode]:
            raise ValueError(f"{mode} replay is not deterministic across repetitions")
        prototypes[mode] = probe
        if mode != "baseline" and (probe["hmm"] is None or probe["hmm"]["status_counts"].get("VALID", 0) == 0):
            raise ValueError("benchmark HMM never had valid inference; inactive overhead is not representative")
        return duration, peak

    for round_index in range(warmups + repetitions):
        order = orders[round_index % len(orders)]
        for mode in order:
            duration, _ = run(mode)
            if round_index >= warmups:
                measurements[mode].append(duration)
        if round_index >= warmups:
            measured_order.append(list(order))
        if progress is not None:
            progress(
                f"{'warmup' if round_index < warmups else 'measured'} round {round_index + 1}/{warmups + repetitions}"
            )
    for index in range(memory_runs):
        for mode in orders[index % len(orders)]:
            _, peak = run(mode, trace_memory=True)
            peaks[mode].append(peak)
        if progress is not None:
            progress(f"separate memory round {index + 1}/{memory_runs}")
    if prototypes["baseline"]["core_sha256"] != prototypes["observe"]["core_sha256"]:
        raise ValueError("baseline/observe core summary, book, fill, markout or latency identities differ")
    if parents != {
        "input_sha256": file_sha256(path),
        "model_file_sha256": file_sha256(model_path),
        "package": checkpoint_code_identity(),
        "benchmark_script_sha256": file_sha256(Path(__file__)),
    }:
        raise ValueError("benchmark source or evidence changed during measurement")
    packages: dict[str, str | None] = {}
    for name in ("numpy", "hmmlearn", "scipy", "scikit-learn"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    modes = {}
    for mode in MODES:
        ratios = [value / baseline for value, baseline in zip(measurements[mode], measurements["baseline"])]
        records = prototypes[mode]["records_processed"]
        modes[mode] = {
            "config": config_snapshot(configurations[mode]),
            "raw_wall_ns": measurements[mode],
            "wall_ns": percentiles(measurements[mode]),
            "matched_round_relative_runtime": percentiles(ratios),
            "median_relative_overhead_percent": (percentiles(ratios)["median"] - 1) * 100,
            "raw_peak_traced_bytes": peaks[mode],
            "peak_traced_bytes": max(peaks[mode]),
            "median_input_records_per_second": records * 1_000_000_000 / percentiles(measurements[mode])["median"],
            "probe": prototypes[mode],
        }
    report = {
        "schema_version": "lob_sim.hmm_overhead_benchmark.v1",
        "claim_ready": False,
        "input": {"path": path.as_posix(), "size_bytes": path.stat().st_size},
        "model_file": model_path.as_posix(),
        "parents": parents,
        "model_sha256": model.model_sha256,
        "model_states": model.parameters.state_count,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "cpu_description": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
            "packages": packages,
            "source": source_state(),
        },
        "protocol": {
            "warmups_per_mode": warmups,
            "measured_repetitions_per_mode": repetitions,
            "memory_runs_per_mode": memory_runs,
            "measured_order": measured_order,
            "order": "six deterministic permutations cycling; no best-run selection",
            "timing": "engine construction + input read/hash/decode + simulation + EOF drain; no tracemalloc",
            "outside_timing": "model load, GC preconditioning, bounded summary/hash reduction, publication",
            "memory": "separate replay tracemalloc runs; frozen model loaded before tracing; peak traced replay allocations, NOT total resident model memory or process RSS",
            "sinks": "null/aggregate; no disk audit or retained event/fill/markout traces",
            "quantiles": "linear empirical interpolation; p99 is a RUN quantile, NOT per-event latency",
            "host_policy": "ordinary unpinned local process; CPU frequency/power/other load uncontrolled",
        },
        "modes": modes,
        "observation_core_summary_book_fill_markout_latency_parity": True,
        "limitations": [
            "offline Python replay throughput, not exchange or trading latency",
            "policy changes quote/fill workload; not isolated inference cost or evidence of benefit",
            "fixture/model calibration origin must be disclosed; timing does not establish real-market representativeness",
            "no dedicated-host release gate, allocations-per-event, RSS, soak or full audit-I/O performance claim",
            "matched-round ratios are descriptive; no inferential performance confidence interval",
        ],
    }
    report["report_sha256"] = identity(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True, help="new output; never overwrite evidence")
    parser.add_argument("--env", default=str(REPO_ROOT / ".env.example"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--requote-ms", type=int, help="explicit shared quote cadence; not a tuned optimum")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--memory-runs", type=int, default=1)
    args = parser.parse_args(argv)
    if args.json_out.exists() or args.json_out.with_name(args.json_out.name + ".partial").exists():
        raise FileExistsError("benchmark output already exists")
    # Explicit shared reference profile; snapshot every effective override.
    cfg = replace(
        load_config(args.env, inherit_environment=False),
        symbols=(args.symbol,),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
    )
    if args.requote_ms is not None:
        integer(args.requote_ms, "requote milliseconds", minimum=1)
        cfg = replace(cfg, mm_requote_ms=args.requote_ms)
    report = benchmark_hmm(
        args.file,
        args.model,
        cfg,
        symbol=args.symbol,
        warmups=args.warmups,
        repetitions=args.repetitions,
        memory_runs=args.memory_runs,
        progress=lambda value: print(value, flush=True),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    publish_json(args.json_out, report)
    for mode, value in report["modes"].items():
        print(
            f"{mode}: median={value['wall_ns']['median'] / 1e9:.6f}s; matched overhead={value['median_relative_overhead_percent']:.2f}%; traced peak={value['peak_traced_bytes']} bytes"
        )
    print("Offline unpinned Python replay; not exchange latency or a policy-benefit claim.")
    print(f"Report: {args.json_out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
