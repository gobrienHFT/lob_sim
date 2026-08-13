# Futures Stress Case

This is a deterministic synthetic-but-exchange-shaped BTCUSDT fixture. It is intentionally not recorded market data; it is a compact stress pack for queue and event-ordering audit paths that are hard to see in a short real clip.

The fixture uses Binance USD-M-style `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` records, then replays them through a scripted strategy that exists only for this evidence pack.

## Coverage

- Snapshot-seeded visible queue ahead and partial passive fills.
- Mutually exclusive trade-only attribution: depth decreases remain diagnostic and unmatched.
- `aggTrade`-inferred passive fills; no depth-inferred or taker fill in this post-only scenario.
- Per-fill scenario, resolvable input evidence, validity, synthetic queue trajectory, configured latency draws, lifecycle state, and fee-model provenance.
- Signed post-fill markout accounting.
- Cancel latency, including an old quote fill before acknowledgement.
- Same-timestamp cancel acknowledgement before public trade consumption in the legacy coarse-timestamp compatibility policy.
- Arrival-time post-only rejection and conservative self-trade prevention.
- No-gap replay continuity; `book_gap_count` stays zero.

## Summary

- Records processed: `14`
- Depth updates: `8`
- AggTrade records: `4`
- Fill-source counts: `{"agg_trade": 1, "depth_update": 0, "taker_order": 0}`
- Order lifecycle counts: `{"arrival_scheduled": 4, "arrived": 4, "cancel_acknowledged": 2, "cancel_requested": 2, "expired_unfilled_arrivals": 2, "immediate_fill_arrivals": 0, "rested_after_arrival": 2, "self_trade_prevented": 1}`
- Depth diagnostic unmatched lots: `14`

## Files

- Input: `input_stress.ndjson`
- Summary: `summary.json` and `summary.csv`
- Trades: `trades.csv`
- Event trace: `event_trace.csv`
- Manifest: `manifest.json`
- Notes: `case_notes.md`
