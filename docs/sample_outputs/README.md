# Sample Outputs

This directory contains committed, deterministic sample artifacts for the options market-making case study.

## Fixed Configuration

- Scenario: `toxic_flow`
- Steps: `180`
- Seed: `7`

## Exact Command Used

The refresh script runs:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir <temp_dir> --progress-every 30 --log-mode compact
```

## What to Look at First

Single-run case study:

1. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)
2. [`toxic_flow_seed7/demo_report.md`](toxic_flow_seed7/demo_report.md)
3. [`toxic_flow_seed7/pnl_over_time.png`](toxic_flow_seed7/pnl_over_time.png)
4. [`toxic_flow_seed7/inventory_over_time.png`](toxic_flow_seed7/inventory_over_time.png)
5. [`toxic_flow_seed7/fills_head.csv`](toxic_flow_seed7/fills_head.csv)

Cross-scenario credibility check:

1. [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)
2. [`scenario_matrix_seed7/scenario_comparison.png`](scenario_matrix_seed7/scenario_comparison.png)
3. [`scenario_matrix_seed7/scenario_matrix.csv`](scenario_matrix_seed7/scenario_matrix.csv)

## How to Refresh

From the repo root:

```bash
python scripts/refresh_sample_outputs.py
```

That regenerates:

- the fixed case-study pack under [`docs/sample_outputs/toxic_flow_seed7/`](toxic_flow_seed7/)
- the same-seed comparison pack under [`docs/sample_outputs/scenario_matrix_seed7/`](scenario_matrix_seed7/)

## Scope

These artifacts come from the synthetic options dealer study. They do not come from replayed exchange options order-book data.
