# Fault-injection evidence

The reviewer gate runs a deterministic integrity matrix:

```bash
python scripts/check_fault_injection.py
```

The matrix is intentionally small and reproducible. It covers:

- a clean schema-v3 control tape, which must remain receipt-integrity and
  claim-ready;
- capture failure and rejected-snapshot boundaries, which must invalidate
  capture evidence;
- a receive-sequence gap, which must fail closed before execution can be
  considered claim-ready;
- a regressing receive-monotonic clock, which must invalidate the clock and
  claim gate;
- a truncated segmented capture, where only the checksummed prefix is
  recoverable and no manifest is published; and
- payload checksum corruption, which must produce a failed segment report and
  no recoverable event.

The command prints `lob_sim.fault_injection.v1` JSON and exits non-zero if any
observed result differs from its expected fail-closed outcome. This is a
correctness and recovery demonstration, not evidence of zero venue-side packet
loss, private fill truth, production readiness, or live profitability.
