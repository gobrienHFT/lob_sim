# Futures Replay Reference Benchmark

- Benchmark date: `2026-05-31T19:36:35Z`
- Commit SHA at run time: `7952b38bfec64c71f51f7e0091f4caf3e5f79fc3`
- Git dirty at run time: `False`
- OS/platform: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `729d4ed0bd5afc0ea7d8594fbefe64cc055be2d2b16c3d992babed6cf814c3f4`
- Config digest: `96a334750a6d40d0084088ba1c252cb54205c395c3310b9ae54db6f6bf4f33f4`
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
- Wall time: `1.721339s`
- Events/sec: `46.48`
- Loop latency p50: `276.85us`
- Loop latency p99: `289514.63us`
- Peak traced memory: `0.78 MiB`

This result is specific to this machine, this Python interpreter, and this committed fixture. The fixture is intentionally small, so fixed interpreter and validation overhead dominate.

## Structured Result

The committed JSON artifact contains the schema version, input/config/source metadata, event counts, p50/p99 loop timing, events/sec, and traced-memory peak. Prefer the JSON file for repeated local comparisons; this Markdown file is the human-readable summary.
