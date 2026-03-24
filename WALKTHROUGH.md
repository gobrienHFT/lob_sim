# Walkthrough

## 60-Second Pitch

Start with the futures side. The core artifact is a deterministic Binance USD-M L2 replay that records `exchangeInfo`, snapshots, depth diffs, and `aggTrade` prints, reconstructs the local book with explicit continuity checks, and runs an event-driven passive-fill simulation with FIFO queue assumptions and queue-ahead tracking.

The options side is a separate controlled dealer-pricing case study. It is there to make fair value, reservation price, inventory skew, signed markout, and hedging assumptions easy to inspect, not to claim venue-realistic options microstructure.

## 90-Second Walkthrough

1. Open [README.md](README.md) and anchor the discussion on the futures replay, not the options artifact.
2. Point to [docs/binance_usdm_feed_semantics.md](docs/binance_usdm_feed_semantics.md) for snapshot seeding, `U/u/pu` continuity, and gap handling.
3. Point to [lob_sim/sim/fill_model.py](lob_sim/sim/fill_model.py) and [tests/test_fill_model.py](tests/test_fill_model.py) for FIFO queue consumption, queue-ahead tracking, and passive fill attribution.
4. Open [docs/sample_outputs/futures_replay_walkthrough/README.md](docs/sample_outputs/futures_replay_walkthrough/README.md) for the zero-click futures artifact path.
5. Open [docs/sample_outputs/futures_replay_walkthrough/summary.json](docs/sample_outputs/futures_replay_walkthrough/summary.json) and [docs/sample_outputs/futures_replay_walkthrough/trades.csv](docs/sample_outputs/futures_replay_walkthrough/trades.csv).
6. Open [docs/sample_outputs/futures_replay_walkthrough/walkthrough.md](docs/sample_outputs/futures_replay_walkthrough/walkthrough.md) for the continuity, queue-ahead, and passive-fill notes.
7. Then show the controlled options case study via [docs/sample_outputs/toxic_flow_seed7/case_brief.md](docs/sample_outputs/toxic_flow_seed7/case_brief.md) and [docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md](docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md).

## 5-Minute Walkthrough

1. README overview, futures replay internals, matching model, and limitations.
2. [docs/binance_usdm_feed_semantics.md](docs/binance_usdm_feed_semantics.md) for market-data semantics and what is inferred.
3. [docs/futures_validation.md](docs/futures_validation.md) for invariants, tests, and non-goals.
4. [docs/sample_outputs/futures_replay_walkthrough/README.md](docs/sample_outputs/futures_replay_walkthrough/README.md) for the zero-click futures walkthrough pack.
5. [docs/sample_outputs/futures_replay_walkthrough/summary.json](docs/sample_outputs/futures_replay_walkthrough/summary.json), [docs/sample_outputs/futures_replay_walkthrough/trades.csv](docs/sample_outputs/futures_replay_walkthrough/trades.csv), and [docs/sample_outputs/futures_replay_walkthrough/walkthrough.md](docs/sample_outputs/futures_replay_walkthrough/walkthrough.md) for the actual artifact path.
6. [docs/sample_outputs/futures_recorded_clip_case/README.md](docs/sample_outputs/futures_recorded_clip_case/README.md) and [docs/sample_outputs/futures_recorded_clip_case/case_notes.md](docs/sample_outputs/futures_recorded_clip_case/case_notes.md) for one recorded-data proof point.
7. [docs/futures_strategy_profiles.md](docs/futures_strategy_profiles.md) and [docs/strategy_results/futures_strategy_profile_reference.md](docs/strategy_results/futures_strategy_profile_reference.md) for baseline-vs-layered quoting choices on the committed recorded clip.
8. [docs/futures_benchmarks.md](docs/futures_benchmarks.md), [docs/benchmark_results/futures_replay_reference.md](docs/benchmark_results/futures_replay_reference.md), and [experiments/benchmark_futures_replay.py](experiments/benchmark_futures_replay.py) for the published reference run and the rerunnable benchmark driver.
9. [docs/sample_outputs/toxic_flow_seed7/case_brief.md](docs/sample_outputs/toxic_flow_seed7/case_brief.md) for the dealer-pricing case study.
10. [docs/options_case_study_notes.md](docs/options_case_study_notes.md) for concise options framing if the discussion stays on pricing and hedging.

## Core Talking Points

- The strongest claim here is deterministic event-time replay with explicit book-sync semantics.
- Passive fills are queue-aware and rely on explicit FIFO assumptions rather than bar-level heuristics.
- Gap handling is explicit: the code checks continuity and does not patch over missing updates.
- The strategy is a baseline quoting/control policy on top of the replay and matching core.
- The options artifact is controlled and synthetic by design, which keeps its assumptions inspectable.

## Common Reviewer Questions

### What is directly observed versus inferred?

Directly observed inputs are the Binance snapshot, depth diff messages, `aggTrade` prints, and symbol metadata. Queue position, cancel-vs-trade attribution inside a level reduction, and passive fill timing are inferred from those public signals under explicit assumptions.

### How are gaps handled?

The synchronizer requires the first accepted diff to cover the snapshot id and then checks `pu` continuity on later diffs. Live collection can re-snapshot on gaps; offline replay and simulation do not invent the missing sequence.

### Why call the strategy baseline?

Because the stronger contribution here is the deterministic replay, matching, and measurement core. The strategy is intentionally simple enough to expose queue mechanics, inventory skew, and risk controls without overselling alpha.

### Why keep the options case study?

It shows pricing and risk reasoning that the futures replay does not cover: fair value, reservation price, signed markout, and hedging logic. It is useful precisely because it is framed as a controlled case study rather than a venue-realistic options simulator.

### What would real data change on the options side?

Real data would calibrate the volatility surface, customer flow, toxicity assumptions, and hedge-cost model. The current value is transparency, not calibration.

## Sample Outputs

- Futures walkthrough pack: [docs/sample_outputs/futures_replay_walkthrough/README.md](docs/sample_outputs/futures_replay_walkthrough/README.md)
- Futures summary: [docs/sample_outputs/futures_replay_walkthrough/summary.json](docs/sample_outputs/futures_replay_walkthrough/summary.json)
- Futures trades: [docs/sample_outputs/futures_replay_walkthrough/trades.csv](docs/sample_outputs/futures_replay_walkthrough/trades.csv)
- Futures notes: [docs/sample_outputs/futures_replay_walkthrough/walkthrough.md](docs/sample_outputs/futures_replay_walkthrough/walkthrough.md)
- Recorded clip case: [docs/sample_outputs/futures_recorded_clip_case/README.md](docs/sample_outputs/futures_recorded_clip_case/README.md)
- Recorded clip summary: [docs/sample_outputs/futures_recorded_clip_case/summary.json](docs/sample_outputs/futures_recorded_clip_case/summary.json)
- Recorded clip trades: [docs/sample_outputs/futures_recorded_clip_case/trades.csv](docs/sample_outputs/futures_recorded_clip_case/trades.csv)
- Recorded clip notes: [docs/sample_outputs/futures_recorded_clip_case/case_notes.md](docs/sample_outputs/futures_recorded_clip_case/case_notes.md)
- Strategy profile notes: [docs/futures_strategy_profiles.md](docs/futures_strategy_profiles.md)
- Strategy profile comparison: [docs/strategy_results/futures_strategy_profile_reference.md](docs/strategy_results/futures_strategy_profile_reference.md)
- Futures semantics and validation: [docs/binance_usdm_feed_semantics.md](docs/binance_usdm_feed_semantics.md), [docs/futures_validation.md](docs/futures_validation.md)
- Options case-study pack: [docs/sample_outputs/toxic_flow_seed7/](docs/sample_outputs/toxic_flow_seed7/)
- Scenario matrix: [docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md](docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md)
- Spread-vs-toxicity sweep: [docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md](docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)
- Options case study notes: [docs/options_case_study_notes.md](docs/options_case_study_notes.md)
