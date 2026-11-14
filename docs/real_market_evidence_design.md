# Real-market evidence pipeline: implementation specification

This is the specification for the next evidence milestone, not a claim that
the pipeline or empirical study is already complete. The starting revision is
`063906e821ace02db8aa9dc9dbaa3298162533e1`. Its clean Windows reviewer gate
passed all 15 checks, including 1,668 Python tests and 16 Rust tests. The
existing independent repaired-core reference remains the behavioural oracle.

## Question and scope

Does causal regime information provide stable incremental information about
execution quality, adverse selection, inventory risk or quote behaviour under
multiple defensible execution assumptions?

The experiment is an offline public-L2 sensitivity study. A verified bundle
does not establish private fills, historical FIFO, profitability, production
readiness or measured gateway latency. Synthetic mechanics are separate from
empirical market evidence. Funding is not implemented; net figures cannot be
described as fully funded trading returns.

## Separate admission decisions

1. Capture integrity: finalized schema-v3 segments and manifest, checksums,
   receipt identities, clock consistency, producer completion and trailer.
2. Research-day eligibility: complete UTC-day denominators, both instruments,
   feed freshness, book/trade/mark validity and explicit exclusions.
3. Research execution: an immutable, content-addressed protocol, physical
   feature partitions and models fitted without test rows.
4. Evidence verification: reopen serialized parents, recompute statistics and
   reconcile variants, scenarios and every published output.
5. Interpretation: descriptive differences, uncertainty and robustness are
   different statements; none automatically implies economic usefulness.

No stage may infer the next stage merely from success at the preceding stage.

## Clock contract

Kernel and study durations use integer receipt-monotonic nanoseconds. A
versioned research clock projects them onto the capture host's UTC-labelled
wall clock using **the first receipt anchor only**. Later wall receipts check
the projection; they never fit, interpolate or rewrite earlier observations.

The initial research contract permits at most 50 ms observed wall/projection
deviation. A larger deviation, non-increasing receipt identity or monotonic
regression prevents research admission. This tolerance is an engineering
assumption, not measured absolute UTC accuracy. Host synchronization and
venue timestamps remain separate, and absolute UTC accuracy is unmeasured.

The projection identity and observed deviation must accompany feature and
research artifacts. Samples within the declared uncertainty of a partition
boundary must be excluded explicitly; an uncertain timestamp cannot move a
sample into calibration merely because that produces a convenient result.
Native legacy replay clocks are not rewritten or reclassified as claim-ready.

## Validity intervals and UTC-day rule

Use the simulation engine's existing normalizer and synchronization state
machine with quoting disabled and a read-only observer. Do not reconstruct a
second book. Track fresh validated depth, fresh observed trades, usable
uncrossed marks, independent stream epochs and global capture/clock validity.

Integrate the state known at the left endpoint of `[start,end)`. Same-time
observations follow receive-sequence order and determine only the subsequent
positive-duration interval. Expire depth/marks after 5 seconds and trades
after 60 seconds. No future observation rescues an expired interval. Do not
extrapolate beyond the final observed receipt or a declared capture stop.

Stream coalesced interval rows to an exclusive partial file. Retain one current
state per symbol and one pending interval, not one row per input event in
memory. Source reports identify raw manifests, all segment hashes, instrument
metadata, clocks, events, epochs, freshness and interval artifact identities.

The initial production rule has an **86,400-second denominator per UTC day**:

- required instruments are BTCUSDT and ETHUSDT, Binance USD-M linear contracts;
- at least 99% captured coverage and at least 95% joint-valid and mark coverage;
- finalized, structurally valid, continuous receipt identities and monotonic
  clocks, complete producer/trailer, and internally consistent instrument grids;
- declared empirical provenance, not a generated mechanics fixture;
- every missing, stale or invalid interval retains a reason;
- overlapping independent captures cannot be silently deduplicated or counted
  twice; the initial workflow requires nonoverlapping paired-symbol sessions.

The rule must be versioned, frozen and independently re-applied by the reader.
Do not expose a threshold-reduction option to make short tapes pass. An
observed 99% fraction of a ten-minute snippet is not 99% of a UTC day.

## Long capture and preservation

Provide a public-data-only BTCUSDT+ETHUSDT runner. Default to 25 hours so a
session started before midnight can contain a complete UTC day. Record process,
runtime, package, source and non-secret configuration identities; complete
relevant instrument metadata; periodic RSS, disk availability, event-loop lag,
queue high-water and writer lag; and finalization/failure state.

Use bounded queues and sampled telemetry written off the event loop. Disk,
memory, writer or telemetry failure aborts visibly. A restart creates a new
exclusive session directory and optionally references the prior session; it
does not append to, overwrite, delete or relabel failed evidence. A finalized
short capture is not a passed 24-hour soak. Sampled RSS is not proof that there
were no unsampled transient peaks. Segment metadata grows with segment count;
the runner's explicit duration cap bounds it rather than claiming arbitrary
duration-independent capture memory.

Follow Binance's stream-first snapshot bridge and independent depth/trade
routes. A venue connection normally expires after 24 hours; reconnects must
produce epoch boundaries rather than make a nominally uninterrupted soak.
The official procedures are the [USD-M stream specification](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)
and [local-book reconstruction procedure](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly).

Zero internal drops means the observed internal writer/receipt contract passed.
It never means zero exchange-side packet loss or that unavailable trades were
recovered.

## Frozen protocol and held-out access

Integrity and eligibility certification may inspect raw data before protocol
registration; neither may compute strategy outcomes or select parameters.
Register the research question, source and clock identities, eligible dates,
chronological 60/20/20 split, feature formulas, model candidates and seeded
restarts, train-only preprocessing, validation selection rule, strategy
variants, fill/latency scenarios, fees, risk, outcomes, bootstrap settings,
resource limits and exclusions before opening test feature rows or runs.

The real-market runner refuses fewer than ten eligible UTC days. Fitting reads
only calibration/validation physical day files. Opening test features or
inputs requires an explicit expected frozen protocol digest and matching
prepared-bundle identity; a boolean `frozen=True` alone is insufficient. Save
the access marker before execution and never accept retrospective registry edits.
Hashes prevent accidental mismatch, not deliberate falsification by an author.

Replay baseline, observe-only and policy with conservative/base/aggressive
public-L2 assumptions, multiple fixed new/cancel latency scenarios, and the
registered cadence/model sensitivities. Fixed delays are assumptions, not
private measurements. Retain every fitted attempt, failed model and attempted
variant. Observe-only must match baseline core events, fills and markouts before
its sidecars can supply baseline measurements.

Pair common complete minutes and preserve source/day/epoch/contiguous strata.
Primary uncertainty uses 30-minute moving blocks; retain 5/60-minute block
sensitivities, quiet minutes, actual denominators and unavailable intervals.
No best-cell headline, holdout tuning, IID-fill bootstrap or zero-filled missing
marks. Unvalued inventory keeps gross/net PnL null.

## Independent release reader

Reopen the raw parents, frozen protocol, feature/model artifacts and every run.
Verify content identities, configuration, source/variant/scenario correspondence,
causal filter/quote/risk links, fill and markout censuses, accounting and period
statistics. Use existing independent native-bundle decoders and new hand-derived
oracles for admission and clock geometry. Do not call hash checking alone
statistical verification, or call reusing the same book state machine a second
independent book implementation.

## Acceptance ledger

Before completion, implement and adversarially verify every item below:

- capture integrity + bounded native validity intervals + UTC-day reader;
- guarded long runner + telemetry + interruption/restart forensics;
- full frozen protocol + physical feature partitions + held-out access guard;
- registered model fitting + scenario/latency/cadence runner + paired analysis;
- independent complete-bundle re-reader and mutation tests;
- CLI/long-job commands + reviewer gate + Windows/Linux verification;
- current evidence inventory + explicit empirical blockers + conservative claims;
- unchanged full-engine/checkpoint behaviour when this infrastructure is unused.

The current short tapes cannot supply ten certified UTC days. Do not mark the
mission complete after only the first admission stage, a green unit suite, or
this specification. If the finished pipeline still lacks representative data,
publish the exact blocker and reproducible collection/research commands without
manufacturing an empirical study.
