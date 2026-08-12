# Rust kernel boundary

`rust/lob_core` is the deliberately small, unsafe-free hot-path boundary. It
provides fixed-point atomic book primitives and an exact synthetic MBO matcher
behind PyO3 bindings. The reviewer gate differentially compares the independent
Python oracle and Rust for generated book batches, synthetic new/cancel/replace
lifecycles, integer-nanosecond scheduler transitions, per-symbol
live-plus-pending lot reservations, cross-symbol gross-notional reservations,
fixed-point fill accounting, nullable mark valuation, signed markouts,
fixed/empirical/stress-tail scenario-latency sampler traces, and periodic
full-state hashes.

This is not full-engine parity. The scheduler and reservation ledgers are proven
kernel primitives, as is the cross-language latency sampler. They are not yet
the engine-integrated scheduler, latency path, or portfolio-notional risk
system. Public-L2 execution scenarios, engine-integrated accounting/markouts,
and manifests remain named explicitly in the parity report.
