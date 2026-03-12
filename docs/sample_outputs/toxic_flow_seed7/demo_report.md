# Options Market-Making Case Study

## What this project demonstrates
- A dealer quoting options around Black-Scholes fair value on a simple skewed surface.
- Inventory-aware reservation price shifts when delta and vega risk build.
- Synthetic customer flow with a configurable toxic-flow share.
- Delta hedging in the underlying, plus realized and unrealized PnL decomposition.

## What is synthetic vs real
- The option chain, customer arrivals, toxicity, and fills are synthetic and scenario-driven.
- The demo does not replay a live options venue order book.
- The point is transparency: every quote, hedge, markout, and PnL component is inspectable.

## Scenario overview
- **Scenario**: `toxic_flow`
- **Description**: Flow is more informed, so markouts matter and toxic fills are easier to discuss.
- **Volatility regime**: Moderate volatility, but adverse-selection drift is deliberately stronger.
- **Flow characteristics**: A larger share of customer trades are informed against the quoted price.
- **Hedging pressure**: Moderate hedge pressure, but post-trade markouts are the real story.
- **Intended lesson**: Shows the difference between quoted edge and realized edge after markout.

## Simulation loop
1. Build a quote around fair value for one option contract from the synthetic chain.
2. Sample customer flow side, size, and toxicity from the chosen scenario.
3. Apply the fill, update option inventory and cash, then recalculate portfolio Greeks.
4. Hedge net delta in the underlying when the configured trigger is breached.
5. Evolve underlying spot, mark the portfolio, and record path, fills, and checkpoints.

## Markout definition
- Signed markout is measured at a fixed future horizon of `1-step` against the option fair value at that future step.
- Formula: `signed_markout = direction * (future_fair_value - fill_price) * qty * contract_size`.
- `direction = +1` for a market-maker buy fill and `direction = -1` for a market-maker sell fill.
- Positive signed markout means the fill aged well for the dealer; negative means adverse selection.

## Parameter choices
- **Seed**: 7
- **Steps**: 180
- **Underlying spot start**: 100.0000
- **Underlying spot final**: 91.9884
- **Markout horizon**: 1-step
- **Hedge trigger**: |delta| > 110.00
- **Customer arrival probability**: 0.80
- **Toxic flow probability**: 0.55

## Key metrics
- **Ending PnL**: 4068.17
- **Realized PnL**: 7393.54
- **Unrealized PnL**: -3325.37
- **Gross spread captured**: 7481.06
- **Hedge costs**: 87.53
- **Total signed markout**: -5339.31
- **Average signed markout**: -35.13
- **Average toxic markout**: -201.75
- **Average non-toxic markout**: 176.27
- **Toxic fill rate**: 55.9%
- **Adverse fill rate**: 48.0%
- **Average quote width**: 1.2797
- **Average half-spread**: 0.6399

## Inventory and hedging
- **Hedge trades**: 76
- **Average |delta| before hedge**: 127.68
- **Max inventory**: 157
- **Max single-contract position**: 14
- **Max underlying hedge position**: 3064.00
- **Final net delta**: 8.94
- **Final net vega**: 19732.24
- **Worst drawdown**: 18713.60

## Interpretation
- Quoted edge held up after hedge costs and inventory marking.
- A large share of fills were toxic, so post-trade selection pressure stayed elevated.
- Hedge triggers were reached with meaningful delta, so underlying hedging mattered.
- The book finished close to delta-flat after hedging.

## Warehoused Vega and Surface Risk
- Largest signed contract inventory sat in strike `95` / `14` day expiry at `-12` contracts.
- Largest net vega sat in strike `105` / `90` day expiry at `+18578` vega.
- Risk was spread across `12` non-zero strike/expiry buckets, so the book is not just one contract position.
- Underlying hedges flattened delta, but the volatility surface exposure remained warehoused.

## Pricing surface used by the demo
- The demo uses an initial snapshot implied-vol surface at spot `100.00`; that surface feeds Black-Scholes fair value for every quoted option.
- Vega exposure should be read alongside this surface because the book can be close to delta-flat while still carrying large strike/expiry volatility risk.
- The surface shape is synthetic and parametric here; real calibration would need live option quotes or trades across strike and expiry.

## Economics of the run
- **Gross spread captured**: 7481.06 contract dollars
- **Hedge costs**: 87.53 contract dollars
- **Total signed markout**: -5339.31 contract dollars
- **Ending PnL**: 4068.17 contract dollars
- **Realized PnL**: 7393.54 contract dollars
- **Unrealized PnL**: -3325.37 contract dollars

Signed markout is a contract-dollar diagnostic of fill quality. It is not used here as a separate additive PnL line item.
- Gross spread capture can stay positive while signed markout is negative because the dealer still earns quoted edge at the fill even when the next fair-value move goes against the position.
- Ending PnL can still finish positive when quoted edge and subsequent inventory moves outweigh hedge costs, even if post-trade markouts are poor on average.
- Signed markout is a diagnostic in contract dollars, not a separate PnL line item that is added mechanically into ending PnL in this toy accounting.
- That combination means the strategy earned enough spread and inventory carry to survive adverse selection, but the fill quality still deserves skepticism.

## Fill Examples
Quoted prices below are per-option premium. `signed_markout`, `gross_spread_captured`, and `hedge_costs` are shown in contract dollars after multiplying by `qty_contracts * contract_size`.

### Representative Fill

Representative fill = the hedged fill whose absolute signed markout is closest to the median absolute signed markout across all hedged fills.

| Field | Value |
|---|---|
| step | 14 |
| contract | CALL_95.00_14D |
| option_type | call |
| strike | 95.00 |
| expiry_days | 13.83 |
| customer_side | sell |
| dealer_side | buy |
| quantity | 4 |
| contract_size | 100 |
| spot_before | 94.37 spot units |
| fair_value | 1.897 premium per option |
| base_half_spread | 0.090 premium per option |
| vol_half_spread_component | 0.501 premium per option |
| gamma_half_spread_component | 0.008 premium per option |
| reservation_price | -0.816 premium per option |
| delta_reservation_component | -0.013 premium per option |
| vega_reservation_component | -0.803 premium per option |
| final bid | 2.115 premium per option |
| final ask | 3.312 premium per option |
| fill_price | 2.115 premium per option |
| toxic_flow | True |
| signed_markout | -314.54 contract dollars |
| portfolio_delta_before | -22.8 |
| portfolio_delta_after_trade | +163.7 |
| hedge_qty | -164 underlying units |
| portfolio_delta_after_hedge | -0.3 |
| option_position_after | 4 contracts |
| short_interpretation | Reservation pressure of -0.816 and half-spread 0.598 kept the quote close to model fair value. The fill transacted 845.89 contract dollars of premium before the 1-step signed markout of -314.54 contract dollars. |

### Stress-case toxic fill

Stress-case toxic fill = the toxic hedged fill with the worst signed markout.

| Field | Value |
|---|---|
| step | 124 |
| contract | CALL_100.00_45D |
| option_type | call |
| strike | 100.00 |
| expiry_days | 43.42 |
| customer_side | sell |
| dealer_side | buy |
| quantity | 3 |
| contract_size | 100 |
| spot_before | 95.34 spot units |
| fair_value | 2.125 premium per option |
| base_half_spread | 0.090 premium per option |
| vol_half_spread_component | 0.483 premium per option |
| gamma_half_spread_component | 0.013 premium per option |
| reservation_price | -14.556 premium per option |
| delta_reservation_component | +0.004 premium per option |
| vega_reservation_component | -14.560 premium per option |
| final bid | 16.095 premium per option |
| final ask | 17.268 premium per option |
| fill_price | 16.095 premium per option |
| toxic_flow | True |
| signed_markout | -4326.87 contract dollars |
| portfolio_delta_before | +9.0 |
| portfolio_delta_after_trade | +111.5 |
| hedge_qty | -111 underlying units |
| portfolio_delta_after_hedge | +0.5 |
| option_position_after | 0 contracts |
| short_interpretation | Reservation pressure of -14.556 premium per option dominated fair value, so the dealer buy side was skewed far from model mid. The fill transacted 4828.54 contract dollars of premium before the 1-step signed markout of -4326.87 contract dollars. |

- This is not a units mismatch: the per-option fair value is low, but a large negative vega reservation shifted the dealer bid above model mid to attract offsetting flow.
- The trader read is that inventory transfer pricing became aggressive here, and the subsequent negative markout is evidence to question whether that skew should have been capped or hedged earlier.

## Most traded contracts
- CALL_90.00_14D: count=18, signed_qty=10
- CALL_95.00_14D: count=13, signed_qty=-5
- PUT_90.00_14D: count=12, signed_qty=-3
- PUT_100.00_14D: count=10, signed_qty=9
- PUT_95.00_14D: count=9, signed_qty=-7

## Suggested artifact reading order
- `interview_brief.md`: docs/sample_outputs/toxic_flow_seed7/interview_brief.md
- `overview_dashboard.png`: docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png
- `implied_vol_surface_snapshot.png`: docs/sample_outputs/toxic_flow_seed7/implied_vol_surface_snapshot.png
- `position_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/position_surface_heatmap.png
- `vega_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/vega_surface_heatmap.png
- representative fill: docs/sample_outputs/toxic_flow_seed7/interview_brief.md#representative-fill
- `scenario_matrix.md`: docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md
- `toxicity_spread_sensitivity.md`: docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md

## Glossary
- **Underlying spot**: the simulated price of the underlying used for option fair value and delta hedging.
- **Fair value**: Black-Scholes option value per option, quoted in premium units from current spot, time to expiry, and implied vol.
- **Half-spread**: one-sided quote width around fair value before reservation price shifts both sides.
- **Reservation price**: inventory-driven quote adjustment that discourages more unwanted risk.
- **Quote skew**: the directional shift in bid and ask caused by reservation price.
- **Signed markout**: future fair-value edge relative to fill price, reported in contract dollars after multiplying by quantity and contract size.
- **Warehoused vega / surface risk**: the strike/expiry volatility exposure that remains after delta hedging.
- **Toxic flow**: customer flow more likely to be informed against the current quote.
- **Realized PnL**: contract-dollar gross spread capture less hedge slippage costs.
- **Unrealized PnL**: residual contract-dollar mark-to-market of the option inventory and hedge book.
- **Delta hedge**: underlying trade used to reduce net delta after option fills.

## Output files
- Summary JSON: `docs/sample_outputs/toxic_flow_seed7/summary.json`
- Fills CSV: `docs/sample_outputs/toxic_flow_seed7/fills_head.csv`
- Checkpoints CSV: `docs/sample_outputs/toxic_flow_seed7/checkpoints_head.csv`
- PnL timeseries CSV: `docs/sample_outputs/toxic_flow_seed7/pnl_timeseries_head.csv`
- Final positions CSV: `docs/sample_outputs/toxic_flow_seed7/positions_final.csv`
- Report Markdown: `docs/sample_outputs/toxic_flow_seed7/demo_report.md`
- Overview dashboard: `docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png`
- Implied-vol surface snapshot: `docs/sample_outputs/toxic_flow_seed7/implied_vol_surface_snapshot.png`
- Position surface heatmap: `docs/sample_outputs/toxic_flow_seed7/position_surface_heatmap.png`
- Vega surface heatmap: `docs/sample_outputs/toxic_flow_seed7/vega_surface_heatmap.png`
