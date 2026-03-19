# Binance USD-M Feed Semantics

## Scope

This repo records and replays public Binance USD-M market data. The futures core is built around four record types: `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade`.

## Snapshot Seeding

- A REST snapshot seeds the local book with `lastUpdateId`, bid levels, and ask levels.
- The book is considered ready after the snapshot arrives, but it is not considered synced until an accepted diff covers that snapshot id.
- The same snapshot also seeds the visible venue queue used by the passive-fill model.

## Depth Diff Continuity and the Role of `U`, `u`, and `pu`

- The first accepted diff must satisfy `U <= lastUpdateId <= u`.
- Diffs with `u < lastUpdateId` are stale and ignored.
- After the first accepted diff, later diffs are expected to continue the sequence with `pu == previous_u`.
- [`lob_sim/book/sync.py`](../lob_sim/book/sync.py) enforces these rules directly.

## What Happens on Gaps

- In live collection, a continuity break can trigger a fresh snapshot when `RESYNC_ON_GAP=1`.
- In offline replay, gaps are counted and surfaced rather than silently patched over.
- In offline simulation, gap-affected depth updates are not used to fabricate continuity. The conservative behavior is to stop trusting that diff chain until a fresh snapshot exists.

## What Is Directly Observed vs Inferred

Directly observed:

- symbol metadata from `exchangeInfo`
- visible book state from REST snapshot
- level updates and sequence ids from `depthUpdate`
- aggregated public trades from `aggTrade`

Inferred:

- queue position of a resting strategy order inside a price level
- whether a level reduction was pure cancel flow, pure trading flow, or a mix
- exact passive fill attribution from public data alone

## Passive Fill Attribution

- Book reductions are treated as queue consumption at that price level.
- `aggTrade` prints are treated as an additional observed execution signal at the traded price.
- Both signals are routed through the FIFO queue model in [`lob_sim/sim/fill_model.py`](../lob_sim/sim/fill_model.py).
- The model only fills a resting strategy order after the queue in front of it has been consumed.

## Known Ambiguities

- Public depth data does not reveal participant-level order ids.
- A depth reduction alone does not separate cancels from trades.
- `aggTrade` is aggregated trade flow, not a full exchange execution log.
- Hidden liquidity, iceberg behavior, and venue-specific matching edge cases are out of scope for this public-data model.
