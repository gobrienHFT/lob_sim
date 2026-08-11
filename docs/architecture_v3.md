# Schema-v3 architecture and event priority

The canonical pipeline is:

`capture -> validate -> normalize -> Python oracle / Rust kernel -> venue scenario -> risk/accounting -> bounded sinks -> manifest`

Each envelope carries a capture ID, schema version, venue/instrument, route,
global receipt sequence, wall and monotonic receipt clocks, optional exchange
timestamps, stream/sync epochs, payload checksum and raw payload. Validity is
tracked independently for book, trade stream, clock and capture; execution is
valid only at their intersection.

For schema-v3 replay, the logical priority at equal receipt time is:

1. Drain actions strictly earlier than the next observation.
2. Apply market observations in receipt-sequence order.
3. Update marks and markouts.
4. Let the strategy observe the resulting causal state.
5. Apply actions due exactly at that time.

Legacy v1 tapes retain their documented action-first compatibility policy and
are never mixed into a claim-ready markout report.

`NullSink` and streaming sinks make the trace optional. The kernel must not
retain an unbounded event list; a CSV trace is an explicit audit choice.

The current cross-language differential boundary covers logical-time and
fixed-point book primitives, exact-synthetic MBO new/cancel/replace lifecycle
results, an integer-nanosecond action scheduler, and per-symbol worst-case
live-plus-pending lot reservations. Transition results and periodic full-state
hashes agree across the independent Python and Rust implementations. These
primitives are not yet the engine's end-to-end scheduler or portfolio-notional
risk path. The public-L2 scenario venue, engine integration, accounting,
markouts and run manifests remain outside the parity claim, so artifacts keep
`full_engine_parity=false`.
