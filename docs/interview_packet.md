# Interview Packet

## 60-Second Pitch

`lob_sim` is a deterministic Binance USD-M public L2 replay and execution-sensitivity laboratory. It records schema-v3 receipt metadata, reconstructs validity epochs with explicit continuity checks, runs causal strategy/order/risk/accounting lifecycles, and exports bounded audit artifacts with manifests. It does not claim alpha, profitability, private fill truth, historical participant FIFO, or production latency. The signal is engineering discipline: replay correctness, explicit public-data assumptions, reproducibility, and reviewer-grade evidence.

## Architecture

```mermaid
flowchart LR
  A["Public Binance USD-M stream"] --> B["NDJSON records"]
  B --> C["Schema validation and inspection"]
  C --> D["ReplayFeedAdapter"]
  D --> E["BookSynchronizer"]
  E --> F["LocalOrderBook"]
  F --> G["SimulationEngine"]
  G --> H["PassiveFillModel"]
  G --> I["MM strategy profile"]
  H --> J["SimulationMetrics"]
  I --> J
  J --> K["summary / trades / event trace / manifest"]
  K --> L["artifact verifier and pack auditor"]
```

## Exact Reviewer Command

```bash
python scripts/reviewer_gate.py
```

With `make` available:

```bash
make reviewer-gate
```

## Strongest Files

- `lob_sim/book/sync.py`: snapshot/diff continuity and gap policy.
- `lob_sim/record/async_writer.py`: hard-bounded off-event-loop capture persistence and fail-closed overflow/I/O semantics.
- `lob_sim/record/segmented.py`: checksummed segments, visible partial recovery, atomic finalization, and hashed writer evidence.
- `lob_sim/replay/adapters.py`: venue record normalization into instrument, snapshot, depth, and trade events.
- `lob_sim/sim/fill_model.py`: synthetic queue-ahead scenarios, passive/taker fills, public consumption, and self-trade prevention.
- `lob_sim/sim/engine.py`: event-time scheduling for market rows, decisions, arrivals, cancels, fills, and risk halts.
- `lob_sim/sim/metrics.py`: fill-source metrics, per-fill evidence/validity/queue/latency provenance, markouts, inventory, PnL, lifecycle counts, and public-consumption summaries.
- `scripts/audit_futures_pack.py`: resolves fill evidence against replay input and cross-checks summary, CSVs, event trace, trades, manifest, validity, queue trajectories, latency labels, provenance, per-file hashes, and the content-addressed bundle digest.
- `scripts/verify_committed_artifacts.py`: repository-level evidence gate.
- `docs/sample_outputs/futures_recorded_clip_case/README.md`: small recorded public-data proof point.
- `docs/sample_outputs/futures_stress_case/README.md`: synthetic stress pack for rare mechanics.
- `scripts/run_real_data_report.py`: local real-tape inspect/simulate/audit/benchmark/report pipeline with report-only docs publishing.
- `docs/real_data_runbook.md`: 10-30 minute real-tape run path.

## Historical Real-Data Reports

The committed reports under `docs/real_data_runs/` are retained only as pre-semantic-repair regression history. They predate the current stream-first capture, validity-epoch, arrival-risk, and per-fill provenance gates, so their fill counts, PnL, and throughput-expanded full traces are not reviewer economic evidence and should not be cited in an interview. A replacement claim-ready report requires a schema-v3 capture, a complete validity interval, resolvable `lob_sim.fill_provenance.v1` coverage, and a clean current pack audit.

## Assumptions Tested

- First diff must cover snapshot update id; later diffs must be continuous.
- Non-resync replay records gaps and avoids applying gap-affected book mutations.
- Snapshot visible quantity is queue ahead of strategy orders.
- The selected public-L2 scenario is explicit and mutually exclusive: confirmed trade prints consume synthetic queue in trade mode, while displayed decreases do so only in the optimistic depth sensitivity.
- Same-side depth/aggTrade overlap is netted before consumption.
- Cancel latency leaves old quotes fillable until acknowledgement.
- Same-timestamp cancel acknowledgements are applied before the corresponding market row.
- Marketable strategy orders are taker fills and cannot self-trade with own resting liquidity.
- Every fill's scenario, input-record evidence, validity, synthetic queue trajectory, configured latency draws, lifecycle state, fee model, economics, and artifact hashes agree across summary, trades, and trace outputs.
- Deterministic fixture runs produce identical summary and event-trace hashes.

## Not Claimed

- No private exchange execution-report truth.
- No participant-level queue IDs or hidden-liquidity reconstruction.
- No production gateway, colocated latency, or exchange certification.
- No alpha, Sharpe, profitability, or deployment claim.
- No venue-calibrated options microstructure.

## Likely Interview Q&A

### Why is this better than a bar backtest?

It keeps event ordering, book continuity, queue-ahead state, cancel latency, and fill attribution in the replay. The exported event trace lets a reviewer inspect the exact path from public depth/trade signal to modeled fill.

### How do you avoid overstating passive fills?

Fills are labeled as public-data queue inferences. The manifest states no private execution reports, the auditor checks the assumptions, and summaries expose unmatched public consumption instead of forcing every level decrease into a fill.

### What happens on data gaps?

The synchronizer enforces Binance `U/u/pu` continuity. Live collection can resnapshot; offline replay reports the gap and does not invent missing diffs.

### Why include synthetic packs?

The recorded clip proves the path on public tape. The synthetic walkthrough and stress packs make rare mechanics compact and deterministic: overlap netting, cancel races, taker fills, self-trade prevention, and adverse/non-adverse markouts.

### What would you do next for a desk-grade extension?

Add more venues behind `ReplayFeedAdapter`, run longer public tapes with the real-data runbook, calibrate strategy parameters without claiming alpha, and optionally add private execution reports when a venue/account can legally provide them.
