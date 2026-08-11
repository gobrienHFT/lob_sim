# Rust kernel boundary

`rust/lob_core` is the deliberately small, unsafe-free hot-path boundary. It
provides fixed-point atomic book primitives and an exact synthetic MBO matcher
behind PyO3 bindings. The reviewer gate differentially compares the independent
Python oracle and Rust for generated book batches plus synthetic new/cancel/replace
lifecycle results, fills, and periodic full-state hashes.

This is not full-engine parity. Public-L2 execution scenarios, latency
scheduling, risk reservations, accounting/markouts, and manifests remain on the
Python side and are named explicitly in the parity report.
