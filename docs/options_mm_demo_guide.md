# Options MM Demo Guide

## Goal

Use `run_options_mm_case.bat` as a compact explanation of how an options market maker prices, warehouses risk, and decides when to hedge. The point is not to claim this is a production options venue simulator. The point is to show that you understand the pricing and risk mechanics deeply enough to discuss them clearly and honestly.

## What the batch file does

`run_options_mm_case.bat` launches:

```bat
python -u -m lob_sim.cli options-demo --out-dir ... --steps ... --seed ... --verbose --progress-every ...
```

That command runs the synthetic options dealer case study in [lob_sim/options/demo.py](/C:/bitbucket/kibert/lob_sim/lob_sim/options/demo.py).

## Best live-demo order

1. Start with `latest_summary.txt`.
2. Explain the quote formula.
3. Open `latest_trades.csv` and walk through one trade row carefully.
4. Open `latest_pnl.csv` and explain how realized/unrealized PnL, inventory, and delta evolve through time.
5. Finish with `latest_report.png`.
6. Use `options_mm_walkthrough.md` and `options_mm_config.csv` when you want the fuller detail.

## The core quote formula

The demo quotes one option at a time using:

`bid = fair_value - half_spread - reservation`

`ask = fair_value + half_spread - reservation`

Explain each term precisely:

- `fair_value`: Black-Scholes price using the current implied vol from a skewed surface.
- `reservation`: inventory pressure. If the book already has too much delta or vega in the same direction, the quote is shifted to discourage more of that exposure and attract offsetting flow.
- `half_spread`: compensation for making a market. In this model it has a baseline component plus extra width for realized volatility and portfolio gamma risk.

## How to explain one trade row

Use one row from `latest_trades.csv` and walk left to right:

- `contract`, `option_type`, `strike`, `expiry_days`: what is being quoted.
- `fair_value`, `implied_vol`, `option_delta`, `option_gamma`, `option_vega`: how the option is valued and what risk it carries.
- `delta_reservation_component` and `vega_reservation_component`: why the quote is skewed.
- `base_half_spread`, `vol_half_spread_component`, `gamma_half_spread_component`: why the spread is as wide as it is.
- `customer_side` and `mm_side`: who initiated the trade and what the dealer actually did.
- `spread_capture_pnl`: the edge earned at the trade itself.
- `mm_markout_1_step`: whether the trade was actually good after the next price move.
- `hedge_qty`, `hedge_cost`, `portfolio_delta_before`, `portfolio_delta_after_trade`, `portfolio_delta_after_hedge`: how the dealer controls risk without over-hedging.

## Important concepts to say out loud

- Market making is a balance between earning spread and managing adverse selection.
- Reservation pricing is the mechanism that converts inventory into quote skew.
- Spread capture without markout analysis is incomplete; informed flow can erase quoted edge immediately after the trade.
- Delta is hedged with the underlying because it is the cheapest and fastest risk to neutralize.
- Gamma and vega are not fully hedged here; they are intentionally warehoused so the trade-off is visible.

## Strong answers to likely follow-ups

### Why is this synthetic?

Because the purpose is transparency. A synthetic model lets you expose fair value, quote skew, toxic flow, and hedge decisions in a way that is easy to audit from the CSV outputs.

### What would you build next?

- A richer implied-vol surface model.
- Cross-hedging gamma and vega with other listed options.
- Explicit options order-book microstructure and queue modelling.
- Customer segmentation and flow calibration from real market data.

### What is the biggest weakness right now?

There is no explicit options exchange matching engine. This is primarily a dealer pricing and risk-management simulator, not yet a full options venue simulator.

## What not to claim

- Do not say this is calibrated to a live options venue unless you actually calibrate it.
- Do not say reservation price is a complete model of optimal quoting; say it is a transparent first-principles approximation.
- Do not say markout is profit; say it is an adverse-selection diagnostic.

## What makes this a strong project

- The logic is inspectable.
- The outputs are Excel-friendly.
- The pricing formula is decomposed into understandable parts.
- The PnL is broken into spread capture, hedge costs, and residual inventory effects.
- You can explain both the strengths and the limitations without hand-waving.

## Quick run options

- `run_options_mm_case.bat`: fuller case-study run with richer progress output.
- `run_options_mm_interview_mode.bat`: fast preset with only the most important metrics and a short interpretation.
