# Sample Outputs

This directory contains committed, deterministic sample artifacts for the options market-making case study.

## Fixed Configuration

- Scenario: `toxic_flow`
- Steps: `180`
- Seed: `7`

## Exact Command Used

The refresh script runs:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir <temp_dir> --progress-every 30 --log-mode compact --interview-mode
```

## What to Look at First

Canonical screen-share order:

1. [`toxic_flow_seed7/interview_brief.md`](toxic_flow_seed7/interview_brief.md)
2. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)
3. [`toxic_flow_seed7/implied_vol_surface_snapshot.png`](toxic_flow_seed7/implied_vol_surface_snapshot.png)
4. [`toxic_flow_seed7/position_surface_heatmap.png`](toxic_flow_seed7/position_surface_heatmap.png)
5. [`toxic_flow_seed7/vega_surface_heatmap.png`](toxic_flow_seed7/vega_surface_heatmap.png)
6. representative worked fill in [`toxic_flow_seed7/interview_brief.md#representative-hedged-fill`](toxic_flow_seed7/interview_brief.md#representative-hedged-fill)
7. [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)
8. [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)

If you want the raw event rows after the brief, open [`toxic_flow_seed7/fills_head.csv`](toxic_flow_seed7/fills_head.csv).

Cross-scenario credibility check:

1. [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)
2. [`scenario_matrix_seed7/scenario_comparison.png`](scenario_matrix_seed7/scenario_comparison.png)
3. [`scenario_matrix_seed7/scenario_matrix.csv`](scenario_matrix_seed7/scenario_matrix.csv)

Economics sensitivity check:

1. [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)
2. [`toxicity_spread_sensitivity_seed7/toxicity_spread_heatmap.png`](toxicity_spread_sensitivity_seed7/toxicity_spread_heatmap.png)
3. [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.csv`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.csv)

## How to Refresh

From the repo root:

```bash
python scripts/refresh_sample_outputs.py
```

Exact deterministic commands behind each pack:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir outputs --progress-every 30 --log-mode compact --interview-mode
python -m experiments.run_options_scenario_matrix --steps 180 --seed 7 --out-dir outputs
python -m experiments.run_options_toxicity_spread_sensitivity --steps 180 --seed 7 --out-dir outputs
```

That regenerates:

- the fixed case-study pack under [`docs/sample_outputs/toxic_flow_seed7/`](toxic_flow_seed7/)
- the same-seed comparison pack under [`docs/sample_outputs/scenario_matrix_seed7/`](scenario_matrix_seed7/)
- the toxicity-versus-spread sweep under [`docs/sample_outputs/toxicity_spread_sensitivity_seed7/`](toxicity_spread_sensitivity_seed7/)

## Scope

These artifacts come from the synthetic options dealer study. They do not come from replayed exchange options order-book data.
