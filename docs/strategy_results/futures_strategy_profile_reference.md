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
| quote_count | 14 | 59 |
| cancel_count | 8 | 40 |
| fill_count | 6 | 17 |
| quote_fill_probability | 0.42857142857142855 | 0.288135593220339 |
| fills_per_quote_request | 0.42857142857142855 | 0.288135593220339 |
| fills_per_arrived_order | 0.42857142857142855 | 0.288135593220339 |
| fill_from_top_count | 6 | 17 |
| avg_queue_ahead_lots | 0.0 | 0.0 |
| avg_arrival_queue_ahead_lots | 502.75 | 295.3809523809524 |
| max_arrival_queue_ahead_lots | 1996 | 2745 |
| avg_markout_1s | 13.15 | 13.15 |
| adverse_fill_rate_1s | 0.0 | 0.0 |
| inventory_stdev | 0.0021183355916880465 | 0.004225181711338242 |
| realized_pnl | -0.17183928 | -0.48687796 |
| unrealized_pnl | 0.0789 | 0.22355 |
| total_pnl | -0.09293928 | -0.26332796 |
| kill_switch_triggered | False | False |

## Interpretation

On this short committed BTCUSDT clip, `research_mm` quotes and refreshes differently from the baseline (14 quotes versus 59). It also changes fill frequency (6 baseline fills versus 17 for `research_mm`) and the resulting inventory/PnL mix. The clip is intentionally small, so the comparison is useful for inspecting profile behavior, not for making broad performance claims.

## Scope Note

This is a strategy-profile comparison on one committed replay input. It is not a claim of alpha, production profitability, or stronger fill realism than the repo's existing passive-fill assumptions.
