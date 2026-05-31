# Replay Contract

## Recorded Event Schema

Replay inputs are newline-delimited JSON. Each row has:

- `ts_local`: event timestamp in seconds.
- `symbol`: venue symbol, for example `BTCUSDT`.
- `type`: one of `exchangeInfo`, `snapshot`, `depthUpdate`, or `aggTrade`.
- `data`: raw or normalized payload for that event type.

The reader validates required fields before replaying. Bad JSON, missing payload fields, malformed price/quantity levels, and unsupported event types fail with file and line-number context.

After validation, `lob_sim.replay.adapters.DEFAULT_REPLAY_ADAPTER` is the shared feed-adapter boundary used by replay, simulation, and benchmark code. The default `BinanceUsdMReplayAdapter` delegates to `lob_sim.replay.normalization` and turns rows into `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` using integer ticks/lots before downstream book or fill logic sees them. `InstrumentSpec` rejects empty symbols, non-positive tick sizes, non-positive lot sizes, and non-finite multipliers at this boundary. `SimulationEngine` and the replay runner accept an injected adapter for future L2 venues without changing queue/fill mechanics.

## Event Types

- `exchangeInfo`: contains positive `tickSize` and `stepSize`; optional `baseAsset`, `quoteAsset`, positive finite `contractMultiplier`, and `venue` fields carry instrument metadata used by reporting and fee audit fields.
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

The simulation summary includes event-count diagnostics for processed replay rows, accepted depth-change counts, book-sync gap counts, fill-source counts, order-lifecycle counts, and self-trade-prevention counts. Those fields make it visible when a run skipped gap-affected depth data, relied on depth-inferred fills, posted quotes that never arrived, expired a marketable remainder, or prevented a strategy own-cross instead of silently advancing the book.

Summaries also include `public_consumption_summary`: observed lots from public depth reductions and `aggTrade` prints, lots eligible for modeled queue consumption after overlap reconciliation, lots actually consumed from the internal FIFO queue, lots netted away inside the overlap window, and unmatched lots when no internal queue remained at that level. This makes the cancel-vs-trade and book-divergence ambiguity explicit even when the strategy receives no fill.

The event trace CSV is the compact event-time audit trail: replay records, strategy decisions, scheduled order arrivals, cancel requests and acknowledgements, book gaps, and fills share one timestamped sequence. Arrival rows include resting-state queue-ahead metadata; cancel-request rows include the reason and replacement context when a quote is being refreshed.

Decision rows include strategy diagnostics in `details`: decision reason when present, best bid/ask ticks, mid, inventory, volatility, quote-size lots, spread inputs, imbalance inputs, and profile-specific gate state when available. These fields are intended for offline audit of why a quote target was chosen or why live quotes were pulled.

Summaries and manifests include the adapter-normalized `instrument_specs` block for each replayed symbol: venue, price currency, quantity unit, tick size, lot size, and contract multiplier. This makes the units behind inventory, notional, fee, and markout math visible in the generated artifacts.

Trades CSV rows include fill source, normalized quantity, instrument notional, contract multiplier, maker/taker fee rate, fee amount, and fee currency. PnL, spread capture, fees, and markout are multiplier-adjusted; inventory remains in normalized quantity units.

Committed event traces are semantically checked by `scripts/verify_committed_artifacts.py`: row counts must match `summary["event_trace_count"]`, sequence numbers must be contiguous, rows must stay in event-time order, `details` must be JSON objects, fill rows must match `summary["fill_count"]` with a recognized fill source, and order lifecycle counters must agree with the trace rows.

The manifest records:

- input path, size, modified time, and SHA-256 digest;
- non-secret replay and strategy configuration;
- replay feed-adapter name, venue label, and supported record types;
- normalized instrument metadata by symbol;
- Python/platform/runtime metadata;
- git branch, commit, and dirty-worktree flag when available;
- output paths and a deterministic `run_id` derived from input digest, simulator version, config, and feed adapter.
- output artifact size and SHA-256 metadata for generated summary, summary CSV, trades CSV, and event trace CSV files.

Committed futures sample manifests are verified with `source.git_dirty == false`; if a pack is refreshed while another generated file is still dirty, `scripts/verify_committed_artifacts.py` fails instead of publishing ambiguous provenance.

The manifest is provenance metadata. It is not a latency or production-readiness claim.
