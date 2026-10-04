# Causal HMM implementation ledger

The HMM is an optional research-layer estimator. Reconstruction, causal event
ordering, execution scenarios, accounting and hard risk controls remain owned by
the existing simulator. The current spread/imbalance `regime` bucket is not an HMM
and is preserved. No fitted HMM or economic benefit is claimed by this ledger.

## Baseline and sequence

Canonical repository: `gobrienHFT/lob_sim`; base `b2764b39d1785991555531a3eae66c0dc8f03a6a`.
Before implementation the reviewer gate passed 543 Python tests, 16 Rust tests,
typing, formatting, differential parity, artifacts, fault checks and installed
wheel smoke tests (`--skip-benchmark`). Work is on `codex/hmm-regime-layer`.

1. Specify bounded fixed-grid microstructure features; independently test their
   formulas, warmup, epoch boundaries, missingness and prefix causality.
2. Implement train-only preprocessing, immutable numeric model parameters,
   log-space forward filtering and strict content-addressed JSON artifacts.
3. Extract training sequences through the existing validated replay/book path.
   Reuse whole-UTC-day partitions and `ResearchRegistry`; never open test data
   during fitting/selection. Fit K=2..5 with ten seeded restarts; retain failures,
   convergence diagnostics, likelihood, parameter-count BIC and occupancy.
4. Canonicalize states from training-only signatures. Implement uncertainty and
   hysteresis; document geometric duration in steps and seconds.
5. Integrate observation-only mode, bounded audit sinks, checkpoint/resume and
   decision/arrival/fill state attribution. Prove paired strategy identity.
6. Compose an opt-in `hmm_regime_mm` policy with the existing research baseline;
   test monotone bounded adaptations and the dominance of hard risk controls.
7. Freeze all model/policy/cadence/feature variants before evaluation. Run paired
   baseline/observer/policy comparisons and 30-minute moving-block bootstrap
   intervals with 5/60-minute sensitivities. Report failures and negative results.
8. Publish synthetic switching diagnostics, real-tape diagnostic results,
   overhead benchmarks, causal regression fixtures, documentation and commands.
   Run the complete reviewer gate and cross-platform CI before release.

Walk-forward fitting is optional. The primary implementation is a frozen model;
no implicit refit or test-dependent model selection is permitted. Fewer than ten
joint-valid UTC days means diagnostic-only research, not a holdout claim.

## Acceptance tracking

All criteria remain required. A checked foundation test is not a substitute for
an engine-integrated or real-data proof. Update the evidence column as each
milestone is verified; do not mark the goal complete from this plan alone.

| Criterion | Required evidence | Status |
| --- | --- | --- |
| A | Causal features and raw-tape future-mutation tests | Implemented through authoritative engine observations; raw-tape prefix mutation test |
| B | Enforced train/validation/test separation | Physical UTC-day files; selected-partition-only readers; fitter rejects test |
| C | Reproducible K=2..5 selection | Candidate/restart ledgers and validation/BIC rules; synthetic fit tests |
| D | Deterministic multiple restarts | SHA-256 seeds; repeated full model/report equality in pinned environment |
| E | Independently loaded, validated JSON artifact | Fitted provenance and independent load/inspection tests implemented |
| F | Runtime forward filter, independent reference proof | Implemented; independent latent-path enumeration tests |
| G | Historical runtime posterior unaffected by future data | Raw-tape mutation preserves available historical features, posterior, hysteresis and decision diagnostics |
| H | Invalid/stale epochs cannot report a valid regime | Integrated observer clears confidence at between-grid feed faults; stale diagnostics contain no posterior |
| I | Observation-only simulation mode | simulate --hmm observe; bounded verified trace and frozen model export |
| J | Observer versus baseline action identity | Exact core action/state/accounting parity across three profiles and trade/depth fill modes; extra diagnostics explicitly separated |
| K | State-conditioned execution and markouts | Frozen decision/arrival/pre-fill attribution; configured-horizon coverage and quantity-weighted markouts; decision-to-fill matrix; bounded decision/arrival quote cohorts with explicit request, acceptance, rejection and unique-filled denominators; source/risk tables remain |
| L | Opt-in HMM market-making profile | hmm_regime_mm composes research_mm; six actual bounded controls, explicit policy mode, immutable training-risk/config identities |
| M | Hard risk dominance tests | Live-plus-pending send/arrival guards, portfolio and unknown-unit exposure, sub-lot suppression, feed faults and kill-switch tests; no model override |
| N | Hysteresis and uncertainty tests | Estimator tests plus independent scalar policy oracle, 1001-risk monotonicity, uncertainty penalties and real pending-cancel/refresh races |
| O | HMM-disabled golden baseline identity | Preimplementation summary/trace hashes preserved through observation-mode integration |
| P | Existing chronological research protocol reused | Reused for physical dataset partitions; diagnostic-only without coverage certification |
| Q | Frozen variant registry before untouched test | Pending |
| R | Paired moving-block bootstrap study | Pending |
| S | Human-readable model/state diagnostics | regime-inspect includes every valid K's likelihood, convergence, occupancy and duration diagnostics; regime-report verifies serialized causal raw/active state summaries before printing |
| T | Filtering versus smoothing documentation | Documented in hmm_regime_model.md |
| U | Public-L2 limitations documented | Documented in hmm_regime_model.md |
| V | Bounded runtime windows and audit output | Streamed audits; fixed windows, one signal, two current diagnostic episodes, fixed K-by-feature moments, capped live-order contexts and core pending horizons; independent generated census/virtual stream regressions (not soak evidence) |
| W | Existing tests pass after integration | Full suite passes at 218027e; repeat after remaining research integration |
| X | New tests pass | Foundation, dataset/fit, observation/checkpoint, execution-attribution and policy suites implemented; final evaluation checks pending |
| Y | Standard lint and typing pass | Gate Mypy (57 source files), Ruff and format (162 files) pass at 218027e; full-package Mypy also passes across 71 source files; repeat after remaining work |
| Z | Complete reviewer gate passes | All 15 steps pass at 218027e, including 1019 Python/16 Rust tests, primitive differential, artifacts, faults, installed wheel and fixture benchmark; final research release still pending |

## Release boundaries

State characterization now separates raw MAP and confirmed active labels,
conditional market-feature/confidence/entropy moments, sample fractions and
complete/censored episodes. It is descriptive, not an economic evaluation.
Quote cohorts now distinguish scheduled/arrived/accepted/rejected/discarded
requests, unique filled orders and fill events, with explicit cutoff censoring.
Their fixed-cardinality stream and checkpoint are checked against core orders,
scheduler requests and accounted fill rows. All 48 new regressions and the full
clean reviewer gate pass at 218027e. The first gate also rejected a corrupted
quantity correctly, but a prior test expected the later risk-reservation error;
its precise earlier-identity expectation and unchanged-core assertion now pass.
Fill-source tables, time-weighted state risk, registered
paired research, raw synthetic recovery and representative overhead remain.
New observer checkpoint version 2 rejects older HMM continuation state rather
than inventing missing moments/episodes. Execution checkpoint version 2 rejects
older HMM state rather than fabricating missing quote denominators; disabled
checkpoints are unchanged.

Public L2 cannot identify private FIFO, hidden liquidity or actual private fills.
An HMM posterior is not an edge estimate, Kelly input or causal discovery of
real exchange participants. Offline inference/replay throughput is not trading
latency. Negative policy results are valid research outcomes. Safe model JSON
is not executable pickle; checksums identify content, not a trusted author.
