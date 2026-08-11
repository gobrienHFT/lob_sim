# Schema-v3 architecture and event priority

The canonical pipeline is:

`capture -> validate -> normalize -> Python oracle / Rust kernel -> venue scenario -> risk/accounting -> bounded sinks -> manifest`

Each envelope carries a capture ID, schema version, venue/instrument, route,
global receipt sequence, wall and monotonic receipt clocks, optional exchange
timestamps, stream/sync epochs, payload checksum and raw payload. Validity is
tracked independently for book, trade stream, clock and capture; execution is
valid only at the intersection required by the selected venue and strategy
scenario.

Every exported fill uses `lob_sim.fill_provenance.v1`. The record includes the
public-L2 scenario ID, the order-decision/arrival and triggering market-record
IDs, the source-specific validity intersection, the synthetic queue trajectory,
the actual configured new/cancel latency draws, the order lifecycle state at
the fill, and the fee-model ID. Schema-v3 manifest captures use a
capture/receive-sequence/payload-checksum identity. Legacy fixtures use a stable
input-row identity and are labeled as such. These IDs make a modeled fill
replayable against immutable input; they do not turn public L2 into a private
execution report or measured latency evidence.

Stream lifecycle records are causal boundaries, not logging decoration.
Schema-v3 capture records `connect`, `disconnect`, `connect_failure`, and
`parse_failure` events before retrying, plus a final `capture_trailer` only on
normal completion. Receipt sequence and monotonic time are assigned immediately
after websocket receipt and before JSON or venue parsing; a parse-failure record
therefore retains the identity of the failed observation. A public-stream outage invalidates the
book and all dependent execution state. A market-stream outage preserves an
independently valid depth book, clears stale trade-flow history, and invalidates
live/pending state when the chosen fill or strategy scenario requires trades.
Repeated failure records do not double-invalidate. A new stream epoch resumes
prospectively only after a connect record (or an explicitly handled legacy
implicit boundary); no queue or order state crosses the outage. Regressing
stream or sync epochs are rejected.

Disk and compression work is serialized on one dedicated writer thread. The
producer uses a non-blocking queue with a configured hard capacity. A full queue
or sink exception is an integrity failure: collection stops, no success manifest
is written, a checksummed-record prefix remains recoverable from the visible
`.partial` segment, and a sanitized hashed failure sidecar records the failure
types and writer counters. Normal shutdown drains the queue, persists the
capture trailer, closes the writer, and only then finalizes segments and a
manifest containing queue capacity, queue/outstanding high-water, maximum
enqueue-to-write lag, counts, and completion state. These mechanics do not establish a zero-drop or
24-hour-soak claim by themselves.

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
