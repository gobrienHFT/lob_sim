# Replay Contract

## Recorded Event Schema

Replay inputs are newline-delimited JSON. Each row has:

- `ts_local`: event timestamp in seconds.
- `symbol`: venue symbol, for example `BTCUSDT`.
- `type`: one of `exchangeInfo`, `snapshot`, `depthUpdate`, or `aggTrade`.
- `data`: raw or normalized payload for that event type.

The reader validates required fields before replaying. Bad JSON, missing payload fields, malformed price/quantity levels, and unsupported event types fail with file and line-number context.

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

Every futures simulation writes a manifest next to `summary_*.json`, `summary_*.csv`, and `trades_*.csv`.

The manifest records:

- input path, size, modified time, and SHA-256 digest;
- non-secret replay and strategy configuration;
- Python/platform/runtime metadata;
- git branch, commit, and dirty-worktree flag when available;
- output paths and a deterministic `run_id` derived from input digest, simulator version, and config.
- output artifact size and SHA-256 metadata for generated summary, summary CSV, and trades CSV files.

The manifest is provenance metadata. It is not a latency or production-readiness claim.
