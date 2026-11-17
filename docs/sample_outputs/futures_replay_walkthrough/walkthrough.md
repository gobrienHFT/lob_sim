# Futures Replay Walkthrough

## Input

- Source: [`input_fixture.ndjson`](input_fixture.ndjson)
- Input type: synthetic deterministic walkthrough fixture
- Symbol: `BTCUSDT`
- Purpose: distinguish a change in displayed depth from the trade that triggers a modeled passive fill.

## Exact Command Used

```bash
python scripts/refresh_futures_showcase.py
```

## Continuity

- Snapshot seeds the book at `ts_local=1.0` with `lastUpdateId=100`.
- The first accepted diff at `ts_local=2.0` has `U=95`, `u=105`, `pu=94`, so it covers the snapshot id because `95 <= 100 <= 105`.
- The next diff at `ts_local=2.2` has `pu=105`, which matches the prior `u=105`.
- The final diff at `ts_local=3.6` has `pu=106`, which matches the prior `u=106`.

## Why the bid fills

At `ts_local=2.0` the strategy joins the bid at `100.0` for one lot
(`0.001` BTC). Two lots are displayed ahead of it. Here are the two
`queue_consumption` rows from [the trace](event_trace.csv); applied lots
include consumption ahead **and** any resulting fill.

| Local time (s) | Source | Observed lots | Applied lots | Unmatched lots |
| --- | --- | ---: | ---: | ---: |
| 2.2 | depth_update | 1 | 0 | 1 |
| 2.4 | agg_trade | 3 | 3 | 0 |

The displayed bid shrinks, but that observation does not advance the order
in this trade-triggered scenario. Next, a sell print of `0.003` BTC at
`100.0` consumes the two modeled lots ahead and fills the one-lot bid.
In [the fill row](trades.csv), `evidence_ids` points to input rows 3 and 5:
the book observation used at arrival and the triggering trade. These are
public-L2 **scenario** mechanics on a synthetic tape, not private FIFO evidence.

The one-second markout is due at 3.4 s. The next observed mid is at 3.6 s:
`100.05`, or `+0.05` per BTC relative to the buy price. That is a mark
observed 200 ms after the target, not a quote observed exactly at the horizon.

## What to Inspect First

1. [`summary.json`](summary.json)
2. [`trades.csv`](trades.csv)
3. [`summary.csv`](summary.csv)
