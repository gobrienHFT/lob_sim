# Futures Stress Case

This is a deterministic synthetic-but-exchange-shaped BTCUSDT fixture. It is intentionally not recorded market data; it is a compact stress pack for queue and event-ordering audit paths that are hard to see in a short real clip.

The fixture uses Binance USD-M-style `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` records, then replays them through a scripted strategy that exists only for this evidence pack.

## Coverage

- Snapshot-seeded visible queue ahead and partial passive fills.
- Depth/`aggTrade` overlap netting on the same side and price.
- Depth-inferred, `aggTrade`-inferred, and marketable taker fills.
- Adverse and non-adverse post-fill markouts.
- Cancel latency, including an old quote fill before acknowledgement.
- Same-timestamp cancel acknowledgement before public trade consumption.
- Conservative self-trade prevention for a marketable strategy order.
- No-gap replay continuity; `book_gap_count` stays zero.

## Summary

- Records processed: `14`
- Depth updates: `8`
- AggTrade records: `4`
- Fill-source counts: `{"agg_trade": 2, "depth_update": 1, "taker_order": 2}`
- Order lifecycle counts: `{"arrival_scheduled": 4, "arrived": 4, "cancel_acknowledged": 2, "cancel_requested": 2, "expired_unfilled_arrivals": 1, "immediate_fill_arrivals": 1, "rested_after_arrival": 2, "self_trade_prevented": 1}`
- Public overlap-netted lots: `1`

## Files

- Input: `input_stress.ndjson`
- Summary: `summary.json` and `summary.csv`
- Trades: `trades.csv`
- Event trace: `event_trace.csv`
- Manifest: `manifest.json`
- Notes: `case_notes.md`
