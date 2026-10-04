# Causal microstructure regime model

## Implementation status

The dependency-free feature/filter/artifact foundation is implemented under
`lob_sim/regime`. It is not yet connected to `simulate`, and no fitted model,
HMM strategy benefit, real-data regime result or holdout finding is published.
The [implementation ledger](hmm_implementation_plan.md) tracks the remaining
dataset, fitting, observer, policy and evaluation work. Existing simulation
profiles and their descriptive spread/imbalance `regime` field are unchanged.

## Ownership and data path

The intended path is authoritative replay/book state → fixed-grid features →
frozen training scaler → forward filter → diagnostics / optional policy.
`BookView.from_book` copies the reconstructed integer tick/lot levels; the
feature engine does not parse Binance payloads, synchronize its own exchange
book, change orders, invent fills or alter hard risk limits. Model fitting will
remain separate from runtime inference. No optional scientific dependency is
needed for this foundation.

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
future dataset/partition layer. It optionally clips each column at training
quantiles 0.5% and 99.5%, then computes its population mean and standard
deviation from the clipped training values. Frozen bounds are also applied at
runtime. An exactly constant training feature has scale one and an explicit
constant flag; its live behavior is still visible when clipping is disabled.
Validation/test transforms cannot update training statistics. Reordered
features, changed formula identities, missing dimensions and non-finite
values are rejected. Full chronological partition enforcement is still part
of the pending fitting milestone, not claimed from the scaler API alone.

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
and canonical ordering will be added during fitting. Model parameters can be
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
Provenance currently supports hand-specified synthetic test models; complete
partition/seed/input/dependency/selection provenance is required when fitting
is implemented. A checksum is not author authentication.

Saving uses an exclusive `.partial`, flush/fsync, and no-clobber hard-link
finalization. Existing final or partial evidence is preserved. A failed
finalization leaves a visibly incomplete partial; unsupported filesystem
semantics must fail rather than overwrite. Numeric filter and hysteresis
checkpoints validate their parameter/configuration identity and state before
restoring. Complete simulator/sampler checkpoints are a remaining milestone.

## Selection and research protocol (not yet implemented)

The fitter will use K=2,3,4,5, diagonal covariance and ten deterministic restarts
per K. The best restart for each K is selected by training likelihood, with
candidate selection based on validation likelihood and registered simplicity/
BIC tie rules. Test data cannot choose K, preprocessing, features, policy or
cadence. Parameter-count BIC includes `(K-1) + K*(K-1) + 2*K*D` free parameters.
Convergence, invalid/collapsed fits and occupancy must remain in the candidate
ledger; reaching an iteration cap is not automatically a convergence proof.

The existing whole-UTC-day 60/20/20 protocol and frozen variant registry will
be reused. At least ten joint-valid UTC days are needed for holdout claims;
otherwise results are diagnostic. The economic study will pair identical tapes,
seeds, fill scenarios, latencies and fees across baseline, observation-only and
opt-in policy runs, with moving-block bootstrap sensitivity. No such study has
been completed by the numerical foundation tests.

## Reproduce the foundation checks

```bash
python -m pip install -r requirements.txt
python -m pytest -q tests/test_hmm_filter.py tests/test_hmm_preprocess_artifact.py tests/test_hmm_features.py tests/test_hmm_runtime.py tests/test_hmm_baseline.py
python -m mypy lob_sim/regime
python scripts/reviewer_gate.py
```

The HMM-disabled golden fixture freezes preimplementation summary and event
trace hashes. Adding source necessarily changes repository/code provenance;
that is not a behavioral change and is not disguised as identical source.
Fitting, inspect, simulation and comparison CLI commands will be documented
only once they exist and have been verified.

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
