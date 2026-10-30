# Research scale and recovery review

Status: work in progress, 2026-10-29. This is not a completed scale,
crash-recovery or empirical research release.

## Starting evidence

The canonical checkout is `C:/bitbucket/kibert/lob_sim`, with origin
`https://github.com/gobrienHFT/lob_sim.git`. Refreshed remote master remains
`063906e821ace02db8aa9dc9dbaa3298162533e1`. PR #100 remains open and draft at
`ec49b9a0fba2f18201c72691f5103a3ece1db8df`; it has not been merged. Its ten
October 7 hosted jobs are green, but are historical evidence, not reruns of
this review's subsequent changes.

Scale/recovery work starts on `codex/research-scale-recovery`, based on that
unmerged head. This dependency does not imply human approval of PR #100.

## Reproduced verification defect

The case reader checked each artifact's hash and the native auditor checked
canonical files. However, two graph labels could refer to the wrong genuine
file: `summary_csv` and `hmm_model` could each be rebound to `summary.json`.
Neither the file bytes nor their real hash needed alteration. Verification
accepted the incorrect role bindings, making the serialized graph misleading.

Two minimal mutations reproduced this on PR #100's implementation. Genuine
native baseline/observe bundles were first verified as controls; the graph
bindings alone were then changed. Both regression tests failed before repair
because verification did not raise. The failure report is retained at
`outputs/scale_artifact_alias_before_20261029.xml`, SHA-256
`271c9ff097b45f960b5981a1ab7d9e054866ce19508a9efe4af71e682b3b443d`.

The research case reader now binds each label to its canonical filename and
requires the exact binding schema before checking hashes. The ordinary engine,
matching, fees, accounting and HMM code are unchanged. All ten actual bounded
replay/case tests pass after repair; Mypy and Ruff/format checks pass for the
changed reader/test. Full current-source verification remains separate work.

Reproduce the focused tests with:

```powershell
python -m pytest tests/test_research_study_replay.py -q
```

### Clean initial gate and strict JSON identity repair

Clean `1fe8610b05113f5cff5125c56338512264e0cc8e` subsequently passed all
15 reviewer steps: 1,819 Python tests, 16 Rust tests, Mypy/Ruff/format,
published primitive/composed differential references, deterministic/artifact
and fault checks, installed-wheel smoke and the short diagnostic benchmark.
The retained native report is `outputs/research_scale_initial_gate_20261029.json`,
SHA-256 `5bda9f3fd5c5f01932d4d22be5a9c95d32a6ca901c3aeef2ae5847aeda099185`.
Elapsed time was 2,668.23 seconds under competing diagnostic work; this is
correctness evidence, not a controlled performance baseline. The extended
graph marker is not included in this fast gate.

A second actual defect was reproduced afterward: Python dictionary equality
accepted boolean `false` in place of the frozen numeric `0.0` new-order
latency in a study record. The genuine native bundle and its expected numeric
configuration were unchanged. That failing regression is retained in
`outputs/scale_boolean_binding_before_20261029.xml`, SHA-256
`8e789f1d12872930abc7f4259ba7c47bd1e611b6054da87ac6f574e4176f932c`.

Case/configuration and native configuration bindings now compare canonical
JSON, not Python's boolean/numeric equality. All 11 focused native replay/case
tests pass, together with Mypy and Ruff/format. This later repair is **not**
retrospectively covered by the clean `1fe8610` full-gate report. It changes the
research verifier only; ordinary matching, clocks and accounting are untouched.

A later front-of-reader audit reproduced the same Python equality pitfall at
two additional graph boundaries: a rehashed `plan.json` could replace latency
`0` with `false` or `claim_ready: false` with `0`, and a comparison cell could
replace its zero latency with `false`. Three focused tests currently fail
before the repair; the genuine numeric control reaches the next reader
boundary. Parent admission is deliberately substituted only inside these
boundary tests, which stop before any native case audit. This is not a claim
that the substitute graph is a complete admitted study. The failure report is
`outputs/scale_plan_identity_before_v2_20261029.xml`, SHA-256
`d0125b6870c4d35de4c66ab0328487a789dcff401c8fa3aaf678851230c3dab3`.
Four further failing controls reproduced integer/boolean/float substitutions
in a retained failed-fit ledger and its training provenance. Their pre-repair
report is `outputs/scale_fit_ledger_identity_before_20261029.xml`, SHA-256
`21f9941a40ca5edeeacd57b13dadd6d13d87af4c443e9fd10dbc5febd798b2bd`.
No fitting or test-partition admission was needed to reproduce this problem.

Both readers now compare canonical JSON for these frozen identities. Fitted
provenance/selection bindings and state-permutation indices also retain exact
types. All 18 focused graph-boundary and successful/failed-fit tests pass,
including every new failure, and Mypy passes. The post-repair report is
`outputs/scale_graph_fit_identity_after_20261030.xml`, SHA-256
`6655fe658674da3e1c323f0a9a69ac8a2642bf78d10637c1aa86de0ec0a800b9`.
Full native-graph mutations and clean-source release verification remain
separate required checks. These reader repairs do not change replay physics
or the HMM-off behavioral reference.

## Case storage and publication components

### Subsequent capture aliasing repair

Two new failing tests reproduced a distinct storage defect: nested payloads
remain caller-owned even when their `EventEnvelope` dataclass is frozen.
Changing such a payload before writing allowed a completed manifest whose
receipt checksum was invalid. Changing it between serialization and content
hashing also allowed a completed segment with a mismatched trailer digest.
This is not a claim that the observed interrupted public capture suffered
either defect; its terminal cause remains unknown.

The failure report is `outputs/scale_payload_aliasing_before_20261029.xml`,
SHA-256 `3c5b0bb24d0587861e4826644b707f2a3ad3ef2f8b714f9b52768a27b223b4a4`.
The writer now freezes one serialized snapshot, checks its payload against
the original receipt checksum before rotation/writing, and hashes exactly
that same snapshot. A mismatch latches failure and leaves incomplete evidence;
it cannot publish a complete manifest. Ordinary simulation/HMM logic is not
changed. This correctness repair is committed separately from optimizations.

All 45 targeted aliasing, preservation, segment-recovery and forensic checks
pass, as does Mypy. The report is
`outputs/scale_payload_aliasing_after_20261029.xml`, SHA-256
`bbda6ac3d7ac9db1f5016d02f02bbad5c877f1d8630f99cda8d0203c7786e5bf`.
The earlier 112-case/648-checkpoint comparison matches the unchanged behavioral
digest `de0409402a588284e528f2df939e828c7b7f5fc538f6856ff4a7bbc2d64c23a0`;
its file hash is `b3113214041498a7cf3377db3e4b5c975fcd36376feb5edfd9fa08c311007abb`.
That comparison predates this storage repair, so a final current-source rerun
is still required. Its reference has not been rewritten.

### Corrupt-tape diagnostic bounds

Two further failing controls showed that segment validation retained 1,000
messages for 1,000 unknown records and a 20,030-character message for one
oversized record label. Diagnostic retention therefore grew with corrupt
tape length. The pre-repair report is
`outputs/scale_segment_diagnostic_bounds_before_20261029.xml`, SHA-256
`3622f7ca9e7a18846f5273c7b5e1842fc884fe6390bf131f2ff060fba561a79c`.

The validator now retains at most 128 detailed messages of at most 512
characters, plus an explicit omitted-count message. Validation still rejects
the entire damaged tape; forensic recovery still stops at the first invalid
record. This does not bound the size of an input row or change valid receipts.
All 52 targeted storage/wire/recovery checks and Mypy pass afterward. The
report is `outputs/scale_segment_diagnostic_bounds_after_20261029.xml`, SHA-256
`7a02397004c724b5d0edbfa6db614ceee380b92113b3c75712e9728de3032306`.

`research.case_store.CaseStore` now provides a frozen full-plan snapshot,
exclusive OS-backed executor lock, exclusive attempt directories, immutable
completion receipts and mandatory independent verification on both first
completion and reuse. Failed/interrupted attempts remain separate and are
never reinterpreted as completed cases. The controller is sequential and
rejects case execution from a different process/thread owner. Retry and case
counts have explicit caps.

Seven component/native-bundle tests pass. The interruption test launches and
kills a real child holding the executor lock mid-attempt, then proves that
restart reuses the verified earlier completion without rerunning it, retries
the interrupted case, and retains its partial bytes. Separate checks reject
corruption, plan drift, boolean/integer plan substitution and cross-thread
execution. The control payload in that kill test is deliberately not a market
simulation; full-study interrupted/resumed matching equivalence remains open.

`research.case_publication.materialize_case` hard-links verified native files
into a fresh publication tree without rewriting their bytes or stored origin
paths. Baseline, observe and policy bundles independently reverify after this
relocation; source/destination file identities agree. A cross-volume failure
is explicit, with no silent copying or metadata repair fallback.

Reproduce these components with:

```powershell
python -m pytest tests/test_research_case_store.py tests/test_research_case_publication.py -q
```

The registered study now accepts opt-in `--case-store` and `--resume-store`.
The existing one-shot v1 path remains available. Durable v2 publications bind
the full frozen census, independently verified case receipts and external
failed-attempt identities; a separate reader verifies that evidence without
running cases. Eight mutation/control tests pass for this serialized reader,
including a rehashed boolean/numeric plan substitution, missing cases, aliased
receipts, changed attempt ordering, unsafe paths and typed-size corruption.

The complete native matching interruption/restart test now passes (932.95
seconds on the contended host). It kills a live process during the second
case, independently reuses the first completion without running it, retries
the interrupted case, and preserves the original partial bytes. Resumed and
clean three-case synthetic graphs independently verify and agree on exact
event/fill/markout/regime/policy/risk/model/table bytes and every non-runtime
summary/manifest field. Explicit exceptions are output paths, creation and
file-modification times, and summary hashes/sizes derived from those paths.
A third publication forbids all matching and reuses all three verified cases.
A rehashed missing-case mutation is rejected by the separate graph reader.

The passing JUnit report is
`outputs/scale_native_matching_recovery_v3_20261029.xml`, SHA-256
`8369e5d252c6f24ede5d835cdf7799dab6c5f9b9d51acb5c77049ee37c378d5b`.
The raw synthetic test tree was copied, without rewriting origin metadata,
to `outputs/research_scale_native_recovery_evidence_20261029` before ordinary
pytest temporary-directory retention can remove it. These are working-tree
Windows test results, not a clean-clone release or real-day/grid recovery.
No physical power failure or execution-order permutation is certified.

Thirty focused case, reader, publication and legacy replay tests also pass;
the four original extended graph cases remain separate. The report is
`outputs/scale_case_execution_focused_20261029.xml`, SHA-256
`ad1a695dbf65df6709b15ff676c0e1ec45c0a65b840d406fa7e3e9a69bfd6e51`.
Mypy passes with both Windows and Linux targets; Linux runtime remains
unverified until hosted checks run. Reproduce the native recovery test with:

```powershell
python -m pytest tests/test_research_study_recovery.py -m long_research -q
```

File fsync and POSIX parent-directory fsync do not establish a filesystem/
controller-wide durability guarantee. Linux runtime and physical reboot/
power-loss testing remain separate from Windows process-termination checks.

The corrected-core behavioral reference remains immutable. Clock/risk repairs
are standalone historical fixes, not HMM-off exceptions; all HMM-off behavior
must match that repaired baseline exactly. This research work does not change
the ordinary engine or refresh the 112-case/648-checkpoint reference to accept
changed outputs.

## Existing public capture: incomplete, not missing or successfully finished

The original `outputs/real_market_soak_25h_c88970e_20261007` directory exists.
Inspection found 167 finalized compressed segments, one partial segment,
partial telemetry, the original request and reserved capture identity. There
is no final capture manifest or soak completion record. Process inspection
found no running Python collector matching the capture/soak entrypoints.

The last retained telemetry row has sample index 10,070 and receive wall time
`1791384096513406500` ns. It is a historical observation, not current liveness
or proof of final delivery. The producer's terminal cause is unknown: absent
failure metadata cannot distinguish power loss, forced termination or another
interruption. No source bytes were altered or tails finalized by this review.
The full segment/prefix census has now completed. All 167 finalized segments
pass native envelope/checksum/trailer checks, containing 2,712,607 records.
The partial segment retains another 13,252 fully checked records, followed
by an unterminated JSON string and no completion trailer. Checked receive
sequence ranges are contiguous through record 2,725,859. This is **not**
proof of upstream packet delivery, valid book/trade/clock intervals, or a
complete capture. Source size, modification time and SHA-256 snapshots agree
before and after the read; no bytes were repaired, renamed or finalized.

The retained forensic report is
`outputs/research_scale_incomplete_capture_forensics_20261029.json`, SHA-256
`a91e104ccd49915905b5387d7ee61f56ff2d3a4bab4b2a78e902dc54ce160c04`.
It records each segment's file/content hashes, record count, sequence range
and failure details. The processor was loaded at clean `1fe8610`; relevant
capture validators were unchanged during the run. Elapsed time was 5,096.90
seconds under competing work, not a controlled validation benchmark. This
audit checks serialized receipts, not an independent book reconstruction.

The existing independent completion command correctly rejects this directory
with exit code 2:

```powershell
python -m lob_sim.cli capture-soak-verify --bundle outputs/real_market_soak_25h_c88970e_20261007
```

It therefore cannot establish a successful 25-hour soak, venue packet loss,
or an admitted UTC day. Other local tapes still require individual inventory
and audit; this finding does not assert that all available input is absent.
No new network collector has been launched.

## Full objective and outstanding proof

### Measured copy/checksum optimization

A 16,012-record joint-book null-sink profile identified redundant payload
serialization/CRC work and a second deep copy of already-owned risk rows.
Risk observation accounted for 150.60 cumulative seconds in a 263.70-second
profiled run; the 30 actual valid HMM updates accounted for 0.0278 seconds.
These overlapping call-tree times are not additive or uninstrumented latency.
No risk boundary, feature cadence, audit row, quote timer or warmup was removed.

Three narrowly scoped changes remove the discarded recursive `asdict` payload
copy, reuse the CRC just recomputed by envelope construction when checking the
outer storage binding, and avoid copying an independently detached risk row
twice. Corrupt inner/outer checksums still fail separately. A stdlib serializer
and bitwise CRC oracle cover 1,000 generated nested payloads; hostile sink and
caller-mutation tests cover risk ownership. The 233 focused checks pass.

The interleaved 16,012-record experiment uses the current engine with four
explicitly selected old function bodies from `f18c458e`, not a historical
whole-binary build. Each mode has one warmup pair and three measured pairs;
mode and old/new execution order are balanced. Every repeated event, fill,
markout, accounting and complete checkpoint-state probe agrees, including
HMM-risk continuation. Baseline and observation-only core probes agree too.

| Mode | Before median wall seconds | After median wall seconds | Change |
| --- | ---: | ---: | ---: |
| HMM off | 10.4485 | 9.3937 | -10.10% |
| HMM observe | 43.2092 | 26.0330 | -39.75% |
| HMM policy | 33.1093 | 21.6837 | -34.51% |

Raw rows and full identities are retained at
`outputs/research_scale_paired_16012_20261029/report.json`. The shared,
unpinned Windows host exhibited large within-pair variation, including some
slower candidate trials. These are diagnostic medians, not a confidence
interval, causal certification of the earlier approximately 4x cross-run
timing difference, per-event tail latency, or an HFT performance claim.

Separate full-audit before/after runs independently reverify and match all
event/fill/markout/regime/policy/risk/model/table bytes and non-provenance
summary fields. Their common artifact fingerprint is
`f373743e2c88b0b05359cf9b31207afa7a42cd9cebde418018543540cec4e60f`.
The comparison report is
`outputs/research_scale_audit_observe_comparison_20261029.json`, SHA-256
`85aedcc35ae17a3be80a700f60c4e0a0b495b2609355a45b095f71ada4527882`.
Audit size was 64,954,729 versus 64,954,685 bytes; the 44-byte difference is
metadata, not suppressed audit evidence. Separate monitored 3,212-record
audit runs used approximately 19.15 MB peak managed logical file lengths
including temporary indexes in both versions. Peak process RSS was about
101.1 versus 102.1 MB: there is **no measured whole-process memory reduction**.
The disk sampler is not physical filesystem allocation or a continuous peak.

| Phase | Current status / required evidence |
| --- | --- |
| Adversarial audit | Partial: role-binding defect reproduced/repaired. Clock, lifecycle, queue, risk, aliasing, fitting, valuation, statistics and crash-publication review remain open. Preserve corrected-core historical references. |
| Workload measurement | Open: several sizes including substantial replay; raw timings, CPU/wall/RSS/allocations, cold/warm initialization, profiles, snapshots/scheduling/inference/queue/export costs and bytes/event. |
| Optimization | Not started. Duplicate scans/reductions and repeated regime inspection are hypotheses, not measured bottlenecks. Require identical-workload before/after performance, memory/disk and behavioral parity. |
| Durable execution | CLI integration, 30 focused checks and the complete real-process interruption/clean-equivalence/all-completed-reuse synthetic test pass on Windows. Full-grid, execution-order permutations, Linux-runtime and power-loss evidence remain open. |
| Real-market readiness | Original attempt remains incomplete. All 167 finalized segments pass native structural/checksum checks; the checked partial prefix ends in truncated JSON. Audit other available tapes and apply unchanged UTC eligibility. Capture planning needs resource/liveness checks first. |
| Execution evidence | Open: count maker/taker, queue/crossing, pending-cancel and missing-markout samples; compare event/quantity weighting and dependence. No maker sufficiency claim. |
| Verification | Focused repair tests pass. Full current Python/Rust/golden/checkpoint/audit/wheel checks and bounded extended workload workflow remain required. |
| Public package | This initial review exists; measured tables, projections with explicit assumptions, profiler evidence, actual interruption/recovery reports and final adversarial review remain required. |

## Scope and resource guardrails

The frozen research grid remains 162 cases per source/day/symbol unit and up
to 32 units. No input thinning, missing cases, changed fees, shortened warmup
or relaxed admission rule is permitted to improve timing. Before measurement,
full-day, ten-day and 5,184-case runtime/storage costs are **unknown**, not
benchmarks. The complete experiment's feasibility on this host is unproven.

Available C: storage was approximately 6.3 billion free bytes during initial
inspection and can change independently of this project. Preserve prior
evidence; do not delete it to make a benchmark fit. Existing short synthetic
graph tests are not substantial-scale performance or real-maker evidence.

This work does not claim production readiness, private Binance fills, actual
gateway latency, historical FIFO, profitable alpha or completed empirical
research. Canonical portfolio state has not been modified.
