# Futures Strategy Profile Reference

- Input file: `data/raw_1772633471.ndjson`
- Input status: local-only recorded BTCUSDT raw file (not committed)
- Compared profiles: `baseline` vs `layered_mm`
- Exact command:

```bash
python experiments/compare_futures_strategy_profiles.py --file data/raw_1772633471.ndjson --env .env.example
```

## Baseline vs Layered

| Metric | Baseline | `layered_mm` |
|---|---:|---:|
| quote_count | 36 | 486 |
| cancel_count | 27 | 424 |
| fill_count | 9 | 59 |
| fill_from_top_count | 9 | 59 |
| avg_queue_ahead_lots | 0.0 | 0.0 |
| avg_markout_1s | -0.019005555555555557 | -0.006969491525423729 |
| inventory_stdev | 0.0020108730767606003 | 0.007128811795608896 |
| realized_pnl | -0.072214798 | 0.36611542048366014 |
| unrealized_pnl | -0.08525 | -0.36675 |
| total_pnl | -0.157464798 | -0.0006345795163398693 |
| kill_switch_triggered | False | False |

## Interpretation

On this recorded BTCUSDT file, `layered_mm` quoted and refreshed much more aggressively than the baseline, produced more fills, and improved the average 1-second markout from `-0.0190` to `-0.0070`. It also carried materially more inventory variability, so this is a strategy-profile comparison rather than a claim of alpha.
