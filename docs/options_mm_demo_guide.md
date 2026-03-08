# Options MM Demo Guide

## Goal

Use `run_options_mm_case.bat` as a compact walkthrough of how an options dealer prices, warehouses risk, hedges delta, and evaluates adverse selection. The point is not to claim this is production infrastructure. The point is to show clear pricing and risk reasoning with outputs that are easy to audit.

## What the batch file does

`run_options_mm_case.bat` launches:

```bat
python -u -m lob_sim.cli options-demo --out-dir ... --steps ... --seed ... --scenario ... --verbose --progress-every ... --log-mode compact
```

That command runs the synthetic options dealer study in [lob_sim/options/demo.py](/C:/bitbucket/kibert/lob_sim/lob_sim/options/demo.py).

## Best screen-share order

1. Start with the terminal summary block.
2. Open `demo_report.md`.
3. Open `fills.csv` and walk through one row carefully.
4. Open `pnl_timeseries.csv` and connect spot, inventory, delta, and PnL through time.
5. Finish with `pnl_over_time.png` and `inventory_over_time.png`.

## The core quote formula

The demo quotes one option at a time using:

`bid = fair_value - half_spread - reservation_price`

`ask = fair_value + half_spread - reservation_price`

Explain each term precisely:

- `fair_value`: Black-Scholes price using the current implied vol from a skewed surface.
- `reservation_price`: inventory pressure. If the book already has too much delta or vega in one direction, the quote shifts to discourage more of that exposure and attract offsetting flow.
- `half_spread`: compensation for making a market. In this model it has a baseline component plus extra width for realized volatility and portfolio gamma pressure.

## Markout definition

Signed markout is the future fair-value edge of a fill at a fixed step horizon from the realized simulation path:

`signed_markout = direction * (future_fair_value - fill_price) * qty * contract_size`

- `direction = +1` for a dealer buy fill
- `direction = -1` for a dealer sell fill
- positive signed markout means the fill aged well
- negative signed markout means adverse selection

In the default setup the horizon is `1-step`, and that label is printed directly in both the terminal logs and the CSV outputs.

## How to explain one fill row

Use one row from `fills.csv` and walk left to right:

- `step`, `spot_before`, `contract`, `option_type`, `strike`, `expiry_days`: what was quoted and where the underlying was.
- `customer_side`, `mm_side`, `qty_contracts`: who initiated the trade and what the dealer actually did.
- `fair_value`, `bid`, `ask`, `fill_price`: the valuation and quoted market before the fill.
- `reservation_price`, `delta_reservation_component`, `vega_reservation_component`: why the quote was skewed.
- `half_spread`, `quote_width`, `vol_half_spread_component`, `gamma_half_spread_component`: why the spread was that wide.
- `toxic_flow`, `signed_markout`, `markout_reference_fair_value`, `markout_reference_spot`: whether the fill aged well after the horizon move.
- `portfolio_delta_before`, `portfolio_delta_after_trade`, `hedge_qty`, `portfolio_delta_after_hedge`: how the dealer controlled delta risk.
- `option_position_after`, `running_inventory_contracts`, `comment_flag`: what happened to inventory and the short one-line interpretation.

## Important concepts to say out loud

- Market making is a trade-off between spread capture and adverse selection.
- Reservation pricing is the mechanism that converts inventory pressure into quote skew.
- Spread capture without markout analysis is incomplete because informed flow can erase quoted edge immediately after the trade.
- Delta is hedged with the underlying because it is the fastest and cheapest risk to reduce.
- Gamma and vega are not fully hedged here; they are intentionally warehoused so the trade-off stays visible.

## Artifact map

The run writes:

- `summary.json`: machine-readable run summary and artifact paths.
- `demo_report.md`: the clean overview document.
- `fills.csv`: event-level fill and hedge story.
- `checkpoints.csv`: checkpoint snapshots from the run.
- `pnl_timeseries.csv`: step-by-step PnL, spot, and risk path.
- `positions_final.csv`: ending option and hedge inventory.
- `pnl_over_time.png`, `realized_vs_unrealized.png`, `spot_path.png`, `inventory_over_time.png`, `net_delta_over_time.png`, `markout_distribution.png`, `toxic_vs_nontoxic_markout.png`, `top_traded_contracts.png`: lightweight plots for quick review.

## Strong follow-ups

### Why is this synthetic?

Because the goal is clarity. A synthetic model lets you expose fair value, quote skew, toxic flow, hedging, and PnL decomposition without hiding the logic behind venue-specific data plumbing.

### What would you build next?

- A richer implied-vol surface model.
- Gamma and vega hedging with other listed options.
- Calibrated customer-flow assumptions from real data.
- A separate recorder for live options market data if venue realism becomes the main goal.

### What is the biggest weakness right now?

There is no explicit options exchange matching engine. This is primarily a dealer pricing and risk-management simulator, not a venue microstructure replay.

## Quick run options

- `run_options_mm_case.bat`: fuller run with compact fill events and checkpoints.
- `run_options_mm_quick.bat`: fast preset with just the summary and artifacts.
