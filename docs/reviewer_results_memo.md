# Results Memo

## Purpose

`lob_sim` is a deterministic Binance USD-M L2 replay and queue-aware
passive-execution simulator. The packs below let you inspect book mechanics,
replay determinism, public-data fill assumptions, adverse-selection markouts,
inventory and risk accounting, and benchmark provenance. They are not alpha,
profitability, production-gateway, or private-execution results.

## Commands

```bash
python scripts/reviewer_gate.py
python scripts/audit_futures_pack.py --committed-futures
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --mode all --pack docs/sample_outputs/futures_stress_case --json-out outputs/futures_benchmark.json
python experiments/sweep_futures_latency.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --out-dir outputs/futures_latency_sweeps
python scripts/run_real_data_report.py --file data/capture_....manifest.json --env .env.real-data --label BTCUSDT_10m --publish-dir docs/real_data_runs
```

## Inputs

- `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson` is a clipped recorded BTCUSDT Binance USD-M public-data stream.
- `docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson` is a tiny synthetic walkthrough fixture.
- `docs/sample_outputs/futures_stress_case/input_stress.ndjson` is synthetic-but-exchange-shaped. It puts rare queue, cancel, taker, and self-trade-prevention mechanics into one compact, deterministic stress pack.
- `docs/real_data_runs/raw_1780500354_10m.md` is historical pre-semantic-repair material retained for regression comparison only; its fills, PnL, and performance are excluded from current conclusions.
- Larger public-data runs should follow `docs/real_data_runbook.md` and `docs/real_data_results_template.md`, then publish report-only results under `docs/real_data_runs/`; raw files stay local-only unless they are small and redistributable.

## Historical real-data runs

The two files under `docs/real_data_runs/` are old regression history, not
current results. They predate stream-first capture, independent validity epochs,
arrival-time risk checks, and resolvable per-fill provenance. Do not use their
fill counts, PnL, or replay throughput as current measurements. A replacement
report needs a schema-v3 tape with complete validity coverage, a passing pack
audit, and `lob_sim.fill_provenance.v1` coverage for every modeled fill.

## Stress-pack event counts

From `docs/sample_outputs/futures_stress_case/summary.json`:

- Records processed: `14`
- Depth updates: `8`
- Aggregate trades: `4`
- Book gaps: `0`
- Event trace rows: `52`
- Fill count: `5`

## Fill-source mix

The stress pack has all three fill sources:

- `depth_update`: `1`
- `agg_trade`: `2`
- `taker_order`: `2`

This matters because the same trace exposes passive public-consumption fills alongside explicit marketable strategy fills.

## Queue consumption

Stress-pack public queue-consumption summary:

- Observed lots: `19`
- Modeled lots: `18`
- Overlap-netted lots: `1`
- Synthetic queue-consumed lots: `16` (a public-L2 queue-ahead scenario, not historical participant FIFO)
- Unmatched lots: `2`
- Overlap window: `0.125s`

The overlap-netted lot comes from same-side/same-price depth and `aggTrade` consumption. Unmatched lots remain visible instead of being hidden or forced into a fill.

## Markouts by source

Stress-pack one-second markouts:

- `depth_update`: `1` sample, `0` adverse, average markout `0.2`
- `agg_trade`: `2` samples, `1` adverse, average markout `0.1`
- `taker_order`: `2` samples, `1` adverse, average markout `-0.05`

These rows let you compare fill quality by modeled source; they are not a
strategy result.

## Inventory and risk

Stress-pack inventory/risk summary:

- Final BTCUSDT inventory: `0.005`
- Average inventory: `0.003285714285714286`
- Inventory stdev: `0.002163635524139563`
- Max drawdown: `0.00105`
- Kill switch triggered: `false`
- Self-trade prevention count: `1`

## Latency sensitivity

The published latency reference is `docs/strategy_results/futures_latency_sweep_reference.md`.
It varies modeled order-arrival and cancel-ack delays over `0, 10, 50` ms grids
for mutually exclusive public-L2 `trade` and `depth` signals. These are
scenario assumptions, not gateway-latency measurements, fill bounds, or a
latency-arbitrage result.

## Benchmark scope

`experiments/benchmark_futures_replay.py --mode all` times replay-only, simulation without export, simulation with event-trace export, and futures pack audit. The output includes input SHA, config digest, adapter metadata, instrument specs, Python/platform/git state, event counts, p50/p99 timing, wall time, and memory. Python fixture-scale numbers should not be compared to colocated production systems.

## What the public feed cannot tell us

- Public L2 cannot identify private queue position or hidden liquidity.
- Depth reductions can be cancels, trades, or both.
- `aggTrade` prints are public aggregate prints, not private execution reports.
- Synthetic stress rows are exchange-shaped but not recorded market data.
- Strategy profiles are transparent controls for exercising replay mechanics, not production market-making models.
