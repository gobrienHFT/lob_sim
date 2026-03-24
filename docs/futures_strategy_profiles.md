# Futures Strategy Profiles

The replay and matching core is the main artifact. The strategy layer stays deliberately small and explicit so the queueing assumptions are easy to inspect.

## Baseline Profile

- Profile name: `baseline`
- Default path used by the repo and the committed futures sample packs
- One resting bid and one resting ask
- Quote placement from best bid/ask and mid
- Half-spread widened by short-horizon realized volatility
- Inventory skew shifts both sides away from accumulating too much position
- Queue reposts when queue ahead deteriorates past `MM_QUEUE_REPOST_LOTS`

## Layered Profile

- Profile name: `layered_mm`
- Opt-in via `MM_STRATEGY_PROFILE=layered_mm`
- Two quote levels per side: `inner` and `outer`
- Explicit widths from `MM_LAYERED_INNER_SPREAD_BPS` and `MM_LAYERED_OUTER_SPREAD_BPS`
- The same inventory skew is applied across all four quotes
- Refresh logic is stricter than the baseline: quotes repost on price change, queue deterioration, or a microstructure-gate state change

## Signals Used

- Top-of-book imbalance from visible best-bid and best-ask size
- Recent trade-sign imbalance from the last `MM_TRADE_IMBALANCE_WINDOW` `aggTrade` prints
- Short-horizon realized volatility for spread scaling, as in the baseline

The microstructure gate is intentionally simple:

- Bullish pressure = bid-side book imbalance and aggressive-buy trade imbalance both exceed `MM_MICROSTRUCTURE_GATE_THRESHOLD`
- Bearish pressure = ask-side book imbalance and aggressive-sell trade imbalance both exceed the same threshold
- The gate widens the vulnerable side by `MM_MICROSTRUCTURE_GATE_BPS`

## Reference Comparison

- Reproducible reference: [docs/strategy_results/futures_strategy_profile_reference.md](strategy_results/futures_strategy_profile_reference.md)
- Committed input: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Refresh command: `python scripts/refresh_futures_strategy_profile_reference.py`

## Why The Layered Profile Is More Realistic

- It can rest inside and outside levels at the same time instead of assuming a single quote per side.
- It treats top-of-book pressure and recent trade direction as explicit inputs to quote placement.
- It reposts stale quotes more aggressively when the visible microstructure state changes even if the rounded quote tick has not moved yet.

## What It Still Does Not Model

- No alpha forecast beyond the small explicit imbalance gate
- No hidden-liquidity or queue-jump model
- No participant-level queue identifiers
- No venue-specific order types beyond the existing passive-fill simulation assumptions

## Limitations And Non-Goals

- Public depth and `aggTrade` data still leave cancel-vs-trade attribution ambiguous inside a level reduction.
- The layered profile is a quoting/control variant for comparison, not a production market-making model.
- The reference comparison in [docs/strategy_results/futures_strategy_profile_reference.md](strategy_results/futures_strategy_profile_reference.md) is a strategy-profile comparison on one recorded input, not a claim of alpha.
