# Design decisions

## Ticks and lots

Replay normalization converts venue prices and quantities into integer ticks and lots at the adapter boundary. This keeps matching deterministic and avoids floating-point equality in queue logic. `InstrumentSpec` carries tick size, step size, price currency, quantity unit, contract multiplier, and venue label.

## Adapter boundary

`ReplayFeedAdapter` is the seam between raw recorded rows and normalized events. Binance USD-M is the working adapter. Future venues should map their metadata into the same `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` shapes before simulation state is touched.

## Synthetic queue-ahead model

The passive fill model uses a synthetic visible queue-ahead by price level. Snapshot depth seeds venue liquidity ahead of strategy orders. Later depth increases append behind existing visible queue. Selected public signals consume the modeled queue. This is an assumption over market-by-price data, not private exchange matching-engine truth or historical Binance FIFO.

## Overlap netting

Depth reductions and aggregate trade prints can describe the same public consumption. The fill model nets recent depth/`aggTrade` consumption at the same symbol, side, and price within a short window before consuming queue again. Summaries expose observed, modeled, overlap-netted, consumed, and unmatched lots so the assumption is auditable.

## Event trace

The simulator emits event-time rows for market records, decisions, order arrivals, cancels, fills, queue consumption, markouts, gaps, and risk halts. The trace is the primary audit surface: it shows ordering, queue-ahead-at-arrival, fill economics, cancel races, and post-fill markouts without stepping through a debugger.

## Independent stream epochs

Public depth and market trade connections fail independently, so one boolean feed flag is insufficient. Capture emits route-specific connection/failure events. Replay invalidates a depth book at a public-route failure, but a market-route failure preserves that book while clearing trade-flow history. Live orders, cancel acknowledgements, replacements, and scheduled actions are terminated only when the selected fill or strategy scenario depends on trades. Recovery is prospective: a new market epoch can support new orders, but never completes an old queue trajectory.

## Fee model

Fees are static maker/taker bps in the current implementation. Rebates are negative fees. Each fill export includes notional, contract multiplier, fee bps, fee amount, and fee currency. PnL, spread capture, fees, and markout use the instrument multiplier; inventory remains in normalized quantity units.

## Run manifests

Simulation manifests record the input hash, non-secret config, config digest,
adapter metadata, instrument specs, source Git state, output paths, output
hashes, and public-data assumptions. Refresh committed packs from a clean source
tree.

## Public-data limits

Public L2 and aggregate-trade feeds cannot reveal private queue identity, hidden
liquidity, participant priority, whether every level reduction was a cancel or a
trade, or private execution reports. The fill model therefore treats fills as
public-data inferences with stated assumptions.

## Gradual type checking

The committed type-check target starts with the highest-risk futures core:

```bash
python -m mypy lob_sim/book lob_sim/replay lob_sim/record lob_sim/binance/ws.py lob_sim/cli.py lob_sim/config.py lob_sim/oracle_kernel.py lob_sim/util.py lob_sim/sim/fill_model.py lob_sim/sim/engine.py lob_sim/sim/metrics.py lob_sim/sim/run_manifest.py lob_sim/sim/mm_strategy.py lob_sim/sim/contracts.py lob_sim/sim/latency.py lob_sim/sim/sinks.py lob_sim/sim/synthetic_exchange.py lob_sim/sim/synthetic_demo.py
```

This includes replay inspection through the `lob_sim/replay` package, record schema/writing, core CLI/config/util helpers, run-manifest provenance, and the market-making strategy layer. Options demos, plotting-heavy experiments, artifact refresh scripts, and tests remain outside the gradual mypy gate because they are more dynamic and less central to replay/fill correctness. They still run under pytest, ruff, artifact verification, and CI.
