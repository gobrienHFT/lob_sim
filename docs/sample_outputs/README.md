# Sample Outputs

This directory contains committed, deterministic sample artifacts for both the futures replay walkthrough and the controlled dealer-pricing case study.

## Futures Replay Walkthrough

- Pack entry: [`futures_replay_walkthrough/README.md`](futures_replay_walkthrough/README.md)
- Summary: [`futures_replay_walkthrough/summary.json`](futures_replay_walkthrough/summary.json)
- Trades: [`futures_replay_walkthrough/trades.csv`](futures_replay_walkthrough/trades.csv)
- Notes: [`futures_replay_walkthrough/walkthrough.md`](futures_replay_walkthrough/walkthrough.md)
- Input type: synthetic deterministic walkthrough fixture
- Regenerate with:

```bash
python scripts/refresh_futures_showcase.py
```

Open first:

1. [`futures_replay_walkthrough/README.md`](futures_replay_walkthrough/README.md)
2. [`futures_replay_walkthrough/summary.json`](futures_replay_walkthrough/summary.json)
3. [`futures_replay_walkthrough/trades.csv`](futures_replay_walkthrough/trades.csv)
4. [`futures_replay_walkthrough/walkthrough.md`](futures_replay_walkthrough/walkthrough.md)

## Recorded Futures Clip Case

- Pack entry: [`futures_recorded_clip_case/README.md`](futures_recorded_clip_case/README.md)
- Summary: [`futures_recorded_clip_case/summary.json`](futures_recorded_clip_case/summary.json)
- Trades: [`futures_recorded_clip_case/trades.csv`](futures_recorded_clip_case/trades.csv)
- Notes: [`futures_recorded_clip_case/case_notes.md`](futures_recorded_clip_case/case_notes.md)
- Input type: clipped recorded BTCUSDT event stream from an existing local raw capture
- Regenerate with:

```bash
python scripts/refresh_futures_recorded_case.py
```

Open first:

1. [`futures_recorded_clip_case/README.md`](futures_recorded_clip_case/README.md)
2. [`futures_recorded_clip_case/summary.json`](futures_recorded_clip_case/summary.json)
3. [`futures_recorded_clip_case/trades.csv`](futures_recorded_clip_case/trades.csv)
4. [`futures_recorded_clip_case/case_notes.md`](futures_recorded_clip_case/case_notes.md)

## Controlled Options Case Study

- Pack: [`toxic_flow_seed7/`](toxic_flow_seed7/)
- Scenario matrix: [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)
- Sensitivity sweep: [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)

### Fixed Configuration

- Scenario: `toxic_flow`
- Steps: `180`
- Seed: `7`

Fastest prep for a live walkthrough: [`../interview_talk_track.md`](../interview_talk_track.md)

### Exact Command Used

The refresh script runs:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir outputs --progress-every 30 --log-mode compact --interview-mode
```

### What to Look at First

Canonical screen-share order:

1. [`toxic_flow_seed7/interview_brief.md`](toxic_flow_seed7/interview_brief.md)
2. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)
3. [`toxic_flow_seed7/implied_vol_surface_snapshot.png`](toxic_flow_seed7/implied_vol_surface_snapshot.png)
4. [`toxic_flow_seed7/position_surface_heatmap.png`](toxic_flow_seed7/position_surface_heatmap.png)
5. [`toxic_flow_seed7/vega_surface_heatmap.png`](toxic_flow_seed7/vega_surface_heatmap.png)
6. representative fill in [`toxic_flow_seed7/interview_brief.md#representative-fill`](toxic_flow_seed7/interview_brief.md#representative-fill)
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

### Futures Replay Walkthrough

From the repo root:

```bash
python scripts/refresh_futures_showcase.py
```

### Recorded Futures Clip Case

From the repo root:

```bash
python scripts/refresh_futures_recorded_case.py
```

### Controlled Options Case Study

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

- the futures walkthrough pack under [`docs/sample_outputs/futures_replay_walkthrough/`](futures_replay_walkthrough/)
- the recorded futures clip case under [`docs/sample_outputs/futures_recorded_clip_case/`](futures_recorded_clip_case/)
- the fixed case-study pack under [`docs/sample_outputs/toxic_flow_seed7/`](toxic_flow_seed7/)
- the same-seed comparison pack under [`docs/sample_outputs/scenario_matrix_seed7/`](scenario_matrix_seed7/)
- the toxicity-versus-spread sweep under [`docs/sample_outputs/toxicity_spread_sensitivity_seed7/`](toxicity_spread_sensitivity_seed7/)

### Scope

These artifacts come from the synthetic options dealer study. They do not come from replayed exchange options order-book data.
