from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from experiments.benchmark_futures_replay import benchmark_replay, write_benchmark_json


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = Path("docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson")
ENV_PATH = ".env.example"
REFERENCE_MD = REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md"
REFERENCE_JSON = REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.json"
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
COMMAND = (
    "python experiments/benchmark_futures_replay.py "
    "--file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson "
    "--env .env.example "
    "--json-out docs/benchmark_results/futures_replay_reference.json"
)


def _created_at(metadata: dict[str, Any]) -> str:
    value = str(metadata["benchmark_created_at_utc"])
    return value.split(".", 1)[0] + "Z" if "." in value else value


def _render_reference(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    counts = result["event_counts"]
    timing = result["timing"]
    memory = result["memory"]
    source = metadata["source"]

    return f"""# Futures Replay Reference Benchmark

- Benchmark date: `{_created_at(metadata)}`
- Commit SHA at run time: `{source["git_commit"]}`
- Git dirty at run time: `{source["git_dirty"]}`
- OS/platform: `{metadata["platform"]}`
- Python: `{metadata["python_version"]}`
- Input file: `{metadata["input_file"]}`
- Input SHA-256: `{metadata["input_sha256"]}`
- Config digest: `{metadata["config_digest"]}`
- Structured JSON: [`futures_replay_reference.json`](futures_replay_reference.json)

Exact benchmark command:

```bash
{COMMAND}
```

Summary:

- Total events: `{counts["total_events"]}`
- ExchangeInfo events: `{counts["exchange_info_events"]}`
- Snapshot events: `{counts["snapshot_events"]}`
- Depth events: `{counts["depth_events"]}`
- AggTrade events: `{counts["agg_trade_events"]}`
- Gap count: `{counts["gap_count"]}`
- Wall time: `{timing["wall_time_seconds"]:.6f}s`
- Events/sec: `{timing["events_per_second"]:.2f}`
- Loop latency p50: `{timing["loop_latency_p50_us"]:.2f}us`
- Loop latency p99: `{timing["loop_latency_p99_us"]:.2f}us`
- Peak traced memory: `{memory["peak_traced_mib"]:.2f} MiB`

This result is specific to this machine, this Python interpreter, and this committed fixture. The fixture is intentionally small, so fixed interpreter and validation overhead dominate.

## Structured Result

The committed JSON artifact contains the schema version, input/config/source metadata, event counts, p50/p99 loop timing, events/sec, and traced-memory peak. Prefer the JSON file for repeated local comparisons; this Markdown file is the human-readable summary.
"""


def _render_benchmark_doc(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    counts = result["event_counts"]
    timing = result["timing"]
    memory = result["memory"]

    return f"""# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a small committed-fixture reference for reproducibility and instrumentation, not as a low-latency claim.

## Published Reference Run

- Input file: `{metadata["input_file"]}`
- Input SHA-256: `{metadata["input_sha256"]}`
- Config digest: `{metadata["config_digest"]}`
- Machine: `{metadata["platform"]}`
- Python: `{metadata["python_version"]}`
- Benchmark date: `{_created_at(metadata)}`
- Human-readable output: [docs/benchmark_results/futures_replay_reference.md](benchmark_results/futures_replay_reference.md)
- Structured JSON: [docs/benchmark_results/futures_replay_reference.json](benchmark_results/futures_replay_reference.json)

Event counts for the committed input:

- Total events: `{counts["total_events"]}`
- ExchangeInfo events: `{counts["exchange_info_events"]}`
- Snapshot events: `{counts["snapshot_events"]}`
- Depth events: `{counts["depth_events"]}`
- AggTrade events: `{counts["agg_trade_events"]}`
- Gap count: `{counts["gap_count"]}`

| Run | Total events | ExchangeInfo events | Snapshot events | Depth events | AggTrade events | Gap count | Wall time (s) | Events/sec | Loop latency p50 (us) | Loop latency p99 (us) | Peak traced memory (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Reference | {counts["total_events"]} | {counts["exchange_info_events"]} | {counts["snapshot_events"]} | {counts["depth_events"]} | {counts["agg_trade_events"]} | {counts["gap_count"]} | {timing["wall_time_seconds"]:.6f} | {timing["events_per_second"]:.2f} | {timing["loop_latency_p50_us"]:.2f} | {timing["loop_latency_p99_us"]:.2f} | {memory["peak_traced_mib"]:.2f} |

Exact benchmark command:

```bash
{COMMAND}
```

Interpretation:

- This is a tiny committed replay clip, so fixed overhead dominates throughput.
- The value of the benchmark is provenance: input digest, config digest, Python/platform/git metadata, p50/p99 loop timing, events/sec, memory, and gap count are reported together.
- For serious throughput analysis, use a larger recorded file and publish the input digest plus hardware context alongside the result.

## Benchmark Tool

Use the lightweight replay benchmark runner:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --json-out outputs/futures_benchmark.json
```

The script prints:

- input SHA-256
- non-secret config digest
- Python/platform/git metadata
- total events
- exchangeInfo events
- snapshot events
- depth events
- aggTrade events
- gap count
- wall time
- events per second
- p50 / p99 loop timing
- peak traced memory

With `--json-out`, the same evidence is written as a machine-readable artifact with schema version, metadata, event counts, timing, and memory sections. This is the preferred format for comparing repeated local runs or attaching benchmark evidence to a review.

## Caveats

- `tracemalloc` measures Python-traced allocations, not every native allocation.
- Loop timing includes Python bookkeeping overhead from the benchmark itself.
- Fixture-scale benchmark numbers should not be compared to colocated production systems.
- Benchmark numbers should always be reported with dataset size, input digest, config digest, and hardware context.
"""


def refresh_futures_benchmark_reference() -> dict[str, Path]:
    previous_cwd = Path.cwd()
    try:
        os.chdir(REPO_ROOT)
        result = benchmark_replay(INPUT_FILE, ENV_PATH)
    finally:
        os.chdir(previous_cwd)

    write_benchmark_json(result, REFERENCE_JSON)
    REFERENCE_MD.write_text(_render_reference(result), encoding="utf-8")
    FUTURES_BENCHMARKS.write_text(_render_benchmark_doc(result), encoding="utf-8")
    return {
        "reference_md": REFERENCE_MD,
        "reference_json": REFERENCE_JSON,
        "futures_benchmarks": FUTURES_BENCHMARKS,
    }


def main() -> int:
    paths = refresh_futures_benchmark_reference()
    print(f"Refreshed futures benchmark reference in {paths['reference_md'].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
