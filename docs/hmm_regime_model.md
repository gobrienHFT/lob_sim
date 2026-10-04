# Causal microstructure regime model

## Implementation status

The feature/filter/artifact foundation, authoritative replay extraction, UTC-day
partition reader, deterministic offline fitter, model inspector, observation-only
simulation and quote-lifetime execution audits are implemented under
`lob_sim/regime`. `simulate --hmm observe` uses a frozen model. No HMM-aware
quote policy, strategy benefit, real-data regime result or holdout finding is
published. The [implementation ledger](hmm_implementation_plan.md) tracks the
remaining policy, evaluation and overhead work. Existing simulation
profiles and their descriptive spread/imbalance `regime` field are unchanged.

## Ownership and data path

The data path is authoritative replay/book state → fixed-grid features →
frozen training scaler → forward filter → diagnostics / optional policy.
`BookView.from_book` copies the reconstructed integer tick/lot levels; the
feature engine does not parse Binance payloads, synchronize its own exchange
book, change orders, invent fills or alter hard risk limits. Model fitting remains
separate from runtime inference. The optional `hmm` extra supplies hmmlearn and
the scientific fitting stack; extraction and the frozen runtime filter use only
the base package. Reviewer/development requirements include the extra so the
fitting tests are actually exercised in CI.

## Sampling and sequence boundaries

The default interval is 1,000 ms, the trailing window is ten intervals, visible
depth is the first five available levels on each side, and book staleness is
five seconds. These integers are part of the feature/model identity.

The sampling grid is anchored at integer-clock zero, not at the first trade or
strategy decision. A transition represents one interval, not one feed message.
An observation at grid time `t` belongs to `(t - interval, t]`; equal-time
receipts are consumed in receive-sequence order. `advance_before(t)` closes
grid points strictly before an incoming event using only the previously
observed state. Consume this iterator before calling `observe(t, ...)`.
`finish(last_observation_ns)` can close the final point after all same-time
receipts have been observed. It cannot invent an unobserved clock tail, and
later receipts cannot reopen a closed point. Runtime integration must preserve
the distinction between sample time and when the closed sample is available
to a strategy; it must not retroactively apply the new posterior to old actions.

Warmup requires a complete trailing window within the current valid epoch.
Initial state establishes the return/imbalance reference and is not counted
as a message inside a later right-closed window. No look-ahead interpolation
is used. Between boundaries, the strategy integration will use the latest
available valid posterior, never a future sample.

## Feature vector

All book prices below are integer ticks: ratios cancel the instrument's tick
scale. Quantities are integer instrument lots; depth and flow scaling are
symbol-specific, so one fitted scaler must not be treated as a universal
cross-symbol normalization. `m` is midpoint, `b/a` are best bid/ask, `q_b/q_a`
their sizes, and `D` is total available top-N lots. `W` is the configured
trailing window in seconds. Returns and imbalance changes use the previous
sampling point, not the previous feed event.

| Feature | Formula and units | Window |
| --- | --- | --- |
| `spread_bps` | `10000 * (a-b) / m`, bps | Current book |
| `log_mid_return_bps` | `10000 * log(m / previous_sample_mid)`, bps | One interval |
| `realized_volatility_bps` | Root mean square of sampled log returns, bps | Trailing W |
| `imbalance_l1` | `(q_b-q_a)/(q_b+q_a)` | Current book |
| `imbalance_depth` | `(sum_bid_N-sum_ask_N)/D` | Current book |
| `signed_trade_imbalance` | Signed aggressive lots / total aggressive lots | Trailing W |
| `log_visible_depth_lots` | `log1p(D)` | Current book |
| `visible_depth_change_fraction` | Observed net visible level-change lots / mean sampled D | Trailing W |
| `log_market_event_rate` | `log1p((depth records + trade records)/W)` | Trailing W |
| `log_trade_rate` | `log1p(trade records/W)` | Trailing W |
| `microprice_displacement_bps` | `10000 * ((a*q_b+b*q_a)/(q_b+q_a)-m)/m` | Current book |
| `imbalance_change` | Current L1 imbalance minus previous sampled L1 imbalance | One interval |

`buyer_is_maker=True` identifies an aggressive sell; false identifies an
aggressive buy. A valid window with no trades has trade imbalance zero **and**
trade intensity zero. A broken trade route is unavailable, not zero activity.

Visible level change includes validated changes at prices in the union of the
previous and current top-N views. It measures observed net additions/removals,
including executions and cancellations that cannot be separated by public L2.
Snapshot/epoch resets are not counted as flow. It is not a cancellation rate,
an estimate of private order arrivals or a fully identified order-flow process.

These transforms run before scaling. The vector includes no raw BTC price,
inventory, strategy decision, fill outcome, PnL, future markout or future label.
The covariance model is a research approximation, not evidence that Gaussian
conditional independence is true for market data.

## Validity, missingness and resets

Book, trade, clock and capture validity are independently supplied by replay.
Every one is required by this initial full feature vector. Unavailable samples
carry null values and a reason: warming up, invalid book/trade/clock/capture,
stale book, or non-finite feature. They do not carry an old confident posterior.
The estimator clears both its filter and hysteresis on invalid input and
restarts from the frozen start probabilities after recovery. A book/public/
market epoch change also restarts inference. An unexpected missing grid point
is recorded as `sample_interval_gap`, not treated as one ordinary transition.

The feature window is a bounded deque of interval aggregates, not a list of
every feed message. No mean imputation or forwarding of a posterior through an
invalid interval is permitted. Invalid numeric observations cannot silently
mutate the forward filter. An unrepresentable emission stops inference and
clears state rather than preserving a misleading previous regime.

## Train-only preprocessing

`TrainOnlyScaler.fit_training` accepts only the training rows selected by the
dataset/partition layer. It optionally clips each column at training
quantiles 0.5% and 99.5%, then computes its population mean and standard
deviation from the clipped training values. Frozen bounds are also applied at
runtime. An exactly constant training feature has scale one and an explicit
constant flag; its live behavior is still visible when clipping is disabled.
Validation/test transforms cannot update training statistics. Reordered
features, changed formula identities, missing dimensions and non-finite
values are rejected. Whole-day chronological separation is enforced by the
partition reader and fitter, not merely by the scaler API's method name.

## Filtering is not smoothing

For frozen start probabilities `pi`, transition matrix `A` and Gaussian
emission density `f_j`, runtime computes:

```text
first sample: prior[j] = pi[j]
later sample: prior[j] = sum_i posterior_previous[i] * A[i,j]
posterior[j] = prior[j] * f_j(x_t) / sum_k prior[k] * f_k(x_t)
next_prior[j] = sum_i posterior[i] * A[i,j]
```

This is `P(S_t | X_1,...,X_t)`. It is not `P(S_t | X_1,...,X_T)` for `T>t`,
Viterbi decoding or retrospectively choosing the most likely state path.
The filter uses diagonal Gaussian log densities and log-sum-exp. Normalized
log probabilities stay internal even when an exported probability underflows
to zero. Start/transition rows must normalize; covariance diagonals must be
strictly positive and finite. Numeric errors fail explicitly.

The independent test oracle enumerates all latent paths for short sequences
in probability space. A separate fixture shows that future observations change
the *smoothed* earlier posterior while the runtime earlier posterior stays
identical. Future-mutation tests also compare raw MAP, hysteretic state,
confirmation streaks and switch records. End-to-end raw-tape/strategy causality
proof remains required at integration.

## State IDs, duration and uncertainty

State numbers have no inherent economic meaning. No current artifact labels
an index as “calm”, “toxic” or “profitable”. Training-only state characterization
and canonical ordering are computed during fitting. Model parameters can be
permuted with both transition axes, start probabilities and emissions changing
together; likelihood must stay unchanged.

For state `i`, expected geometric dwell time is `1 / (1-A[i,i])` samples, or
that value times the interval in seconds. An absorbing state is unbounded and
is exported as null duration, not a fabricated finite number.

Each signal retains the full filtered posterior, next prior, raw MAP,
maximum probability, Shannon entropy and normalized entropy. Default hysteresis
requires probability at least 0.70, entropy at most 0.95, three consecutive
confirmations and at least three samples of active-state age before switching.
Uncertain input breaks confirmation, retains an already active descriptive
state and sets `confident=False`. Invalid data clears the active state entirely.
The raw posterior is never replaced with a one-hot active state. Policy risk
controls are pending; a state posterior is not a probability of profit and must
not be converted into Kelly sizing.

## Model artifact and checkpoint contracts

`FrozenRegimeModel` is immutable safe JSON: feature definitions/cadence,
preprocessing parameters, start/transition/emission parameters and immutable
provenance text are covered by a canonical SHA-256. The loader checks schema,
unknown/missing fields, checksum, dimensions, feature order/formulas,
probabilities, finite values and positive variances. It rejects duplicate JSON
keys and non-finite constants and limits reads to 8 MiB. No pickle is loaded.
Fitted provenance includes source identity, dataset/partition hashes, whole UTC
days, independent sequence lengths, row identities, restart seeds, fit settings,
complete attempt/candidate ledgers, dependency versions, training-only state
characterization and canonical label mapping. Hand-specified synthetic unit
models remain supported but do not become fitted evidence. A checksum is not
author authentication.

Saving uses an exclusive `.partial`, flush/fsync, and no-clobber hard-link
finalization. Existing final or partial evidence is preserved. A failed
finalization leaves a visibly incomplete partial; unsupported filesystem
semantics must fail rather than overwrite. Numeric filter and hysteresis
checkpoints validate their parameter/configuration identity and state before
restoring. Complete simulator/sampler checkpoints are a remaining milestone.

## Validated extraction and partition isolation

`regime-features` runs the same record validator, normalizer, synchronizer,
integer receipt clock and independent validity state as `SimulationEngine`.
The observer receives immutable bounded level copies, validated changes and
normalized trades. It never gets mutable books, order actions or latency RNGs,
and does not emit into the simulator's event-ID sequence. Paired regression
tests compare complete action traces, metrics, counters and checkpoint state.

Before-record samples close *after* earlier actions have drained and before the
new market state is applied. A grid point at t includes every receive-sequence
tie at t; it becomes available on the next strictly later receipt, or at EOF.
Rows distinguish `sample_ns` from `available_at_ns`. UTC time is projected from
the last already-observed wall/monotonic anchor, never from a future receipt.
Legacy float-clock rows are labeled `legacy_diagnostic`. Nanosecond integer
division prevents the last nanosecond of a UTC day rounding into the next day.

The exporter retains one row buffer, one open daily stream, per-symbol feature
windows, and per-day path/count metadata—not tape-sized traces. Each UTC day is
physically separate. Files are fsynced and finalized without clobbering; a final
checksummed manifest is published only after successful replay and an unchanged
source-file hash. Failed extractions remain visibly incomplete and cannot be
loaded as a dataset. Checksums identify bytes, not a trusted author.

`dataset_split` reuses the existing chronological 60/20/20 UTC-day protocol.
Calibration/validation readers do not even open other partitions' row files.
Each selected file is checked before opening and hashed again over the exact
bytes consumed, so a change between integrity checking and parsing fails closed.
Test access requires an already frozen `ResearchRegistry`; the guard is a
research workflow contract, not protection against manually opening files.
Lengths reset across symbols, input captures, instrument changes, epochs,
invalid/stale intervals, sampling gaps and UTC-day boundaries. Offline fitting
accepts one instrument/grid and defaults to a one-million-row cap per partition.
Training may hold finite matrices in RAM; it is not the bounded runtime path.

Days containing valid rows are not complete joint-valid days. Extraction and
fitting are explicitly diagnostic-only until independent coverage evidence is
available, even if the folder contains ten different UTC filenames.

Observer checkpoint/resume is explicitly rejected before replay or checkpoint
publication until feature/estimator state is integrated into checkpoints. The
ordinary HMM-disabled checkpoint path remains available.

## Model selection

Defaults are K=2,3,4,5, diagonal covariance and ten SHA-256-derived restart seeds
per K, base seed 7, 300 EM iterations and tolerance 0.0001. BLAS thread pools are
limited to one thread. Numerical reproducibility is tested within an identical
software/CPU environment; it is not a promise of bit identity across libraries.

Only calibration rows estimate the clipping/scaling and emission/transition
parameters. Independent sequence lengths are supplied to fitting and scoring.
Convergence requires two finite likelihood observations and a final gain in
[-tolerance, tolerance), not merely the library's iteration-limit flag. Failed
restarts remain in the ledger with seeds, reasons and available diagnostics.
Default gates require 20 training rows per state, all positive finite numeric
parameters, variance >= 1e-8, effective occupancy >= one observation per state,
and standardized RMS mean separation >= 0.05 over nonconstant features. These
are explicit numerical gates, not proof that rare states are economically real.

For each K, the best valid restart is selected by **training** likelihood;
restart order resolves exact ties. Candidates are then scored on validation
likelihood per observation. Within 0.01 of the best value, choose the lowest
training BIC, then smaller K, then restart index. Parameter-count BIC includes
`(K-1) + K*(K-1) + 2*K*D` free parameters. The fitter accepts calibration and
validation only and rejects test, nonchronological and incompatible inputs
before loading the fitting dependency. Test cannot choose K or preprocessing.

Training-only smoothed responsibilities characterize states retrospectively.
Canonical labels sort by a relative risk signature: equal percentile-rank
weights for wider spread, higher realized volatility, thinner visible depth,
absolute L1 imbalance and absolute aggressive-trade imbalance. Ties use the
emission means/variances, then raw index; both transition axes and start/emission
parameters are permuted together. Reports expose occupancy, raw-feature means,
relative risk, raw-to-canonical correspondence, transition matrix and geometric
duration. Labels remain `STATE_0...`; no economic names or profit probabilities
are invented. Signed imbalance describes direction, not an edge estimate.

Numeric switching tests recover separated synthetic Gaussian emissions and
compare prefix-end posteriors and sequence likelihoods with hmmlearn. They are
not a synthetic exchange calibration or evidence that Binance has those states.

The existing frozen variant registry will govern the economic study. At least
ten joint-valid UTC days are needed for holdout claims;
otherwise results are diagnostic. The economic study will pair identical tapes,
seeds, fill scenarios, latencies and fees across baseline, observation-only and
opt-in policy runs, with moving-block bootstrap sensitivity. No such study has
been completed by the numerical foundation tests.

## Commands available now

First install optional fitting dependencies:

```bash
python -m pip install ".[hmm]"
```

The following paths are examples for a user-supplied finalized multi-day capture,
not bundled research data. At least three distinct days with valid rows are
needed for three nonempty partitions; short/tiny tapes cannot support a fit.
Use fresh output names: no final/partial evidence is overwritten.

```bash
python -m lob_sim.cli --env .env.example regime-features --file data/multiday.capture.manifest.json --out outputs/regime_features --symbol BTCUSDT
python -m lob_sim.cli regime-fit --dataset outputs/regime_features --symbol BTCUSDT --model outputs/regime_model.json --report outputs/regime_fit_report.json
python -m lob_sim.cli regime-inspect --model outputs/regime_model.json
```

If all candidates fail, the attempt report is still saved and no model is
published. `regime-fit --help` exposes K, restarts, seed, iteration and row caps;
`regime-inspect --json` exposes complete provenance. Run observation-only
inference using the same strategy/execution configuration as the baseline:

```bash
python -m lob_sim.cli --env .env.example simulate --file data/multiday.capture.manifest.json --strategy research_mm --hmm observe --hmm-model outputs/regime_model.json
```

The training symbol is checked automatically. A hand-specified diagnostic model
without training-symbol provenance requires `--hmm-symbol BTCUSDT`. A fitted
model's instrument grid must match the authoritative replay metadata. The JSON
model is loaded once into immutable parameters; file paths are not model identity.
`simulate --help` lists the available flags. Ordinary runs use bounded streaming
export; HMM mode rejects the fixture-only `--in-memory-export` path. Policy and
paired-comparison commands remain pending and are not advertised as working.

## Observation-only timing, audits and recovery

The observer uses exactly the feature-extraction sampler, not a second book.
Earlier scheduled actions drain first. Samples strictly before the incoming
receipt close from the previously known book; the incoming market record then
updates the authoritative state and observer. All equal-time receive sequences
enter one right-closed feature bin. A sample at t is available at the next
strictly later receipt (or at EOF), not retrospectively to actions at t. The
trace distinguishes `sample_ns` from `available_at_ns`. Earlier actions are
never informed by a sample closed using a later receipt watermark.

Observation mode changes no quote targets, queue consumption, execution filters,
latency draws, fee assumptions, risk checks, fills or marks. Decision rows gain
`hmm` diagnostics and explicit unit policy multipliers; `observation_only` is
the reason. Extra diagnostic rows use a separate sink and cannot consume order
or core event IDs. Enabled run configuration/state identities intentionally
include the HMM; disabled configuration, summary and event schemas are unchanged.

Disconnects, epoch changes and invalid clock/capture/book/trade state clear the
filter, hysteresis and current confidence immediately, including between sample
grid points. Recovery requires a complete causal feature warmup. A stale query
returns null posterior/confidence, never the last apparently confident state.
This does not change the base strategy's trade-stream execution requirements.

Each enabled bounded run adds `hmm_model.json`, `regime_trace.csv` and
`regime_execution.csv` to its
existing manifest. The trace includes raw/scaled features, posterior, next-state
prior, generic state label, confidence/entropy, hysteresis, validity/epochs and
reset reasons. Sample counts, sample-state transitions and entropy aggregates
are model diagnostics, not economic attribution. The trace is streamed back
through its canonical hash chain before the completion sentinel is removed.
Writer/verification failures leave the bundle visibly incomplete.

## Quote-lifetime attribution and descriptive execution statistics

`regime_execution.csv` separates three immutable information sets:

| Field | Information available at |
| --- | --- |
| `decision` | The strategy decision that actually sent this quote, not subsequent decisions that retained it |
| `arrival` | Modeled venue acceptance, after arrival-time risk/post-only checks |
| `pre_fill` | The fill-generating observation, before that observation enters feature sampling |

Each includes causal logical/sample/availability timestamps, receive sequence,
epochs, model identity, validity, posterior, raw and active state, confidence
and normalized entropy. An invalid/stale signal has null inference. Missing
creation/arrival attribution is explicitly null; it is never reconstructed from
the later fill label. `STATE_n` remains a generic training-canonical label, not
an asserted economic state. A valid but not yet confirmed active state is
`UNCONFIRMED`; missing or invalid inference is `UNAVAILABLE`.

Pre-fill attribution is captured before the current public trade/depth event
updates the feature sampler, even when legacy action-first scheduling delays
the accounting callback. Pending cancels retain their original context until
acknowledgement or fill. Terminal fills/cancels, rejected/non-resting arrivals
and invalidated epochs release order contexts. A deterministic `fill_id` is the
existing global fill ordinal, so same-time, same-quantity partial fills cannot
collide. Core order/fill dataclasses, matching rules and audit CSV schemas are
unchanged. Paired `trades.csv` and `markouts.csv` remain byte-identical.

The existing accounting/markout machinery—not a second execution model—emits
attribution for every configured resolved or invalidated horizon. Each markout
retains the at-fill snapshots rather than re-querying the estimator at its
future observation. Signed markout is the core per-quantity, contract-scaled
side-signed price difference. Its actual observation lag and invalid reason
remain visible. Legacy markout resolution uses the existing seconds-based
metric clock; HMM timestamp precision is not a new exchange-latency claim.

The `hmm_execution` summary groups fill quantity/lots, fees, marked spread
capture, quote age and pending-cancel fills by each of the three information
sets. Per-state/horizon tables show resolved, invalidated and unresolved
samples, resolved/fill coverage, quantity-weighted signed markout, adverse
fraction and mean/max actual lag. Zero observed samples produce null means,
not an invented zero outcome. Tail-unresolved horizons stay in the denominator;
they are not silently discarded. The decision-to-pre-fill matrix counts fills,
including partial fills, not distinct orders or time-weighted transitions.
Spread capture and fees here are descriptive components, not a state-level PnL
allocation, funding decomposition or economic-benefit claim.

Book/capture gaps invalidate pending marks through the core validity state.
A trade-only outage prevents future trade-dependent execution and invalidates
the HMM, but does not erase valid book-only marks of already completed fills.
Adversarial attribution tests found a prior replay bug: control-record
invalidations could leave markout trace rows buffered until EOF, where their
earlier timestamps violated causal order. All record boundaries now flush those
rows before advancing or checkpointing. This repair applies with HMM disabled
as well; it does not change fills, cash or horizon-resolution semantics.

Both audit streams are independently re-read through their canonical hash
chains before the run manifest is finalized. Aggregates have fixed cardinality
in K, three phases and configured horizons. Live quote contexts are capped at
the existing profile's two/four slots; pending snapshot memory is bounded by
the existing markout cap. There is no tape-length posterior/fill history.
Execution checkpoints validate model/configuration, posterior/entropy, bounded
groups and counters, then cross-check actual live orders, outbound decisions,
scheduled fills and pending horizon counts before changing the engine. Resume
requires a null execution sink, as for the other audits.

These tables answer descriptive quote-lifetime questions under the selected
public-L2 fill/latency assumptions. They do not demonstrate predictive power,
private Binance FIFO, true fills or profitable regime adaptation. Policy,
registered paired evaluation and representative overhead measurement remain
required next milestones.

Feature windows, the current book anchor, forward log probabilities, hysteresis,
last available signal, counts and hash chain enter strict model/config-bound JSON
checkpoints. No fitting library or historical posterior recomputation is needed.
Restore validates a candidate before replacing observer state. Engine loading
also checks input, configuration, code and adapter identities. Control and
ignored invalid records now honor checkpoint and stop boundaries; the previous
early-return omission is an explicit recovery repair, not a trading semantic
change. Resume requires null sinks and does not append a partial audit. The
resumed state and whole-stream hash match uninterrupted execution; this is not
a claim that a resumed suffix file contains the missing prefix rows.

Runtime configuration may also use the coherent `HMM_MODE=off|observe`,
`HMM_MODEL_PATH`, `HMM_SYMBOL`, `HMM_ENTER_PROBABILITY`, `HMM_CONFIRM_SAMPLES`,
`HMM_MIN_STATE_AGE_SAMPLES` and `HMM_MAX_NORMALIZED_ENTROPY` namespace. Defaults
are disabled; sampling/formulas come from the frozen model and cannot be
silently overridden at runtime. Numerical fitting dependencies are optional;
the feature/filter/hysteresis path itself uses the standard library.

## Reproduce checks

```bash
python -m pip install -r requirements.txt
python -m pytest -q -k hmm
python -m mypy lob_sim/regime
python scripts/reviewer_gate.py
```

The HMM-disabled golden fixture freezes preimplementation summary and event
trace hashes. Adding source necessarily changes repository/code provenance;
that is not a behavioral change and is not disguised as identical source.
Schema-v3 requote timers now use integer nanoseconds. The new long-enough receipt
fixture exposed a pre-existing float-accumulation/epsilon bug that could schedule
a just-before-t action after the t market record and raise a causal trace error.
That boundary is deliberately repaired; legacy golden behavior remains intact.

Fitting reference: [hmmlearn API](https://hmmlearn.readthedocs.io/en/stable/api.html).

## Limitations and portfolio claim

Public Binance L2 cannot identify private participant FIFO, hidden liquidity,
our actual fills, cancellations ahead of us or counterfactual market impact.
Regimes do not remove execution uncertainty, establish alpha or identify real
participant strategies. Synthetic unit-model states establish mathematical
test conditions, not exchange ground truth. The exact synthetic matching venue
and inferred historical-L2 scenarios remain separate. No employer endorsement,
production trading readiness, private latency measurement or profitability is
claimed. Fitting and inference overhead must be measured before any runtime
performance claim; offline replay is not trading latency.
