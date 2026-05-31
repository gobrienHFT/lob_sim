# Replay Contract

## Recorded Event Schema

Replay inputs are newline-delimited JSON. Each row has:

- `ts_local`: event timestamp in seconds.
- `symbol`: venue symbol, for example `BTCUSDT`.
- `type`: one of `exchangeInfo`, `snapshot`, `depthUpdate`, or `aggTrade`.
- `data`: raw or normalized payload for that event type.

The reader validates required fields before replaying. Bad JSON, missing payload fields, malformed price/quantity levels, and unsupported event types fail with file and line-number context.

After validation, `lob_sim.replay.normalization` is the shared conversion boundary used by replay, simulation, and benchmark code. It turns rows into `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` using integer ticks/lots before downstream book or fill logic sees them.

## Event Types

- `exchangeInfo`: contains `tickSize` and `stepSize`; optional `baseAsset`, `quoteAsset`, and `venue` fields carry instrument metadata used by reporting and fee audit fields.
- `snapshot`: contains `lastUpdateId`, `bids`, and `asks`.
- `depthUpdate`: contains Binance diff ids `U`, `u`, optional `pu`, and changed bid/ask levels `b` and `a`.
- `aggTrade`: contains trade price `p`, quantity `q`, and maker side flag `m`.

## Stream Inspection

Use the inspection command before replaying unfamiliar data:

```bash
python -m lob_sim.cli inspect --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson
```

It reports record counts, symbols, event-time span, symbol tick/lot metadata, file size, and a SHA-256 digest of the exact input bytes.

## Simulation Manifests

Every futures simulation writes a manifest next to `summary_*.json`, `summary_*.csv`, `trades_*.csv`, and `event_trace_*.csv`.

The simulation summary includes event-count diagnostics for processed replay rows, accepted depth-change counts, book-sync gap counts, fill-source counts, and self-trade-prevention counts. Those fields make it visible when a run skipped gap-affected depth data, relied on depth-inferred fills, or prevented a strategy own-cross instead of silently advancing the book.

The event trace CSV is the compact event-time audit trail: replay records, strategy decisions, scheduled order arrivals, cancel requests and acknowledgements, book gaps, and fills share one timestamped sequence. Arrival rows include resting-state queue-ahead metadata; cancel-request rows include the reason and replacement context when a quote is being refreshed.

Committed event traces are semantically checked by `scripts/verify_committed_artifacts.py`: row counts must match `summary["event_trace_count"]`, sequence numbers must be contiguous, rows must stay in event-time order, `details` must be JSON objects, and fill rows must match `summary["fill_count"]` with a recognized fill source.

The manifest records:

- input path, size, modified time, and SHA-256 digest;
- non-secret replay and strategy configuration;
- Python/platform/runtime metadata;
- git branch, commit, and dirty-worktree flag when available;
- output paths and a deterministic `run_id` derived from input digest, simulator version, and config.
- output artifact size and SHA-256 metadata for generated summary, summary CSV, trades CSV, and event trace CSV files.

Committed futures sample manifests are verified with `source.git_dirty == false`; if a pack is refreshed while another generated file is still dirty, `scripts/verify_committed_artifacts.py` fails instead of publishing ambiguous provenance.

The manifest is provenance metadata. It is not a latency or production-readiness claim.
