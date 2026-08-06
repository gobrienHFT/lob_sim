from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import statistics
import subprocess
import sys
import tracemalloc
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from .config import Config, load_config
from .provenance import build_run_provenance
from .replay.reader import iter_records
from .sim.engine import SimulationEngine

BENCHMARK_SCHEMA_VERSION = "lob_sim.benchmark.v1"
DEFAULT_KNOWN_LIMITATIONS = (
    "Wall-clock timings include SimulationEngine construction and complete fixture replay.",
    "Operating-system scheduling, background load, CPU frequency, and thermal state are not controlled.",
    (
        "Events per second weights every yielded fixture record equally despite different payload sizes "
        "and work."
    ),
    "The benchmark measures end-to-end throughput, not per-event latency or model correctness.",
    (
        "Peak-memory runs use tracemalloc and are separate from timing runs; tracemalloc reports Python "
        "allocations, not process RSS or native-library allocations."
    ),
    (
        "The generated default command assumes the same configuration is loadable from .env and fails on "
        "fingerprint mismatch."
    ),
)


def count_fixture_records(path: str | Path) -> int:
    """Count exactly the records the replay reader would yield."""
    return sum(1 for _ in iter_records(path))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between 0 and 1")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summarize(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("values must not be empty")
    return {
        "median": statistics.median(values),
        "p95": _percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.pstdev(values),
    }


def _shell_join(arguments: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _canonical_reproduction_command(
    fixture_path: Path,
    *,
    warmups: int,
    repetitions: int,
    fixture_sha256: str,
    config_fingerprint: str,
    code_fingerprint: str,
) -> str:
    return _shell_join(
        [
            sys.executable,
            "-m",
            "lob_sim.benchmark",
            "--input",
            str(fixture_path),
            "--env",
            ".env",
            "--warmups",
            str(warmups),
            "--repetitions",
            str(repetitions),
            "--expect-fixture-sha256",
            fixture_sha256,
            "--expect-config-fingerprint",
            config_fingerprint,
            "--expect-code-fingerprint",
            code_fingerprint,
        ]
    )


def benchmark_simulation(
    input_path: str | Path,
    config: Config,
    *,
    warmups: int = 1,
    repetitions: int = 5,
    known_limitations: Sequence[str] = (),
    reproduction_command: str | None = None,
    invocation_command: str | None = None,
) -> dict[str, Any]:
    """Benchmark deterministic fixture replay using a fresh engine for every run."""
    if warmups < 0:
        raise ValueError("warmups must be >= 0")
    if repetitions <= 0:
        raise ValueError("repetitions must be > 0")

    fixture_path = Path(input_path).resolve()
    provenance = build_run_provenance(fixture_path, config)
    record_count = count_fixture_records(fixture_path)
    provenance["fixture"]["record_count"] = record_count

    for _ in range(warmups):
        SimulationEngine(config).run(fixture_path)

    runs: list[dict[str, float | int]] = []
    for run_number in range(1, repetitions + 1):
        started_ns = perf_counter_ns()
        SimulationEngine(config).run(fixture_path)
        elapsed_ns = max(1, perf_counter_ns() - started_ns)
        duration_seconds = elapsed_ns / 1_000_000_000
        events_per_second = record_count / duration_seconds
        runs.append(
            {
                "run": run_number,
                "duration_seconds": duration_seconds,
                "record_count": record_count,
                "events_per_second": events_per_second,
            }
        )

    memory_runs: list[dict[str, int]] = []
    for run_number in range(1, repetitions + 1):
        if tracemalloc.is_tracing():
            raise RuntimeError("benchmark_simulation requires tracemalloc not to be active")
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            SimulationEngine(config).run(fixture_path)
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        memory_runs.append({"run": run_number, "peak_bytes": peak_bytes})

    durations = [float(run["duration_seconds"]) for run in runs]
    throughput = [float(run["events_per_second"]) for run in runs]
    peak_allocations = [float(run["peak_bytes"]) for run in memory_runs]
    limitations = list(DEFAULT_KNOWN_LIMITATIONS)
    for limitation in known_limitations:
        if limitation not in limitations:
            limitations.append(limitation)

    if reproduction_command is None:
        reproduction_command = _canonical_reproduction_command(
            fixture_path,
            warmups=warmups,
            repetitions=repetitions,
            fixture_sha256=str(provenance["fixture"]["sha256"]),
            config_fingerprint=str(provenance["configuration"]["fingerprint_sha256"]),
            code_fingerprint=str(provenance["code"]["fingerprint_sha256"]),
        )

    baseline = dict(runs[0])

    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": provenance["created_at_utc"],
        "fixture": provenance["fixture"],
        "configuration": provenance["configuration"],
        "environment": provenance["environment"],
        "code": provenance["code"],
        "reproduction_command": reproduction_command,
        "invocation_command": invocation_command,
        "protocol": {
            "warmup_runs": warmups,
            "measured_runs": repetitions,
            "fresh_engine_per_run": True,
            "timing_instrumentation": "perf_counter_ns with tracemalloc disabled",
            "memory_measurement_runs": repetitions,
            "timing_scope": (
                "Immediately before engine construction through completion of SimulationEngine.run."
            ),
        },
        "metric_definitions": {
            "record_count": {
                "unit": "records",
                "definition": (
                    "Number of nonblank NDJSON records yielded by lob_sim.replay.reader.iter_records."
                ),
            },
            "duration_seconds": {
                "unit": "seconds",
                "clock": "time.perf_counter_ns",
                "definition": (
                    "Elapsed wall-clock time for fresh engine construction and one complete replay."
                ),
            },
            "events_per_second": {
                "unit": "records/second",
                "definition": "fixture record_count divided by duration_seconds for the measured run.",
            },
            "peak_bytes": {
                "unit": "bytes",
                "allocator_scope": "Python allocations traced by tracemalloc",
                "definition": (
                    "Peak traced Python memory during fresh engine construction and complete replay."
                ),
            },
            "p95": {
                "unit": "same as summarized metric",
                "definition": "Linear interpolation at index (sample_count - 1) * 0.95 over sorted values.",
            },
            "stdev": {
                "unit": "same as summarized metric",
                "definition": "Population standard deviation across measured runs.",
            },
        },
        "runs": runs,
        "memory_runs": memory_runs,
        "summary": {
            "duration_seconds": _summarize(durations),
            "events_per_second": _summarize(throughput),
            "peak_bytes": _summarize(peak_allocations),
        },
        "comparator": {
            "baseline": {
                "type": "first_measured_run",
                **baseline,
            },
            "external_comparator": None,
            "external_comparator_statement": (
                "No external implementation or prior-version comparator was supplied."
            ),
            "comparison_scope": {
                "fixture_sha256": provenance["fixture"]["sha256"],
                "code_fingerprint_sha256": provenance["code"]["fingerprint_sha256"],
                "configuration_fingerprint_sha256": provenance["configuration"]["fingerprint_sha256"],
            },
        },
        "known_limitations": limitations,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark deterministic lob_sim fixture replay")
    parser.add_argument("--input", required=True, help="NDJSON or NDJSON gzip fixture")
    parser.add_argument("--env", default=".env", help="Configuration environment file")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", help="Optional path for the JSON report")
    parser.add_argument("--expect-fixture-sha256")
    parser.add_argument("--expect-config-fingerprint")
    parser.add_argument("--expect-code-fingerprint")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(raw_arguments)
    exact_command = _shell_join([sys.executable, "-m", "lob_sim.benchmark", *raw_arguments])
    config = load_config(args.env)
    preflight = build_run_provenance(args.input, config)
    expected_values = {
        "fixture SHA-256": (args.expect_fixture_sha256, preflight["fixture"]["sha256"]),
        "configuration fingerprint": (
            args.expect_config_fingerprint,
            preflight["configuration"]["fingerprint_sha256"],
        ),
        "code fingerprint": (args.expect_code_fingerprint, preflight["code"]["fingerprint_sha256"]),
    }
    for label, (expected, actual) in expected_values.items():
        if expected is not None and expected != actual:
            parser.error(f"{label} mismatch: expected {expected}, got {actual}")

    report = benchmark_simulation(
        args.input,
        config,
        warmups=args.warmups,
        repetitions=args.repetitions,
        invocation_command=exact_command,
    )

    if args.output:
        output_path = Path(args.output).resolve()
        report["output_path"] = str(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
