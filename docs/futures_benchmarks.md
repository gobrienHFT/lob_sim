# Futures Benchmarks

No benchmark numbers are committed here because they depend on machine, Python version, dataset mix, and whether tracing is enabled.

## What To Benchmark

- replay throughput in events per second
- p50 and p99 per-record loop timing
- peak traced memory
- gap count surfaced during replay
- repeatability of replay results on the same input

## Benchmark Tool

Use the lightweight replay benchmark runner:

```bash
python experiments/benchmark_futures_replay.py --file data/raw_....ndjson --env .env.example
```

The script prints:

- total events
- depth events
- trade events
- wall time
- events per second
- p50 / p99 loop timing
- peak traced memory

## Reporting Template

| Field | Value |
|---|---|
| CPU / machine | `TBD` |
| Python version | `TBD` |
| Input file | `TBD` |
| Total events | `TBD` |
| Wall time | `TBD` |
| Events per second | `TBD` |
| Loop latency p50 | `TBD` |
| Loop latency p99 | `TBD` |
| Peak traced memory | `TBD` |
| Gap count | `TBD` |

## Caveats

- `tracemalloc` measures Python-traced allocations, not every native allocation.
- Loop timing in the benchmark includes Python bookkeeping overhead from the benchmark itself.
- Benchmark numbers should always be reported with dataset size and hardware context.
