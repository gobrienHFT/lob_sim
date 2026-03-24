# Futures Strategy Profile Reference

- Compared profiles: `baseline` vs `layered_mm`
- Committed input: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input note: this committed recorded clip is short, so the comparison is intentionally modest.
- Refresh command:

```bash
python scripts/refresh_futures_strategy_profile_reference.py
```

- Underlying comparison command:

```bash
python experiments/compare_futures_strategy_profiles.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example
```

## Baseline vs Layered

| Metric | Baseline | `layered_mm` |
|---|---:|---:|
| quote_count | 6 | 20 |
| cancel_count | 4 | 15 |
| fill_count | 0 | 1 |
| fill_rate | 0.0 | 0.05 |
| fill_from_top_count | 0 | 1 |
| avg_queue_ahead_lots | 0.0 | 0.0 |
| avg_markout_1s | 0.0 | -0.01145 |
| adverse_fill_rate_1s | 0.0 | 1.0 |
| inventory_stdev | 0.0 | 0.0004758403625512567 |
| realized_pnl | 0.0 | 0.00143196 |
| unrealized_pnl | 0.0 | -0.01145 |
| total_pnl | 0.0 | -0.01001804 |
| kill_switch_triggered | False | False |

## Interpretation

On this short committed BTCUSDT clip, `layered_mm` quotes and refreshes more often than the baseline (6 quotes versus 20). It also changes fill frequency (0 baseline fills versus 1 for `layered_mm`) and the resulting inventory/PnL mix. The clip is intentionally small, so the comparison is useful for inspecting profile behavior, not for making broad performance claims.

## Scope Note

This is a strategy-profile comparison on one committed replay input. It is not a claim of alpha, production profitability, or stronger fill realism than the repo's existing passive-fill assumptions.
