# Python / Rust differential evidence

[`rust_python_parity_v3.json`](rust_python_parity_v3.json) is the current committed,
deterministic result checked by the reviewer gate. It compares the independent
Python oracle and the Rust kernel over:

- 10,000 logical-time cases;
- 10,000 generated atomic fixed-point book batches, including invalid batches;
- 10,000 generated exact-synthetic MBO operations (6,633 new, 1,895 cancel,
  and 1,472 replace);
- 10,000 generated scheduler operations covering strict/inclusive drains,
  exact logical-time ties, cancellation, duplicate identity rejection, and
  stable insertion priority;
- 10,000 generated per-symbol reservation operations covering live and pending
  exposure, cancel acknowledgements, partial fills, invalid transitions, and
  epoch invalidation;
- every lifecycle result, fill, drain, reservation decision, position, and
  outstanding reservation total from those operations; and
- 39 independently computed full-state hash checkpoints per stateful trace.

[`rust_python_parity_v2.json`](rust_python_parity_v2.json) is retained as the
immutable predecessor evidence for the earlier synthetic-exchange boundary.

Refresh the report with the pinned Rust toolchain:

```bash
python scripts/check_rust_python_parity.py --cases 10000 --json-out docs/differential_results/rust_python_parity_v3.json
```

The reviewer gate passes the same file through `--expected`, so behavioral or
corpus drift fails until the evidence is reviewed and intentionally refreshed.

This report keeps `full_engine_parity=false`. It does not compare the public-L2
scenario venue, the engine-integrated latency path, portfolio-notional risk,
accounting/markouts, or run manifests, and it does not claim historical Binance
FIFO. Scheduler and reservation results are kernel-primitive proofs, not a
claim that the whole Python engine already executes through Rust.
