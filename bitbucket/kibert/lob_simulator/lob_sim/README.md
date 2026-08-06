# LOB Sim

LOB Sim is an auditable Binance USD-M market-by-price replay and execution-research harness: it makes sequence integrity, clocks, order state, fill uncertainty, and evidence quality explicit.

It is not presented as a matching engine, a low-latency system, or proof that a quoting strategy is profitable. The portfolio claim is narrower and more defensible: this project can reconstruct an L2 feed, reject invalid state, replay it without known look-ahead, and expose how execution assumptions change the result.

## Evidence snapshot

| Gate | Current evidence |
| --- | --- |
| Correctness | 62 tests covering sync, reconnects, causality, order lifecycle, fills, accounting, markouts, provenance, and benchmark guards |
| Static quality | `ruff check lob_sim tests` passes |
| Deterministic replay | Bundled 352-record fixture finishes synced for BTCUSDT and ETHUSDT with stable final-book SHA-256 checksums |
| Integrity disclosure | The same legacy fixture reports 2 recovered initial sync gaps and 36 clock regressions; its subsecond markouts are therefore `diagnostic_only` |
| Timing baseline | Median 1,910 records/s over 5 uninstrumented runs on CPython 3.13 / Windows 11 |
| Memory baseline | Median 1,109,418 peak Python-traced bytes over 5 separate `tracemalloc` runs |

The timing sample is small and machine-specific. It is an executable baseline, not a latency claim or an external comparison. Full protocol, hashes, environment, raw runs, and limitations are in [`evidence/baseline_windows_python313.json`](evidence/baseline_windows_python313.json).

## What changed in the recognition milestone

- Rebuilt collection around Binance's stream-first snapshot procedure: connect depth, buffer diffs, fetch a snapshot, validate the bridge, and require `pu` continuity thereafter.
- Split depth and aggregate trades across the current `/public` and `/market` WebSocket routes. This matters because Binance retired the legacy unrouted behavior for market streams in 2026.
- Added capture schema v2 with receive time, a global receive sequence, stream epoch, sync epoch, route, and accepted/rejected snapshot-attempt metadata.
- Made every gap and reconnect an order/markout invalidation boundary. Stale orders cannot survive into a new book epoch.
- Replaced implicit timing with a deterministic action queue and a documented market-data-first tie rule. Decisions strictly before an event see only the previous book.
- Added an explicit order lifecycle, cancel-ack-then-new replacement, stale-action rejection, arrival-time post-only checks, and per-symbol position limits.
- Split the fill model into mutually exclusive cases: conservative trade-only queue consumption by default, or an explicitly optimistic depth-decrease sensitivity.
- Corrected partial-close and reversal accounting; separated gross PnL, fees/rebates, and net PnL; made missing marks invalidate aggregate valuation.
- Added causal 100/1,000/5,000 ms markouts with coverage, gap invalidation, and observation lag.
- Made simulation outputs content-addressed and self-describing with fixture, configuration, code, interpreter, OS, and CPU fingerprints.
- Added packaging, CI, a clean environment template, repository hygiene, and a reproducible benchmark protocol.

## Quick start

Python 3.11 or later is required.

```powershell
python -m pip install -e ".[dev]"
python -m ruff check lob_sim tests
python -m pytest -q
```

Offline commands work from a clean checkout with safe public-data defaults; `.env` is optional. To customize a run:

```powershell
Copy-Item .env.example .env
```

Replay the smallest bundled legacy capture and inspect final integrity/checksums:

```powershell
python -m lob_sim.cli replay --file data/raw_1772140125.ndjson.gz
```

Run the conservative trade-only execution model:

```powershell
python -m lob_sim.cli simulate --file data/raw_1772140125.ndjson.gz
```

The simulation writes content-addressed JSON/CSV under `data/outputs/`. The run ID changes when the fixture, public configuration, or Python source changes.

Reproduce the benchmark protocol:

```powershell
python -m lob_sim.benchmark --input data/raw_1772140125.ndjson.gz --warmups 1 --repetitions 5 --output evidence/local_baseline.json
```

The generated report includes a second command guarded by expected fixture, configuration, and code hashes. Fingerprint mismatches fail before the simulation runs.

Collect a new schema-v2 capture from public market data; credentials are not required:

```powershell
python -m lob_sim.cli collect
```

## Market-data integrity

The collector follows Binance's published local-book rules:

1. Open the depth stream and begin buffering.
2. Fetch the REST snapshot while depth continues to arrive.
3. Discard updates whose final update ID is older than the snapshot.
4. Require the first usable update to bridge the snapshot ID.
5. Require each later `pu` to equal the prior `u`.
6. On any discontinuity or depth reconnect, clear the book, increment the sync epoch, invalidate live simulated orders and pending markouts, and fetch a new snapshot.

See Binance's [local order-book procedure](https://developers.binance.info/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) and [WebSocket route migration notice](https://developers.binance.info/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice).

New captures use separate sessions:

- `wss://fstream.binance.com/public/stream?...` for `depthUpdate`
- `wss://fstream.binance.com/market/stream?...` for `aggTrade`

A 20,000-event pre-snapshot buffer limit fails visibly rather than discarding evidence. Capture files are created exclusively; an existing session is never silently appended to.

## Replay clock and causality

Schema-v2 replay uses local receive wall time and validates the monotonically increasing `recvSeq`. Raw exchange `E` and `T` timestamps remain in each payload for diagnosis. Legacy captures use exchange event time, normalize obvious millisecond values, clamp regressions, and disclose the clamp count.

At each record time `t`:

1. schedule and drain strategy/venue actions strictly before `t` against the previous book;
2. apply the market record at `t`;
3. observe the midpoint and resolve due markouts;
4. drain actions timestamped exactly `t`.

This market-data-first tie policy prevents an order acknowledged at a coarse timestamp from filling on the market event that preceded its acknowledgement. Actions after the final market observation are not executed against a frozen book; they remain reported as pending at the capture boundary.

## Execution model

The simulator is market-by-price. Binance L2 does not expose order identity, hidden liquidity, or true FIFO position, so queue state is synthetic.

`SIM_FILL_MODEL=trade` is the conservative default:

- an accepted passive order starts behind displayed quantity at its price;
- only same-side `aggTrade` volume at that price consumes its synthetic queue-ahead;
- a print through the order price fills the remaining order;
- depth decreases do not also consume queue, avoiding double-counting the same flow.

`SIM_FILL_MODEL=depth` is an optimistic sensitivity:

- displayed decreases at the order price consume queue-ahead;
- aggregate trades are ignored;
- cancellations are indistinguishable from executions, so results must not be treated as fill truth.

Orders have `live`, `pending_cancel`, `filled`, and `cancelled` states. Requotes use cancel acknowledgement followed by a new placement. A stale cancel identifies the exact old order and cannot delete its replacement. Any quote that crosses on arrival is rejected by the post-only gate.

## Metrics that can survive scrutiny

- Gross realized/total PnL is shown before fees; fee PnL contribution and net PnL are separate. A negative configured maker fee is visibly a rebate, not hidden alpha.
- Open inventory without a valid midpoint makes aggregate unrealized and total PnL `null`; it is never silently valued at zero.
- Inventory and PnL are per symbol. Cross-asset base quantities are not summed into a meaningless scalar.
- Fill-event rate, identified-order fill-rate lower bound, filled base quantity, and quote notional are distinct. No ambiguous top-level “fill rate” is reported.
- Markouts are side-signed, notional weighted, resolved at the first causal midpoint at or after the horizon, and include pending/invalidated coverage plus observation lag.
- Subsecond markouts are `claim_ready` only for a schema-v2 receive clock with no observed clock regression. Legacy capture markouts remain diagnostic.

Exact formulas and state transitions are in [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Repository map

```text
lob_sim/
  binance/       REST and split-route WebSocket ingestion
  book/          exact tick/lot conversion, invariant-checked L2 book, sync state machine
  record/        exclusive NDJSON/gzip writer and schema records
  replay/        deterministic reconstruction, final checksums, integrity result
  sim/           action clock, order lifecycle, fill assumptions, strategy, accounting
  benchmark.py   separated timing/memory protocol and guarded reproduction
  provenance.py  secret-safe fixture/config/code/runtime fingerprints
tests/            adversarial unit and integration tests
data/             bundled legacy regression captures and provenance notes
docs/             methodology and interview/demo guide
evidence/         checked benchmark evidence
```

## Honest limitations

- This is aggregated L2 market-by-price data, not market-by-order. FIFO position, cancellations ahead/behind, hidden liquidity, liquidation flow, and self-order visibility are unknown.
- Simulated orders have no market impact and cannot alter later queue state. Results are not counterfactual live-trading PnL.
- `aggTrade` and depth arrive on independent WebSocket sessions; receive order is observable locally, but venue-side packet loss and cross-stream total ordering are not provable.
- Legacy bundled captures predate schema v2, begin with snapshot-first gaps, and contain clock defects. They are regression fixtures, not economic evidence.
- Inventory statistics are sampled once per replay record, not time weighted.
- The benchmark fixture is only 352 records. It is useful for regression and reproducibility, not capacity planning.
- The strategy is intentionally simple; the milestone is execution/replay correctness, not signal sophistication.

## Intentionally parked

No ML strategy, dashboard, C++ rewrite, multi-venue abstraction, or live order entry was added. Those would enlarge the surface area before the evidence foundation is trustworthy. The next highest-value milestone is a longer schema-v2 capture with feed-liveness diagnostics and paired fill-model sensitivity results.

For a concise walkthrough, use [`docs/DEMO.md`](docs/DEMO.md). Data caveats and immutable fixture hashes are in [`data/README.md`](data/README.md).
