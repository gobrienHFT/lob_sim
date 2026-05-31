# Futures Replay Reference Benchmark

- Benchmark date: `2026-05-31T13:53:54Z`
- Commit SHA at run time: `5438f49f06c868365735b6607696cb85479abcbd`
- Git dirty at run time: `true`
- OS/platform: `Windows-11-10.0.26200-SP0`
- Python: `3.13.1`
- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `826795685d02f78a5fac2d07b409c1d7c37b2cb3ddfbacd5c79d99e79d9997be`
- Config digest: `f7707661e9bfb641a9771046406699948081496b10d23e1c878cb6b14052e562`

Exact benchmark command:

```bash
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example
```

Summary:

- Total events: `80`
- Snapshot events: `1`
- Depth events: `9`
- AggTrade events: `69`
- Gap count: `0`
- Wall time: `0.898962s`
- Events/sec: `88.99`
- Loop latency p50: `146.35us`
- Loop latency p99: `128700.99us`
- Peak traced memory: `0.78 MiB`

This result is specific to this machine, this Python interpreter, and this committed fixture. The fixture is intentionally small, so fixed interpreter and validation overhead dominate.

## Raw Stdout

```text
Replay benchmark file: docs\sample_outputs\futures_recorded_clip_case\input_clip.ndjson
Input SHA-256: 826795685d02f78a5fac2d07b409c1d7c37b2cb3ddfbacd5c79d99e79d9997be
Config digest: f7707661e9bfb641a9771046406699948081496b10d23e1c878cb6b14052e562
Python: 3.13.1
Platform: Windows-11-10.0.26200-SP0
Git commit: 5438f49f06c868365735b6607696cb85479abcbd
Git branch: master
Git dirty: True
Total events: 80
Snapshot events: 1
Depth events: 9
AggTrade events: 69
Gap count: 0
Wall time: 0.898962s
Events/sec: 88.99
Loop latency p50: 146.35us
Loop latency p99: 128700.99us
Peak traced memory: 0.78 MiB
Benchmark metadata JSON:
{
  "benchmark_created_at_utc": "2026-05-31T13:53:54.160822Z",
  "input_file": "docs\\sample_outputs\\futures_recorded_clip_case\\input_clip.ndjson",
  "input_sha256": "826795685d02f78a5fac2d07b409c1d7c37b2cb3ddfbacd5c79d99e79d9997be",
  "config_digest": "f7707661e9bfb641a9771046406699948081496b10d23e1c878cb6b14052e562",
  "python_version": "3.13.1",
  "platform": "Windows-11-10.0.26200-SP0",
  "source": {
    "git_commit": "5438f49f06c868365735b6607696cb85479abcbd",
    "git_branch": "master",
    "git_dirty": true
  }
}
```
