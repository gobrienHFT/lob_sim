# Causal microstructure regime model

## Implementation status

The feature/filter/artifact foundation, authoritative replay extraction, UTC-day
partition reader, deterministic offline fitter, model inspector, observation-only
simulation, quote-lifetime execution audits and conservative quote policy are
implemented under `lob_sim/regime`. `simulate --hmm observe` leaves the strategy
unchanged; `simulate --hmm policy --strategy hmm_regime_mm` explicitly enables
adaptation. No strategy benefit, real-data regime result or holdout finding is
published. The [implementation ledger](hmm_implementation_plan.md) tracks the
remaining registered evaluation and representative overhead work. Known-regime
raw-tape recovery and source/risk/economic diagnostics are implemented. Existing simulation
profiles and their descriptive spread/imbalance `regime` field are unchanged.
The registered comparison runner and independent-source collection reader now
exist; integration tests exercise all five declared variants. Real-data
evidence and representative three-mode overhead are separate release checks,
not consequences of a green synthetic fixture.

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
is used. Between boundaries, the strategy integration uses the latest
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
controls use an explicit posterior-weighted training-relative risk score; a
state posterior is not a probability of profit and must not become Kelly sizing.

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
restoring. Complete simulator/sampler and execution-attribution checkpoints
also validate causal pending state before any core restoration.

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

Observation and policy checkpoints now include the feature sampler, estimator,
hysteresis, execution attribution and bounded state diagnostics. Resume requires
null sinks: it reproduces whole-stream state/hashes, not an appended audit CSV.
The observer checkpoint is version 2; older HMM observer checkpoints are rejected
because they lack the new diagnostic continuation state. The ordinary
HMM-disabled checkpoint contract remains unchanged.

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

Every valid candidate now retains train and validation likelihood per row, the
winning restart's convergence/seed/parameter identity, a transition matrix,
weighted and MAP training occupancy, geometric durations in steps/seconds, and
empirical MAP episode spans with censored sequence edges. These candidate
diagnostics use **retrospective training smoothing in raw candidate labels**;
they are not runtime posteriors, validation outcomes or latent-state recovery.
Independent sequence boundaries never contribute a transition. Failed K values
remain visible. `regime-inspect` includes the complete candidate selection table.

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
opt-in policy runs, with moving-block bootstrap sensitivity. Numerical
foundation tests alone do not complete this study.

## Commands available now

### Registered comparison

```bash
python experiments/run_hmm_regime_study.py --help
python experiments/run_hmm_regime_study.py \
  --input capture_day_a.manifest.json \
  --input capture_day_b.manifest.json \
  --input capture_day_c.manifest.json \
  --out-dir outputs/new_registered_study --requote-ms 500
```

Use independent single-UTC-day tapes. Each source gets a fresh engine and native
receive clock; no raw-message concatenation, invented sequence or transition
between unrelated runs is performed. Collection identity binds every child
manifest, source checksum, configuration and feature specification. Valid rows
on different dates do not certify complete joint-valid days. Fitting reads only
the selected physical calibration/validation files; test access requires the
frozen registry. The study registers its entire universe before extraction.

Five variants share tapes, fees, instrument metadata, fill assumptions, fixed
order/cancel latency and a common quote cadence: baseline, observation-only,
posterior-weighted policy, hard-active-state policy, and 250 ms policy sampling.
The cadence ablation keeps a ten-second feature window and three-second
confirmation/minimum-age settings. Default controls and policy-config-v1 bytes
remain unchanged; hard-active mode has an explicit version-2 configuration.
All K=2..5 restart attempts, failed fits and failed runs remain visible. No
best-PnL cell determines selection. Positions reset to zero per independent
source, which is a diagnostic convention, not continuous multi-day trading.

Observation-sidecar measurements can stand in for baseline measurements only
after exact baseline/observe core-event, fill-file and markout-file parity.
Only namespaced HMM event diagnostics are removed for this comparison; no
economic difference is stripped. Runtime audits stream to disk, rather than
retaining traces in memory. The immutable model's derived content identity is
cached; artifact bytes and checkpoint dataclass fields remain unchanged.

Risk comparisons integrate actual left-continuous integer-nanosecond intervals
into complete UTC-minute buckets, then bootstrap paired period statistics using
the existing moving-block implementation. Blocks are 30 clock minutes, with
5/60-minute sensitivities—not 30 event rows. Resampling is independently
stratified by source/day/validity/contiguous eligible grid at original weights.
Partial capture edges, stale marks, missing observations and epoch crossings
remain excluded. Every short stratum must fit the requested block; otherwise
the interval is null, not silently shortened. The mean within-period inventory
variance is not whole-path variance, and a period mean is not global drawdown.
The bootstrap is a conditional descriptive method, not a proof of stationarity,
independent days, execution-model truth or a multiple-testing-adjusted alpha.

An observed changing wall/monotonic offset or mixed clock basis prevents the
fixed-UTC projection. The run's native risk/economics audit can still be
verified, but its clock comparison is explicitly unavailable. This conservative
restriction must not be sold as a real-data confidence interval. Study schema
v2 also compares native execution-quality sufficient statistics, fees and
turnover on these same jointly eligible minutes. Markout means are ratios of
pooled quantity-weighted sums to resolved quantity; adverse fractions and
observation lag use resolved observation counts. Quote age, pending-cancel
fills, modeled queue evidence and marked spread capture retain their own
denominators. Resolved/invalidated/unresolved counts and coverage accompany
each configured horizon. A zero-activity minute remains a valid clock period;
missing markouts are not filled with zero. Any zero-denominator bootstrap
replicate is counted and leaves a null interval, without discard or redraw.

Each fixed-clock run publishes a content-hashed `clock_outcomes.json` table
with rational components and native audit parents. Later resolutions belong
to the original integer fill-time minute, not their observation-time minute.
The streaming consumer verifies every execution row and global fill while
consuming them, including partial capture edges outside the table. Baseline
still uses observation sidecars only after exact core-event/fill/markout parity.
Fees and turnover are not marked net PnL, and activity per minute is not a
quote-denominated fill probability.

Study schema v3 separately freezes causal marked-PnL rules and publishes
`clock_pnl.json`. An independent accounting replay rehashes every consumed risk
and execution row, including markout-only records, and verifies every global
fill prefix. Its immutable exact boundary states are provisional until the
complete replay and economic parent reconcile. The clock sampler retains one
current state plus explicitly capped endpoint/period tables, not a fill history.

Each `[start,end)` minute uses equity immediately before either boundary:
same-time market observations, actions and fills belong to the new minute.
Net equity is cash plus fresh marked inventory minus cumulative fees. A fresh
strictly-earlier mid must remain valid up to an open-inventory endpoint; a later
mark cannot fill in missing history. Flat equity needs no mid, but an invalid
risk period still has null PnL. Exact gross/fee/net deltas and independently
reduced execution fees must agree. No invalid period's return is bridged into
an eligible minute. The paired 30/5/60-minute bootstrap estimates mean eligible
minute equity changes, not a total-run PnL or a full-path drawdown interval.
Short or broken strata retain explicit interval-unavailability reasons.
Full-path drawdown intervals and representative overhead remain release work.
Offline fitting/comparison uses explicit row/source/period caps. Those caps are
distinct from runtime bounded-memory claims and from a 24-hour soak.

### Single-model workflow

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
export; HMM mode rejects the fixture-only `--in-memory-export` path.

For explicitly opt-in adaptation, use the same frozen fitted model:

```bash
python -m lob_sim.cli --env .env.example simulate --file data/multiday.capture.manifest.json --strategy hmm_regime_mm --hmm policy --hmm-model outputs/regime_model.json
```

The policy requires exactly one configured symbol matching the model. Models
without a calibration-only, internally consistent training risk signature are
rejected; hand-specified observation models do not automatically become policy
models. The default one-lot order can be reduced below one lot and produce no
quote. This is conservative quantization, not a failure or an automatic size
increase. Calibrate base size on the training partition, never on the test PnL.

`--hmm-policy-config` loads a strict versioned JSON `RegimePolicyConfig.as_dict()`;
every field is required and unknown fields, duplicate keys, non-finite numbers
and unsafe bounds fail. Environment equivalents are `HMM_MODE=policy`,
`MM_STRATEGY_PROFILE=hmm_regime_mm`, `HMM_MODEL_PATH`, optional `HMM_SYMBOL` and
optional `HMM_POLICY_PATH`. Model and policy content are frozen once; editing
their files later cannot change the run. The complete config is in its manifest.
The registered paired-study command remains a future milestone.

## Conservative quote policy

`hmm_regime_mm` composes the existing `research_mm` quote generator. It does not
invent a separate fair-value forecast, queue model, fill model or accounting
path. The stateless immutable controller sees only the causal forward signal;
it cannot inspect future data, orders, inventory or PnL.

Training characterizes generic states by the disclosed five equal-weight
percentile ranks. Policy loading checks the calibration role, feature/data/grid
identities, canonical state order, rank discreteness and cross-state consistency,
score arithmetic and occupancy totals. These checks establish consistency,
not author authentication or proof of an economic meaning for those states.

For filtered posterior p and training state risks r:

```text
weighted_risk = sum_s p[s] * r[s]
effective_risk = min(1, weighted_risk + uncertainty_weight * normalized_entropy)
```

Default mappings are explicit and configurable:

| Control | Default mapping from effective risk R | Actual effect |
| --- | --- | --- |
| Width | min(3, 1 + 2R) | Multiply research half-width after its fee/toxicity floor; widen configured outer width too |
| Size | max(0.25, 1 - 0.75R) | Floor the scaled baseline integer lots; zero suppresses the quote |
| Desired inventory | max(0.5, 1 - 0.5R) | Floor a soft position cap no larger than the hard cap; reserve live plus outbound lots |
| Inventory skew | min(2, 1 + R) | Multiply the existing symmetric inventory-skew strength; no directional alpha |
| Refresh | min(4, 1 + 3R) | Maximum quote age is 2000 ms divided by this multiplier; expire at the existing decision cadence |
| Stand aside | R >= 0.95, or highest-risk active state with probability >= 0.90 | Stop sending new quotes and request existing cancels through modeled acknowledgement latency |

The uncertainty weight defaults to 0.25 and must be nonnegative. For a fixed
posterior, increasing this penalty cannot increase size or desired capacity or
narrow width. Controls are monotone in **effective risk**. Arbitrarily changing
the posterior can lower its weighted state risk while increasing entropy;
there is no false claim of monotonicity across every possible posterior pair.
Warm, stale, invalid, unconfirmed or insufficiently confident information stands
aside. A tied risk ordering does not create an invented uniquely extreme state.

Maximum age is checked only on scheduled decisions, not via an extra timer or
an invented response latency. Its calculation uses integer logical acceptance
timestamps, not the legacy float-seconds projection. Base decision cadence and
existing queue/price refresh rules remain in force. A replacement waits for cancel acknowledgement
and new-order transit. Pending cancels stay fillable and reserve capacity; a
cancel request cannot be spent as a completed cancellation. Soft-cap contraction
can cancel existing quotes, but cannot undo in-flight orders or reset inventory.
There is no flattening operation.

Before sending, the policy reserves worst-case same-side inventory plus live
and outbound lots and, when enabled, gross portfolio notional. Unknown marks
or instrument units fail closed. Arrival still checks current hard position,
portfolio, post-only and venue state. Its additional soft cap and size are the
constraints frozen with the **sent** decision; the venue does not magically
learn a later posterior. Later signals affect later strategy decisions and
latency-respecting cancel requests. Invalid epochs and the existing global
kill switch remain authoritative outside the controller.

Decision rows log both risk scores, all multipliers, age limit, soft lots,
stand-aside status, reason and content-addressed policy identity. Checkpoint
loading recomputes sent soft caps and quantities from frozen causal decisions
before restoring core state. Independent scalar/quantization tests, a 1001-risk
monotonicity grid, actual quote/refresh changes, cancel/fill races, hard-risk
guards, future-prefix invariance and checkpoint recovery verify mechanics.
None of these fixture tests establishes economic benefit or optimal parameters.

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

Each enabled bounded run adds `hmm_model.json`, `regime_trace.csv`,
`regime_execution.csv`, `regime_quotes.csv` and `regime_risk.csv` to its
existing manifest. The trace includes raw/scaled features, posterior, next-state
prior, generic state label, confidence/entropy, hysteresis, validity/epochs and
reset reasons. Sample counts, sample-state transitions and entropy aggregates
are model diagnostics, not economic attribution. The trace is streamed back
through its canonical hash chain before the completion sentinel is removed.
Writer/verification failures leave the bundle visibly incomplete.

### Time-weighted inventory and reserved exposure

`regime_risk.csv` freezes the actual information set at market receipts,
internal actions, accounted fills and the final cutoff. Both raw-MAP and
confirmed-active state tables integrate the left-continuous interval
`[boundary_ns, next_boundary_ns)`, starting at the first audited boundary after
the instrument metadata is available. A sample's earlier `sample_ns` never makes
its label available before `available_at_ns`. Multiple labels becoming
available at the same receipt have zero intervening duration. A market-data gap
does not erase inventory or invent a fresh mark.

The sufficient statistics use integer nanoseconds, signed lots, lots squared,
and twice-midpoint ticks. Staleness splits an interval at
`last_book_ns + stale_after_ns + 1`, matching the runtime's strict `>` stale
rule. This split does not need a subsequent market event. Unknown/invalid
regimes are `UNAVAILABLE`; a valid unconfirmed active state is `UNCONFIRMED`.
Inventory remains measured in both. Trade-stream invalidity can make the
regime unavailable while the independent book mark remains valid.

For duration `D`, signed-lot integral `I`, and squared-lot integral `Q`, the
reported population time-weighted variance is `(Q*D - I*I) / D^2`. Exact lot
ratios are stored as rational strings; quantity/notional presentation values
are Decimal strings at explicit 50-digit precision. Empty denominators are
null. Means of marked inventory and reserved notional use **marked duration**,
with its coverage reported; a missing mark is never treated as a zero price.
Maxima describe positive-duration holdings, not zero-time transients.

Reservation here means absolute marked inventory plus the limit-price
notional of all live and outbound pending new orders. Pending cancels remain
live until the existing venue model acknowledges them. This is a descriptive
single-symbol audit of the reservation basis, not a new risk rule, netted
portfolio exposure, drawdown contribution, funding model or economic benefit.
The separate economic audit below adds an explicit measurement contract;
registered policy evaluation remains unfinished.

The audit retains one boundary and fixed `2*(K+2)` aggregate cells. Streaming
verification recomputes the raw integrals and derived denominators. Paired
verification checks each referenced regime-prefix count/hash and valid
information set; internal hash consistency alone is insufficient. Checkpoint
loading cross-checks the current inventory, live/pending orders, mark,
staleness, halt state and regime anchor against decoded core state before
restoring anything. Older HMM checkpoints lacking risk state are rejected;
HMM-disabled checkpoint and artifact contracts remain unchanged.

Schema-v3 measurement stops at the last observation. Legacy action-first
post-tape draining is labeled `legacy_compatibility_nanoseconds`, not certified
market wall-time coverage. These diagnostics are not a held-out HMM study or
a performance measurement. Representative baseline/observe/policy overhead
remains part of the unfinished research release.

```bash
python -m pytest tests/test_hmm_risk.py
python -m lob_sim.cli regime-report --run-dir <completed-hmm-run-directory>
```

### Reconciled cash, fees, marked equity and observed drawdown

Completed bounded HMM runs include `hmm_economics` in `summary.json`.
`regime-report` independently reconstructs it before displaying any result.
This analysis joins `regime_risk.csv`, `trades.csv` and
`regime_execution.csv` through the global fill-prefix count/hash at each risk
boundary. It verifies the entire global trade stream, including other symbols,
but values only the configured HMM symbol. Inventory must reconcile at every
boundary; fills cannot appear before their accounted prefix. Core global PnL
is never silently assigned to one symbol.

For a linear contract, let `q = tick_size * step_size * contract_multiplier`.
Each buy changes signed cash tick-lots by `-price_tick * qty_lots` and each
sell by the opposite amount. Turnover sums the absolute traded tick-lots.
Starting with zero cash/inventory, gross marked PnL is
`cash_tick_lots*q + inventory_lots*mid_twice_tick*q/2`; net subtracts recorded
fees. Paid fees and rebates are shown separately. This independent cash-flow
identity handles partial closes and long/short reversals without reusing the
core average-cost calculation. Exact amounts are rational strings, with
explicit 50-digit decimal presentation in the human-readable report.

Open inventory requires a fresh independently valid book mark. Its marked PnL
is null when that mark is missing; last observed equity is labeled separately.
Flat equity needs no price. A stale interval is detected even without a row
at the expiry. The audit records unpriced held-inventory nanoseconds and never
extrapolates beyond the run cutoff. It is not a funding or multi-currency
portfolio ledger.

Cash/fee/turnover cells use frozen pre-fill raw/active labels. Changes in
observed marked equity use the previously available holding label, not a
later state backfilled into an earlier interval. A return bridging an unpriced
gap is `UNATTRIBUTED`. The two tables answer different descriptive questions;
pre-fill fees must not be subtracted again from already-net endpoint deltas.
No causal state-level PnL or strategy advantage is inferred.

Observed drawdown is the running peak minus available net equity, beginning
at zero and including same-time causal accounting boundaries. Missing prices
do not reset the peak. It is not continuous-market maximum drawdown: unseen
peaks/troughs can be missed. State maxima are tagged at the current detection
label and are **not additive**. New-maximum extensions sum mechanically to the
observed maximum but are bookkeeping, not causal "drawdown contributions".

The reducer retains fixed `2*(K+3)` cells, one boundary and no transaction
history. It runs during bounded export finalization and on report inspection,
not in matching or strategy decisions; its re-verification cost belongs in
full-audit benchmarks. Economic verification failure leaves `_INCOMPLETE.json`
and no completed manifest. Risk stream/checkpoint version 2 adds the fill-prefix
identity; version-1 HMM continuation/report state is rejected rather than
inventing missing provenance. HMM-disabled artifacts and economics are unchanged.

```bash
python -m pytest tests/test_hmm_economics.py tests/test_hmm_risk.py
python -m lob_sim.cli regime-report --run-dir <completed-hmm-run-directory>
```

These are measurement contracts and deterministic fixture proofs, not a
trained-market economic result. The registered paired study, raw synthetic
recovery and representative HMM overhead measurements are still required.

The two-symbol economic regression exposed an existing schema-v3 scheduler
defect: a symbol's periodic decisions were created only when its own next
receipt arrived, potentially inserting a 2.04-second decision after another
symbol's 3-second market trace. Active integer-clock timers now advance before
each global observation, including control records; dispatch and same-time
heap rules are unchanged. Independent HMM-disabled tests cover both-symbol and
quiet-symbol receipt patterns and checkpoint continuation. This is an explicit
core correctness repair, not an HMM policy change. Legacy per-symbol
compatibility scheduling and the preimplementation golden hashes are preserved.

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

All regime audit streams are independently re-read through their canonical hash
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
private Binance FIFO, true fills or profitable regime adaptation. Registered
paired evaluation and representative overhead measurement remain
required next milestones. The opt-in conservative policy is implemented; its
economic usefulness is not established by those mechanical tests.

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

Runtime configuration may also use the coherent `HMM_MODE=off|observe|policy`,
`HMM_MODEL_PATH`, `HMM_SYMBOL`, `HMM_ENTER_PROBABILITY`, `HMM_CONFIRM_SAMPLES`,
`HMM_MIN_STATE_AGE_SAMPLES` and `HMM_MAX_NORMALIZED_ENTROPY` namespace. Defaults
are disabled; sampling/formulas come from the frozen model and cannot be
silently overridden at runtime. Numerical fitting dependencies are optional;
the feature/filter/hysteresis path itself uses the standard library.

## Causal state characterization

The observation summary's `state_diagnostics` contains separate **raw MAP** and
**active hysteretic** tables. A valid posterior without a confirmed active state
belongs to `UNCONFIRMED`, not silently to its MAP state. Tables report sample
counts/fractions, per-feature mean/population variance/range, confidence and
normalized entropy, label transitions including self-transitions, and episode
counts. Zero-sample statistics are null, not zero.

Occupancy means a fraction of the emitted sample grid. Multiplying samples by
the interval gives a quantized observed span; neither is joint-valid wall-clock
coverage or a true latent-state dwell time. Complete episodes require observed
state-change boundaries on both sides. The initial edge, gaps, invalidations,
filter resets and the open final tail are censored. Mean complete duration
excludes censored episodes; mean observed span includes them and is explicitly
descriptive. Querying a summary does not close a live episode or change a resume.

Counters use fixed K-by-feature cardinality with only two current episodes.
They do not retain sample rows, depend on fitting libraries, or feed back into
policy. Checkpoints validate moment counts/bounds, episode spans, transitions,
current labels and causal sample anchors before replacing state. Serialized
regime CSV verification reconstructs these aggregates and checks both the new
statistics and the existing summary counters, not merely a writer's row count.

After a bounded observation or policy simulation, inspect its printed run
directory with:

```bash
python -m lob_sim.cli regime-report --run-dir outputs/<completed-run-directory>
```

This read-only command checks the frozen model identity and sampling clock,
rejects incomplete/partial exports, and reduces the serialized regime trace
before printing market-state diagnostics. It verifies that regime audit, not
raw capture coverage, private fills, economic benefit or an untouched holdout.
Checksums identify content, not a trusted author.

Source-conditioned execution and causal time-weighted inventory now have the
separate audits described here. Sample counts must not be repurposed as
quote counts or fill probabilities; the existing fill-transition table counts
fills/partial fills, not unique orders. No per-state net PnL or drawdown
contribution is invented here.

## Quote cohorts and explicit denominators

`regime_quotes.csv` records outbound requests, accepted/rejected arrivals,
discarded outbound requests, terminal orders and **accounted** fills. A request
is counted only when the engine actually schedules an outbound quote, after its
send-time controls. Strategy targets suppressed before sending are not requests.
Each request receives a diagnostic identity that does not consume a core order
ID or latency draw.

The `hmm_execution.quote_lifecycles` summary uses the original decision and
arrival labels, even if the active regime changes later. Arrival cohorts include
rejections, with explicit unavailable labels where appropriate. Accepted-order
denominators exclude rejected and discarded requests. There is no pre-fill-state
quote denominator: grouping successful fills by their later state would select a
different population from the originally sent quotes.

Tables distinguish:

- scheduled requests, arrived requests, accepted orders and rejected requests;
- outbound requests discarded by an epoch fault or halted before arrival;
- unique filled orders versus partial/full fill events and filled quantity;
- terminal filled/cancelled/invalidated/halted orders and outstanding requests
  and live orders at the tape cutoff;
- unique-filled fractions of accepted orders (both cohorts), unique-filled
  fractions of scheduled requests (decision cohorts only), acceptance fractions
  of arrived requests and filled fractions of accepted lots.

Fractions describe this tape, cutoff and execution scenario. Outstanding quotes
are right-censored, and quote ages differ; these are **not** uncensored lifetime
fill probabilities or live venue estimates. Empty denominators are null. A
pending cancel remains eligible to fill until the existing acknowledgement;
cancelled unfilled quotes remain in their original accepted cohort. Halting an
outbound request is distinguished from a venue rejection. The legacy core
`quote_fill_probability` metric retains its existing arrived-including-rejects
denominator; these new explicitly named fractions do not silently redefine it.

The cutoff follows the existing scheduler contract: schema-v3 stops at the last
market observation and leaves later actions pending; legacy tapes retain their
labeled post-tape compatibility drain. Terminal `filled` states reflect modeled
matching, while cohort fill counters reflect accounting callbacks. A fault that
discards a delayed execution can therefore leave more matched terminal orders
than accounted unique filled orders. Those quantities are not interchangeable.

The first-fill flag is frozen when matching consumes quantity, before a terminal
order context can be removed or delayed legacy accounting can run. The engine
cross-checks it against the matching model's independent `is_first_fill_for_order`
flag. Only the accounting callback adds a cohort fill. An execution discarded by
a later epoch fault cannot appear as an accounted fill merely because it matched.

The collector retains at most the profile's two/four pending requests and
two/four live bindings, plus fixed K+2 cohort and rejection counters. It has no
historical order-ID set. Quote CSV verification reconstructs lifecycle and cutoff
counts with bounded state, checks the hash and sufficient statistics, and pairs
each quote fill with its execution-audit row. Writer or verification failure
preserves `_INCOMPLETE.json` and any partial evidence.

Execution audit/checkpoint schema version 3 carries the frozen request/first-fill
metadata, fill-source identity, queue diagnostics and quote collector state.
Older HMM continuation state is rejected; missing diagnostics are not fabricated.
Disabled checkpoints and base fill, markout and event schemas are unchanged.
Loading cross-checks actual order
quantity, remaining quantity, side/slot, causal attribution and scheduler requests
before replacing state. Resume requires null quote and execution sinks.

`regime-report` verifies the quote and execution streams before adding the cohort
tables to the existing market-state report. This audit verifies internally
consistent scenario records, not actual exchange fills, capture coverage,
authorship, economic benefit or an untouched holdout result.

## Execution source and modeled queue diagnostics

`hmm_execution.conditioned_by_source` splits each frozen decision, arrival and
pre-fill cohort into `depth_update`, `agg_trade`, `taker_order` and `OTHER`.
Unrecognized or unavailable source names stay visible in the trace but cannot
create an ever-growing set of aggregate keys. Source is frozen at the accounting
callback and carried through every pending horizon; it cannot be rewritten by
a later regime observation or markout callback.

Each cell reports fill events and quantity, fees, mean quote age, pending-cancel
fills, marked spread capture, modeled queue-ahead observations and configured
signed markout horizons. Markouts remain quantity-weighted, and their coverage,
invalidations, pending samples and actual observation lag remain explicit.
Fees and spread-capture values use the existing instrument/accounting units;
mean marked spread capture divides the marked value by its covered quantity,
not by all fills. These components are not a net-PnL decomposition.

These are **fill-population tables**, not fill probabilities. A source only
exists after a modeled fill; attaching accepted-quote denominators to that
population would introduce selection bias. Quote-cohort fractions remain in
their separate decision/arrival tables. No pre-fill/source quote denominator is
invented. Empty populations and unavailable marks have null means, not zeros.

The execution CSV now preserves `queue_ahead_lots` and the core's integer
`queue_trajectory`. Passive trajectories describe modeled queue before the
trigger, at the fill, consumed lots and remaining order quantity. Taker
trajectories describe consumed visible depth separately. These values describe
the configured public-L2 scenario, never private participant FIFO or a measured
queue position. Aggregate trajectory totals include per-field observation
counts: an absent field must not be interpreted as a measured zero.

Before publication, the streamed verifier checks the hash, all state/source
sufficient statistics, transitions, frozen fill-to-markout identities, unique
horizon resolutions, deadlines and actual lags. Pending identities are bounded
by the sum of the core's configured primary/additional horizon capacities;
completed fill history is not retained. The exact stored primary deadline is
preserved even when the legacy horizon label rounds to milliseconds. Checkpoint
loading also checks source marginals against state totals, source counts against
core fills, and every unresolved source/horizon against the core's pending list.

Independent generated batch reducers cover K=2..5, source mixtures, negative
fees, partial/missing queue diagnostics, missing marks and censored horizons.
Adversarial tests cover internally rehashed but inconsistent streams, duplicate
markouts, source relabeling and corrupt continuation state. The paired native
baseline retains the same actions, fills and accounting; this is audit evidence,
not proof of economic benefit. `regime-report` prints the verified source tables
after the market-state and quote-cohort diagnostics.

Schema-v2 HMM traces/checkpoints are not silently upgraded to v3. Reproduce an
old audit with its original revision, or rerun the same immutable inputs under
the new revision. Legacy market-tape importers and the HMM-disabled public audit
schemas are unchanged. Time-weighted inventory/reservation evidence is now
implemented. Reconciled cash/fee/equity and observed drawdown diagnostics are
also implemented. Registered paired research remains unfinished.

## Known-regime raw market-tape recovery

The synthetic recovery command tests the complete input path rather than fitting
precomputed Gaussian vectors:

```bash
python experiments/run_hmm_synthetic_recovery.py --out-dir outputs/hmm_synthetic_recovery
```

It generates five eight-minute snippets on distinct UTC days. A two-state
one-second Markov process changes visible spread, depth, price increments and
public-print activity. Hidden labels are stored in separate per-day truth files,
never in market messages or emission vectors. This is a synthetic market-by-price
state-transition fixture, not the separate exact market-by-order exchange, a
calibrated Binance process, or economic evidence. Both routes disconnect at each
snippet boundary; compressed monotonic gaps do not represent valid elapsed days.
The [recorded reference](strategy_results/hmm_synthetic_recovery_reference.md)
reports both the strong mapped classification and the weaker native clustering
and hysteretic detection, rather than describing the run as perfect recovery.

The ordinary engine reconstructs the tape and feeds the existing fixed-grid
feature extractor with strategy quoting disabled. Feature files use physical UTC
partitions. The existing chronological split supplies three calibration snippets,
one validation snippet and one diagnostic test snippet. All generator, feature,
fit, hysteresis, alignment and lag specifications are registered and frozen
before extraction. K=2..5 and every restart—including failed fits—remain in the
fit report. Scaling, EM fitting, state signatures and label alignment never use
test observations. No valid fitted candidate means a visible failure report and
no test prediction access.

After unsupervised model selection, the actual runtime forward filter and
hysteresis run independently on every contiguous sequence. Labels are aligned
using **training forward predictions only**. K=2 uses a deterministic exhaustive
permutation. A selected K>2 uses an explicitly named many-to-one training mapping;
it is not presented as permutation recovery. Unobserved training states and
lexicographic tie rules remain visible. Validation/test labels cannot improve
the frozen mapping.

The recovery report includes:

- confusion matrices, including unavailable active labels;
- raw clustering adjusted Rand index, which exposes extra fitted clusters even
  when many-to-one classification looks strong;
- mapped accuracy with both all-sample and available-sample denominators;
- sequence-separated empirical transitions, transition error, and fitted
  geometric durations; fitted parameters can be compared directly to the truth
  transition matrix only for a two-state permutation;
- complete and edge-censored quantized episode durations;
- every observed truth switch, confirmation availability lag, and switches not
  detected before the next truth switch or sequence cutoff;
- feature validity/warmup counts, prediction identities, immutable parent
  checksums, frozen registry and the complete fitting ledger.

Raw detection requires three consecutive mapped matches. Active detection uses
the first available matching state because the runtime hysteresis has already
confirmed it. Lag is measured at actual availability, never backdated to the
first sample in a confirmation streak. Sequence edges and unavailable neighbors
censor duration statistics; neither gaps nor omitted labels become zero delay.

Trailing-window emissions are correlated and mixed near generating switches;
they are not a conditionally Gaussian realization of the latent Markov process.
The model may split states, respond late or miss short episodes. Perfect recovery
is not required and cannot establish a useful market-making policy. The
[adjusted Rand reference](https://scikit-learn.org/stable/modules/clustering.html#adjusted-rand-index)
describes the pair-count statistic; regression tests compare its exact local
implementation with the independent scikit-learn implementation. Runtime
filtering remains separate from [hmmlearn fitting](https://hmmlearn.readthedocs.io/en/stable/tutorial.html).

Generation and feature export stream to no-clobber, fsynced partial/final files.
Offline recovery has an explicit diagnostic cap (30 snippets, at most one hour
each); it may retain the capped analysis observations. That is distinct from the
bounded runtime filter/windows and does not claim arbitrary-tape constant-memory
fitting. Run the command again only with a new output directory.

## Measuring baseline, observation and policy overhead

```bash
python experiments/benchmark_hmm_overhead.py \
  --file outputs/hmm_synthetic_recovery/tape/market.ndjson \
  --model outputs/hmm_synthetic_recovery/model.json \
  --json-out outputs/hmm_overhead.json --requote-ms 500
```

Generate the preceding recovery bundle first, or supply an immutable compatible
tape/model pair. The command explicitly uses a shared `research_mm` reference
and the model's default conservative policy; the effective configuration is
recorded for all three modes. The example's 500 ms quote cadence is a declared
diagnostic workload, not an optimized setting or the default 40 ms cadence.
Synthetic input remains synthetic: this command does not establish real-market
representativeness, economic benefit or an untouched market holdout.

Defaults are three warmups and thirty measured repetitions per mode, rotating
all six mode orders. Each replay starts a fresh engine with null/aggregate sinks.
Model loading, GC preconditioning and bounded diagnostic reduction are outside
timing; initialization, raw input read/hash/decode, scheduling and EOF draining
are inside. Ordinary runtime GC remains enabled. Separate tracemalloc runs do
not contaminate timing; their peak excludes the already-loaded model and is not
process RSS. Median/p95/p99 are **run-duration** quantiles, not per-event latency.
Every raw sample, matched-round ratio, workload count, parent/source checksum
and runtime identity remains in the no-clobber JSON report.

Observation mode must reproduce the baseline's core summary, final book, fill,
markout and latency identities. Every mode must be deterministic across runs;
HMM modes must actually produce valid inference. A changed artifact/source,
inactive estimator or parity failure aborts rather than publishing an apparently
successful benchmark. Existing event-by-event observer tests remain a separate
proof; the benchmark's bounded summary/hash probe is not a full trace oracle.

The policy can reduce or change quote/fill work, so its ratio is total workload
cost, not isolated inference cost. The host is unpinned and power/frequency/load
are uncontrolled. The command does not assert dedicated-host thresholds,
allocation-free processing, full audit-I/O speed, soak reliability or trading
latency. No representative measurement is claimed merely because this command
and its tests exist.

The [recorded synthetic smoke run](benchmark_results/hmm_overhead_smoke_reference.md)
uses one repetition, not the default release protocol. It preserves observer
core parity but exposes a zero-quote policy workload: reducing the one-lot
reference size floors it to zero. The command now prints quote/cancel/fill
counts and warns when policy sends no quotes. Do not round quantities up or
relax risk controls to make the timing look active. That run also records the
uncontrolled host and concurrent diagnostic load; it is not a representative
performance or economic result.

Schema-v2 reports also include native order-lifecycle counts and whether an
active-policy workload was required. Add `--require-active-policy` to reject
every replay without both a quote request and an accepted resting quote;
rejected-only requests do not qualify. Fills are not required. This is a
necessary workload check, not a measure of market representativeness. It does
not change matching, risk, lot rounding or the policy. Historical schema-v1
reports remain unchanged.

Before timing, a bounded pass through the native validated tape reader checks
instrument metadata against the frozen model; missing or incompatible metadata
fails before constructing a timed engine. Runtime and checkpoint restoration
still check the same instrument contract. Insignificant Decimal zeros can match
a model's minimal grid spelling (`0.10` and `0.1`), without ambient-context
rounding or tolerance. Exact historical dataset/trace/checkpoint hashes and
model bytes remain unchanged. An opaque legacy hash from a nonminimal spelling
cannot itself be canonicalized: it still requires an exact match or a refit.

The [short public-capture workload](benchmark_inputs/hmm_public_btcusdt_20261005/README.md)
ships byte-identical capture segments, the frozen **synthetic-trained** model
and a shared ten-lot/500 ms benchmark configuration. It can exercise accepted
policy quotes without rounding up a suppressed one-lot quote or loosening the
hard exposure cap. Its capture is about 130 seconds; its model is not calibrated
on that capture. Neither is sufficient for a real-market policy-benefit,
holdout, soak or dedicated-host performance claim.

The first public attempt at `e259673` failed on that decimal-spelling mismatch
and produced no benchmark JSON. Its
[failed-attempt record](benchmark_results/hmm_public_overhead_attempt_e259673.json)
is retained; no incomplete measurement is promoted into performance evidence.

A second attempt at `5951541` passed all three warmup rounds and one measured
round but was deliberately stopped for checksum implementation work. Its
[attempt record](benchmark_results/hmm_public_overhead_attempt_5951541.json)
retains that status, not provisional timing numbers. Raw-capture CRC32C now uses
an immutable reflected Castagnoli lookup table rather than eight interpreted
bit steps per byte. An independent bit-stream oracle, byte/Unicode cases and
the original checksummed public tape verify unchanged wire semantics. Neither
an unfinished run nor passing checksum tests establish a measured speedup.

For the exact built-in `NullSink`, observation, risk-boundary, quote-lifecycle
and execution emitters skip the defensive copy that a no-op would discard.
Canonical hash chains, input validation, counters, risk integrals and owned
checkpoint anchors still run. Subclasses and real sinks keep defensive copies
and write-failure propagation. `tests/test_hmm_null_sink.py` compares that path
with copying and deliberately mutating custom sinks, including actual fills,
full engine state, traces and checkpoints. This removes one discarded copy per
emitted audit row; it is not a quantified end-to-end speedup. The historical
smoke measurements above predate this optimization.

## Reproduce checks

```bash
python -m pip install -r requirements.txt
python -m pytest -q -k hmm
python -m pytest -q tests/test_hmm_sources.py
python -m pytest -q tests/test_hmm_risk.py
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
