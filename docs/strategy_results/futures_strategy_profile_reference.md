# Futures Strategy Profile Reference

- Compared profiles: `baseline` vs `research_mm`
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

## Baseline vs Candidate

| Metric | Baseline | `research_mm` |
|---|---:|---:|
| quote_count | 6 | 50 |
| cancel_count | 4 | 46 |
| fill_count | 0 | 0 |
| quote_fill_probability | 0.0 | 0.0 |
| fills_per_quote_request | 0.0 | 0.0 |
| fills_per_arrived_order | 0.0 | 0.0 |
| fill_from_top_count | 0 | 0 |
| avg_queue_ahead_lots | 0.0 | 0.0 |
| avg_arrival_queue_ahead_lots | 0.5 | 250.08 |
| max_arrival_queue_ahead_lots | 2 | 2096 |
| avg_markout_1s | 0.0 | 0.0 |
| adverse_fill_rate_1s | 0.0 | 0.0 |
| inventory_stdev | 0.0 | 0.0 |
| realized_pnl | 0.0 | 0.0 |
| unrealized_pnl | 0.0 | 0.0 |
| total_pnl | 0.0 | 0.0 |
| kill_switch_triggered | False | False |

## Interpretation

On this short committed BTCUSDT clip, `research_mm` quotes and refreshes differently from the baseline (6 quotes versus 50). It also changes fill frequency (0 baseline fills versus 0 for `research_mm`) and the resulting inventory/PnL mix. The clip is intentionally small, so the comparison is useful for inspecting profile behavior, not for making broad performance claims.

## Scope Note

This is a strategy-profile comparison on one committed replay input. It is not a claim of alpha, production profitability, or stronger fill realism than the repo's existing passive-fill assumptions.
