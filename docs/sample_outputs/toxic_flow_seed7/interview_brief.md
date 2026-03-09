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

## Pricing surface used by the demo
- The demo uses an initial snapshot implied-vol surface at spot `100.00`; that surface feeds Black-Scholes fair value for every quoted option.
- Vega exposure should be read alongside this surface because the book can be close to delta-flat while still carrying large strike/expiry volatility risk.
- The surface shape is synthetic and parametric here; real calibration would need live option quotes or trades across strike and expiry.

## Economics of the run
| Metric | Value |
|---|---:|
| Gross spread captured | 7481.06 contract dollars |
| Hedge costs | 87.53 contract dollars |
| Total signed markout | -5339.31 contract dollars |
| Ending PnL | 4068.17 contract dollars |
| Realized PnL | 7393.54 contract dollars |
| Unrealized PnL | -3325.37 contract dollars |

Signed markout is reported here as a contract-dollar fill-quality diagnostic. It is not treated as a separate additive PnL line item in this demo.

- Gross spread capture can stay positive while signed markout is negative because the dealer still earns quoted edge at the fill even when the next fair-value move goes against the position.
- Ending PnL can still finish positive when quoted edge and subsequent inventory moves outweigh hedge costs, even if post-trade markouts are poor on average.
- Signed markout is a diagnostic in contract dollars, not a separate PnL line item that is added mechanically into ending PnL in this toy accounting.
- That combination means the strategy earned enough spread and inventory carry to survive adverse selection, but the fill quality still deserves skepticism.

## Worked fill examples
Quoted prices below are per-option premium. `signed_markout`, `gross_spread_captured`, and `hedge_costs` are shown in contract dollars after multiplying by `qty_contracts * contract_size`.
### Representative hedged fill

Representative hedged fill = the hedged fill whose absolute signed markout is closest to the median absolute signed markout across all hedged fills.

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

## What I would build next
- Calibrate the implied-vol surface and customer-flow assumptions from real market data.
- Add cross-option hedging so gamma and vega can be managed with listed options, not just underlying delta hedges.
- Build a separate live options market-data recorder if venue microstructure realism becomes the primary goal.

## Files to open next
- `interview_brief.md`: docs/sample_outputs/toxic_flow_seed7/interview_brief.md
- `overview_dashboard.png`: docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png
- `implied_vol_surface_snapshot.png`: docs/sample_outputs/toxic_flow_seed7/implied_vol_surface_snapshot.png
- `position_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/position_surface_heatmap.png
- `vega_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/vega_surface_heatmap.png
- representative worked fill: see the `Representative hedged fill` section in this file
- `scenario_matrix.md`: docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md
- `toxicity_spread_sensitivity.md`: docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md
