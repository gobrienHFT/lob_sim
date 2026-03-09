# Options MM Demo Guide

## Goal

Use `run_options_mm_interview_mode.bat` as the cleanest walkthrough of how an options dealer prices, warehouses risk across the surface, hedges delta, and evaluates adverse selection. The point is not to claim this is production infrastructure. The point is to show clear pricing and risk reasoning with outputs that are easy to audit.

## What the batch file does

`run_options_mm_interview_mode.bat` launches:

```bat
python -u -m lob_sim.cli options-demo --out-dir ... --steps ... --seed ... --scenario ... --brief --interview-mode
```

That command runs the synthetic options dealer study in [lob_sim/options/demo.py](../lob_sim/options/demo.py).

The non-Windows equivalent is:

```bash
bash run_options_mm_case.sh
```

If you want the fuller event-level walkthrough instead of the concise screen-share path, use `run_options_mm_case.bat`.

## Best screen-share order

1. Open `interview_brief.md`.
2. Open `overview_dashboard.png`.
3. Open `position_surface_heatmap.png`.
4. Open `vega_surface_heatmap.png`.
5. Open `fills.csv` and walk through one row carefully.
6. Open `scenario_matrix.md`.
7. Open `toxicity_spread_sensitivity.md`.

If you want to show the case study is not one cherry-picked path, run:

```bash
python -m experiments.run_options_scenario_matrix --steps 180 --seed 7 --out-dir outputs
```

Then open `outputs/scenario_matrix.md` and `outputs/scenario_comparison.png`.

If you want the same comparison without running the repo locally, open [`docs/sample_outputs/scenario_matrix_seed7/`](sample_outputs/scenario_matrix_seed7/).

If you want one small economics trade-off study without running the repo locally, open [`docs/sample_outputs/toxicity_spread_sensitivity_seed7/`](sample_outputs/toxicity_spread_sensitivity_seed7/).

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

## Warehoused risk across the surface

- `position_surface_heatmap.png` shows where signed contract inventory ended up by strike and expiry.
- `vega_surface_heatmap.png` shows where volatility exposure remained after delta hedging.
- If delta finishes near flat while vega buckets stay large, that is the honest point: the underlying hedge fixed directional risk, not the whole options surface.
- Concentrated buckets matter because they show where reservation pricing and future cross-option hedging would need to work harder.

## Artifact map

The run writes:

- `summary.json`: machine-readable run summary and artifact paths.
- `interview_brief.md`: concise screen-share order, metrics table, worked fill, takeaways, and limitations.
- `demo_report.md`: the clean overview document.
- `overview_dashboard.png`: one screen with headline PnL, inventory, delta, and toxic versus non-toxic markout.
- `position_surface_heatmap.png`: signed contract inventory across strike and expiry at the final snapshot.
- `vega_surface_heatmap.png`: warehoused vega across strike and expiry at the final snapshot.
- `fills.csv`: event-level fill and hedge story.
- `checkpoints.csv`: checkpoint snapshots from the run.
- `pnl_timeseries.csv`: step-by-step PnL, spot, and risk path.
- `positions_final.csv`: ending option and hedge inventory.
- `pnl_over_time.png`, `realized_vs_unrealized.png`, `spot_path.png`, `inventory_over_time.png`, `net_delta_over_time.png`, `markout_distribution.png`, `toxic_vs_nontoxic_markout.png`, `top_traded_contracts.png`: lightweight plots for quick review.

The cross-scenario comparison script writes:

- `scenario_matrix.csv`: one row per scenario with headline PnL, markout, flow, and hedging metrics.
- `scenario_matrix.md`: same-seed comparison table plus short scenario-by-scenario notes.
- `scenario_comparison.png`: compact visual comparison across all presets.

The toxicity-versus-spread sensitivity script writes:

- `toxicity_spread_sensitivity.csv`: one row per toxicity/spread pair.
- `toxicity_spread_sensitivity.md`: compact table plus two short interpretation sections.
- `toxicity_spread_heatmap.png`: visual trade-off across toxic share and baseline quote width.

For a compact note on what desk-quality data would be needed to calibrate each synthetic component, open [what_real_data_would_change.md](what_real_data_would_change.md).

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
- `run_options_mm_case.sh`: same flow for macOS/Linux shells.
- `run_options_mm_quick.bat`: fast preset with just the summary and artifacts.
