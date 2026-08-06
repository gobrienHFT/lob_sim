# Three-minute demo and interview guide

The strongest story is not “I found a profitable market-making strategy.” It is: “I found the places where a toy L2 simulator lies, then built explicit integrity boundaries and evidence around them.”

## Minute 0-1: prove the book

Run:

```powershell
python -m lob_sim.cli replay --file data/raw_1772140125.ndjson.gz
```

Point out:

- 352 records and 166 depth events;
- two initial snapshot gaps detected and recovered;
- both books synced at end;
- exact final update IDs and SHA-256 book checksums;
- `integrity_ok=true` means final reconstruction is valid, not that the feed was globally complete.

The useful discussion: why stream-first buffering matters, what `U/u/pu` prove, and why a reconnect is a new book epoch.

## Minute 1-2: prove the simulator does not hide uncertainty

Run:

```powershell
python -m lob_sim.cli simulate --file data/raw_1772140125.ndjson.gz
```

Point out:

- trade-only and depth-decrease fill models are mutually exclusive;
- pending cancels can fill until acknowledgement;
- post-only is checked on arrival, after latency;
- orders/markouts are invalidated at gaps and reconnects;
- gross PnL, rebate contribution, and net PnL are separate;
- a missing midpoint makes aggregate valuation unavailable;
- the bundled legacy fixture reports 36 clock clamps and marks subsecond evidence diagnostic-only.

The current conservative default produces zero fills on the smallest legacy fixture. That is a feature of honest evidence, not a demo failure: the previous proxy could manufacture fills by consuming both trades and depth decreases.

## Minute 2-3: prove reproducibility

Run:

```powershell
python -m lob_sim.benchmark --input data/raw_1772140125.ndjson.gz --warmups 1 --repetitions 5 --output evidence/local_baseline.json
```

Open the report and show:

- fixture/configuration/code fingerprints;
- environment identity;
- uninstrumented timing runs separated from traced-memory runs;
- exact metric definitions and limitations;
- no invented external comparator;
- the guarded reproduction command, which performs fingerprint checks before replay.

## High-value interviewer questions

“Is this FIFO?”

No. It is a synthetic market-by-price queue-ahead model. Binance L2 lacks order IDs and cancellation position. The code and output avoid the FIFO claim.

“Why ignore depth changes in the default model?”

An execution can appear both as aggregate trade flow and a displayed-level decrease. Consuming both double-counts it. Trade-only is the conservative case; depth-only is an optimistic sensitivity.

“What happens on a packet gap?”

The book is cleared, the sync epoch increments, triggering depth is buffered, live orders are cancelled, pending markouts are invalidated, and a new stream-first snapshot bridge is required.

“What is your same-timestamp policy?”

Market data first, then strategy/venue actions. An order acknowledged at a coarse timestamp cannot fill on the already-observed event at that timestamp.

“Can you claim the markouts?”

Only for schema-v2 receive-clock data without clock regression, and never across a gap. Bundled legacy markouts are diagnostic-only.

“Why Python?”

The present milestone optimizes auditability and correctness. The benchmark identifies where performance stands; a lower-level rewrite is justified only after a longer representative schema-v2 corpus and profiling establish the bottleneck.

## Milestone definition of done

- [x] current split-route stream ingestion
- [x] stream-first snapshot synchronization
- [x] explicit capture and sync epochs
- [x] causal action ordering and end-of-capture boundary
- [x] explicit order lifecycle and post-only arrival checks
- [x] mutually exclusive fill assumptions
- [x] correct partial close/reversal accounting
- [x] causal gap-aware markouts
- [x] content-addressed output provenance
- [x] clean lint, tests, CI, benchmark, methodology, and demo

## Next milestone, not current scope

Capture a longer schema-v2 session, add independent liveness/packet-loss diagnostics, and publish paired trade-only/depth-only sensitivity with coverage—not just PnL—as the headline. Do not add ML, a dashboard, or live execution before that evidence exists.
