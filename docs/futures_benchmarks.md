# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a small committed-fixture reference for reproducibility and instrumentation, not as a low-latency claim.

For determinism rather than throughput, run `python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example`; it compares repeated in-memory summary and event-trace hashes instead of timing a single pass.

For modeled latency sensitivity rather than benchmark throughput, use [docs/strategy_results/futures_latency_sweep_reference.md](strategy_results/futures_latency_sweep_reference.md). It varies replay order-arrival and cancel-ack delays and reports queue/fill metrics without treating the numbers as production gateway latency.

## Published Reference Run

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `729d4ed0bd5afc0ea7d8594fbefe64cc055be2d2b16c3d992babed6cf814c3f4`
- Config digest: `96a334750a6d40d0084088ba1c252cb54205c395c3310b9ae54db6f6bf4f33f4`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Instrument specs: `BTCUSDT` tick `0.10` lot `0.001` unit `BTC` price `USDT` multiplier `1` venue `BINANCE_USDM`
- Machine: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Benchmark date: `2026-06-01T00:08:01Z`
- Human-readable output: [docs/benchmark_results/futures_replay_reference.md](benchmark_results/futures_replay_reference.md)
- Structured JSON: [docs/benchmark_results/futures_replay_reference.json](benchmark_results/futures_replay_reference.json)

Event counts for the committed input:

- Total events: `80`
- ExchangeInfo events: `1`
- Snapshot events: `1`
- Depth events: `9`
- AggTrade events: `69`
- Gap count: `0`

| Run | Total events | ExchangeInfo events | Snapshot events | Depth events | AggTrade events | Gap count | Wall time (s) | Events/sec | Loop latency p50 (us) | Loop latency p99 (us) | Peak traced memory (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Reference | 80 | 1 | 1 | 9 | 69 | 0 | 0.701763 | 114.00 | 178.40 | 100968.35 | 0.67 |

Exact benchmark command:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --json-out docs/benchmark_results/futures_replay_reference.json
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
- p50 / p99 loop timing
- peak traced memory

With `--json-out`, the same evidence is written as a machine-readable artifact with schema version, metadata, event counts, timing, and memory sections. Metadata includes the full non-secret config snapshot and normalized instrument specs so repeated runs can be audited without guessing units or environment settings. This is the preferred format for comparing repeated local runs or attaching benchmark evidence to a review.

## Caveats

- `tracemalloc` measures Python-traced allocations, not every native allocation.
- Loop timing includes Python bookkeeping overhead from the benchmark itself.
- Fixture-scale benchmark numbers should not be compared to colocated production systems.
- Benchmark numbers should always be reported with dataset size, input digest, config digest, and hardware context.
