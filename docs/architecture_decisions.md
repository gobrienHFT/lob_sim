# Architecture Decisions

## Ticks And Lots

Replay normalization converts venue prices and quantities into integer ticks and lots at the adapter boundary. This keeps matching deterministic and avoids floating-point equality in queue logic. `InstrumentSpec` carries tick size, step size, price currency, quantity unit, contract multiplier, and venue label.

## Adapter Boundary

`ReplayFeedAdapter` is the seam between raw recorded rows and normalized events. Binance USD-M is the working adapter. Future venues should map their metadata into the same `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` shapes before simulation state is touched.

## FIFO Queue Assumptions

The passive fill model uses visible price-time FIFO queues. Snapshot depth seeds venue liquidity ahead of strategy orders. Later depth increases append behind existing visible queue. Depth decreases and same-price public trade prints consume from the front. This is an assumption over public data, not private exchange matching-engine truth.

## Overlap Netting

Depth reductions and aggregate trade prints can describe the same public consumption. The fill model nets recent depth/`aggTrade` consumption at the same symbol, side, and price within a short window before consuming queue again. Summaries expose observed, modeled, overlap-netted, consumed, and unmatched lots so the assumption is auditable.

## Event Trace

The simulator emits event-time rows for market records, decisions, order arrivals, cancels, fills, queue consumption, markouts, gaps, and risk halts. The trace is the primary audit surface: it shows ordering, queue-ahead-at-arrival, fill economics, cancel races, and post-fill markouts without stepping through a debugger.

## Fee Model

Fees are static maker/taker bps in the current implementation. Rebates are negative fees. Each fill export includes notional, contract multiplier, fee bps, fee amount, and fee currency. PnL, spread capture, fees, and markout use the instrument multiplier; inventory remains in normalized quantity units.

## Manifests

Simulation manifests record input hash, non-secret config, config digest, adapter metadata, instrument specs, source git state, output paths, output hashes, and public-data simulation assumptions. Committed reviewer artifacts should be refreshed from a clean source tree.

## Public Data Limits

Public L2 and aggregate-trade feeds cannot prove private queue identity, hidden liquidity, participant priority, cancel-vs-trade attribution inside every level reduction, or private execution reports. The repo makes those limits explicit and treats fills as conservative public-data inferences.
