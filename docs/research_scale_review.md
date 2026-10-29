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
Independent full receipt/prefix integrity remains to be examined.

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

| Phase | Current status / required evidence |
| --- | --- |
| Adversarial audit | Partial: role-binding defect reproduced/repaired. Clock, lifecycle, queue, risk, aliasing, fitting, valuation, statistics and crash-publication review remain open. Preserve corrected-core historical references. |
| Workload measurement | Open: several sizes including substantial replay; raw timings, CPU/wall/RSS/allocations, cold/warm initialization, profiles, snapshots/scheduling/inference/queue/export costs and bytes/event. |
| Optimization | Not started. Duplicate scans/reductions and repeated regime inspection are hypotheses, not measured bottlenecks. Require identical-workload before/after performance, memory/disk and behavioral parity. |
| Durable execution | Open: current runner starts a fresh exclusive study after failure. No completed-case reuse or real process-interruption recovery has yet been implemented or demonstrated. |
| Real-market readiness | Original attempt found incomplete; preserve and investigate prefix integrity. Audit other available tapes and apply unchanged UTC eligibility. Capture planning needs resource/liveness checks first. |
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
