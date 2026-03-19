# Futures Replay Reference Benchmark

- Benchmark date: `2026-03-19 21:17:35 +00:00`
- Commit SHA: `3d219dd7856f46db4d1e080e74affea2632e5661`
- OS: `Microsoft Windows 11 Pro (10.0.26200)`
- CPU: `Intel(R) Core(TM) i5-1035G7 CPU @ 1.20GHz`
- Python: `3.13.1`
- Input file: `data/raw_1772633471.ndjson`
- Input status: local-only recorded BTCUSDT raw file (not committed)

Exact benchmark commands:

1. `python experiments/benchmark_futures_replay.py --file data/raw_1772633471.ndjson --env .env.example`
2. `python experiments/benchmark_futures_replay.py --file data/raw_1772633471.ndjson --env .env.example`
3. `python experiments/benchmark_futures_replay.py --file data/raw_1772633471.ndjson --env .env.example`

Median summary numbers:

- Total events: `1997`
- Snapshot events: `2`
- Depth events: `281`
- AggTrade events: `1713`
- Gap count: `1`
- Wall time: `4.325651s`
- Events/sec: `461.66`
- Loop latency p50: `66.10us`
- Loop latency p99: `32194.98us`
- Peak traced memory: `1.28 MiB`

This result is specific to this machine and dataset.

## Run 1 Raw Stdout

```text
Replay benchmark file: data\raw_1772633471.ndjson
Total events: 1997
Snapshot events: 2
Depth events: 281
AggTrade events: 1713
Gap count: 1
Wall time: 4.814621s
Events/sec: 414.78
Loop latency p50: 72.40us
Loop latency p99: 33975.98us
Peak traced memory: 1.28 MiB
```

## Run 2 Raw Stdout

```text
Replay benchmark file: data\raw_1772633471.ndjson
Total events: 1997
Snapshot events: 2
Depth events: 281
AggTrade events: 1713
Gap count: 1
Wall time: 4.325651s
Events/sec: 461.66
Loop latency p50: 66.10us
Loop latency p99: 32194.98us
Peak traced memory: 1.28 MiB
```

## Run 3 Raw Stdout

```text
Replay benchmark file: data\raw_1772633471.ndjson
Total events: 1997
Snapshot events: 2
Depth events: 281
AggTrade events: 1713
Gap count: 1
Wall time: 3.613670s
Events/sec: 552.62
Loop latency p50: 62.90us
Loop latency p99: 26668.89us
Peak traced memory: 1.28 MiB
```
