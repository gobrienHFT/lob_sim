# Reviewer Results Memo

## Thesis

`lob_sim` is a deterministic Binance USD-M L2 replay and queue-aware passive-fill simulator. The artifact is meant to show order-book mechanics, replay determinism, public-data fill assumptions, adverse-selection measurement, inventory/risk accounting, benchmark provenance, and extensible research infrastructure. It is not an alpha model, profitability claim, production gateway, or private exchange fill validator.

## Commands

```bash
python scripts/reviewer_gate.py
python scripts/audit_futures_pack.py --committed-futures
python experiments/benchmark_futures_replay.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --mode all --pack docs/sample_outputs/futures_stress_case --json-out outputs/futures_benchmark.json
python experiments/sweep_futures_latency.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example --out-dir outputs/futures_latency_sweeps
python scripts/run_real_data_report.py --file data/raw_....ndjson.gz --env .env.real-data --label BTCUSDT_30m --publish-dir docs/real_data_runs
```

## Fixture Provenance

- `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson` is a clipped recorded BTCUSDT Binance USD-M public-data stream.
- `docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson` is a tiny synthetic walkthrough fixture.
- `docs/sample_outputs/futures_stress_case/input_stress.ndjson` is synthetic-but-exchange-shaped. It exists to place rare queue, cancel, taker, and self-trade-prevention mechanics into one compact, deterministic evidence pack.
- Larger public-data runs should follow `docs/real_data_runbook.md` and `docs/real_data_results_template.md`, then publish report-only results under `docs/real_data_runs/`; raw files stay local-only unless they are small and redistributable.

## Published Local Real-Data Report

`docs/real_data_runs/raw_1772633471.md` is generated from an available local BTCUSDT public-data tape. It is explicitly labeled as a short local tape: `30.0871000289917` seconds, not the requested 10-30 minute target. The JSON includes exact commands for generating a longer report-only artifact.

Key evidence:

- Input: `2,054,090` bytes, `1,997` records, SHA-256 `520e65919c86c552162028c52da92b642018daf69b4bdb8ca8a9d1626eecb5c8`.
- Fill-source mix: `20` fills from `467` arrived quote orders, quote-fill probability `0.042826552462526764`, with `depth_update=4`, `agg_trade=5`, and `taker_order=11`.
- Audit/queue evidence: clean local audit, `35,561` event-trace rows, `30,986` queue-consumption rows, one documented book gap.

Interpretation: negative or positive PnL is not the point. The report demonstrates replay/audit/fill-source/queue evidence on public tape while preserving the limits: no private fill truth, no alpha claim, no profitability claim, no production latency claim, and no gateway-readiness claim.

## Stress Pack Event Counts

From `docs/sample_outputs/futures_stress_case/summary.json`:

- Records processed: `14`
- Depth updates: `8`
- Aggregate trades: `4`
- Book gaps: `0`
- Event trace rows: `52`
- Fill count: `5`

## Fill-Source Mix

The stress pack has all three fill sources:

- `depth_update`: `1`
- `agg_trade`: `2`
- `taker_order`: `2`

This matters because a reviewer can inspect passive public-consumption fills and explicit marketable strategy fills in the same trace.

## Queue Consumption

Stress-pack public queue-consumption summary:

- Observed lots: `19`
- Modeled lots: `18`
- Overlap-netted lots: `1`
- FIFO queue-consumed lots: `16`
- Unmatched lots: `2`
- Overlap window: `0.125s`

The overlap-netted lot comes from same-side/same-price depth and `aggTrade` consumption. Unmatched lots remain visible instead of being hidden or forced into a fill.

## Markout By Source

Stress-pack one-second markouts:

- `depth_update`: `1` sample, `0` adverse, average markout `0.2`
- `agg_trade`: `2` samples, `1` adverse, average markout `0.1`
- `taker_order`: `2` samples, `1` adverse, average markout `-0.05`

The point is not performance. The point is that the trace and summary split fill quality by modeled source.

## Inventory And Risk

Stress-pack inventory/risk summary:

- Final BTCUSDT inventory: `0.005`
- Average inventory: `0.003285714285714286`
- Inventory stdev: `0.002163635524139563`
- Max drawdown: `0.00105`
- Kill switch triggered: `false`
- Self-trade prevention count: `1`

## Latency Sensitivity

The published latency reference is `docs/strategy_results/futures_latency_sweep_reference.md`. It varies modeled order-arrival and cancel-ack delays over `0, 10, 50` ms grids. These are simulator assumptions, not gateway latency measurements or latency-arbitrage claims.

## Benchmark Caveats

`experiments/benchmark_futures_replay.py --mode all` times replay-only, simulation without export, simulation with event-trace export, and futures pack audit. The output includes input SHA, config digest, adapter metadata, instrument specs, Python/platform/git state, event counts, p50/p99 timing, wall time, and memory. Python fixture-scale numbers should not be compared to colocated production systems.

## Limitations

- Public L2 cannot identify private queue position or hidden liquidity.
- Depth reductions can be cancels, trades, or both.
- `aggTrade` prints are public aggregate prints, not private execution reports.
- Synthetic stress rows are exchange-shaped but not recorded market data.
- Strategy profiles are transparent controls for exercising replay mechanics, not production market-making models.
