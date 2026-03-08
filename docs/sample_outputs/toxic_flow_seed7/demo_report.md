# Options market making case study

## What this project demonstrates
- A dealer quoting options around Black-Scholes fair value on a simple skewed surface.
- Inventory-aware reservation pricing that shifts quotes when delta and vega risk build.
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
- **Average half-width**: 0.6399

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

## Most traded contracts
- CALL_90.00_14D: count=18, signed_qty=10
- CALL_95.00_14D: count=13, signed_qty=-5
- PUT_90.00_14D: count=12, signed_qty=-3
- PUT_100.00_14D: count=10, signed_qty=9
- PUT_95.00_14D: count=9, signed_qty=-7

## Suggested artifact reading order
- `overview_dashboard.png`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\overview_dashboard.png
- `demo_report.md`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\demo_report.md
- `fills.csv`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\fills.csv
- `pnl_timeseries.csv`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\pnl_timeseries.csv
- `checkpoints.csv`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\checkpoints.csv
- `pnl_over_time.png`: C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\pnl_over_time.png

## Glossary
- **Underlying spot**: the simulated price of the underlying used for option fair value and delta hedging.
- **Fair value**: Black-Scholes option value from current spot, time to expiry, and implied vol.
- **Reservation price**: inventory-driven quote adjustment that discourages more unwanted risk.
- **Quote skew**: the directional shift in bid and ask caused by reservation price.
- **Signed markout**: future fair-value edge relative to fill price, positive when the fill ages well for the dealer.
- **Toxic flow**: customer flow more likely to be informed against the current quote.
- **Realized PnL**: gross spread capture less hedge slippage costs.
- **Unrealized PnL**: residual mark-to-market of the option inventory and hedge book.
- **Delta hedge**: underlying trade used to reduce net delta after option fills.

## Output files
- Summary JSON: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\summary.json`
- Fills CSV: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\fills.csv`
- Checkpoints CSV: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\checkpoints.csv`
- PnL timeseries CSV: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\pnl_timeseries.csv`
- Final positions CSV: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\positions_final.csv`
- Report Markdown: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\demo_report.md`
- Overview dashboard: `C:\Users\PC\AppData\Local\Temp\lob_sim_options_sample_gbvbyk3w\case_study\overview_dashboard.png`
