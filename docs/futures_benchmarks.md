# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a small committed-fixture reference for reproducibility and instrumentation, not as a low-latency claim.

For determinism rather than throughput, run `python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example`; it compares repeated in-memory summary and event-trace hashes instead of timing a single pass.

For modeled latency sensitivity rather than benchmark throughput, use [docs/strategy_results/futures_latency_sweep_reference.md](strategy_results/futures_latency_sweep_reference.md). It varies replay order-arrival and cancel-ack delays and reports queue/fill metrics without treating the numbers as production gateway latency.

## Published Reference Run

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Config digest: `71e82aca923842ca836f224170cd3f89e67ec0821e6d4bae73a969c3e82b92db`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Instrument specs: `BTCUSDT` tick `0.10` lot `0.001` unit `BTC` price `USDT` multiplier `1` venue `BINANCE_USDM`
- Machine: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Benchmark date: `2026-08-11T16:30:38Z`
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
| Reference | 80 | 1 | 1 | 9 | 69 | 0 | 0.494617 | 161.74 | 133.95 | 64577.26 | 0.68 |

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

Use the reviewer benchmark mode to time replay-only, aggregate-only simulation without writing artifacts, simulation plus event-trace export, and futures-pack audit:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --mode all --pack docs/sample_outputs/futures_stress_case --json-out outputs/futures_benchmark.json
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

With `--json-out`, the same evidence is written as a machine-readable artifact with schema version, metadata, event counts, timing, and memory sections. Metadata includes the full non-secret config snapshot and normalized instrument specs so repeated runs can be audited without guessing units or environment settings. In reviewer mode, the JSON includes per-mode timing for replay-only, aggregate-only simulation without export, simulation plus export, and pack audit. The no-export mode records both event-trace and fill/markout retention contracts; neither detail stream is retained in memory, and deterministic audit-chain hashes remain available. This is the preferred format for comparing repeated local runs or attaching benchmark evidence to a review.

## Caveats

- `tracemalloc` measures Python-traced allocations, not every native allocation.
- The aggregate-only simulation claim is bounded by configured live state and `SIM_MAX_PENDING_MARKOUTS`; a hostile same-time burst may hit that cap and fail closed.
- Fixture-scale CSV export deliberately retains detail rows and therefore is not a tape-duration-independent memory benchmark.
- Loop timing includes Python bookkeeping overhead from the benchmark itself.
- Fixture-scale benchmark numbers should not be compared to colocated production systems.
- Benchmark numbers should always be reported with dataset size, input digest, config digest, and hardware context.
