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
| Q | Frozen variant registry before untouched test | Independent-source runner freezes baseline/observe/policy, hard-active and cadence variants; successful native-clock integration and failed real-data eligibility are recorded separately |
| R | Paired moving-block bootstrap study | Matched UTC-minute risk, execution and causal gross/net equity-delta statistics; ratio-of-sums quality/coverage, fee/turnover activity and explicit 30/5/60-minute unavailability; mean eligible-minute PnL is not total-path PnL; full-path drawdown intervals and representative held-out evidence remain |
| S | Human-readable model/state diagnostics | regime-inspect includes every valid K's likelihood, convergence, occupancy and duration diagnostics; regime-report verifies serialized causal raw/active state summaries before printing |
| T | Filtering versus smoothing documentation | Documented in hmm_regime_model.md |
| U | Public-L2 limitations documented | Documented in hmm_regime_model.md |
| V | Bounded runtime windows and audit output | Streamed audits; fixed windows, one signal, two current diagnostic episodes, fixed K-by-feature moments, capped live-order contexts and core pending horizons; independent generated census/virtual stream regressions (not soak evidence) |
| W | Existing tests pass after integration | Full 1320-test Python suite passes on clean source commit ae676fa; the additional overhead command has 12 separately passing tests |
| X | New tests pass | 81 new collection, clock-statistics, policy, study and cache cases enter the full gate; 12 overhead regressions also pass; empirical release conditions remain |
| Y | Standard lint and typing pass | Gate Mypy (65 files), Ruff and format (181 files) pass at ae676fa; package plus overhead-script Mypy passes 80 files and current Ruff/format passes 183 files |
| Z | Complete reviewer gate passes | All 15 steps pass on clean ae676fa in 1554.78 s, including 1320 Python/16 Rust tests, primitive differential, artifacts, faults, installed wheel and fixture benchmark; final empirical release still pending |

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

Registered independent-source research adds 81 test cases. The complete clean
15-step gate passes at `ae676fa` in 1554.78 s, including 1320 Python tests
(1368.42 s), 16 Rust tests, 65 gate typing targets and 181 formatted files.
Evidence is `outputs/hmm_gate_registered_study_20261005_clean.json`. An earlier
attempt passed Python/lint but stopped because this shell could not find
`cargo-fmt`; its failed report is preserved. Adding the already-installed pinned
toolchain to this process's PATH allowed the complete rerun, without skipping
checks. Timings are local execution records, not controlled performance results.

The [real-data eligibility audit](strategy_results/hmm_regime_reference.md)
records two admissible dates, no fitted real model and no evaluated real policy
result. Invalid-clock legacy rows and zero-quantity prints are not silently
promoted into usable training data. A fresh public capture supplies receipt and
bounded-writer diagnostics, not a soak or ten-day coverage certificate.

The overhead command has 12 independent regressions, including exact observer
core-summary/book/fill/markout/latency parity, actual valid inference, separate
tracing phases, source races, no-clobber and scalar quantile/ratio checks.
Full-package plus script typing passes 80 source files; Ruff/format passes 183
files. This separate verification is not misrepresented as part of the earlier
1320-test gate. Representative measurements and the empirical research release
remain outstanding.

The later [synthetic overhead smoke run](benchmark_results/hmm_overhead_smoke_reference.md)
at clean `4cde350` records all three modes, matching baseline/observation core
identities and actual valid inference. It has only one warmup/measured/memory
run per mode on an uncontrolled host. Policy sent no quotes because fractional
reductions of the one-lot reference floor to zero; the smaller workload must
not be sold as faster quoting or policy benefit. The console now prints
quote/cancel/fill counts and warns about zero policy activity. The historical
report/source hashes remain unchanged; representative overhead is still open.

The null-sink follow-up removes only the discarded defensive copy in four HMM
audit emitters. Twelve new cases prove exactly one fewer copy per emitted row,
unchanged canonical hashes/checkpoints, custom-sink mutation isolation and
write-failure propagation. The focused observation/risk/quote/execution/policy/
benchmark/null suite passes 310 tests (170.27 s); the separate golden/benchmark/
null suite passes 27 tests. Full-package plus benchmark typing passes 80 files,
and Ruff/format passes 184 files. No representative speedup is claimed from
those mechanical and state-equivalence tests. The original smoke reference
still names its historical source rather than being relabeled as a newer run.

The matched-clock outcome extension adds a separate bounded offline consumer,
not a new fill model. Native execution and global trade streams are verified
and rehashed while read, joined against exact instrument-grid economics, and
reduced into content-addressed UTC-minute sufficient-statistic tables. The
frozen registry now includes every outcome definition before untouched test
access; study schema v2 retains the existing risk comparisons and adds execution
quality, coverage, fee and turnover activity. Delayed markouts retain their
original integer fill-time assignment.

Ratio outcomes resample the same period indices for both variants and use
ratios of component sums. Quiet valid minutes stay in the sequence. Missing
markouts, excluded intervals, short strata and undefined replicates cannot
silently become zero-valued quality or narrower confidence intervals. This
does not complete marked-net-PnL/full-path drawdown intervals, representative
latency/scenario studies, ten-day eligibility or the full empirical release.

Local targeted verification on the frozen package source passes 120
study/statistics/baseline/null-sink cases and 85 execution/accounting cases.
The two new files contribute 49 cases, including a separate batch/RNG oracle,
unequal-quantity estimates, sparse replicate accounting, clock-boundary
stratification, delayed resolution, zero-activity coverage and consumed-stream
mutations. Package-plus-benchmark typing passes 81 files; Ruff/format passes
187 files. Artifact verification and the offline demo also pass. These checks
are not representative market-performance or economic-benefit evidence.

The causal clock-PnL extension freezes its valuation/assignment contract before
test access and independently reconstructs risk/trade/execution parents. Every
consumed risk and execution row is rehashed, including markout-only rows. Exact
immutable accounting points remain provisional until the whole parent replay
reconciles. The separate capped sampler uses strict endpoint left limits for
`[start,end)` minutes, preventing same-time fills or later marks from changing
the preceding minute's PnL. Missing fresh open-inventory marks and invalid-risk
minutes stay null; no excluded return is bridged into an eligible period.
Native fee-minute totals independently reconcile against endpoint fee deltas.

Study schema v3 content-addresses `clock_pnl.json`, including model/grid/source
and audit parents, valuation reasons and endpoint anchors. Its paired
30/5/60-minute bootstrap concerns the mean eligible-minute gross/net equity
change, not whole-run PnL or full-path drawdown. Independent transaction-prefix
and native-export batch tests check valuation separately from the online
accounting reducer. None of this establishes representative economic benefit,
execution truth, a full data release or measured HMM overhead.

Local frozen-source verification passes 116 accounting/outcome/PnL/study cases
plus the historical-report compatibility regression. Full-package and overhead
benchmark typing pass 82 source files; Ruff/format pass 189 files. The 33 new
cases include consumption-time audit mutations, an independent native fill-
prefix equity oracle, exact-clock and stale-mark boundaries, metadata isolation,
offline caps, reproducible long-sample intervals and invalid-period block splits.
These focused checks are not a replacement for the complete reviewer gate or
representative research evidence.

Public L2 cannot identify private FIFO, hidden liquidity or actual private fills.
An HMM posterior is not an edge estimate, Kelly input or causal discovery of
real exchange participants. Offline inference/replay throughput is not trading
latency. Negative policy results are valid research outcomes. Safe model JSON
is not executable pickle; checksums identify content, not a trusted author.

## Active public-capture overhead evidence

The [completed short workload](benchmark_results/hmm_active_public_reference.md)
records 102 fresh-engine replays: three warmups, thirty measured repetitions
and one separate traced-memory replay per mode. Its portable capture/model
bytes are unchanged. Every policy replay has accepted resting quotes, and the
baseline/observer bounded core, book, fill, markout and latency identities match.
The declared shared ten-lot size and 500 ms cadence do not loosen hard exposure
limits or modify execution assumptions. The original inactive smoke and both
incomplete public attempts remain labeled and preserved.

The grid-compatibility repair treats insignificant Decimal spelling exactly,
without context rounding or tolerance; raw dataset/trace/checkpoint hashes and
the frozen model stay intact. Opaque nonminimal legacy hashes are not guessed.
Native preflight rejects missing/mismatched metadata and corrupt tails before
timing. Table-based CRC32C is checked against an independent bit-stream oracle,
byte/Unicode cases and the stored original public-capture checksums.

Clean source `8c2a004` passes all eight hosted CI jobs, including 1490 Python
and 16 Rust tests. Four publication cases independently reconcile the native
report/data hashes, configurations, balanced ordering, accepted activity,
all raw timings and matched ratios. These do not reinterpret the short capture
as valid research days or the synthetic-trained model as Binance calibration.

The observed 87.90% observation-mode overhead is material engineering cost,
not a cheap-inference or HFT-latency claim. Policy's 69.94% overhead accompanies
fewer quotes/fills, and all fill activity appears as immediate-fill arrivals.
No passive-fill quality or economic benefit follows. Broader representative
workloads, profiling, dedicated-host performance and eligible registered
multi-day research remain release work.

## Decision-snapshot optimization contract

Publication source `cfc02b7` passes the complete local reviewer gate: 1494
Python tests, 16 Rust tests and all 15 steps, including the installed-wheel
demo. Hosted final-head verification is recorded separately in the draft PR.

A diagnostic observer replay of the same public workload reproduces the
published bounded core hash. Its profile identifies repeated snapshot JSON
round-trips and audit copying as engineering work, not evidence of inference
or trading latency. The profile ran alongside correctness tests and is not
a release benchmark or a before/after timing comparison.

Before changing this path, `tests/test_hmm_snapshot.py` freezes its behavior
against an independent stdlib JSON oracle. The 29 cases cover availability
and exact stale-time edges, independent nested ownership, faults and epoch
resets, invalid clocks, restoration of an older signal, event-by-event
observation/policy replay and checkpoint continuation. Full state, audit
hashes, latency draws and exact checkpoints must agree. A cache, if added,
may retain only a copy of the current signal, cannot bypass validity checks,
and must remain outside serialized continuation state. No end-to-end speedup
is established by this contract.

The implementation now lazily canonicalizes one current eligible signal and
copies its normalized JSON containers without repeated serialization. Every
query still checks time, availability and staleness. New samples and immediate
fault/epoch invalidations release the template; restore constructs fresh derived
state without changing the checkpoint schema. Independent ownership extends
to arbitrary nested JSON shapes, not just today's flat feature/posterior lists.
Primed failed restore remains atomic. No policy, matching, accounting, risk,
model/data bytes or default configuration changed. The original 29-case contract
passed before this change; additional tests check normalization counts, cache
invalidation, nested ownership and full public-capture replay parity. Published
benchmark artifacts retain source `8c2a004`; no new timing claim is made.

The first local full-gate attempt at `0de91bb` is
[preserved as incomplete](benchmark_results/hmm_snapshot_gate_attempt_0de91bb.json),
not called green. A deterministic controlled-clock reproduction exposes a
pre-existing exact-float assertion in the benchmark test: equivalent midpoint
and linear interpolation formulas differ by one ULP. The test now permits only
that rounding bound while all parent/probe/report hash assertions remain exact.
A fixed-duration regression exercises the same native benchmark protocol;
its artificial durations are not performance evidence. Simulator and benchmark
calculation code are unchanged. The full reviewer gate must pass at the repair
commit before this attempt can be superseded.
