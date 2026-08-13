# Rust kernel boundary

`rust/lob_core` is the deliberately small, unsafe-free hot-path boundary. It
provides fixed-point atomic book primitives and an exact synthetic MBO matcher
behind PyO3 bindings. The reviewer gate differentially compares the independent
Python oracle and Rust for generated book batches, synthetic new/cancel/replace
lifecycles, a restricted single-price public-L2 trade queue-ahead transition,
integer-nanosecond scheduler transitions, per-symbol
live-plus-pending lot reservations, cross-symbol gross-notional reservations,
fixed-point fill accounting, nullable mark valuation, signed markouts,
fixed/empirical/stress-tail scenario-latency sampler traces, and periodic
full-state hashes.

This is not full-engine parity. The scheduler and reservation ledgers are proven
kernel primitives, as is the cross-language latency sampler. They are not yet
the engine-integrated scheduler, latency path, or portfolio-notional risk
system. Engine-integrated public-L2 execution scenarios beyond the restricted
single-price trade queue, engine-integrated accounting/markouts, and manifests
remain named explicitly in the parity report. The restricted queue boundary is
not participant FIFO, depth/trade overlap reconciliation, or historical fill
truth.
