# Rust kernel boundary

`rust/lob_core` is the deliberately small, unsafe-free hot-path boundary. It
currently provides fixed-point book primitives and a PyO3 feature-gated module;
the Python implementation remains the independent readable oracle. Build and
differential parity are a later gated milestone and must not be claimed until a
pinned toolchain has run the golden/property corpus.
