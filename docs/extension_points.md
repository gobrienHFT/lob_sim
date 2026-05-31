# Extension Points

The futures replay core is intentionally built around normalized events and integer ticks/lots so new venues or asset classes can be added without rewriting the simulation engine.

## Current Boundaries

- `lob_sim.book.types.InstrumentSpec`: tick size, lot size, optional currency/unit metadata, contract multiplier, and venue label. Tick size, lot size, multiplier, and symbol identity are validated before normalized events can enter replay or simulation state. `SymbolSpec` remains as a compatibility alias.
- `lob_sim.record.schema`: normalized replay row contract for `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade`.
- `lob_sim.replay.adapters`: explicit feed-adapter boundary. The default `BinanceUsdMReplayAdapter` maps the committed Binance USD-M-style record contract into normalized events, and tests show the engine can accept an injected adapter without changing queue/fill logic.
- `lob_sim.replay.normalization`: single conversion boundary from validated replay rows into `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent`.
- `lob_sim.binance.*`: Binance USD-M REST/websocket adapter that translates venue payloads into normalized events.
- `lob_sim.book.sync`: venue sequence semantics and gap policy.
- `lob_sim.book.local_book`: deterministic local depth state in integer ticks/lots.
- `lob_sim.sim.fill_model`: queue-aware fill attribution over normalized book changes and public trade prints.
- `lob_sim.sim.fees`: explicit maker/taker fee assessment over normalized fills and instrument metadata.
- `lob_sim.sim.metrics`: inventory, fee, markout, queue, and risk metrics.

## Adding Another L2 Venue

1. Add an adapter package beside `lob_sim/binance/`.
2. Translate venue metadata into `InstrumentSpec`.
3. Implement a `ReplayFeedAdapter` that returns the same `InstrumentSpec`, `SnapshotEvent`, `DepthUpdateEvent`, and `AggTradeEvent` outputs as the default Binance USD-M adapter.
4. Translate snapshots and incremental updates into the replay record contract, or into a venue-specific sibling contract behind that adapter.
5. Write a feed-semantics doc that states sequence rules, snapshot coverage rules, and gap behavior.
6. Confirm summaries, manifests, benchmark metadata, and parameter-sweep reports identify the adapter name, venue label, supported record types, and normalized instrument metadata used for units and multipliers.
7. Add sync tests with stale diffs, first-diff coverage, continuity gaps, and resync/non-resync behavior.
8. Run `python -m lob_sim.cli inspect --file ...` before replaying captured data.

## Adding Another Asset Class

The simulator expects prices and quantities to become integer ticks and lots. Asset-specific work should live at the adapter/config boundary:

- positive finite tick size and lot size;
- price currency and quantity unit;
- positive finite contract multiplier;
- maker/taker fee model inputs;
- venue-specific event ordering and sequence semantics;
- whether public prints are reliable enough to use as a queue-consumption hint.
- how fill sources should be labeled when the venue has different public trade/update semantics.

Do not put venue-specific sequencing, decimal parsing, or product metadata inside `PassiveFillModel`. That layer should stay event-contract driven.

## Fee Model Boundary

Today fees are configured as maker/taker bps in `Config` and applied through `lob_sim.sim.fees.StaticFeeModel`. Rebates are represented as negative fees, taker costs as positive fees, and each fill export records the notional, contract multiplier, fee rate, fee amount, and fee currency used. PnL, spread capture, fees, and markout use `InstrumentSpec.contract_multiplier`; inventory remains in normalized quantity units. The current model is intentionally static; a venue adapter can replace it with a richer fee schedule object with:

- maker/taker rates by product tier;
- rebates expressed explicitly as negative fees;
- currency of fee charging;
- multiplier handling for derivatives or tokenized products.

Keep fee assumptions and instrument units in manifests and summaries. Fee-aware spread floors should remain visible and configurable rather than hidden inside a strategy.

## What Not To Generalize Yet

- Do not introduce a production gateway abstraction; collection is public-data capture only.
- Do not model private queue IDs unless the data source actually provides them.
- Do not treat every venue's trade feed as equivalent to Binance USD-M `aggTrade`; document the observed semantics first.
