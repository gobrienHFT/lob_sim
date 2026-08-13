# Replay Contract

## Recorded Event Schema

Replay inputs are newline-delimited JSON. Each row has:

- `ts_local`: event timestamp in seconds.
- `symbol`: venue symbol, for example `BTCUSDT`.
- `type`: one of `captureMeta`, `captureEvent`, `exchangeInfo`, `snapshot`, `depthUpdate`, or `aggTrade`.
- `data`: raw or normalized payload for that event type.

The reader validates required fields before replaying. Bad JSON, non-finite timestamps or numeric fields, missing payload fields, malformed price/quantity levels, and unsupported event types fail with file and line-number context.

After validation, `lob_sim.replay.adapters.DEFAULT_REPLAY_ADAPTER` is the shared feed-adapter boundary used by replay, simulation, and benchmark code. The default `BinanceUsdMReplayAdapter` delegates to `lob_sim.replay.normalization` and turns rows into `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` using integer ticks/lots before downstream book or fill logic sees them. `InstrumentSpec` rejects empty symbols, non-positive tick sizes, non-positive lot sizes, and non-finite multipliers at this boundary. `SimulationEngine` and the replay runner accept an injected adapter for future L2 venues without changing queue/fill mechanics.

## Event Types

- `exchangeInfo`: contains positive `tickSize` and `stepSize`; optional `baseAsset`, `quoteAsset`, positive finite `contractMultiplier`, and `venue` fields carry instrument metadata used by reporting and fee audit fields.
- `captureMeta`: declares schema version, receipt-clock policy, independent routes, and validity intersection for schema-v3 captures. Once schema version 3 is declared, every subsequent non-metadata record must carry `recvSeq`, `recvMonotonicNs`, and `route`; receipt identity and `streamEpoch`/`syncEpoch` values must be exact non-negative JSON integers (booleans, strings, and fractional values are rejected rather than coerced). Receipt-monotonic regressions invalidate the clock dimension and halt execution state.
- `captureEvent`: records route lifecycle/failure boundaries, snapshot attempts/rejections that could not be represented as a snapshot row, or the normal capture trailer with its receipt identity and epoch context. `parse_failure` and `overflow` boundaries invalidate the independent capture dimension for subsequent fills, clear live/pending execution state, and halt strategy actions.
- `snapshot`: contains `lastUpdateId`, `bids`, and `asks`. A rejected snapshot may retain raw, off-grid levels with `_capture.snapshotAccepted=false`; replay never applies that payload to the book.
- `depthUpdate`: contains Binance diff ids `U`, `u`, optional `pu`, and changed bid/ask levels `b` and `a`.
- `aggTrade`: contains trade price `p`, quantity `q`, and maker side flag `m`.

## Stream Inspection

Use the inspection command before replaying unfamiliar data:

```bash
python -m lob_sim.cli inspect --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
```

It reports record counts, symbols, event-time span, symbol tick/lot metadata, file size, and a SHA-256 digest of the exact input bytes.

For schema-v3 tapes, inspection also emits bounded `capture_liveness` diagnostics:
receipt coverage, route counts, receive-sequence gaps/regressions, monotonic-clock
regressions and maximum observed inter-arrival, capture lifecycle-event counts,
invalidation-event count, and trailer/completeness state. `receipt_integrity_ok`
means that the recorded receipt identity is internally coherent; it is not proof
that the venue delivered every packet, that the writer experienced no loss before
capture, or that the tape is economically claim-ready. Use replay validity and
the per-symbol execution-input intersection for that stricter conclusion.

`validate` preserves that distinction in its exit status. A structurally valid
schema-v3 tape exits non-zero when receipt identity is incomplete/non-monotonic,
the receive sequence has gaps, the trailer is absent, or a capture-invalidation
event is recorded. Its report says `validation_scope=\"schema_and_capture_receipt\"`;
it still does not claim that the books synchronized or that fills are executable.
Legacy tapes remain compatible and use `validation_scope=\"record_schema_only\"`.

The replay and audit commands add a separate validity reduction.  A schema-v3
run reports receipt-sequence and monotonic-clock checks, capture invalidation
reasons, trailer/completeness evidence, public and market stream epochs, and
whether each symbol has valid execution inputs.  ``claim_ready`` is stricter
than a parser or book-sync success: it requires schema-v3 receive identity,
the receive clock, a complete capture trailer, no capture/clock invalidation,
and valid book/trade inputs for every symbol.  Legacy tapes remain replayable
for compatibility, but their validity output is diagnostic-only and cannot
become claim-ready evidence by omission.

Receipt-sequence gaps are fail-closed boundaries, not merely counters: the
replay validity reducer invalidates capture evidence at the first observed gap
and reports the number of missing sequence values. This catches a missing
record even when later book events happen to reconstruct cleanly.

The simulation engine uses `recvMonotonicNs` from a schema-v3 receipt envelope
as its causal event-time source (represented in seconds for the existing
metrics/export API). The raw wall-clock `ts_local` remains in market-record
trace details for audit. A wall-clock adjustment therefore cannot create a
false schema-v3 clock regression; a receive-monotonic regression still
invalidates the clock dimension. Legacy rows retain their compatibility
`ts_local` policy and remain diagnostic for subsecond claims. The internal
action heap compares integer nanoseconds, with receipt sequence preserved by
insertion order for exact schema-v3 ties. Legacy float rows retain a bounded
sub-nanosecond compatibility component so binary-float arithmetic artefacts do
not reorder an old tape; that component is not a claim of clock precision.

```bash
python -m lob_sim.cli audit --file data/capture_....manifest.json
```

The audit report uses ``lob_sim.capture_audit.v2``.  ``ok`` remains a
backward-compatible convenience flag meaning that the reconstructed books and
currently selected execution inputs are valid.  The top-level ``status`` object
now makes the three different outcomes explicit:

- ``structurally_valid`` means at least one symbol was reconstructed, every
  reconstructed symbol is synced, and no book continuity gap was observed;
- ``execution_inputs_valid`` means the selected capture, clock, stream, and
  trade inputs are valid for the configured scenario;
- ``claim_ready`` is the stricter evidence gate: schema-v3 receipt identity,
  complete trailer, no invalidated boundaries, and valid execution inputs.

This prevents a clean legacy replay (or a structurally valid but invalidated
capture) from being mistaken for claim-ready evidence.  The nested
``replay.validity`` object remains the detailed source of truth.

Schema-v3 validity also carries a bounded ``boundaries`` timeline.  Each row
is anchored to the recorded receive sequence/monotonic timestamp and includes
the route, stream/sync epochs, symbol, transition kind (``recovered`` or
``invalidated``), scope, and reason.  A reconnect, rejected snapshot, clock
regression, capture failure, or replacement of an already-synced book by a
new snapshot is therefore an explicit validity boundary, not a silent repair.
The timeline is capped at 4,096 rows; truncation is reported through
``boundaries_omitted`` and prevents ``claim_ready``.

## Determinism Check

Use the determinism checker when you want one command that proves a replay fixture is stable across repeated simulator runs:

```bash
python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example
```

The checker runs the same input and config multiple times, computes canonical SHA-256 hashes for the metrics summary and event trace, and exits non-zero if any repeated run differs. The event-trace hash is streamed incrementally while retaining only counters and a digest; it preserves the historical canonical JSON-list hash without keeping every row in memory. Its JSON report includes the input digest, config digest, feed-adapter metadata, normalized instrument specs, runtime/source metadata, per-run hashes, event-trace counts, fill counts, and mismatch details.

For an interrupted long replay, use the engine checkpoint API:

```python
engine.run(input_path, checkpoint_path="run.checkpoint.json", stop_after_records=100000)
resumed.run(input_path, resume_from="run.checkpoint.json")
```

The checkpoint is ordinary JSON with a state hash, input SHA-256, behavioral
configuration digest, exact logical time, and the continuation state. The
current `lob_sim.simulation_checkpoint.v2` schema records the integer action
heap key; older v1 checkpoints are rejected rather than resumed under a
different ordering contract. Resume rejects
input or configuration drift and revalidates the skipped prefix. Compare
`state_sha256()`, `event_trace`, and the final metrics summary against an
uninterrupted run. Checkpointing is deliberately restricted to `NullSink`
outputs; a resumed streaming export needs an explicit append/transaction
protocol and is not silently fabricated.

Event-trace causality uses the same exact internal key as the action heap. The
exported `ts_local` remains a compatibility/reporting field; it is not the
ordering authority when large receipt timestamps collapse to the same binary
float.

Depth/trade overlap reconciliation follows the same rule for schema-v3
observations: expiry and netting use the integer receipt nanosecond key passed
from the engine, not the derived float seconds. Direct legacy callers may omit
that key and use a rounded compatibility conversion; they remain diagnostic
for subsecond claims.

## Pack Audit

Use the pack auditor when you want to check a generated futures pack without reading each CSV by hand:

```bash
python scripts/audit_futures_pack.py --committed-futures
```

It verifies that each pack's replay input, `summary.json`, `summary.csv`, `trades.csv`, `event_trace.csv`, and `manifest.json` agree on record/event counts, per-fill economics, fill-source counts, order lifecycle counters, queue-ahead-at-arrival metrics, public queue-consumption totals, source-split markouts, markout event details, public-data assumptions, and output artifact hashes. It also resolves every exported fill evidence ID against the immutable replay input and rejects inconsistent validity intersections, queue trajectories, latency metadata, lifecycle states, and unsupported measured-latency claims.

## Simulation Manifests

Every ordinary futures simulation writes a unique run directory containing
`summary.json`, `summary.csv`, `trades.csv`, `markouts.csv`, `event_trace.csv`,
and `manifest.json`. The three row-level audits stream through fixed-schema
`.partial` files and are fsynced before promotion. `_INCOMPLETE.json` remains
until every declared artifact and its manifest exist. Absence of the manifest
or presence of that sentinel means the bundle is incomplete. The explicit
`--in-memory-export` option retains the historical stem-based fixture layout.

The low-level reader may recover a fully checksummed prefix from a visible
capture `.partial` segment for forensic replay. `SimulationEngine.run` rejects
such a path before processing records, so economic results cannot silently
present an incomplete capture as a finalized tape.

The simulation summary includes event-count diagnostics for processed replay rows, accepted depth-change counts, book-sync gap counts, fill-source counts, order-lifecycle counts, queue-ahead-at-arrival counts, kill-switch state, optional portfolio-notional reservations, and self-trade-prevention counts. Those fields make it visible when a run skipped gap-affected depth data, relied on depth-inferred fills, posted quotes that never arrived, rested behind visible queue, expired a marketable remainder, halted on configured risk limits, rejected a quote because gross marked inventory plus live/pending orders exceeded the configured portfolio cap, or prevented a strategy own-cross instead of silently advancing the book.

Summaries and manifests include `simulation_assumptions`: the structured public-data contract for the run. It states that the simulator uses public L2 and aggregate-trade records only, does not claim private exchange execution reports, uses synthetic queue-ahead assumptions, keeps cancel latency explicit, records the selected fill-assumption profile, and records the overlap-netting window used to reduce double counting between depth reductions and trade prints.

Summaries also include `public_consumption_summary`: observed lots from public depth reductions and `aggTrade` prints, lots eligible for modeled queue consumption after overlap reconciliation, lots actually consumed from the synthetic queue, lots netted away inside the overlap window, and unmatched lots when no internal queue remained at that level. This makes the cancel-vs-trade and book-divergence ambiguity explicit even when the strategy receives no fill.

`fill_assumption_diagnostics.overlap_credit_state` reports active overlap-credit
keys, active credits, expiry entries, and the configured window. Credits are
expired globally rather than only when the same price is seen again, making the
reconciliation state bounded by the overlap window on one-sided tapes.

Use [`docs/fill_assumption_envelope.md`](fill_assumption_envelope.md) when you want the same input replayed under conservative/base/aggressive public-L2 fill assumptions. Public L2 cannot prove private fills; robust conclusions should survive the envelope.

The event trace CSV is the causal-time audit trail: replay records, strategy decisions, public `queue_consumption` rows, scheduled order arrivals, cancel requests and acknowledgements, book gaps, risk halts, fills, and post-horizon `markout` rows share one monotonically emitted sequence. For schema-v3 rows, receipt identity and capture/stream validity are checked before due actions are drained; a malformed observation therefore cannot execute an order scheduled earlier in the same logical interval. A regressing legacy input clock is clamped to the prior logical time and the raw value is retained as `observed_ts_local` with `clock_clamped=true`; streaming export never relies on an after-the-fact in-memory sort. `queue_consumption` rows tie each public depth/trade signal at a price level to observed lots, overlap-netted lots, modeled queue-consumption candidates, actual synthetic queue lots consumed, and unmatched lots. Fill rows carry `lob_sim.fill_provenance.v1`: scenario ID, resolvable decision/arrival/trigger evidence IDs, source-specific validity, queue trajectory, configured latency draws, non-measured latency-model label, lifecycle state, fee-model ID, notional, fee, spread capture, mid-at-fill, time-in-book, regime, and local book ticks. `markout` rows tie each fill to the later mid used for signed adverse-selection measurement, including the horizon, fill source, side, quantity, markout, and adverse flag. Arrival rows include resting-state queue-ahead metadata; summaries aggregate those arrival queue-position samples separately from fill-time residual queue ahead. Cancel-request rows include the reason and replacement context when a quote is being refreshed. Risk-halt rows include the configured trigger reason, current PnL/drawdown state, and the live/pending strategy state cleared when trading stops.

Decision rows include strategy diagnostics in `details`: decision reason when present, best bid/ask ticks, mid, inventory, volatility, quote-size lots, spread inputs, imbalance inputs, and profile-specific gate state when available. These fields are intended for offline audit of why a quote target was chosen or why live quotes were pulled.

Summaries and manifests include the adapter-normalized `instrument_specs` block for each replayed symbol: venue, price currency, quantity unit, tick size, lot size, and contract multiplier. This makes the units behind inventory, notional, fee, and markout math visible in the generated artifacts.

Valuation is fail-closed. Every non-zero position must have both instrument
metadata and a current midpoint from its active book before `total_pnl` or
`unrealized_pnl` is numeric. If the book is absent, stale, uncrossed, or has no
midpoint, the symbol appears in `missing_mark_symbols`, inventory remains
visible when its instrument metadata is known, and the PnL fields are `null`
rather than silently reporting zero.

Trades CSV rows include the same structured fill-provenance fields as the trace plus fill source, normalized quantity, instrument notional, contract multiplier, maker/taker fee rate, fee amount, fee currency, and per-fill spread capture. Nested evidence, validity, queue, and latency fields are canonical JSON rather than Python representations. PnL, spread capture, fees, and markout are multiplier-adjusted; inventory remains in normalized quantity units.

Completed bounded manifests add a content-addressed SHA-256 over the finalized
non-manifest audit artifacts; the pack auditor recomputes it after checking
each file.

Summaries include `markout_by_fill_source`, splitting markout sample count, adverse sample count, quantity, average signed markout, and adverse-fill rate across `depth_update`, `agg_trade`, and `taker_order` fills. This makes it easier to audit whether a run's fill-quality story is coming from inferred passive fills or explicit taker execution.

When `SIM_ADVERSE_MARKOUT_SECONDS` is enabled, the configured
`SIM_MARKOUT_HORIZONS_MS` set is resolved independently at the first eligible
post-fill midpoint observed at or after each deadline. Summary
`markout_horizon_summary` reports resolved, invalidated, unresolved and covered
samples, signed markout, adverse rate, and actual observation lag in
milliseconds for each horizon. The historical one-second markout remains the
detailed CSV/trace audit surface; additional horizons are aggregate-only and
are bounded by the configured pending-markout capacity times the finite
horizon count. A gap or stream epoch invalidation removes every affected
horizon from claimable coverage.

Committed event traces are semantically checked by `scripts/verify_committed_artifacts.py`: row counts must match `summary["event_trace_count"]`, sequence numbers must be contiguous, rows must stay in event-time order, `details` must be JSON objects, fill rows must match `summary["fill_count"]` with a recognized fill source, and order lifecycle counters must agree with the trace rows.

The manifest records:

- input path, size, modified time, and SHA-256 digest;
- non-secret replay and strategy configuration;
- replay feed-adapter name, venue label, and supported record types;
- normalized instrument metadata by symbol;
- structured simulation assumptions and public-data limitations;
- Python/platform/runtime metadata;
- git branch, commit, and dirty-worktree flag when available;
- output paths and a deterministic `run_id` derived from input digest, simulator version, config, and feed adapter.
- output artifact size and SHA-256 metadata for generated summary, summary CSV,
  trades CSV, markouts CSV, and event trace CSV files.

Committed futures sample manifests are verified with `source.git_dirty == false`; if a pack is refreshed while another generated file is still dirty, `scripts/verify_committed_artifacts.py` fails instead of publishing ambiguous provenance.

The manifest is provenance metadata. It is not a latency or production-readiness claim.
