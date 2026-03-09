# Options MM interview brief

## Executive summary
- Scenario `toxic_flow` with seed `7` over `180` steps.
- Ending PnL `4068.17` with realized `7393.54` and unrealized `-3325.37`.
- Toxic fills were `85` (`55.9%`) and total signed markout at `1-step` was `-5339.31`.
- Hedge trades were `76` with max inventory `157` and final net delta `8.94`.
- Readout: Quoted edge held up after hedge costs and inventory marking.

## Metrics
| Metric | Value |
|---|---:|
| Ending PnL | 4068.17 |
| Realized PnL | 7393.54 |
| Unrealized PnL | -3325.37 |
| Gross spread captured | 7481.06 |
| Signed markout | -5339.31 |
| Toxic fill rate | 55.9% |
| Hedge trades | 76 |
| Max inventory | 157 |
| Worst drawdown | 18713.60 |
| Final net delta | 8.94 |
| Final net vega | 19732.24 |

## Strongest takeaways
- Gross spread capture was 7481.06 while signed markout was -5339.31, so the run makes quoted edge versus adverse selection explicit.
- Inventory peaked at 157 contracts and triggered 76 hedge trades, so warehousing risk is visible rather than hidden.
- The book finished with net delta 8.94 and net vega 19732.24, which shows delta control without pretending the book is fully hedged.

## Key limitations
- This is a synthetic dealer study, not a replay of exchange options order-book data.
- The strategy only hedges delta in the underlying; gamma and vega are intentionally warehoused.
- The volatility surface and customer flow are transparent approximations, not venue-calibrated models.

## Warehoused risk across the surface
- Largest signed contract inventory sat in strike `95` / `14` day expiry at `-12` contracts.
- Largest net vega sat in strike `105` / `90` day expiry at `+18578` vega.
- Risk was spread across `12` non-zero strike/expiry buckets, so the book is not just one contract position.
- Underlying hedges flattened delta, but the volatility surface exposure remained warehoused.

## Worked fill example
Selected directly from `fills.csv`.
| Field | Value |
|---|---|
| Step | 124 |
| Underlying spot | 95.34 |
| Contract | CALL_100.00_45D |
| Customer side | sell |
| Dealer side | buy |
| Quantity | 3 |
| Fair value | 2.125 |
| Quoted market | 16.095 / 17.268 |
| Fill price | 16.095 |
| Toxic flow | True |
| Signed markout | -4326.87 |
| Delta after trade -> after hedge | 111.5 -> 0.5 |
| Hedge trade | -111 |
| Position after trade | 0 |
| Comment flag | picked off; hedged long delta |

Interpretation:
- The dealer buys `3` lot(s) of `CALL_100.00_45D` against a customer `sell`.
- The fill printed at `16.095` versus fair value `2.125`, then produced signed markout `-4326.87` at `1-step`.
- Delta moved from `111.5` to `0.5` after a hedge of `-111` underlying units.

## What I would build next
- Calibrate the implied-vol surface and customer-flow assumptions from real market data.
- Add cross-option hedging so gamma and vega can be managed with listed options, not just underlying delta hedges.
- Build a separate live options market-data recorder if venue microstructure realism becomes the primary goal.

## Files to open next
- `interview_brief.md`: docs/sample_outputs/toxic_flow_seed7/interview_brief.md
- `overview_dashboard.png`: docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png
- `position_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/position_surface_heatmap.png
- `vega_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/vega_surface_heatmap.png
- `fills.csv`: docs/sample_outputs/toxic_flow_seed7/fills_head.csv
- `scenario_matrix.md`: docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md
- `toxicity_spread_sensitivity.md`: docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md
