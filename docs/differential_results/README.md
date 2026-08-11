# Python / Rust differential evidence

[`rust_python_parity_v2.json`](rust_python_parity_v2.json) is the committed,
deterministic result checked by the reviewer gate. It compares the independent
Python oracle and the Rust kernel over:

- 10,000 logical-time cases;
- 10,000 generated atomic fixed-point book batches, including invalid batches;
- 10,000 generated exact-synthetic MBO operations (6,633 new, 1,895 cancel,
  and 1,472 replace);
- every lifecycle result and fill from those synthetic operations; and
- 39 independently computed full-state hash checkpoints.

Refresh the report with the pinned Rust toolchain:

```bash
python scripts/check_rust_python_parity.py --cases 10000 --json-out docs/differential_results/rust_python_parity_v2.json
```

The reviewer gate passes the same file through `--expected`, so behavioral or
corpus drift fails until the evidence is reviewed and intentionally refreshed.

This report keeps `full_engine_parity=false`. It does not compare the public-L2
scenario venue, latency scheduler, risk reservations, accounting/markouts or
run manifests, and it does not claim historical Binance FIFO.
