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
CAPTURE_WRITER_QUEUE_MAX=65536
RESYNC_ON_GAP=1
TRADE_STREAM_SUFFIX=@trade
LOG_LEVEL=INFO
```

Then collect:

```bash
python -m lob_sim.cli --env .env.real-data collect
```

The default schema-v3 output is `data/capture_<timestamp>.manifest.json` plus
rotated `.ndjson.zst` segments. A successful capture has a final
`capture_trailer`, no `.partial` files, and `capture_runtime.writer.complete=true`
in the manifest. Queue or disk failure produces no success manifest: retain the
segment `.partial` and `capture_<timestamp>.failure.json` for diagnosis. Do not
publish a capture whose manifest is absent or whose writer completion is false.

For ETH, set `SYMBOLS=ETHUSDT`. For a smaller target-window run, use `COLLECT_SECONDS=600`.

The replay schema consumes public trade prints through its `aggTrade`-compatible record type. If Binance USD-M `@aggTrade` is available in your environment, you may set `TRADE_STREAM_SUFFIX=@aggTrade`; if that stream produces no trade prints, use the more granular public `@trade` stream. The report records the raw event type counts inside replay trade records so reviewers can see whether the source was `trade` or `aggTrade`.

## Inspect

```bash
python -m lob_sim.cli inspect --file data/capture_....manifest.json
```

Record the input SHA-256, event counts, symbols, first/last timestamps, and duration. If there are many gaps, keep the report but do not present it as a clean tape.

## Simulate And Report

Generate a local-only evidence pack, audit, benchmark, and Markdown report:

```bash
python scripts/run_real_data_report.py --file data/capture_....manifest.json --env .env.real-data --label BTCUSDT_30m --publish-dir docs/real_data_runs
```

The input may be a finalized schema-v3 `capture_....manifest.json`, one finalized `.ndjson.zst` segment, or a legacy `.ndjson[.gz]` tape. The local audit pack is written under `outputs/real_data_runs/<label>/`; labels are immutable and an existing `pack` is never overwritten. The generator creates the pack with `_INCOMPLETE.json` first, copies event, fill, and markout audits through fsynced partials, and removes the sentinel only after its independent audit passes. A copy, simulation, or audit failure therefore leaves a visible incomplete pack and any forensic partials. The committed publication path writes only `docs/real_data_runs/<label>.md` and `docs/real_data_runs/<label>.json`; raw input, event traces, CSVs, and local packs are not copied into docs. New JSON reports use `lob_sim.real_data_report.v2` and state the input identity, market/risk metrics, audit memory contract, benchmark context, source state, and target-window status. Historical v1 reports remain labeled as pre-streaming evidence. Keep local-only raw data local; publish only hashes and report-only artifacts.

## Audit

The report script already audits the generated local pack. To rerun it:

```bash
python scripts/audit_futures_pack.py --pack outputs/real_data_runs/BTCUSDT_30m/pack
```

The bounded audit streams the summary JSON/CSV, trades CSV, markout CSV, and event trace; verifies the manifest file hashes and absence of incomplete/partial artifacts; recomputes fill and markout hash chains; checks trace correspondence, lifecycle and public-consumption aggregates; and resolves fill evidence IDs against the hashed replay input. Exact evidence/order sets live in a temporary SQLite file, diagnostics are capped, and no detail rows are retained in Python memory. This is an implementation memory contract, not a claim of constant disk use or protection from one arbitrarily large CSV row.

## Benchmark

The report script writes `benchmark.json`. To run the benchmark manually:

```bash
python experiments/benchmark_futures_replay.py --file data/capture_....manifest.json --env .env.real-data --mode all --pack outputs/real_data_runs/BTCUSDT_30m/pack --json-out outputs/real_data_runs/BTCUSDT_30m/benchmark.json
```

Publish benchmark numbers with hardware, Python version, platform, input SHA-256, event count, and whether bounded streaming audit export was included. The benchmark mode is named `simulation_with_streaming_audit_export`; it writes event, fill, and markout audits without retaining those rows in memory.

## Publish

If the raw file is too large or not appropriate to commit, publish only:

- `docs/real_data_runs/<label>.md`
- `docs/real_data_runs/<label>.json`
- the input SHA-256 and exact collection env

Keep `outputs/real_data_runs/<label>/` local unless a reviewer explicitly asks for a small trace excerpt. Do not claim private fills, alpha, production latency, gateway readiness, or profitability. The purpose is to show that the same replay, queue, audit, and benchmark path scales from committed clips to a larger public tape.
