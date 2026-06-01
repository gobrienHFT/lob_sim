# Futures Replay Reference Benchmark

- Benchmark date: `2026-06-01T15:56:56Z`
- Commit SHA at run time: `d7607bdc601400193c061d4999da62e77874653c`
- Git dirty at run time: `False`
- OS/platform: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Config digest: `96a334750a6d40d0084088ba1c252cb54205c395c3310b9ae54db6f6bf4f33f4`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Instrument specs: `BTCUSDT` tick `0.10` lot `0.001` unit `BTC` price `USDT` multiplier `1` venue `BINANCE_USDM`
- Structured JSON: [`futures_replay_reference.json`](futures_replay_reference.json)

Exact benchmark command:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --json-out docs/benchmark_results/futures_replay_reference.json
```

Summary:

- Total events: `80`
- ExchangeInfo events: `1`
- Snapshot events: `1`
- Depth events: `9`
- AggTrade events: `69`
- Gap count: `0`
- Wall time: `0.466308s`
- Events/sec: `171.56`
- Loop latency p50: `86.80us`
- Loop latency p99: `78106.05us`
- Peak traced memory: `0.67 MiB`

This result is specific to this machine, this Python interpreter, and this committed fixture. The fixture is intentionally small, so fixed interpreter and validation overhead dominate.

## Structured Result

The committed JSON artifact contains the schema version, input/config/source/instrument metadata, event counts, p50/p99 loop timing, events/sec, and traced-memory peak. Prefer the JSON file for repeated local comparisons; this Markdown file is the human-readable summary.
