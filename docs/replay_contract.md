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
- `captureMeta`: declares schema version, receipt-clock policy, independent routes, and validity intersection for schema-v3 captures.
- `captureEvent`: records route lifecycle/failure boundaries, snapshot attempts/rejections that could not be represented as a snapshot row, or the normal capture trailer with its receipt identity and epoch context.
- `snapshot`: contains `lastUpdateId`, `bids`, and `asks`. A rejected snapshot may retain raw, off-grid levels with `_capture.snapshotAccepted=false`; replay never applies that payload to the book.
- `depthUpdate`: contains Binance diff ids `U`, `u`, optional `pu`, and changed bid/ask levels `b` and `a`.
- `aggTrade`: contains trade price `p`, quantity `q`, and maker side flag `m`.

## Stream Inspection

Use the inspection command before replaying unfamiliar data:

```bash
python -m lob_sim.cli inspect --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
```

It reports record counts, symbols, event-time span, symbol tick/lot metadata, file size, and a SHA-256 digest of the exact input bytes.

## Determinism Check

Use the determinism checker when you want one command that proves a replay fixture is stable across repeated simulator runs:

```bash
python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example
```

The checker runs the same input and config multiple times in memory, computes canonical SHA-256 hashes for the metrics summary and event trace, and exits non-zero if any repeated run differs. Its JSON report includes the input digest, config digest, feed-adapter metadata, normalized instrument specs, runtime/source metadata, per-run hashes, event-trace counts, fill counts, and mismatch details.

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

The simulation summary includes event-count diagnostics for processed replay rows, accepted depth-change counts, book-sync gap counts, fill-source counts, order-lifecycle counts, queue-ahead-at-arrival counts, kill-switch state, and self-trade-prevention counts. Those fields make it visible when a run skipped gap-affected depth data, relied on depth-inferred fills, posted quotes that never arrived, rested behind visible queue, expired a marketable remainder, halted on configured risk limits, or prevented a strategy own-cross instead of silently advancing the book.

Summaries and manifests include `simulation_assumptions`: the structured public-data contract for the run. It states that the simulator uses public L2 and aggregate-trade records only, does not claim private exchange execution reports, uses synthetic queue-ahead assumptions, keeps cancel latency explicit, records the selected fill-assumption profile, and records the overlap-netting window used to reduce double counting between depth reductions and trade prints.

Summaries also include `public_consumption_summary`: observed lots from public depth reductions and `aggTrade` prints, lots eligible for modeled queue consumption after overlap reconciliation, lots actually consumed from the synthetic queue, lots netted away inside the overlap window, and unmatched lots when no internal queue remained at that level. This makes the cancel-vs-trade and book-divergence ambiguity explicit even when the strategy receives no fill.

`fill_assumption_diagnostics.overlap_credit_state` reports active overlap-credit
keys, active credits, expiry entries, and the configured window. Credits are
expired globally rather than only when the same price is seen again, making the
reconciliation state bounded by the overlap window on one-sided tapes.

Use [`docs/fill_assumption_envelope.md`](fill_assumption_envelope.md) when you want the same input replayed under conservative/base/aggressive public-L2 fill assumptions. Public L2 cannot prove private fills; robust conclusions should survive the envelope.

The event trace CSV is the causal-time audit trail: replay records, strategy decisions, public `queue_consumption` rows, scheduled order arrivals, cancel requests and acknowledgements, book gaps, risk halts, fills, and post-horizon `markout` rows share one monotonically emitted sequence. A regressing legacy input clock is clamped to the prior logical time and the raw value is retained as `observed_ts_local` with `clock_clamped=true`; streaming export never relies on an after-the-fact in-memory sort. `queue_consumption` rows tie each public depth/trade signal at a price level to observed lots, overlap-netted lots, modeled queue-consumption candidates, actual synthetic queue lots consumed, and unmatched lots. Fill rows carry `lob_sim.fill_provenance.v1`: scenario ID, resolvable decision/arrival/trigger evidence IDs, source-specific validity, queue trajectory, configured latency draws, non-measured latency-model label, lifecycle state, fee-model ID, notional, fee, spread capture, mid-at-fill, time-in-book, regime, and local book ticks. `markout` rows tie each fill to the later mid used for signed adverse-selection measurement, including the horizon, fill source, side, quantity, markout, and adverse flag. Arrival rows include resting-state queue-ahead metadata; summaries aggregate those arrival queue-position samples separately from fill-time residual queue ahead. Cancel-request rows include the reason and replacement context when a quote is being refreshed. Risk-halt rows include the configured trigger reason, current PnL/drawdown state, and the live/pending strategy state cleared when trading stops.

Decision rows include strategy diagnostics in `details`: decision reason when present, best bid/ask ticks, mid, inventory, volatility, quote-size lots, spread inputs, imbalance inputs, and profile-specific gate state when available. These fields are intended for offline audit of why a quote target was chosen or why live quotes were pulled.

Summaries and manifests include the adapter-normalized `instrument_specs` block for each replayed symbol: venue, price currency, quantity unit, tick size, lot size, and contract multiplier. This makes the units behind inventory, notional, fee, and markout math visible in the generated artifacts.

Trades CSV rows include the same structured fill-provenance fields as the trace plus fill source, normalized quantity, instrument notional, contract multiplier, maker/taker fee rate, fee amount, fee currency, and per-fill spread capture. Nested evidence, validity, queue, and latency fields are canonical JSON rather than Python representations. PnL, spread capture, fees, and markout are multiplier-adjusted; inventory remains in normalized quantity units.

Completed bounded manifests add a content-addressed SHA-256 over the finalized
non-manifest audit artifacts; the pack auditor recomputes it after checking
each file.

Summaries include `markout_by_fill_source`, splitting markout sample count, adverse sample count, quantity, average signed markout, and adverse-fill rate across `depth_update`, `agg_trade`, and `taker_order` fills. This makes it easier to audit whether a run's fill-quality story is coming from inferred passive fills or explicit taker execution.

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
