# Futures Replay Walkthrough

## Input

- Source: [`input_fixture.ndjson`](input_fixture.ndjson)
- Input type: synthetic deterministic walkthrough fixture
- Symbol: `BTCUSDT`
- Purpose: show snapshot seeding, `U/u/pu` continuity, one queue-ahead reduction, and one passive fill in the smallest committed pack

## Exact Command Used

```bash
python scripts/refresh_futures_showcase.py
```

## Continuity

- Snapshot seeds the book at `ts_local=1.0` with `lastUpdateId=100`.
- The first accepted diff at `ts_local=2.0` has `U=95`, `u=105`, `pu=94`, so it covers the snapshot id because `95 <= 100 <= 105`.
- The next diff at `ts_local=2.2` has `pu=105`, which matches the prior `u=105`.
- The final diff at `ts_local=3.6` has `pu=106`, which matches the prior `u=106`.

## Queue-Ahead Example

- At `ts_local=2.0`, the baseline strategy posts a passive bid at `100.0` for `0.001`.
- The visible bid queue ahead at `100.0` is `0.002` from the seeded snapshot.
- At `ts_local=2.2`, the depth diff updates the bid level from `0.002` to `0.001`.
- Under the FIFO price-time model, that reduction consumes one visible lot ahead of the strategy order, so queue ahead falls from `2` lots to `1` lot at the same price level.

## Passive-Fill Example

- At `ts_local=2.4`, `aggTrade` prints `p=100.0`, `q=0.002`, `m=true`.
- In this model that is sell pressure into the bid at `100.0`.
- The remaining visible lot ahead is consumed first, then the resting strategy bid fills for `0.001` at `100.0`.
- The resulting passive fill is the single row in [`trades.csv`](trades.csv), and [`summary.json`](summary.json) records one fill with non-adverse 1-second markout.

## What to Inspect First

1. [`summary.json`](summary.json)
2. [`trades.csv`](trades.csv)
3. [`summary.csv`](summary.csv)
