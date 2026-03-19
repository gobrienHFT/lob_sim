# Futures Benchmarks

Benchmark numbers are machine- and dataset-specific. Treat the published run below as a reference point for this workspace and this recorded input, not as a portability claim.

## Published Reference Run

- Input file: `data/raw_1772633471.ndjson`
- Input status: local-only recorded BTCUSDT raw file (not committed)
- Machine: `Microsoft Windows 11 Pro (10.0.26200)` on `Intel(R) Core(TM) i5-1035G7 CPU @ 1.20GHz`
- Python: `3.13.1`
- Benchmark date: `2026-03-19 21:17:35 +00:00`
- Commit SHA: `3d219dd7856f46db4d1e080e74affea2632e5661`
- Raw stdout: [docs/benchmark_results/futures_replay_reference.md](benchmark_results/futures_replay_reference.md)
- The tiny committed futures packs under [docs/sample_outputs/README.md](sample_outputs/README.md) are for mechanics and inspection, not the primary throughput benchmark.

Event counts for the chosen input:

- Total events: `1997`
- Snapshot events: `2`
- Depth events: `281`
- AggTrade events: `1713`
- Gap count: `1`

| Run | Total events | Snapshot events | Depth events | AggTrade events | Gap count | Wall time (s) | Events/sec | Loop latency p50 (us) | Loop latency p99 (us) | Peak traced memory (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1997 | 2 | 281 | 1713 | 1 | 4.814621 | 414.78 | 72.40 | 33975.98 | 1.28 |
| 2 | 1997 | 2 | 281 | 1713 | 1 | 4.325651 | 461.66 | 66.10 | 32194.98 | 1.28 |
| 3 | 1997 | 2 | 281 | 1713 | 1 | 3.613670 | 552.62 | 62.90 | 26668.89 | 1.28 |
| Median summary | 1997 | 2 | 281 | 1713 | 1 | 4.325651 | 461.66 | 66.10 | 32194.98 | 1.28 |

Exact benchmark command used:

```bash
python experiments/benchmark_futures_replay.py --file data/raw_1772633471.ndjson --env .env.example
```

Interpretation:

- On this machine and this recorded BTCUSDT input, replaying 1,997 events took `3.613670s` to `4.814621s` across three runs, with a median of `4.325651s` and `461.66` events/sec.
- The reference file contains one continuity gap, so the published run measures the current replay path with one resync event rather than an idealized gap-free fixture.

## Benchmark Tool

Use the lightweight replay benchmark runner:

```bash
python experiments/benchmark_futures_replay.py --file data/raw_....ndjson --env .env.example
```

The script prints:

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
- Loop timing in the benchmark includes Python bookkeeping overhead from the benchmark itself.
- Benchmark numbers should always be reported with dataset size and hardware context.
