# Methodology and claim boundaries

This note defines the semantics behind LOB Sim's output. If an output and this document disagree, treat the output as unverified until the discrepancy is resolved.

## 1. State and units

Prices are integer ticks and quantities are integer lots. Inbound venue values must divide exactly by the symbol's tick size and step size; replay does not round malformed market data into validity.

For symbol `s`:

- `price = price_tick * tick_size`
- `base_qty = quantity_lots * step_size`
- best bid must be strictly below best ask whenever both exist
- accepted depth IDs must be nondecreasing and continuous under the sync rules below

Snapshot and diff application is atomic. A negative quantity, nonpositive price tick, or crossed/locked resulting book raises an invariant error before the prior valid state is replaced.

## 2. Book synchronization state machine

Each symbol is in one of three useful states:

```text
UNREADY --buffer diffs--> UNREADY
UNREADY --valid snapshot bridge--> SYNCED
SYNCED  --continuous pu/u diff--> SYNCED
SYNCED  --gap or reconnect--> UNREADY (new epoch, empty book)
```

During bootstrap or resync, the first retained diff must satisfy:

```text
U <= snapshot.lastUpdateId <= u
```

For each subsequent nonduplicate diff:

```text
current.pu == previous.u
```

Old/duplicate events whose `u` does not advance the book are ignored. A failed bridge leaves buffered diffs intact for a newer snapshot attempt. Every accepted/rejected snapshot attempt in schema v2 is labeled in `_capture.snapshotAccepted`.

`RESYNC_ON_GAP=0` disables automatic recovery, not detection. A gap still raises and invalidates the book; the live collector then fails visibly.

## 3. Capture ordering and clocks

Schema v2 records:

- `ts_local`: receive wall time in Unix seconds;
- raw `E` and `T`: unmodified Binance millisecond timestamps;
- `_capture.recvSeq`: global file-order receive sequence;
- `_capture.recvMonotonicNs`: process monotonic observation clock;
- `_capture.streamEpoch`: reconnect count for that WebSocket route;
- `_capture.syncEpoch`: book-validity epoch;
- `_capture.route`: `public` or `market`.

Replay validates that `recvSeq` and `syncEpoch` never regress. A sync-epoch increase invalidates live orders and pending markouts before the record at that boundary is applied.

Legacy files do not have these guarantees. Replay prefers raw exchange event time, normalizes obvious Unix milliseconds, clamps regressions to the prior timestamp, and reports the number clamped. Any resolved subsecond markout from such a clock is diagnostic-only.

## 4. Event/action ordering

File order is observation order. For market record timestamp `t`, all actions with timestamp `< t` run on the previously observed book. The market record is then applied before actions whose timestamp equals `t`.

Consequences:

- a placement acknowledged at `t` cannot fill on the market record already observed at `t`;
- a pending cancel remains fillable until its cancel acknowledgement;
- actions scheduled after capture end are not executed against a frozen final book;
- timer catch-up is capped, turning clock-unit mistakes or very long outages into explicit failures.

## 5. Order lifecycle and risk

One active order per symbol/side is allowed. The lifecycle is:

```text
decision -> placement latency -> post-only/risk validation -> LIVE
LIVE -> cancel request -> PENDING_CANCEL -> cancel latency -> CANCELLED
LIVE or PENDING_CANCEL -> execution -> FILLED
```

Replacement is serial: cancel acknowledgement, then new-order latency. Revisions drop stale scheduled placements. Cancels use the exact order ID, so an old acknowledgement cannot remove a replacement.

At arrival, a bid at or above best ask, or ask at or below best bid, is rejected as post-only. Quantity is floored to the venue lot. Per-side capacity ensures remaining live quantity plus current inventory cannot breach the configured per-symbol position limit in that direction.

## 6. Fill-model uncertainty

The two fill sources are deliberately mutually exclusive.

### Trade source (default)

Let `Q_ahead` be displayed quantity at the order price on arrival. A same-side aggregate trade at exactly that price reduces `Q_ahead`; only excess volume fills the simulated order. A print through the price fills the remainder. Displayed depth decreases are ignored to avoid double-counting the trade and its corresponding book update.

This is conservative relative to assuming every depth decrease is execution, but it is not true FIFO: the venue does not reveal which displayed quantity is ahead of the simulated order.

### Depth source (sensitivity)

Every displayed reduction at the order price reduces `Q_ahead`; aggregate trades are ignored. Because cancellations ahead, cancellations behind, executions, and feed aggregation are not distinguishable, this is labeled optimistic sensitivity.

## 7. Accounting

For fill quantity `q`, fill price `p`, and fee rate `f_bps`:

```text
fee_cost = q * p * f_bps / 10,000
net_realized = gross_realized - fee_cost
fee_pnl_contribution = -fee_cost
```

A negative maker fee is a rebate: `fee_cost < 0`, so it increases net PnL. Gross and net values are both shown to keep this economically visible.

Average cost changes only when adding in the same direction or crossing through flat. A partial close retains the original average cost of the remaining position. Realized close PnL uses the sign of the position being closed.

Unrealized PnL is signed base inventory times `(mid - average_cost)`. If any open position lacks a valid midpoint, aggregate unrealized and total PnL are `null`; known marked unrealized is shown separately.

## 8. Markouts

For a buy fill, `side_sign = +1`; for a sell, `side_sign = -1`. At horizon `h`, the first observed midpoint with timestamp at or after `fill_ts + h` is used:

```text
markout_pnl = side_sign * (future_mid - fill_price) * fill_qty
markout_bps = side_sign * (future_mid - fill_price) / fill_price * 10,000
```

Reports include resolved, pending, and invalidated counts; notional-weighted bps; mean fill bps; and mean/max observation lag. A book invalidation makes all pending markouts for that symbol unresolved/invalidated, preventing a gap from being crossed.

## 9. Reproducibility

Every simulation output contains:

- fixture path, byte size, and SHA-256;
- secret-safe public configuration and SHA-256;
- stable fingerprint of all package Python files;
- Python executable/version, OS, CPU identity, and logical CPU count;
- a run ID derived from fixture, configuration, and code fingerprints.

Benchmark timing runs use `perf_counter_ns` with `tracemalloc` disabled. Peak Python allocation is measured in separate fresh-engine runs. The benchmark is end-to-end fixture throughput, not per-event latency. No external comparator is claimed unless one is explicitly supplied.

## 10. What the evidence can and cannot support

Supported:

- deterministic replay for a fixed file/config/code triple;
- visible book-gap and reconnect handling;
- no known future-book access in timer catch-up;
- internally consistent order lifecycle and position accounting;
- sensitivity of results to explicit L2 fill assumptions.

Not supported:

- true queue position or order-level matching;
- live fill probability, market impact, or strategy capacity;
- venue-to-local latency distribution from legacy fixtures;
- profitability, alpha, or production readiness;
- feed completeness without independent packet-loss telemetry.

Venue procedure references: [Binance local-book guide](https://developers.binance.info/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) and [Binance WebSocket route notice](https://developers.binance.info/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice).
