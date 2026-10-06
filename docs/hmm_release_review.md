# Causal HMM engineering handoff

The optional research implementation has a runnable registered synthetic study,
independent causal oracles and published overhead evidence. It does **not** have
a fitted real-market model or evidence of economic benefit. The broader HFT
platform roadmap remains incomplete; see [project status](project_status.md).
The final current-head reviewer check is required separately from the completed
historical measurements below.

## What the layer does

The optional HMM estimates statistical latent states from causal public-book
and trade features. It has two modes: `observe` adds diagnostics without changing
strategy actions; `policy` conservatively adapts the existing `research_mm`
quotes. Neither mode repairs public-L2 execution uncertainty or establishes
economic benefit. The existing profiles remain the defaults. The frozen legacy
baseline hashes are unchanged; separately documented core correctness repairs
are not a claim of byte identity on every historical input.

The path is authoritative replay/book state → bounded fixed-grid features →
frozen training-only scaler → log-space forward filter → diagnostic audits or
opt-in quote controls. Offline fitting uses hmmlearn; the runtime filter is a
separate standard-library implementation. There is no live order entry,
automatic refitting, future smoothing in strategy decisions, or LLM strategy loop.

## Implementation acceptance and evidence

The following maps the supplied A–Z criteria to implementation and independent
regressions. These are technical proofs, not a real-market policy result.
Source `676ec15` passes the full local reviewer gate (1,532 Python / 16 Rust tests,
all 15 steps), plus all eight hosted Linux/Windows CI jobs. Publication changes
must receive their own verification; they do not inherit that gate result.

| Criterion | Evidence to inspect |
| --- | --- |
| A — causal features | `features.py`; independent book/lot arithmetic and raw-tape future-mutation tests in `test_hmm_features.py` and `test_hmm_dataset.py`. |
| B — partition separation | `dataset.py` and `collection.py`; physically separate UTC-day files, selected-partition-only readers, rejected test inputs in `test_hmm_dataset.py`, `test_hmm_collection.py`, and `test_hmm_fit.py`. |
| C — K=2..5 | `fit.py`; complete candidate/restart ledgers and independently checked parameter-count BIC in `test_hmm_fit.py`. |
| D — deterministic restarts | SHA-256-derived seeds, single-threaded numerical fitting, repeated artifact/report equality in a pinned environment; `test_hmm_fit.py`. |
| E — independent model loading | Safe, strict, content-addressed JSON in `artifact.py`; separate round-trip, corruption, dimensions, finite-value and no-clobber tests. |
| F — true forward filter | `filter.py`; probability-space enumeration of every latent path for short sequences in `test_hmm_filter.py`, independent of the forward recursion. |
| G — future invariance | Filter, feature, runtime, raw-tape observation, policy and fill-prefix mutation tests; no historical posterior or attribution relabeling. |
| H — invalid epochs | Immediate invalidation and warmup on recovery; `test_hmm_observation.py`, `test_hmm_runtime.py`, and actual between-sample policy fault tests. |
| I — observation mode | `simulate --hmm observe`; `observation.py` and bounded runner/export integration. |
| J — unchanged actions | Exact observer/reference event, fill, markout, latency and core-state comparisons across profiles and public-L2 modes; public-capture snapshot oracle tests. |
| K — state-conditioned outcomes | Separate decision/arrival/pre-fill labels in `execution.py`; quote denominators, horizons/lag/coverage, source/queue, risk, cash/fee/equity and descriptive drawdown audits. |
| L — opt-in policy | `hmm_regime_mm` composes `research_mm`; strict `HMMSettings`, immutable model/policy identities, six bounded controls. |
| M — hard risk dominance | Independent send/arrival live-plus-pending position/notional guards, missing units/marks, post-only arrival, feed invalidity and kill-switch regressions. |
| N — uncertainty/hysteresis | Scalar policy oracle, 1,001-risk monotonicity grid, uncertainty penalty, confirmation/age requirements and actual cancel/fill/refresh races. |
| O — disabled identity | `test_hmm_baseline.py` freezes the preimplementation legacy fixture's summary and event-trace hashes. Documented receipt-clock/global-timer, invalidation-trace and missing-unit risk repairs have separate disabled-mode regressions; source/provenance changes and their narrower scope are not hidden. |
| P — chronological protocol | Existing 60/20/20 whole-day protocol reused. Snippets and invalid clocks cannot certify complete joint-valid days. |
| Q — frozen variants | `study.py` freezes baseline, observer, posterior policy, hard-active and 250 ms cadence variants before extraction/test access; attempted failures remain visible. |
| R — paired bootstrap available | Existing matched 30-minute clock blocks and 5/60-minute sensitivities; independent batch/RNG and native audit tests. Short/broken strata retain null intervals. |
| S — readable diagnostics | `regime-inspect` for fitted-model characterization and `regime-report` for independently verified causal run audits; raw MAP and confirmed active states remain separate. |
| T — filtering vs smoothing | Forward filtering uses only observations available so far; retrospective training smoothing is explicitly labeled and never supplies runtime decisions. |
| U — public-L2 limits | No private identity/FIFO, hidden liquidity, cancellation attribution, private fills, or counterfactual historical impact is recovered. |
| V — bounded runtime | Fixed trailing windows, K-sized tables, two current episodes, capped live contexts/pending horizons and streamed output; generated census/checkpoint tests, not soak evidence. |
| W — existing tests | Historical golden behavior, core replay/accounting and published kernel parity remain in the full gate. |
| X — new tests | HMM feature/filter/artifact/fit/observer/policy/audit/study/cache/publication regressions are included in the normal suite. |
| Y — normal checks | Mypy, Ruff and format checks are required; optional fitting dependencies are installed for reviewer/CI tests. |
| Z — reviewer front door | `python scripts/reviewer_gate.py`; native report records source, tree state, environment, exact commands and all step results. |

## Exact feature vector and model selection

Default sampling is one second, with a ten-second trailing window, top-five
visible levels per side, and five-second book staleness. The 250 ms ablation
retains the same ten-second window and three-second confirmation/age rules.

Ordered features: `spread_bps`, `log_mid_return_bps`,
`realized_volatility_bps`, `imbalance_l1`, `imbalance_depth`,
`signed_trade_imbalance`, `log_visible_depth_lots`,
`visible_depth_change_fraction`, `log_market_event_rate`, `log_trade_rate`,
`microprice_displacement_bps`, `imbalance_change`. Formula identities and units
are frozen in the artifact. Visible decrease is not identified cancellation flow.

Training clips each feature to its calibration-only 0.5%/99.5% quantiles, then
freezes its population mean and standard deviation. Constant columns use scale
one with an explicit flag. Validation/test cannot refit these statistics.
The default fitter evaluates K=2,3,4,5 with ten deterministic restarts each.
The best valid training-likelihood restart represents each K; validation
likelihood per row selects candidates, with the frozen 0.01 tolerance/BIC/K
tie rule. Failed fits remain in the report. Test does not select K or policy.

The [registered five-variant demo](strategy_results/hmm_synthetic_study_reference.md)
selected K=5 at both cadences, retaining all eighty attempts and three failed
cadence restarts. The separate [raw recovery diagnostic](strategy_results/hmm_synthetic_recovery_reference.md)
also selected K=5 despite a two-state generating process. It reports imperfect native clustering and delayed/missed active-state
detection; many-to-one training alignment is not recovery of five true market
regimes. No real-market model has been fitted from the available two short dates.

## Reproduce from the repository root

Use fresh output names; evidence is never overwritten. Normal development setup
also requires the pinned Rust/MSVC toolchain for the full reviewer gate.

```powershell
python -m pip install -e ".[dev,storage,hmm]"
python experiments/run_hmm_synthetic_recovery.py --out-dir outputs/hmm_review_recovery
python -m lob_sim.cli regime-inspect --model outputs/hmm_review_recovery/model.json
python -m lob_sim.cli regime-fit --dataset outputs/hmm_review_recovery/features --symbol BTCUSDT --model outputs/hmm_review_refit.json --report outputs/hmm_review_refit_report.json
python -m lob_sim.cli --env docs/benchmark_results/hmm_active_public_reference.env simulate --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json --strategy research_mm --hmm observe --hmm-model docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json
python -m lob_sim.cli --env docs/benchmark_results/hmm_active_public_reference.env simulate --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json --strategy hmm_regime_mm --hmm policy --hmm-model docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json
python experiments/run_hmm_regime_study.py --synthetic-demo --out-dir outputs/hmm_paired_demo
python -m lob_sim.cli regime-inspect --model outputs/hmm_paired_demo/study/primary/model.json
$hmmStudy = Get-Content outputs/hmm_paired_demo/study/study_report.json -Raw | ConvertFrom-Json
foreach ($hmmRun in $hmmStudy.results) {
    if ($hmmRun.variant -ne 'baseline') {
        python -m lob_sim.cli regime-report --run-dir (Join-Path outputs/hmm_paired_demo/study $hmmRun.run_dir)
    }
}
python -m pytest -q -k hmm
python scripts/reviewer_gate.py
```

The first recovery command generates and fits synthetic data with separated
hidden truth; it is not real-market calibration. The explicit refit illustrates
the standalone fit command and writes different, new artifact paths. Source or
dependency changes can alter model provenance and numerical fitting identities.
The shared ten-lot/500 ms environment exercises quote activity without rounding
suppressed sub-lot sizes upward or changing hard exposure limits.

For the registered paired study, supply your own immutable independent
single-UTC-day tapes (at least three nonempty chronological partitions):

```powershell
python experiments/run_hmm_regime_study.py --input data/day_a.manifest.json --input data/day_b.manifest.json --input data/day_c.manifest.json --out-dir outputs/hmm_review_study --requote-ms 500
```

Those three paths are external-data placeholders, not bundled captures. Use
`--synthetic-demo` above for the self-contained comparison. A diagnostic study
is not a ten-day holdout. The available [real-data audit](strategy_results/hmm_regime_reference.md)
fails eligibility before fitting and retains the failure rather than inventing
a negative or profitable result.

The complete default synthetic comparison ran at clean `0c8d505`: five snippets,
3/1/1 chronological dates, eighty fit attempts, five completed test variants and
exact baseline/observer core event, fill and markout hashes. All four serialized
HMM audit reports passed. Final marked PnLs stay null because the tail has open
inventory without a valid mark; all 30/5/60-minute intervals stay null because
the comparison has only two eligible minutes. These are required omissions,
not a reason to fill missing values or claim policy benefit.

The focused producer/publication/reviewer tests pass 31 cases, including default
source regeneration, exact native-manifest hashes, partial-write failures and
no-clobber. Native study/recovery checks pass 64 cases. Current Mypy covers 70
source files; Ruff/format cover 194 files. These targeted results do not replace
the final full gate or cross-platform CI.

## Measured engineering cost

The [completed post-cache measurement](benchmark_results/hmm_snapshot_public_reference.md)
retains all 102 executions at clean `676ec15`, thirty raw timing samples per
mode, activity checks and exact shared configurations/bounded probes against
the [original measurement](benchmark_results/hmm_active_public_reference.md).
Median paired overhead is 80.65% for observation and 67.13% for policy.
Absolute times were more than three times slower on the uncontrolled host;
there is no causal speedup or dedicated-host regression claim. Run p99 is not
per-event or trading latency, and traced Python memory is not RSS or soak proof.
The cache's independent normalization-count and full-replay oracles establish
its mechanical behavior, not economic or latency improvement.

## Empirical limits and deliberately deferred work

The runtime contracts and a successful synthetic study are not evidence that
the policy improves trading. A claim-ready market study still needs sufficient
joint-valid days, a frozen universe, untouched test evaluation, identical
execution/latency assumptions, and meaningful intervals/coverage. The current
fixed-UTC period comparison rejects changing receipt wall/monotonic offsets;
the available real tapes fail that projection. Do not erase this reason, use a
future anchor, or relax it without a reviewed causal clock-mapping contract.

Moving-block intervals here estimate matched eligible-minute metrics, including
marked equity changes, not cumulative total-path PnL or resampled full-path
drawdown. Observed drawdown maxima are non-additive descriptive measurements;
mechanical state-tagged extensions are not causal drawdown contributions.

Walk-forward refitting/alignment/drift monitoring is optional and deliberately
deferred: this implementation starts with a frozen model. The original broader
HFT-platform roadmap still lacks end-to-end Rust engine parity, representative
24-hour capture/soak, ten-day held-out economics and dedicated-host performance.
Venue/risk/statistical/public-claim interpretation still needs human review.

Separately reviewed core repairs address schema-v3 float timer boundaries,
global quiet-symbol timers, control-record markout trace flushing, and risk
reservation with missing instrument units. These are explicit correctness
changes with disabled-HMM regressions, not effects of a latent-state policy.
Golden fixture identity cannot prove global before/after identity across all
historical inputs. No unsafe legacy behavior should be restored to make such
an overbroad claim appear true.

Failure modes include drifting feature/emission/transition distributions,
inappropriate Gaussian/conditional-independence assumptions, state splitting or
merging, too little rare-stress data, shifted scaling, excessive K, persistent
uncertainty, feed invalidation, and geometric durations that do not describe real
episodes. The state posterior is not a crash forecast, edge estimate or Kelly
probability. A defensible negative result is acceptable; tuning on test is not.

## File inventory and interview value

New implementation families are `lob_sim/regime/`, the three HMM experiment
entrypoints, HMM tests, and model/research/benchmark documents and evidence.
Integration modifies the native CLI/config, simulation observation/scheduling,
audits/metrics/export/checkpoint path, optional dependencies and reviewer checks.
An exact inventory against the standalone base is available without relying on
this prose: `git diff --name-status b2764b39d1785991555531a3eae66c0dc8f03a6a HEAD`.

For a technical interview, demonstrate the independent filtering oracle, a
future-mutation test, observer/reference identity, an invalid epoch, and a
pending-cancel/exposure race before discussing state-conditioned outcomes.
These are inspectable examples of data-quality engineering, causal event systems,
numerical discipline, statistical isolation, and reproducible research. They do
not establish production trading experience, employer endorsement or alpha.

Canonical Drive update: none. Original reports and incomplete attempts remain
unchanged and explicitly identified by their historical producers.

## Required engineering-deliverable map

| Deliverable | Where it is answered |
| --- | --- |
| 1–2: added and modified files | Exact base-to-feature inventory below; `A` means added and `M` modified. |
| 3: architecture | Native replay → bounded features → frozen scaler → independent forward filter → observer or opt-in controls; responsibilities and contracts in [technical specification](hmm_regime_model.md). |
| 4–7: vector, cadence, preprocessing, selection | Exact ordered vector and model-selection section above; immutable artifact contains units/formulas/configuration. |
| 8: selected demonstration model | Both registered cadences choose K=5; model and fit-report hashes plus all failures in the synthetic reference and native generated bundle. |
| 9–10: inference and causality tests | Independent latent-path enumeration, prefix/future mutation, gap invalidation, availability boundaries, checkpoint/resume and smoothing counterexample; A–H above. |
| 11: strategy integration | Observe is action-identical; explicit policy composes six conservative controls with research_mm; hard risk wins. |
| 12: experiment | Registry before extraction/test, 60/20/20 dates, all five variants, fixed latency pairing and matched 30/5/60-minute intervals with explicit missingness. |
| 13: reproduction | Complete command block above; generated inputs require no authentication. |
| 14: verification | Source-qualified native gate reports, targeted checks and hosted CI; pending checks are never described as passed. |
| 15–16: limitations and deferrals | Empirical/public-L2 limits, frozen-model assumptions, optional walk-forward, original platform gaps and human interpretation signoff. |

## Base-to-feature file inventory

This snapshot is relative to standalone base
`b2764b39d1785991555531a3eae66c0dc8f03a6a`. The command above returns the exact
current inventory, including later evidence-only publication commits.

```text
M	Makefile
M	README.md
A	docs/benchmark_inputs/hmm_public_btcusdt_20261005/README.md
A	docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json
A	docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e_000000.ndjson.zst
A	docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json
A	docs/benchmark_results/hmm_active_public_reference.env
A	docs/benchmark_results/hmm_active_public_reference.json
A	docs/benchmark_results/hmm_active_public_reference.md
A	docs/benchmark_results/hmm_overhead_smoke_reference.json
A	docs/benchmark_results/hmm_overhead_smoke_reference.md
A	docs/benchmark_results/hmm_public_overhead_attempt_5951541.json
A	docs/benchmark_results/hmm_public_overhead_attempt_e259673.json
A	docs/benchmark_results/hmm_snapshot_gate_attempt_0de91bb.json
A	docs/benchmark_results/hmm_snapshot_public_reference.json
A	docs/benchmark_results/hmm_snapshot_public_reference.md
M	docs/claims.md
M	docs/futures_strategy_profiles.md
A	docs/hmm_implementation_plan.md
A	docs/hmm_regime_model.md
M	docs/interview_packet.md
M	docs/project_status.md
M	docs/research_protocol.md
A	docs/strategy_results/hmm_regime_reference.json
A	docs/strategy_results/hmm_regime_reference.md
A	docs/strategy_results/hmm_synthetic_recovery_reference.json
A	docs/strategy_results/hmm_synthetic_recovery_reference.md
A	experiments/benchmark_hmm_overhead.py
A	experiments/run_hmm_regime_study.py
A	experiments/run_hmm_synthetic_recovery.py
M	lob_sim/cli.py
M	lob_sim/config.py
M	lob_sim/record/envelope.py
A	lob_sim/regime/__init__.py
A	lob_sim/regime/artifact.py
A	lob_sim/regime/collection.py
A	lob_sim/regime/dataset.py
A	lob_sim/regime/diagnostics.py
A	lob_sim/regime/economics.py
A	lob_sim/regime/execution.py
A	lob_sim/regime/features.py
A	lob_sim/regime/filter.py
A	lob_sim/regime/fit.py
A	lob_sim/regime/hysteresis.py
A	lob_sim/regime/model.py
A	lob_sim/regime/observation.py
A	lob_sim/regime/policy.py
A	lob_sim/regime/preprocess.py
A	lob_sim/regime/quotes.py
A	lob_sim/regime/recovery.py
A	lob_sim/regime/risk.py
A	lob_sim/regime/runtime.py
A	lob_sim/regime/settings.py
A	lob_sim/regime/study.py
A	lob_sim/regime/study_outcomes.py
A	lob_sim/regime/study_periods.py
A	lob_sim/regime/study_pnl.py
A	lob_sim/regime/synthetic.py
A	lob_sim/regime/validation.py
A	lob_sim/research/clock_bootstrap.py
M	lob_sim/research/protocol.py
M	lob_sim/sim/engine.py
M	lob_sim/sim/export.py
M	lob_sim/sim/metrics.py
M	lob_sim/sim/mm_strategy.py
A	lob_sim/sim/observation.py
M	lob_sim/sim/run_manifest.py
M	lob_sim/sim/runner.py
M	pyproject.toml
M	requirements.txt
M	scripts/reviewer_gate.py
A	tests/test_clock_bootstrap.py
A	tests/test_crc32c.py
A	tests/test_global_scheduler.py
A	tests/test_hmm_artifact_cache.py
A	tests/test_hmm_baseline.py
A	tests/test_hmm_benchmark.py
A	tests/test_hmm_benchmark_publication.py
A	tests/test_hmm_collection.py
A	tests/test_hmm_dataset.py
A	tests/test_hmm_diagnostics.py
A	tests/test_hmm_economics.py
A	tests/test_hmm_execution.py
A	tests/test_hmm_features.py
A	tests/test_hmm_filter.py
A	tests/test_hmm_fit.py
A	tests/test_hmm_instrument_identity.py
A	tests/test_hmm_null_sink.py
A	tests/test_hmm_observation.py
A	tests/test_hmm_policy.py
A	tests/test_hmm_preprocess_artifact.py
A	tests/test_hmm_quotes.py
A	tests/test_hmm_recovery.py
A	tests/test_hmm_risk.py
A	tests/test_hmm_runtime.py
A	tests/test_hmm_snapshot.py
A	tests/test_hmm_sources.py
A	tests/test_hmm_study.py
A	tests/test_hmm_study_outcomes.py
A	tests/test_hmm_study_pnl.py
A	tests/test_hmm_synthetic_sources.py
A	tests/test_ratio_bootstrap.py
M	tests/test_reviewer_gate.py
A	docs/hmm_release_review.md
A	docs/strategy_results/hmm_synthetic_study_reference.json
A	docs/strategy_results/hmm_synthetic_study_reference.md
```

