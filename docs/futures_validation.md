# Futures Validation

## Scope

Validation in this repo is about invariants, deterministic behavior, and assumption visibility. It is not a claim of exchange-private fill validation.

## Invariants and What Is Tested

### Snapshot coverage rules

- The first accepted depth diff must cover the snapshot update id.
- Stale diffs older than the snapshot are ignored.
- This behavior is exercised in the book-sync tests.

### Diff continuity rules

- Later depth diffs are checked against the previous accepted `u` through `pu`.
- A continuity break raises or records a gap instead of silently advancing the book.
- Gap-affected diffs are skipped even when automatic re-snapshot is disabled, so `RESYNC_ON_GAP=0` cannot silently patch continuity.
- Simulation summaries surface replay event counts, applied depth-change counts, and book-gap counts so gap handling is visible in generated artifacts.
- Simulation event traces preserve the order of replay records, strategy decisions, scheduled order arrivals, cancel acknowledgements, and fills.
- Gap handling is covered by the gap-resync tests.

### FIFO / price-time assumptions

- Resting queue is modeled explicitly at each price level.
- Queue consumption happens from the front of the level.
- Later venue additions stay behind earlier resting orders at the same price.
- Regression tests cover reconciliation of near-simultaneous depth reductions and `aggTrade` prints so one public-feed signal is not consumed twice.
- Output summaries report observed public queue-consumption lots, overlap-netted lots, modeled queue-consumption candidates, actual FIFO queue lots consumed, and unmatched lots separately for depth reductions and `aggTrade` prints.
- Event traces export the same public queue-consumption accounting per symbol, side, price, source, and timestamp so the summary totals can be audited row by row.
- `scripts/audit_futures_pack.py --committed-futures` checks that committed replay inputs, pack summary JSON/CSV, trades, event traces, manifests, and public-data assumptions agree on event counts, fill counts, per-fill economics, fill-source attribution, public queue-consumption totals, source-split markouts, markout event details, lifecycle counters, and artifact hashes.
- Strategy decisions are not backfilled before the first accepted depth sync or before the snapshot timestamp that made buffered diffs usable; decisions due before a later market row are drained before that row, and same-timestamp reactions are scheduled after the row and any fills it creates.

### Partial fill handling

- A resting order can fill in multiple chunks as queue is consumed.
- Remaining quantity stays active until fully filled or canceled.

### Cancel and taker-order handling

- A canceled resting order cannot fill after its visible queue ahead has cleared.
- A strategy decision with zero desired quotes is treated as a quote pull and cancels existing live quotes for that symbol.
- Replacement quotes wait for the modeled cancel acknowledgement before order-arrival latency is applied, so the old quote remains fillable during cancel latency.
- A public trade that arrives before the modeled cancel acknowledgement can still fill the old quote; if the trade row has the exact cancel-ack timestamp, the engine applies the local cancel acknowledgement before processing that market row.
- Latency sensitivity is tested as a model assumption, not a performance claim: `experiments/sweep_futures_latency.py` reruns a committed fixture across order-arrival and cancel-ack delay grids and publishes the deterministic reference in `docs/strategy_results/futures_latency_sweep_reference.md`.
- Marketable strategy limits and market orders generate taker fills against visible venue liquidity.
- Marketable strategy orders stop before matching the strategy's own opposite-side resting liquidity; the unfilled self-trade-prevented remainder is expired rather than posted crossed.
- Simulation summaries expose `self_trade_prevention_count`, so own-cross prevention is visible without scanning the full event trace.
- Unfilled marketable-limit remainder can rest at its limit price after the visible sweep.

### Queue-ahead behavior

- Strategy orders only fill after visible queue ahead has been consumed.
- Queue-ahead deterioration is visible to the strategy layer and can trigger repost logic.
- Queue-ahead observations passed to strategy refresh logic are read-only views; reading queue position cannot create extra hidden queue ahead inside the fill model.
- Event traces record queue ahead after order arrival and cancel-request reasons, including queue-driven replacement metadata.

### Deterministic replay expectation

- The same input file and config should produce the same replay and simulation outputs.
- Tests cover deterministic behavior on a fixed synthetic event stream.
- `scripts/check_futures_determinism.py` reruns a replay fixture multiple times in memory and compares canonical SHA-256 hashes of the metrics summary and event trace:

```bash
python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example
```

### Recorded stream contract

- Replay rows are validated before they enter book sync or simulation state.
- Invalid JSON, missing required payload fields, malformed price/quantity levels, and unsupported event types fail with file and line-number context.
- Stream inspection reports event counts, symbols, event-time span, and input digest before a run is treated as an experiment artifact.

### Markout / inventory / PnL sanity checks

- Inventory updates are consistent with signed fills.
- Fill exports identify whether the fill was inferred from a depth update, inferred from an `aggTrade`, or generated by a strategy taker order.
- Event-trace fill rows include notional, multiplier, fee, spread capture, mid-at-fill, queue, time-in-book, and regime metadata for inline audit.
- Simulation summaries aggregate `fill_source_counts` for `depth_update`, `agg_trade`, and `taker_order`, making depth-inferred fill reliance visible at a glance.
- Simulation summaries aggregate markout samples, adverse samples, quantity, average markout, and adverse-fill rate by fill source, so fill quality can be separated by modeled passive source versus taker execution.
- Event traces include `markout` rows when each fill's configured post-fill horizon matures, so the later mid and adverse-selection flag are auditable in event time.
- Simulation summaries include `public_consumption_summary` so overlap reconciliation and unmatched public queue-consumption signals are auditable even when no strategy order filled.
- Simulation summaries aggregate `order_lifecycle_counts`, so scheduled arrivals, arrived orders, resting outcomes, immediate-fill arrivals, expired remainders, cancel requests, and cancel acknowledgements can be checked without replaying the trace by hand.
- Maker/taker fees are applied through an explicit fee model; rebates are negative fees and per-fill fee, notional, spread-capture, and contract-multiplier fields are exported for auditability.
- Realized PnL, unrealized PnL, spread capture, fees, and markout are multiplied by `InstrumentSpec.contract_multiplier`; inventory remains in normalized quantity units.
- Unrealized PnL is marked from the current reconstructed mid.
- Markout windows drain deterministically from the stored fill history and report multiplier-adjusted price-currency markout per quantity unit.

## Current Test Coverage

- [`tests/test_book_sync.py`](../tests/test_book_sync.py)
- [`tests/test_gap_resync.py`](../tests/test_gap_resync.py)
- [`tests/test_fill_model.py`](../tests/test_fill_model.py)
- [`tests/test_futures_invariants.py`](../tests/test_futures_invariants.py)
- [`tests/test_fee_model.py`](../tests/test_fee_model.py)
- [`tests/test_record_schema.py`](../tests/test_record_schema.py)

## Limitations

- Validation is against documented assumptions, not private venue truth.
- Public data cannot prove exact passive-fill attribution.
- The baseline strategy is intentionally simple, so validation is focused on replay and matching correctness rather than alpha quality.

## Non-Goals

- No claim of production exchange matching equivalence.
- No claim of venue-private fill reconstruction.
- No benchmark numbers are treated as universal without hardware and dataset context.
