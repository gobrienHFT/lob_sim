# Repaired core baseline

The clock, risk and record-boundary repairs are independent of the optional
HMM layer. Review this branch against `master` first; review HMM against this
repaired baseline second. No historical branch is rewritten or force-pushed.

## Repairs and their independent contracts

| Change | Failure reproduced on the old core | Standalone regression |
| --- | --- | --- |
| Integer schema-v3 requote timers | A decision at 8.999999999999984 was dispatched after the receipt at 9.0 | Exact 40 ms grid and causal trace ordering |
| Global active-symbol scheduling | Another symbol's overdue decision at 2.04 followed a global observation at 3.0 | Two symbols, either quiet, and stable same-time ordering |
| Missing instrument units | Unknown nonzero exposure became a zero notional reservation | Long, short, live, pending, proposed and empty exposure |
| Live-order symbol census | An orphan live order without a position/spec record escaped reservation | A separately reproduced live-order-only failure |
| Record-boundary finalization | Metadata/control stops skipped forward; gap-invalidated markout traces appeared after later receipts | Stop/resume on metadata, faults, malformed and ignored rows; immediate markout flushing |
| CRC32C lookup | Independent core optimization previously mixed into HMM history | Bit-stream division oracle, Castagnoli standard vector, every byte and seeded payloads |

The first five contracts intentionally repair broken behavior. They do not
promise equality to unsafe historical runs. The unaffected legacy golden
summary and trace hashes remain unchanged. CRC32C changes no wire checksum.

A trade-stream failure does not invalidate book-only markouts merely because
it occurred: validity dimensions and execution provenance retain their existing
meaning. The flushing test requires a pending markout actually affected by the
fault, not a fabricated invalidation.

## Cross-revision HMM-off contract

`scripts/core_regression_probe.py` runs without any regime import. Its reference
is generated on the clean core-only branch, before integrating that branch into
HMM. Three existing profiles, both fill modes, fixed/seeded empirical/stress-tail
latency, valid tapes, gaps, malformed levels, depth/trade failures, capture
overflow, quiet symbols and the legacy fixture are represented.

Each comparison includes the complete mutable continuation, scheduler/latency
state, event trace, metrics, validity/risk annotations, fills, markouts and
configuration. Checkpoint cuts include metadata and actual fault/ignored
boundaries. Every resumed run must equal its uninterrupted counterpart.

Fingerprints are read-only: the legacy summary method is evaluated on a detached
metrics snapshot, so reading a report cannot add observations to the running
engine. No risk/economic fields are removed, rounded or HMM-key-filtered to
force agreement. Different source identities remain recorded as provenance;
they are not behavioral equality. Checkpoints stay source-bound and are not
claimed portable across code revisions.

```powershell
python scripts/core_regression_probe.py --expected docs/regression_results/repaired_core_baseline.json
python -m pytest -q tests/test_core_clock.py tests/test_core_risk.py tests/test_core_boundary.py tests/test_core_crc32c.py tests/test_core_probe.py
python scripts/reviewer_gate.py
```

The generated fixtures are diagnostic engineering inputs, not historical
exchange activity, FIFO truth, profitability evidence, complete UTC days,
latency measurements or soak tests. The broader platform gates still apply.

The native reference was produced at clean `900475f`: 112 completed cases and
648 checkpoint/resume comparisons. Its behavioral digest is
`de0409402a588284e528f2df939e828c7b7f5fc538f6856ff4a7bbc2d64c23a0`.
The reference is committed unchanged; it contains hashes/configurations, not
raw captured market data. Twenty-three selected cross-revision cases run in
the ordinary suite; the command above checks the exhaustive matrix.
