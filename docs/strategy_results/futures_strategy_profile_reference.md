# Futures Strategy Profile Reference

- Compared profiles: `baseline` vs `research_mm`
- Committed input: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Config digest: `6464f09a64dd2d4f6e850dbc45800a137b354271f582815f85d69294d6cfcafd`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Frozen research registry SHA-256: `d868a7b646a08302fcd6b99706a7b32382d54321480de26a6cce951583569ef2`
- Registry sidecar: `futures_strategy_profile_reference_registry.json`
- Git commit at run time: `5cebd68a84d0c063e22c2d28885a85c2fca0f0c6`
- Git dirty at run time: `False`
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

## Evidence Status

| Evidence field | Baseline | Candidate |
|---|---|---|
| claim_ready | False | False |
| capture_valid | True | True |
| clock_invalidated | False | False |
| markout evidence | diagnostic_only | diagnostic_only |
| PnL scope | model_output_not_a_live_or_counterfactual_trading_result | model_output_not_a_live_or_counterfactual_trading_result |

The comparison inherits the simulator validity boundary. `claim_ready=False` or `markout evidence=diagnostic_only` means the numbers are behavior diagnostics, not claim-bearing economic evidence.

## Interpretation

On this short committed BTCUSDT clip, `research_mm` quotes and refreshes differently from the baseline (14 quotes versus 59). It also changes fill frequency (6 baseline fills versus 17 for `research_mm`) and the resulting inventory/PnL mix. The clip is intentionally small, so the comparison is useful for inspecting profile behavior, not for making broad performance claims.

## Scope Note

This is a strategy-profile comparison on one committed replay input. It is not a claim of alpha, production profitability, or stronger fill realism than the repo's existing passive-fill assumptions.
