from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.benchmark_futures_replay import benchmark_replay, write_benchmark_json

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
REVIEWER_COMMAND = (
    "python experiments/benchmark_futures_replay.py "
    "--file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson "
    "--env .env.example "
    "--mode all "
    "--pack docs/sample_outputs/futures_stress_case "
    "--json-out outputs/futures_benchmark.json"
)


def _created_at(metadata: dict[str, Any]) -> str:
    value = str(metadata["benchmark_created_at_utc"])
    return value.split(".", 1)[0] + "Z" if "." in value else value


def _format_instrument_specs(metadata: dict[str, Any]) -> str:
    specs = metadata.get("instrument_specs")
    if not isinstance(specs, dict) or not specs:
        return "`<none>`"
    rendered: list[str] = []
    for symbol, spec in sorted(specs.items()):
        if not isinstance(spec, dict):
            rendered.append(f"`{symbol}`")
            continue
        rendered.append(
            f"`{symbol}` tick `{spec.get('tick_size')}` lot `{spec.get('step_size')}` "
            f"unit `{spec.get('quantity_unit')}` price `{spec.get('price_currency')}` "
            f"multiplier `{spec.get('contract_multiplier')}` venue `{spec.get('venue')}`"
        )
    return "; ".join(rendered)


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
- Feed adapter: `{metadata["feed_adapter"]["name"]}` (`{metadata["feed_adapter"]["venue_label"]}`)
- Instrument specs: {_format_instrument_specs(metadata)}
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

The committed JSON artifact contains the schema version, input/config/source/instrument metadata, event counts, p50/p99 loop timing, events/sec, and traced-memory peak. Prefer the JSON file for repeated local comparisons; this Markdown file is the human-readable summary.
"""


def _render_benchmark_doc(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    counts = result["event_counts"]
    timing = result["timing"]
    memory = result["memory"]

    return f"""# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a small committed-fixture reference for reproducibility and instrumentation, not as a low-latency claim.

For determinism rather than throughput, run `python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example`; it compares repeated in-memory summary and event-trace hashes instead of timing a single pass.

For modeled latency sensitivity rather than benchmark throughput, use [docs/strategy_results/futures_latency_sweep_reference.md](strategy_results/futures_latency_sweep_reference.md). It varies replay order-arrival and cancel-ack delays and reports queue/fill metrics without treating the numbers as production gateway latency.

## Published Reference Run

- Input file: `{metadata["input_file"]}`
- Input SHA-256: `{metadata["input_sha256"]}`
- Config digest: `{metadata["config_digest"]}`
- Feed adapter: `{metadata["feed_adapter"]["name"]}` (`{metadata["feed_adapter"]["venue_label"]}`)
- Instrument specs: {_format_instrument_specs(metadata)}
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
- The value of the benchmark is provenance: input digest, full non-secret config metadata, instrument specs, feed adapter, Python/platform/git metadata, p50/p99 loop timing, events/sec, memory, and gap count are reported together.
- For serious throughput analysis, use a larger recorded file and publish the input digest plus hardware context alongside the result.

## Benchmark Tool

Use the lightweight replay benchmark runner:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --json-out outputs/futures_benchmark.json
```

Use the reviewer benchmark mode to time replay-only, simulation without writing artifacts, simulation plus event-trace export, and futures-pack audit:

```bash
{REVIEWER_COMMAND}
```

The script prints:

- input SHA-256
- non-secret config snapshot and digest
- feed adapter
- instrument specs
- Python/platform/git metadata
- total events
- exchangeInfo events
- snapshot events
- depth events
- aggTrade events
- gap count
- wall time
- events per second
- p50 / p99 loop timing for replay and p50 / p99 wall timing for reviewer benchmark phases
- peak traced memory

With `--json-out`, the same evidence is written as a machine-readable artifact with schema version, metadata, event counts, timing, and memory sections. Metadata includes the full non-secret config snapshot and normalized instrument specs so repeated runs can be audited without guessing units or environment settings. In reviewer mode, the JSON includes per-mode timing for replay-only, simulation without export, simulation plus export, and pack audit. This is the preferred format for comparing repeated local runs or attaching benchmark evidence to a review.

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
    REFERENCE_MD.write_text(_render_reference(result), encoding="utf-8", newline="\n")
    FUTURES_BENCHMARKS.write_text(_render_benchmark_doc(result), encoding="utf-8", newline="\n")
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
