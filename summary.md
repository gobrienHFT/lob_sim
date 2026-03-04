# Strategy Run Summary

- Generated: 2026-03-04T14:29:41.294702+00:00
- Source summary: `data/outputs/summary_raw_1772634537.json`

## Headline Metrics

- `total_pnl`: -0.126948
- `realized_pnl`: -0.030698
- `unrealized_pnl`: -0.096250
- `max_drawdown`: 0.138799
- `fill_count`: 5
- `fill_rate`: 0.003551
- `avg_spread_captured`: 0.670000
- `avg_inventory`: -0.001710
- `inventory_stdev`: 0.001346
- `total_fees`: -0.007169

## Sample Fills

| ts_local | symbol | side | price | qty | maker |
|---|---|---|---:|---:|---|
| 1772634546.025 | BTCUSDT | ask | 71671.80 | 0.001 | True |
| 1772634550.927 | BTCUSDT | ask | 71676.00 | 0.001 | True |
| 1772634551.845 | BTCUSDT | ask | 71685.50 | 0.001 | True |
| 1772634558.758 | BTCUSDT | bid | 71699.80 | 0.001 | True |
| 1772634561.153 | BTCUSDT | bid | 71693.60 | 0.001 | True |

## Interpretation

- This is an offline replay of L2 + aggTrade data with queue-aware passive fill approximation.
- No live orders are sent and no real capital is at risk.
