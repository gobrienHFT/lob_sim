# Schema-v3 architecture and event priority

The canonical pipeline is:

`capture -> validate -> normalize -> Python oracle / Rust kernel -> venue scenario -> risk/accounting -> bounded sinks -> manifest`

Each envelope carries a capture ID, schema version, venue/instrument, route,
global receipt sequence, wall and monotonic receipt clocks, optional exchange
timestamps, stream/sync epochs, payload checksum and raw payload. Validity is
tracked independently for book, trade stream, clock and capture; execution is
valid only at the intersection required by the selected venue and strategy
scenario. Schema-v3 replay rejects missing receipt identity and fails closed on
a regressing receipt-monotonic clock; logical receipt order is never inferred
from exchange timestamps.

The synchronizer treats a crossed or otherwise invalid depth batch as a hard
epoch boundary: the candidate batch is never exposed, the bad event is not
retained as a future snapshot bridge, and replay must establish a fresh
snapshot plus contiguous updates before execution can resume.

The Python engine keeps seconds at the metrics/export boundary but orders its
internal action heap by `(recv_monotonic_ns, insertion_order)`. This avoids
collapsing distinct large receipt timestamps through binary floating point;
legacy rows use a compatibility sub-nanosecond tie component solely to retain
their historical float ordering. This is an engine scheduling invariant, not a
claim that legacy wall-clock rows have nanosecond accuracy.
Event-trace causality is checked against that same internal key, while exported
`ts_local` remains a reporting field.

Every exported fill uses `lob_sim.fill_provenance.v1`. The record includes the
public-L2 scenario ID, the order-decision/arrival and triggering market-record
IDs, the source-specific validity intersection, the synthetic queue trajectory,
the actual configured new/cancel latency draws, the order lifecycle state at
the fill, and the fee-model ID. Schema-v3 manifest captures use a
capture/receive-sequence/payload-checksum identity. Legacy fixtures use a stable
input-row identity and are labeled as such. These IDs make a modeled fill
replayable against immutable input; they do not turn public L2 into a private
execution report or measured latency evidence.

The Python engine's Decimal accounting follows the same reversal contract as
the fixed-point oracle: a partial close reduces the existing lot count without
changing its cost basis, and only an over-reversal opens a residual position at
the incoming fill price. This is an accounting invariant, not an assertion of
engine-wide Rust parity.

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

Simulation summaries carry the same finalized-capture boundary in
`integrity.capture_trailer_seen` and `integrity.claim_ready`. A schema-v3 run
without the finalized `capture_trailer` remains diagnostic-only even when its
receipt clock and final book happen to look coherent.

The live collector applies the same boundary before snapshot recovery: every
depth failure, including a failed initial connection, clears buffered depth and
opens a sync epoch. A reconnect callback does not open a second epoch when the
failure callback already did; if the failure callback was not observed, the
stream-epoch change itself opens one. This prevents pre-outage observations
from bridging a later REST snapshot.

Disk and compression work is serialized on one dedicated writer thread. The
producer uses a non-blocking queue with a configured hard capacity. A full queue
or sink exception is an integrity failure: collection stops, no success manifest
is written, a checksummed-record prefix remains recoverable from the visible
`.partial` segment, and a sanitized hashed failure sidecar records the failure
types and writer counters. Normal shutdown drains the queue, persists the
capture trailer, closes the writer, and only then finalizes segments and a
manifest containing queue capacity, queue/outstanding high-water, maximum
enqueue-to-write lag, counts, and completion state. These mechanics do not establish a zero-drop or
24-hour-soak claim by themselves. If a replayed tape contains a capture
parse/overflow failure boundary, the independent capture-validity dimension is
set false, all live/pending execution state is cleared, and strategy actions
halt; downstream fills and markouts cannot be execution-valid after that
boundary.

For schema-v3 replay, the logical priority at equal receipt time is:

1. Validate receipt identity and apply capture/stream validity boundaries
   before any due action can execute.
2. Drain actions strictly earlier than the next observation.
3. Apply market observations in receipt-sequence order.
4. Update marks and markouts.
5. Let the strategy observe the resulting causal state.
6. Apply actions due exactly at that time.

Legacy v1 tapes retain their documented action-first compatibility policy and
are never mixed into a claim-ready markout report.

`NullSink` and streaming sinks make the trace optional. Aggregate-only and
bounded-streaming modes retain neither event rows nor resolved fill/markout
rows: they maintain constant-cardinality aggregates plus domain-separated
SHA-256 audit chains. Unresolved markouts are capped by
`SIM_MAX_PENDING_MARKOUTS`; the next fill fails before accounting mutation if
that cap is exhausted.

The legacy detailed markout horizon is `SIM_ADVERSE_MARKOUT_SECONDS`.
Additional horizons in `SIM_MARKOUT_HORIZONS_MS` share the same causal midpoint
observations and are reported in the bounded `markout_horizon_summary`, with
coverage, invalidation and observation-lag fields. They do not create a second
unbounded trace or imply private exchange execution evidence.

The Python engine also exposes a JSON-only checkpoint contract for long
replays. `SimulationEngine.run(..., checkpoint_path=..., stop_after_records=N)`
writes a fsynced, hashed continuation state; a fresh engine can continue it
with `resume_from=...`. The checkpoint includes books, sync epochs, active
orders, pending actions, markout state, accounting aggregates, strategy
features, explicit SplitMix64 scenario-latency sampler state, and input/config
digests. Resume
revalidates the complete input prefix before continuing, then the uninterrupted
and resumed paths must produce identical state, trace, and summary hashes.
Checkpoint resume is currently a kernel/state contract for `NullSink` outputs;
streaming audit files are intentionally not appended implicitly because doing
so without an explicit sink-transaction protocol would risk duplicate rows.

Ordinary simulation uses a unique run directory and three fixed-schema CSV
sinks for the causal event trace, fills, and markouts. They write `.partial`
files, flush and fsync in a prepare phase, and promote only after every sink has
prepared. `_INCOMPLETE.json` is the visible transaction sentinel; it is removed
only after atomic summary writes and the artifact-hashing manifest succeed.
Legacy clock regressions are clamped before trace emission and retain their raw
timestamp as explicit trace evidence. The fixture-scale compatibility exporter
requires `--in-memory-export`, declares linear retention, and remains available
for small committed packs. Benchmark export mode exercises the bounded path.

A schema-v3 clock regression, including a receipt-monotonic regression, is an
integrity boundary, not a recoverable execution condition: the first
regression invalidates all pending markout horizons, clears live and scheduled
execution state, and halts further strategy actions. Legacy tapes retain their
compatibility replay trace, but their clock-dependent fills and markouts remain
diagnostic-only.

The real-data evidence path preserves that property end to end. It accepts a
schema-v3 capture manifest (or a legacy NDJSON tape), runs the bounded exporter,
copies all three audits through fsynced partial files, and publishes a derived
pack only after a second audit oracle succeeds. That oracle reparses the CSVs,
recomputes both domain-separated chains, correlates fills and markouts against
the trace in order, and resolves evidence IDs exactly through a temporary
on-disk SQLite index. It caps diagnostics on corrupt input and retains no detail
rows in Python memory. A failed derived-pack audit recreates `_INCOMPLETE.json`.

Completed bounded manifests also carry `lob_sim.artifact_bundle.v1`: a
content-addressed SHA-256 over the finalized non-manifest artifact labels,
sizes, and hashes. The independent auditor recomputes it from the bytes on
disk, so copying a pack changes paths but not its evidence identity, while any
post-run edit fails the digest check.

Run manifests also publish a SHA-256 of the non-secret behavioral configuration
and a streamed `lob_sim.code_identity.v1` over tracked repository files. These
are provenance identities, not a claim that a clean Git label alone proves
semantic equivalence or that the repository contains proprietary venue code.

Capture-segment and normalized-Arrow file digests use the same incremental
hashing rule; a 256 MiB segment or a large normalized tape is never loaded as a
single Python bytes object merely to produce provenance.

The reader can recover the fully checksummed prefix of a visible `.partial`
segment for forensic inspection. The economic simulator rejects that path
before processing any records; only finalized capture segments/manifests are
eligible for claim-bearing simulation.

The current cross-language differential boundary covers logical-time and
fixed-point book primitives, exact-synthetic MBO new/cancel/replace lifecycle
results, an integer-nanosecond action scheduler, per-symbol worst-case
live-plus-pending lot reservations, cross-symbol gross-notional
reservations over externally marked inventory, fixed-point fill accounting,
nullable mark valuation, signed markouts, and fixed/empirical/stress-tail
scenario-latency sampler draws. Transition results and periodic
full-state hashes agree across the independent Python and Rust implementations.
These primitives are not yet the engine's end-to-end scheduler, latency path,
portfolio-notional risk path, or accounting path. The public-L2 scenario venue,
engine integration, and run manifests remain outside the parity claim, so artifacts keep
`full_engine_parity=false`.

The reviewer CLI's `demo` command also runs a compact exact-synthetic MBO
scenario. Because that venue owns participant and order identity, its
price-time sequence is an executable ground-truth check for the synthetic mode
only; the output is kept beside, and explicitly separated from, the inferred
public-L2 execution result.
