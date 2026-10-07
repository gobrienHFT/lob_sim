# Real-market pipeline: adversarial review

This review distinguishes implemented contracts from missing empirical proof.
The reference core is the corrected baseline from core PR #99; HMM PR #98 and
the opt-in research path have no disabled-mode behavioural exceptions.

| Reviewer objection | Control and reproducible check | Remaining limit |
| --- | --- | --- |
| A short, good-looking tape is called a valid day. | Fixed 86,400-second day denominator, declared source/instrument identity, 99% coverage and 95% joint/mark validity. Admission/fault tests reject synthetic, overlapping, partial and corrupt parents. | Author-declared network provenance and absolute UTC accuracy are not authenticated. |
| Future clock anchors or feed recovery improve earlier windows. | First-receipt projection, causal freshness controls and full trailing-window admission. Feature/day-view tests mutate gaps, epochs, clocks and availability. | Native feature formulas use the authoritative Python book; the feature reader is not a second exchange reconstruction. |
| The fitter sees test features or selects on PnL. | Physical role files; test-role rejection before IO; calibration-only scaler/EM; fixed validation selection, K/restart ledgers. Model verification independently recomputes scaling and saved forward likelihoods. | No OS security boundary or proof that an author never inspected data elsewhere. Discarded restart parameters cannot be reconstructed from ledgers alone. |
| Only flattering models, cases or intervals survive. | Missing selected models stop test replay. Exact registered case census, failed-attempt journal, exclusive partial outputs and nonempty source/day universe. Rehashed omission/model/scenario attacks fail. | Resource ceilings can block the entire experiment; they do not guarantee a full-day job fits the current host. |
| BTC quotes while ETH is unavailable. | Both books/trades/clocks/marks gate each conditional unit; either invalidation clears selected live/pending orders and pending markouts. The counterpart never gets quote timers. Native replay tests cover outages and subsecond timers. | Joint validity is a declared conservative study rule, not evidence of exchange-side order cancellations. |
| A timeout or end-of-day marker supplies a price. | Half-open cutoff, no endpoint fills, selected validated depth-only markout resolution, source-receipt re-reader and missing-mark nullability. | Native CSV timestamps use floating presentation; no sub-nanosecond accuracy is claimed. |
| Different symbols or execution models are quietly pooled. | Case/configuration/model/source binding and separate symbol/cadence/scenario/latency cells. Every variant is retained. Baseline/observe core hashes must agree before measurement reuse. | Base/aggressive share their passive depth rule. The output is an assumption envelope, not true execution bounds. |
| Inference averages fill ratios or treats missing PnL as zero. | Quantity-weighted maker basis points, exact ratio sufficient statistics, common UTC-minute grid, causal equity endpoints and explicit coverage. Hand-calculated statistical/economic tests exercise unequal denominators and missing observations. | Pointwise blocks are conditional uncertainty, not multiple-testing-adjusted significance or full-path drawdown intervals. |
| Self-audit just reruns the same matching code. | Serialized case auditors reconstruct native accounting/risk/state chains; the full graph verifier cannot call `SimulationEngine.run`. Rehashed parent/case/table/comparison attacks are rejected. | This proves correspondence, not independent exchange truth or full-engine Python/Rust parity. |
| A synthetic graph is presented as empirical proof. | Long harness explicitly declares synthetic provenance and hand models; its pytest-only parent admission/fitting substitutions are documented. Production exposes no bypass. | No ten-day admitted held-out study exists yet. |

## Audit-only repairs isolated from research behaviour

Master's general bundle auditor incorrectly required every unmodeled trade lot
to be overlap-netted, including depth-only configurations where trades are
deliberately disabled as a consumption signal. The original master auditor
rejects the existing walkthrough fixture under `sim_fill_model=depth` with HMM
off. A standalone regression now distinguishes ignored signal quantity from
actual reconciliation, while still rejecting consumption by a disabled signal
and netting beyond observed quantity. Matching/fills/accounting are unchanged.

The general auditor also needs explicit optional-regime artifact schemas and
a finite larger field budget for fixed state/source/horizon summary tensors.
The aggregate CSV limit is restored after reading; hostile oversized fields
remain rejected. Relocated inputs must match original size, SHA-256, event
census and evidence IDs. These are validator changes, not simulation repairs
or exceptions to the HMM-off reference.

## Outstanding evidence

The committed public smoke contains zero eligible full UTC days. The separate
25-hour capture started at `2026-10-07 00:42:31 UTC` from the independently
installed `c88970e` wheel. While in progress, its partial telemetry is not a
completed soak, a joint-validity percentage or venue-packet-loss evidence.
It must be finalized and independently audited before publication.
Its off-midnight 25-hour window does not itself supply a complete eligible UTC
day. Subsequent capture planning must cover whole dates without overlapping
sources or rewriting the attempt's request.

The [local verification projection](regression_results/real_market_pipeline_review_20261007.json)
records the tested clean source and exact native report identities. All 15
reviewer steps, four long synthetic graph tests and the 112-case/648-checkpoint
corrected-core comparison passed. These are mechanics/regression checks, not
evidence that the outstanding empirical requirements are met.

Required next evidence is a completed 24-hour-plus reliability report, at least
ten eligible real UTC days, a frozen registry and untouched held-out results.
Full-day grid resource cost is unmeasured; the current native CSV audit format
can require substantial disk/runtime. No throughput, economic benefit or HFT
equivalence is inferred from the implementation or short tests. Human review
of venue, risk, ordering, statistics and public claims remains necessary.
