# Causal HMM implementation ledger

The HMM is an optional research-layer estimator. Reconstruction, causal event
ordering, execution scenarios, accounting and hard risk controls remain owned by
the existing simulator. The current spread/imbalance `regime` bucket is not an HMM
and is preserved. The synthetic fitted model is diagnostic; no fitted
real-market HMM or economic benefit is claimed by this ledger.

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

The known-regime raw-tape recovery path is now implemented separately from the
remaining paired economic study. `experiments/run_hmm_synthetic_recovery.py`
generates ordinary schema-v3 market messages with hidden labels in physically
separate truth files, extracts through the authoritative engine, reuses the
whole-day split and frozen registry, fits K=2..5, and evaluates the actual causal
runtime after training-only alignment. Native ARI, mapped confusion, censored
switch lag, transition error and complete/censored durations distinguish genuine
recovery from convenient relabeling. Synthetic snippets do not certify valid
full days, demonstrate a real-market model or prove policy benefit. The matching
MBO exchange remains a different mode.

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
| K | State-conditioned execution and markouts | Frozen decision/arrival/pre-fill attribution, horizon coverage, quote denominators and source/queue tables; integer time-weighted risk plus reconciled single-symbol cash/fee/equity and observed drawdown diagnostics implemented; registered out-of-sample evaluation remains |
| L | Opt-in HMM market-making profile | hmm_regime_mm composes research_mm; six actual bounded controls, explicit policy mode, immutable training-risk/config identities |
| M | Hard risk dominance tests | Live-plus-pending send/arrival guards, portfolio and unknown-unit exposure, sub-lot suppression, feed faults and kill-switch tests; no model override |
| N | Hysteresis and uncertainty tests | Estimator tests plus independent scalar policy oracle, 1001-risk monotonicity, uncertainty penalties and real pending-cancel/refresh races |
| O | HMM-disabled golden baseline identity | Preimplementation summary/trace hashes preserved through observation-mode integration |
| P | Existing chronological research protocol reused | Reused for physical dataset partitions; diagnostic-only without coverage certification |
| Q | Frozen variant registry before untouched test | Implemented for synthetic recovery; registered paired policy study remains pending |
| R | Paired moving-block bootstrap study | Pending |
| S | Human-readable model/state diagnostics | regime-inspect includes every valid K's likelihood, convergence, occupancy and duration diagnostics; regime-report verifies serialized causal raw/active state summaries before printing |
| T | Filtering versus smoothing documentation | Documented in hmm_regime_model.md |
| U | Public-L2 limitations documented | Documented in hmm_regime_model.md |
| V | Bounded runtime windows and audit output | Streamed audits; fixed windows, one signal, two current diagnostic episodes, fixed K-by-feature moments, capped live-order contexts and core pending horizons; independent generated census/virtual stream regressions (not soak evidence) |
| W | Existing tests pass after integration | Full 1239-test Python suite passes at 60b0696; repeat after remaining research integration |
| X | New tests pass | Foundation, dataset/fit, observation/checkpoint, execution-attribution and policy suites implemented; final evaluation checks pending |
| Y | Standard lint and typing pass | Gate Mypy (61 source files), Ruff and format (172 files) pass at 60b0696; full-package Mypy also passes across 75 source files; repeat after remaining work |
| Z | Complete reviewer gate passes | All 15 steps pass at 60b0696, including 1239 Python/16 Rust tests, primitive differential, artifacts, faults, installed wheel and fixture benchmark; documentation-only dirty state is recorded accurately; final research release still pending |

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
Fill-source tables now separate fill populations from quote denominators and
preserve modeled queue trajectories and per-field coverage. A bounded streamed
verifier reconstructs all execution sufficient statistics and frozen horizon
identities. The 53 new source/queue regressions and complete clean reviewer gate
pass at 57f9eba, including independent batch reductions for K=2..5 and native
unchanged-core checks. The benchmark still uses the 80-record HMM-disabled clip;
it is not representative HMM overhead or exchange latency.
Time-weighted inventory/risk-reservation intervals now have a bounded sidecar,
explicit stale/mark coverage, strict checkpoint-to-core anchors, and a paired
regime-prefix verifier. Independent batch interval arithmetic covers K=2..5,
large nanosecond clocks and same-time zero-duration transitions. The 58 new
risk regressions and complete clean reviewer gate pass at `3b2fea1` (430.71 s),
including native unchanged-core, strict checkpoint, false rehashed regime-link
and incomplete-writer checks. The idle-interval fixture uses an explicit
delayed arrival rather than assuming unchanged resting quotes create actions.
Generated local evidence is `outputs/hmm_gate_risk_20261005_verified.json`;
the report records the tested commit, clean tree and pinned runtime. The CLI
report also verifies and prints the risk stream from a completed fixture bundle.
Single-symbol economic measurement now reconstructs exact cash/fee/turnover
and fresh-mark equity from three serialized parents, reconciles inventory at
every boundary, preserves observed peaks across gaps, and distinguishes
non-additive state drawdown maxima from mechanical extensions. Risk version 2
binds each boundary to its global fill prefix. It is a bounded export/report
consumer, not a change to matching, policy or core accounting. Independent
cash-flow, reversal, gap and altered-parent regressions are in
`tests/test_hmm_economics.py`. The 62 new regressions and complete clean
15-step reviewer gate pass at `b0becc8` (779.82 s), including 1192 Python and
16 Rust tests. Evidence is `outputs/hmm_gate_economics_20261005_verified.json`;
its source/environment/command identities are explicit. The installed-wheel
demo passes, and the CLI verifies and prints the fixture economics. The gate's
80-record HMM-disabled, single-repetition benchmark is not representative HMM
overhead or a comparative performance result. Registered paired research and
representative overhead remain.
The multi-symbol regression found and reproduced a core schema-v3 timer bug:
another symbol's overdue decision could be inserted after a later market row.
Global observation-clock scheduling now advances all active integer timers.
HMM-disabled multi-symbol/quiet-symbol and checkpoint tests prove the repair;
legacy timer behavior and golden hashes remain unchanged.
New observer checkpoint version 2 rejects older HMM continuation state rather
than inventing missing moments/episodes. Execution checkpoint version 3 rejects
older HMM state rather than fabricating missing quote denominators or
source/queue diagnostics; disabled checkpoints are unchanged.

Raw synthetic recovery adds 47 regressions; the focused recovery/dataset/fit/
golden suite passes 106 tests. The full 15-step gate passes at `60b0696` in
708.83 s, including 1239 Python tests (524.98 s), 16 Rust tests, gate typing across
61 files, formatting across 172 files, and the existing parity/artifact/fault/
installed-wheel/fixture-benchmark checks. Full-package typing passes 75 files.
The generated report is `outputs/hmm_gate_synthetic_recovery_20261005_verified.json`.
It records a documentation-only dirty tree because the reference was written
during verification; package source bytes remained unchanged. This is not
represented as a clean-tree run.

The [recorded recovery reference](strategy_results/hmm_synthetic_recovery_reference.md)
retains the chosen K=5, all forty attempts, lower native ARI despite perfect
many-to-one raw classification, active detection failures and censored lags.
Two complete default runs reproduce seven artifacts byte-for-byte. The 19.14 s
single diagnostic and the gate's 80-record HMM-disabled benchmark are not the
required representative baseline/observe/policy overhead comparison. Registered
paired policy/feature/cadence evaluation, clock-period bootstrap sensitivities,
the real diagnostic study and the final research release remain required.

Public L2 cannot identify private FIFO, hidden liquidity or actual private fills.
An HMM posterior is not an edge estimate, Kelly input or causal discovery of
real exchange participants. Offline inference/replay throughput is not trading
latency. Negative policy results are valid research outcomes. Safe model JSON
is not executable pickle; checksums identify content, not a trusted author.
