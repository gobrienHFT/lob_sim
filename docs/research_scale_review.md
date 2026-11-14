# Research scale and recovery review

2026-10-30. **Limited-scale engineering evidence, not a completed empirical
study or HFT release.** The pipeline now has durable case-level restart and
measured copy/checksum improvements. The complete registered experiment is
not established practical on the available hardware. There are still zero
admitted real UTC research days.

## Evidence and review boundary

The canonical repository is `C:/bitbucket/kibert/lob_sim`, origin
`https://github.com/gobrienHFT/lob_sim.git`. This work is on
`codex/research-scale-recovery`, based on the unmerged
[draft PR #100](https://github.com/gobrienHFT/lob_sim/pull/100), head
`ec49b9a0fba2f18201c72691f5103a3ece1db8df`. Master was independently checked
at `063906e821ace02db8aa9dc9dbaa3298162533e1`; PR #100's October 7 green
checks do not verify these later changes. Neither PR #100 nor master was merged
or rewritten by this review.

The [byte inventory](research_scale_evidence/inventory.json) preserves 64
selected producer reports, including every measured trial and failure listed
below. It is independently checked in the ordinary test suite. It authenticates
neither benchmark authorship nor venue provenance. Producer source identities
and timestamps remain intact; reports from different commits are not silently
relabelled as one release. Full raw tapes, temporary indexes and native audit
trees remain local rather than becoming large Git commits.

[Reproduction commands](research_scale_methods.md) cover synthetic input,
paired measurements, full audit, resource monitoring, source forensics and the
next continuous public capture. All output directories must be fresh.

## Correctness repairs, separate from optimization

Each actual defect had a failing reproduction before its repair. These are
storage/verification changes, not changes to matching, latency, fees, feature
warmup or statistical admission.

| Defect | Repair and permanent regression | Retained before / after evidence |
| --- | --- | --- |
| Genuine artifact hashes could be assigned to the wrong semantic role. | Bind every label to its canonical filename and exact binding schema; `test_research_study_replay.py`. | [Role mutation](research_scale_evidence/artifact_role_before.xml), [native case tests](research_scale_evidence/case_race_after.xml). |
| Python equality accepted boolean/integer/float substitutions in frozen identities. | Canonical JSON at case/configuration, graph-plan/cell, fit-ledger/provenance and prepared-feature/cadence boundaries; dedicated native and boundary controls. | [Case](research_scale_evidence/case_types_before.xml), [graph](research_scale_evidence/graph_types_before.xml), [fit](research_scale_evidence/fit_types_before.xml), [features](research_scale_evidence/feature_types_before.xml); [31 feature/graph/fit checks](research_scale_evidence/feature_types_after.xml). |
| A caller could mutate a nested receipt payload between serialization and hashing. | Freeze one serialized snapshot, check its receipt CRC before writing/rotation, hash that same snapshot and latch failures. `test_capture_payload_aliasing.py`. | [Two failures](research_scale_evidence/payload_alias_before.xml), [45 passing checks](research_scale_evidence/payload_alias_after.xml). |
| Corrupt input retained one potentially huge diagnostic per record. | Cap details at 128 messages of 512 characters, plus omitted count; still validate all records and fail closed. | [Two failures](research_scale_evidence/diagnostic_bounds_before.xml), [52 passing checks](research_scale_evidence/diagnostic_bounds_after.xml). |
| A native file could change after semantic reading but before a case was reported verified. | Recheck all native hashes/finalization and the table hash before return, in baseline and HMM branches. | [Actual model-mutation failure](research_scale_evidence/case_race_before.xml), [33 passing case/store/publication/reader checks](research_scale_evidence/case_race_after.xml). |

The last guard detects observed drift; it is not a filesystem snapshot or a
defense against a hostile writer restoring bytes between reads. Exclusive
executor ownership remains required. Diagnostic caps do not bound a single
input row's decoding allocation.

Historical clock/risk fixes remain standalone **core PR #99**, followed by
**HMM PR #98**. There are no HMM-off exceptions. The corrected reference was
not regenerated to accept changes: at clean `203d5f9`, all **112 cases and
648 checkpoint/resume comparisons** match behavioral SHA-256
`de0409402a588284e528f2df939e828c7b7f5fc538f6856ff4a7bbc2d64c23a0`.
See the [full comparison](research_scale_evidence/corrected_core_112.json).
That source predates the final feature/case-reader repairs; current-source
release/hosted checks must be reported separately.

## Measured optimization and exact parity

A profile identified redundant recursive payload serialization, repeated CRC
work and a second deep copy of already-owned risk rows. Three changes remove
only that work. Envelope construction still recomputes/validates the payload
CRC before its outer storage binding is checked; nested risk stages remain
detached before a sink sees them. No risk boundary or audit row was removed.

An independent stdlib serializer/bitwise CRC oracle covers 1,000 generated
nested payloads, including inner/outer corruption and legacy metadata. Hostile
caller/sink mutations and checkpoint tests cover ownership. All [233 focused
checks](research_scale_evidence/optimization_oracles.xml) pass.

The [interleaved experiment](research_scale_evidence/paired_16012.json) uses
the same current engine with four selected old function bodies from
`f18c458e37d24a1606a7cc831f78ea64507dec29`, not an old whole binary. Each
mode has one warmup pair and three measured pairs, with balanced old/new order.
Full state, events, fills, markouts, accounting and risk-continuation probes
agree in every pair; HMM-off and observation-only core probes also agree.

| Mode, 16,012 normalized records | Before median wall s | After median wall s | Change |
| --- | ---: | ---: | ---: |
| HMM off | 10.4485 | 9.3937 | -10.10% |
| HMM observe | 43.2092 | 26.0330 | -39.75% |
| HMM policy | 33.1093 | 21.6837 | -34.51% |

The shared unpinned host varied substantially; some candidate trials were
slower. N=3 does not support a stable-host threshold or an inferential interval.
The earlier approximately 4x difference between separate runs is **not** a
causal speedup claim.

[Full-audit comparison](research_scale_evidence/audit_comparison_16012.json)
independently reopens the native outputs and matches all CSV/model/table bytes
and non-provenance summary fields, common fingerprint
`f373743e2c88b0b05359cf9b31207afa7a42cd9cebde418018543540cec4e60f`.
Before/after retained bytes are 64,954,729 / 64,954,685: the 44-byte difference
is metadata, not audit thinning. Separate [before](research_scale_evidence/resources_before.json)
and [after](research_scale_evidence/resources_after.json) 3,212-record resource
runs peak at approximately 19.15 MB managed logical file lengths in both
versions, including temporary indexes. OS RSS is 101.1 / 102.1 MB: no
whole-process memory reduction is established. The sampler deduplicates
hardlinks; it is not a physical-cluster or continuous disk-peak measurement.

## Substantial offline workloads

Synthetic paired-symbol tapes use 20 book levels, four records per 10 ms
round, a frozen five-state synthetic model, 40 ms quoting and the unchanged
30-second feature warmup. They are performance inputs, not empirical admission.
New diagnostic contracts set `registry_sha256: null`; older measured contracts
retain their original placeholder identities and are not registrations.

| Workload | Wall s | CPU s | Peak process RSS MB | Measured repetitions |
| --- | ---: | ---: | ---: | ---: |
| 100,012 validated decoding | 28.423 | 28.109 | 64.16 | 3, plus 1 warmup |
| 400,012 validated decoding | 123.900 | 122.234 | 64.08 | 3, plus 1 warmup |
| 100,012 HMM-off null sink | 91.021 | 90.594 | 63.32 | 1 |
| 100,012 HMM-observe null sink | 257.378 | 255.766 | 67.30 | 1 |
| 100,012 HMM-policy null sink | 142.575 | 142.016 | 64.09 | 1 |
| 400,012 HMM-off null sink | 391.179 | 389.156 | 63.45 | 1 |
| 400,012 HMM-observe null sink | 1,012.961 | 1,007.922 | 67.63 | 1 |
| 400,012 HMM-policy null sink | 575.030 | 571.719 | 63.50 | 1 |

Decoding rows are medians. The 400,012-record measured trials are 123.900,
136.997 and 122.590 s after a 120.637 s warmup; no favorable trial is omitted.
All [raw and null reports](research_scale_evidence/inventory.json) retain
identities and individual timings. The larger observation process took
565.2 ms for package import and 12.1 ms for first model/configuration loading;
these are not total process-spawn latency. Every trial constructs a fresh
engine; loaded model/code and OS caches may remain warm. No cache flush is
claimed.

Both larger baseline/observe pairs have identical core probes, including
11,000 / 48,500 fills. The larger observation run emits 1,163,763 trace rows
to `NullSink`, retaining zero, with 990 valid HMM observations and ten warmup
samples. RSS grows from 67.30 to 67.63 MB across 4x input. This supports
bounded retention on these finite workloads, not an infinite-soak guarantee.
**Both policy cells place zero quotes and fills**: the frozen policy prevents
entry despite valid inference. Their cost is not active-policy throughput;
no threshold was tuned to conceal this result.

A separate [allocation-traced 3,212-record run](research_scale_evidence/allocation_3212.json)
takes 34.553 s, retains 2,321,613 traced bytes, peaks at 2,482,734, and ends
with 22,383 live traced allocations. These are not allocation churn or proof
of no per-event allocation. OS peak includes the runtime/imports/prior trials.

### Full audit is the material scale blocker

The [100,012-record full-audit run](research_scale_evidence/audit_observe_100012.json)
covers 250.01 synthetic seconds, retains **630,005,639 bytes** (6,299.30 bytes
per normalized record), and passes independent native audit. It produces three
complete clock-table rows, not a day of research observations.

| Stage | Wall seconds | CPU seconds |
| --- | ---: | ---: |
| Replay, export and finalization | 1,763.795 | 1,628.328 |
| Independent native verification | 776.965 | Not separately measured |
| Table reduction | 1,191.899 | Not separately measured |

OS peak is 77,836,288 bytes; bounded RAM does not mean cheap disk or CPU.
**Table reduction includes external stack sampling and a concurrent short
diagnostic test.** Its wall time is not a clean uninstrumented measurement.
The worker's `profiled: false` describes its internal cProfile setting only.
The [sampling context](research_scale_evidence/sampling_context.json) records
this explicitly. Replay/export and native verification finished before that
sampling began. These measurements also predate the final reader guards;
they do not quantify those guards' additional file reads.

### Profiling: where the time went

The [before](research_scale_evidence/profile_before.json) and
[after](research_scale_evidence/profile_after.json) intrusive profiles each
cover 16,012 records. Before, risk observation accounts for 150.60 cumulative
seconds of a 263.70 s run; 30 valid filter updates take 0.02777 s. There are
53,028 **regime-state**, not book, snapshots. Book construction is separately
visible in 32,017 `nlargest` / `nsmallest` calls (1.098 / 0.750 cumulative s),
with bid/ask tuple generators at 0.183 / 0.133 s. Quote scheduling, cancel
handling and level consumption take 1.031 / 0.191 / 0.980 cumulative s.
These call-tree times overlap and must not be summed as exclusive costs.

After, deepcopy calls fall from about 9.30 million to 4.62 million, payload
checksum calls from 64,048 to 32,024; risk-row construction falls from 88.05
to 59.66 cumulative s. Total profiled wall time is 268.23 s under a different
shared-host load, so this is call-path evidence, not the speedup estimate.
The balanced unprofiled pairs above are the timing comparison. The model's
30 forward updates are not a measured exchange-latency distribution.

Live stack inspection of the large audit identifies repeated serialized risk
validation/economic reconstruction inside reduction, not fitting/EM. A
[short checked flamegraph](research_scale_evidence/reduction_checked.svg)
contains 249 samples with zero read errors, all within economic reconstruction
and reduction. Its companion folded stack is a separate 10-second window,
not the rendering's source. Earlier nonblocking windows have many read errors
and are retained/excluded from quantitative percentages. None is an unbiased
whole-run CPU allocation. Duplicate full audit/reduction scans remain an
identified next engineering bottleneck, not a claimed implemented speedup.

## Conditional capacity projections — not measurements

Project by simulated duration as well as ingress: quote/risk timers continue
between observations. At the 1,000.01 s null-sink workload, constant-cost
day-equivalents are 9.39 h off / 24.31 h observe / 13.80 h policy-with-no-orders.
Ten such paths are 93.88 / 243.11 / 138.00 h, excluding audit, preparation and
fitting. This is not a prediction for a quiet or different real tape.

Holding the short full-audit rate constant implies **217.72 GB retained per
day per case** and 358.32 wall hours for all three stages; ten day-equivalents
imply 2.18 TB and 3,583.21 h. The reduction instrumentation caveat transfers
to these illustrations. They are neither bounds nor measured full-day costs.

Ten admitted days split 60/20/20 give two test days × two symbols = four
source/day/symbol units: **648 registered cases**, not 3,240. The maximum is
32 units / **5,184 cases**. Repeating the measured three-mode null-sink mixture
unchanged implies approximately 427 / 3,420 serial days respectively. Repeating
the observation full-audit case alone 5,184 times would imply about 1.13 PB
retained. These deliberately conditional illustrations ignore varying
cadence, latency, execution, policy activity and market regimes; the inactive
policy can make the mixture optimistic. Neither is a grid forecast or a
parallel-speedup promise. Full-grid feasibility is **not established**.

## Durable execution and actual interruption proof

`research.case_store.CaseStore` freezes the complete content-addressed census,
uses an exclusive OS lock and sequential owner, creates exclusive immutable
attempts, and fsyncs atomic completion records only after independent native
verification. Every reuse independently reverifies artifacts. Changed plans,
corruption, missing cases and mixed-type identities fail closed. Case/attempt
counts are capped at 5,184 / 128. Failed/partial evidence is retained, not
rewritten as success. Publication hard-links canonical files into a fresh
tree and reverifies them; cross-volume failure has no silent copy fallback.

The registered CLI has opt-in `--case-store` and `--resume-store`; the v1
one-shot path remains. Resume publishes to a **new** output tree, never appends
an old streaming export. The final v2 graph binds the full census and failed
attempt history; hashes are not trusted-author signatures.

All [eight extended native graph/recovery tests](research_scale_evidence/native_graph_recovery.xml)
pass at clean `203d5f9` (2,016.72 s on the shared Windows host). A real child
is killed during its second case. Restart reuses the first verified case,
retries the interrupted case, preserves partial bytes and matches a clean
run plus a reverse-order run: exact event/fill/markout/policy/risk/model/table
bytes and all non-runtime summary/manifest fields. Exceptions are explicit
output paths, creation/mtime and hashes/sizes derived from those paths. A
completed-only reuse forbids all matching. Rehashed missing-case and full
native-graph typed-identity attacks are rejected.

These tests deliberately use synthetic mechanics and pytest-only parent
admission/fitting substitutions. Production has no admission bypass. They do
not prove real-day/grid feasibility, physical reboot/power-loss durability or
filesystem-wide atomicity. File fsync and POSIX directory fsync are mechanisms,
not storage-controller guarantees. Local native trees remain under
`outputs/research_scale_native_final_20261030`; the final-source Windows/Linux
hosted run is a separate required result.

## Real data readiness: preserve, exclude, do not invent

The original `outputs/real_market_soak_25h_c88970e_20261007` exists but has no
final manifest or completion record. Its [read-only forensic census](capture_results/incomplete_soak_prefix_20261029.json)
checks 167 finalized segments / 2,712,607 records, then 13,252 fully checked
partial-tail records before an unterminated JSON string. Checked sequence
ranges are contiguous through 2,725,859. Initial/final hashes, sizes and mtimes
agree. Nothing was finalized, repaired, renamed or deleted. Terminal cause
remains unknown; the payload-aliasing repair is **not** an explanation for it.
This is receipt integrity, not upstream zero-drop, book validity or a soak pass.
`capture-soak-verify` correctly rejects the attempt with exit code 2.

The [unchanged UTC admission report](research_scale_evidence/day_admission.json)
audits three finalized short local sources: zero eligible days. One is
BTC-only with unknown empirical provenance; the paired declared-public source
lasts 69.586 s with 83.76% joint validity; another paired short source has
89.86% joint validity and undeclared provenance. Every exclusion is retained;
no provenance, threshold or day denominator was rewritten to admit a tape.
This is a census of accessible relevant workspace captures, not a claim that
data is unavailable everywhere.

The interrupted attempt retains 778,375,430 bytes through its last sampled
telemetry about 50,345.425 s after start. At that observed rate a fresh 48 h
attempt needs about 2.67 GB, or **5.62 GB starting free space** with a 30%
data allowance plus the 2 GiB free-disk floor. This is planning, not a bound.
At 2026-10-30 06:30 UTC the host had about 3.74 GB free disk and 989,436 KiB
free physical RAM. These are point observations, not current reservations.
No new collector was launched or old evidence deleted. The reproduction page
requires a fresh liveness/resource check and 6 GiB starting disk headroom.
A 48 h window contains a whole UTC date, not a guarantee of validity or the
ten eligible days required for research.

## Maker evidence: insufficient, not a profitable headline

The [six-cell public diagnostic](research_scale_evidence/short_public_cases.json)
retains all scenarios/invalid tail, unchanged 30 s warmup and frozen
observation-only synthetic model. It is unregistered, not test admission.

| Execution profile / transit ms | Modeled maker fills | Modeled taker fills |
| --- | ---: | ---: |
| Conservative / 0 | 0 | 22 |
| Conservative / 50 | 0 | 22 |
| Base / 0 | 1 | 0 |
| Base / 50 | 0 | 0 |
| Aggressive / 0 | 1 | 0 |
| Aggressive / 50 | 0 | 0 |

All six native bundles independently audit. The maker is depth-inferred,
not a private exchange fill; base/aggressive share the passive rule and repeat
the **same observation**, not two independent samples or true bounds. Its
100 ms/1 s markouts resolve; 5 s/30 s are epoch-invalidated (100% missing),
not zero. Zero maker cells return null means; one-maker event/quantity weights
coincide trivially. Conservative fills are immediate taker-order fills, with
22/22 one-second coverage. Pending-cancel fill count is zero in every cell.
All ESS and inferential intervals remain null: **insufficient maker evidence**.
Independent unequal-quantity hand calculations test weighting; this sample
cannot establish a meaningful weighting difference or effective sample size.

Every non-flat cell has an unavailable final mark and null gross/net total
PnL. Realized cash is not substituted for a marked portfolio. Peak absolute
inventory is 0.022 BTC against the 0.05 limit (one-maker cells 0.001 BTC).
This is not a comprehensive risk stress test. Per-horizon sample reports in
the inventory bind all native artifacts and input children and remain
`claim_ready: false`. Synthetic FIFO, public-L2 inference and actual exchange
executions remain distinct.

## Final review, verification and remaining gates

The source review covered first-receipt anchoring/full UTC geometry/freshness,
market/action ties, queue/trade-depth exclusivity, pending cancels and joint
reservations, ownership/checkpoints, partition isolation and frozen models,
available-at-time state labels, missing marks, ratio denominators/block
boundaries, and serialized/exclusive publication. The reproducible defects
above were fixed. Independent negative controls live in the corresponding
clock/day/admission, core-risk/lifecycle, feature/fit, primary/statistics,
case-store/graph and ownership test modules. Source inspection does not prove
private venue behavior or make two consumers of one model independent truth.

The [initial clean full gate](research_scale_evidence/initial_gate.json) is
historical `1fe8610`: 15 steps, 1,819 Python / 16 Rust tests, type/style/fmt,
primitive/composed parity, deterministic/artifact/fault/wheel checks and short
benchmark. It does **not** cover subsequent commits. The separate new bounded
workload CI runs synthetic decoding/full audit and the immutable 112-case
reference on Windows/Linux, without noisy speed thresholds. Existing fast
and extended graph gates are not weakened. Final current-source reviewer gate
and hosted results must be checked on the delivery PR before approval.

| Mission component | What is established / what remains |
| --- | --- |
| Adversarial repairs | Failing-before regressions and independent controls; human signoff on venue/risk/statistical claims remains. |
| Measurement/optimization | Several sizes through 400,012 normalized records; paired exact parity, bounded retention and complete audit costs. No dedicated-host latency or allocation-churn proof. |
| Recovery | Actual Windows process kill, verified reuse, clean/reverse-order equality; no physical power-loss or full-grid proof. |
| Data/research | Original incomplete attempt preserved; zero eligible days and insufficient maker sample. Ten-day holdout remains blocked by data, not replaced by a diagnostic. |
| Practical scale | Null/metrics retention is bounded; audit CPU/disk and repeated reducers remain expensive. Full registered study is not demonstrated feasible on this host. |

Next proof-bearing work is reducing repeated verified native scans with strict
independent parity, then a resource-budgeted complete UTC-day measurement and
representative capture. Do not add another strategy, model, UI or venue to
hide these gaps. This release does not claim profitability, production HFT,
private FIFO, measured gateway latency, employer endorsement or completion
of the original full-engine Rust roadmap. Canonical Drive update: **none**.
