# Extension Points

The futures replay core is intentionally built around normalized events and integer ticks/lots so new venues or asset classes can be added without rewriting the simulation engine.

## Current Boundaries

- `lob_sim.book.types.InstrumentSpec`: tick size, lot size, optional currency/unit metadata, contract multiplier, and venue label. `SymbolSpec` remains as a compatibility alias.
- `lob_sim.record.schema`: normalized replay row contract for `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade`.
- `lob_sim.binance.*`: Binance USD-M REST/websocket adapter that translates venue payloads into normalized events.
- `lob_sim.book.sync`: venue sequence semantics and gap policy.
- `lob_sim.book.local_book`: deterministic local depth state in integer ticks/lots.
- `lob_sim.sim.fill_model`: queue-aware fill attribution over normalized book changes and public trade prints.
- `lob_sim.sim.fees`: explicit maker/taker fee assessment over normalized fills and instrument metadata.
- `lob_sim.sim.metrics`: inventory, fee, markout, queue, and risk metrics.

## Adding Another L2 Venue

1. Add an adapter package beside `lob_sim/binance/`.
2. Translate venue metadata into `InstrumentSpec`.
3. Translate snapshots and incremental updates into the replay record contract.
4. Write a feed-semantics doc that states sequence rules, snapshot coverage rules, and gap behavior.
5. Add sync tests with stale diffs, first-diff coverage, continuity gaps, and resync/non-resync behavior.
6. Run `python -m lob_sim.cli inspect --file ...` before replaying captured data.

## Adding Another Asset Class

The simulator expects prices and quantities to become integer ticks and lots. Asset-specific work should live at the adapter/config boundary:

- tick size and lot size;
- price currency and quantity unit;
- contract multiplier;
- maker/taker fee model inputs;
- venue-specific event ordering and sequence semantics;
- whether public prints are reliable enough to use as a queue-consumption hint.

Do not put venue-specific sequencing, decimal parsing, or product metadata inside `PassiveFillModel`. That layer should stay event-contract driven.

## Fee Model Boundary

Today fees are configured as maker/taker bps in `Config` and applied through `lob_sim.sim.fees.StaticFeeModel`. Rebates are represented as negative fees, taker costs as positive fees, and each fill export records the fee rate, fee amount, and fee currency used. The current model is intentionally static; a venue adapter can replace it with a richer fee schedule object with:

- maker/taker rates by product tier;
- rebates expressed explicitly as negative fees;
- currency of fee charging;
- multiplier handling for derivatives or tokenized products.

Keep fee assumptions in manifests and summaries. Fee-aware spread floors should remain visible and configurable rather than hidden inside a strategy.

## What Not To Generalize Yet

- Do not introduce a production gateway abstraction; collection is public-data capture only.
- Do not model private queue IDs unless the data source actually provides them.
- Do not treat every venue's trade feed as equivalent to Binance USD-M `aggTrade`; document the observed semantics first.
