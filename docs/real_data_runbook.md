# Real Data Runbook

This runbook is for a 10-30 minute BTCUSDT or ETHUSDT public-data capture. Keep committed fixtures small; use this path when you want a larger local tape run with reproducible hashes and summary artifacts.

## Capture

Create a local env file for the tape window:

```bash
copy .env.example .env.real-data
```

Set:

```dotenv
SYMBOLS=BTCUSDT
COLLECT_SECONDS=1800
RECORD_DIR=data
RECORD_GZIP=1
RESYNC_ON_GAP=1
TRADE_STREAM_SUFFIX=@trade
LOG_LEVEL=INFO
```

Then collect:

```bash
python -m lob_sim.cli --env .env.real-data collect
```

For ETH, set `SYMBOLS=ETHUSDT`. For a smaller target-window run, use `COLLECT_SECONDS=600`.

The replay schema consumes public trade prints through its `aggTrade`-compatible record type. If Binance USD-M `@aggTrade` is available in your environment, you may set `TRADE_STREAM_SUFFIX=@aggTrade`; if that stream produces no trade prints, use the more granular public `@trade` stream. The report records the raw event type counts inside replay trade records so reviewers can see whether the source was `trade` or `aggTrade`.

## Inspect

```bash
python -m lob_sim.cli inspect --file data/raw_....ndjson.gz
```

Record the input SHA-256, event counts, symbols, first/last timestamps, and duration. If there are many gaps, keep the report but do not present it as a clean tape.

## Simulate And Report

Generate a local-only evidence pack, audit, benchmark, and Markdown report:

```bash
python scripts/run_real_data_report.py --file data/raw_....ndjson.gz --env .env.real-data --label BTCUSDT_30m --publish-dir docs/real_data_runs
```

The local audit pack is written under `outputs/real_data_runs/<label>/`. The committed publication path writes only `docs/real_data_runs/<label>.md` and `docs/real_data_runs/<label>.json`; raw input, event traces, CSVs, and local packs are not copied into docs. The report states `local-only raw data`, the input SHA-256, file size, symbol, duration, fill-frequency metrics, fill-source mix, markouts, inventory, drawdown, audit result, benchmark context, source state, and whether the tape meets the 10-30 minute target window.

## Audit

The report script already audits the generated local pack. To rerun it:

```bash
python scripts/audit_futures_pack.py --pack outputs/real_data_runs/BTCUSDT_30m/pack
```

The audit checks the summary JSON/CSV, trades CSV, event trace, manifest, replay input counts, fill metrics, lifecycle counters, public-consumption diagnostics, and provenance labels.

## Benchmark

The report script writes `benchmark.json`. To run the benchmark manually:

```bash
python experiments/benchmark_futures_replay.py --file data/raw_....ndjson.gz --env .env.real-data --mode all --pack outputs/real_data_runs/BTCUSDT_30m/pack --json-out outputs/real_data_runs/BTCUSDT_30m/benchmark.json
```

Publish benchmark numbers with hardware, Python version, platform, input SHA-256, event count, and whether event-trace export was included.

## Publish

If the raw file is too large or not appropriate to commit, publish only:

- `docs/real_data_runs/<label>.md`
- `docs/real_data_runs/<label>.json`
- the input SHA-256 and exact collection env

Keep `outputs/real_data_runs/<label>/` local unless a reviewer explicitly asks for a small trace excerpt. Do not claim private fills, alpha, production latency, gateway readiness, or profitability. The purpose is to show that the same replay, queue, audit, and benchmark path scales from committed clips to a larger public tape.
