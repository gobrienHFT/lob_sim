# Futures Strategy Profile Reference

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input status: committed recorded BTCUSDT clip
- Compared profiles: `baseline` vs `layered_mm`
- Exact command:

```bash
python experiments/compare_futures_strategy_profiles.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example
```

## Baseline vs Layered

| Metric | Baseline | `layered_mm` |
|---|---:|---:|
| quote_count | 6 | 20 |
| cancel_count | 4 | 15 |
| fill_count | 0 | 1 |
| fill_from_top_count | 0 | 1 |
| avg_queue_ahead_lots | 0.0 | 0.0 |
| avg_markout_1s | 0.0 | -0.01145 |
| inventory_stdev | 0.0 | 0.0004758403625512567 |
| realized_pnl | 0.0 | 0.00143196 |
| unrealized_pnl | 0.0 | -0.01145 |
| kill_switch_triggered | False | False |
| total_pnl | 0.0 | -0.01001804 |
| fill_rate | 0.0 | 0.05 |
| adverse_fill_rate_1s | 0.0 | 1.0 |

## Interpretation

This committed recorded clip is short, so the comparison mostly shows quoting intensity and fill selection rather than a broad performance claim. On this input, `layered_mm` refreshed and quoted more aggressively than the baseline and picked up one fill where the baseline stayed flat, so this is a strategy-profile comparison rather than a claim of alpha.
