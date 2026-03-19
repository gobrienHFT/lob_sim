# Futures Replay Walkthrough

This pack is a tiny deterministic walkthrough fixture for the futures replay and queue-aware passive-fill simulator. The input is synthetic on purpose so snapshot seeding, diff continuity, queue-ahead reduction, and one passive fill stay inspectable on GitHub.

## Files

- [`input_fixture.ndjson`](input_fixture.ndjson): deterministic synthetic Binance USD-M style event stream for one symbol.
- [`walkthrough.md`](walkthrough.md): continuity, queue-ahead, and passive-fill notes tied to exact timestamps.
- [`summary.json`](summary.json): machine-readable run summary.
- [`summary.csv`](summary.csv): flat summary row for quick scanning.
- [`trades.csv`](trades.csv): passive-fill rows produced by the replay.

## Regenerate

From the repo root:

```bash
python scripts/refresh_futures_showcase.py
```

Inspect first: [`summary.json`](summary.json), [`trades.csv`](trades.csv), then [`walkthrough.md`](walkthrough.md).
