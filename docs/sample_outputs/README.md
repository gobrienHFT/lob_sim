# Sample Outputs

This directory contains committed, deterministic sample artifacts for both the futures replay walkthrough and the controlled dealer-pricing case study.

## Futures Replay Walkthrough

- Pack entry: [`futures_replay_walkthrough/README.md`](futures_replay_walkthrough/README.md)
- Summary: [`futures_replay_walkthrough/summary.json`](futures_replay_walkthrough/summary.json)
- Manifest: [`futures_replay_walkthrough/manifest.json`](futures_replay_walkthrough/manifest.json)
- Trades: [`futures_replay_walkthrough/trades.csv`](futures_replay_walkthrough/trades.csv)
- Event trace: [`futures_replay_walkthrough/event_trace.csv`](futures_replay_walkthrough/event_trace.csv)
- Notes: [`futures_replay_walkthrough/walkthrough.md`](futures_replay_walkthrough/walkthrough.md)
- Input type: synthetic deterministic walkthrough fixture
- Regenerate with:

```bash
python scripts/refresh_futures_showcase.py
```

Open first:

1. [`futures_replay_walkthrough/README.md`](futures_replay_walkthrough/README.md)
2. [`futures_replay_walkthrough/summary.json`](futures_replay_walkthrough/summary.json)
3. [`futures_replay_walkthrough/manifest.json`](futures_replay_walkthrough/manifest.json)
4. [`futures_replay_walkthrough/trades.csv`](futures_replay_walkthrough/trades.csv)
5. [`futures_replay_walkthrough/event_trace.csv`](futures_replay_walkthrough/event_trace.csv)
6. [`futures_replay_walkthrough/walkthrough.md`](futures_replay_walkthrough/walkthrough.md)

Determinism check:

```bash
python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example
```

Pack audit:

```bash
python scripts/audit_futures_pack.py --committed-futures
```

## Recorded Futures Clip Case

- Pack entry: [`futures_recorded_clip_case/README.md`](futures_recorded_clip_case/README.md)
- Summary: [`futures_recorded_clip_case/summary.json`](futures_recorded_clip_case/summary.json)
- Manifest: [`futures_recorded_clip_case/manifest.json`](futures_recorded_clip_case/manifest.json)
- Trades: [`futures_recorded_clip_case/trades.csv`](futures_recorded_clip_case/trades.csv)
- Event trace: [`futures_recorded_clip_case/event_trace.csv`](futures_recorded_clip_case/event_trace.csv)
- Notes: [`futures_recorded_clip_case/case_notes.md`](futures_recorded_clip_case/case_notes.md)
- Input type: recorded BTCUSDT public-data clip from an existing local raw capture
- Regenerate with:

```bash
python scripts/refresh_futures_recorded_case.py
```

Open first:

1. [`futures_recorded_clip_case/README.md`](futures_recorded_clip_case/README.md)
2. [`futures_recorded_clip_case/summary.json`](futures_recorded_clip_case/summary.json)
3. [`futures_recorded_clip_case/manifest.json`](futures_recorded_clip_case/manifest.json)
4. [`futures_recorded_clip_case/trades.csv`](futures_recorded_clip_case/trades.csv)
5. [`futures_recorded_clip_case/event_trace.csv`](futures_recorded_clip_case/event_trace.csv)
6. [`futures_recorded_clip_case/case_notes.md`](futures_recorded_clip_case/case_notes.md)

## Futures Stress Case

- Pack entry: [`futures_stress_case/README.md`](futures_stress_case/README.md)
- Summary: [`futures_stress_case/summary.json`](futures_stress_case/summary.json)
- Manifest: [`futures_stress_case/manifest.json`](futures_stress_case/manifest.json)
- Trades: [`futures_stress_case/trades.csv`](futures_stress_case/trades.csv)
- Event trace: [`futures_stress_case/event_trace.csv`](futures_stress_case/event_trace.csv)
- Notes: [`futures_stress_case/case_notes.md`](futures_stress_case/case_notes.md)
- Input type: synthetic-but-exchange-shaped BTCUSDT stress fixture
- Regenerate with:

```bash
python scripts/refresh_futures_stress_case.py
```

Open first:

1. [`futures_stress_case/README.md`](futures_stress_case/README.md)
2. [`futures_stress_case/summary.json`](futures_stress_case/summary.json)
3. [`futures_stress_case/manifest.json`](futures_stress_case/manifest.json)
4. [`futures_stress_case/trades.csv`](futures_stress_case/trades.csv)
5. [`futures_stress_case/event_trace.csv`](futures_stress_case/event_trace.csv)
6. [`futures_stress_case/case_notes.md`](futures_stress_case/case_notes.md)

## Futures Fill Assumption Envelope

- Envelope guide: [`../fill_assumption_envelope.md`](../fill_assumption_envelope.md)
- Pack entry: [`futures_fill_assumption_envelope/README.md`](futures_fill_assumption_envelope/README.md)
- Summary: [`futures_fill_assumption_envelope/fill_envelope_summary.json`](futures_fill_assumption_envelope/fill_envelope_summary.json)
- CSV: [`futures_fill_assumption_envelope/fill_envelope_summary.csv`](futures_fill_assumption_envelope/fill_envelope_summary.csv)
- Report: [`futures_fill_assumption_envelope/fill_envelope_report.md`](futures_fill_assumption_envelope/fill_envelope_report.md)

Public L2 cannot prove private fills. The profiles are assumption bounds, not private execution truth. Robust conclusions should survive conservative/base/aggressive. Conclusions that only work under aggressive assumptions are weak.

Regenerate with:

```bash
python experiments/run_fill_assumption_envelope.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example --out-dir docs/sample_outputs/futures_fill_assumption_envelope
```

## Controlled Options Case Study

- Pack: [`toxic_flow_seed7/`](toxic_flow_seed7/)
- Scenario matrix: [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)
- Sensitivity sweep: [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)

### Fixed Configuration

- Scenario: `toxic_flow`
- Steps: `180`
- Seed: `7`

Recommended artifact path: [`../options_case_study_notes.md`](../options_case_study_notes.md)

### Exact Command Used

The refresh script runs:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir outputs --progress-every 30 --log-mode compact --walkthrough-mode
```

### What to Look at First

Recommended artifact order:

1. [`toxic_flow_seed7/case_brief.md`](toxic_flow_seed7/case_brief.md)
2. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)
3. [`toxic_flow_seed7/implied_vol_surface_snapshot.png`](toxic_flow_seed7/implied_vol_surface_snapshot.png)
4. [`toxic_flow_seed7/position_surface_heatmap.png`](toxic_flow_seed7/position_surface_heatmap.png)
5. [`toxic_flow_seed7/vega_surface_heatmap.png`](toxic_flow_seed7/vega_surface_heatmap.png)
6. representative fill in [`toxic_flow_seed7/case_brief.md#representative-fill`](toxic_flow_seed7/case_brief.md#representative-fill)
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

### All Futures Reviewer Artifacts

From a clean source tree:

```bash
python scripts/refresh_futures_reviewer_artifacts.py
```

Exact deterministic commands behind each pack:

```bash
python -m lob_sim.cli options-demo --scenario toxic_flow --steps 180 --seed 7 --out-dir outputs --progress-every 30 --log-mode compact --walkthrough-mode
python -m experiments.run_options_scenario_matrix --steps 180 --seed 7 --out-dir outputs
python -m experiments.run_options_toxicity_spread_sensitivity --steps 180 --seed 7 --out-dir outputs
python scripts/refresh_futures_stress_case.py
```

That regenerates:

- the futures walkthrough pack under [`docs/sample_outputs/futures_replay_walkthrough/`](futures_replay_walkthrough/)
- the recorded futures clip case under [`docs/sample_outputs/futures_recorded_clip_case/`](futures_recorded_clip_case/)
- the synthetic futures stress case under [`docs/sample_outputs/futures_stress_case/`](futures_stress_case/)
- the fixed case-study pack under [`docs/sample_outputs/toxic_flow_seed7/`](toxic_flow_seed7/)
- the same-seed comparison pack under [`docs/sample_outputs/scenario_matrix_seed7/`](scenario_matrix_seed7/)
- the toxicity-versus-spread sweep under [`docs/sample_outputs/toxicity_spread_sensitivity_seed7/`](toxicity_spread_sensitivity_seed7/)

### Scope

The futures packs come from deterministic Binance USD-M style replay inputs. The options packs come from the synthetic dealer-pricing study and do not represent replayed exchange options order-book data.
