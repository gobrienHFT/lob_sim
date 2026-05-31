# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a small committed-fixture reference for reproducibility and instrumentation, not as a low-latency claim.

## Published Reference Run

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `826795685d02f78a5fac2d07b409c1d7c37b2cb3ddfbacd5c79d99e79d9997be`
- Config digest: `f7707661e9bfb641a9771046406699948081496b10d23e1c878cb6b14052e562`
- Machine: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Benchmark date: `2026-05-31T13:53:54Z`
- Raw stdout: [docs/benchmark_results/futures_replay_reference.md](benchmark_results/futures_replay_reference.md)

Event counts for the committed input:

- Total events: `80`
- Snapshot events: `1`
- Depth events: `9`
- AggTrade events: `69`
- Gap count: `0`

| Run | Total events | Snapshot events | Depth events | AggTrade events | Gap count | Wall time (s) | Events/sec | Loop latency p50 (us) | Loop latency p99 (us) | Peak traced memory (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Reference | 80 | 1 | 9 | 69 | 0 | 0.898962 | 88.99 | 146.35 | 128700.99 | 0.78 |

Exact benchmark command:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example
```

Interpretation:

- This is a tiny committed replay clip, so fixed overhead dominates throughput.
- The value of the benchmark is provenance: input digest, config digest, Python/platform/git metadata, p50/p99 loop timing, events/sec, memory, and gap count are reported together.
- For serious throughput analysis, use a larger recorded file and publish the input digest plus hardware context alongside the result.

## Benchmark Tool

Use the lightweight replay benchmark runner:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example
```

The script prints:

- input SHA-256
- non-secret config digest
- Python/platform/git metadata
- total events
- snapshot events
- depth events
- aggTrade events
- gap count
- wall time
- events per second
- p50 / p99 loop timing
- peak traced memory

## Caveats

- `tracemalloc` measures Python-traced allocations, not every native allocation.
- Loop timing includes Python bookkeeping overhead from the benchmark itself.
- Fixture-scale benchmark numbers should not be compared to colocated production systems.
- Benchmark numbers should always be reported with dataset size, input digest, config digest, and hardware context.
