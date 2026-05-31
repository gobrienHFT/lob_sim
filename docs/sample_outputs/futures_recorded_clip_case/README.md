# Recorded Futures Clip Case

This pack is a clipped recorded BTCUSDT event stream from an existing local Binance USD-M capture. It is not synthetic: the committed input is a small slice of recorded `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` rows, replayed through the current queue-aware passive-fill simulator.

## Exact Regenerate Command

```bash
python scripts/refresh_futures_recorded_case.py
```

## What To Inspect First

1. [`summary.json`](summary.json)
2. [`manifest.json`](manifest.json)
3. [`trades.csv`](trades.csv)
4. [`case_notes.md`](case_notes.md)
5. [`input_clip.ndjson`](input_clip.ndjson)

## Provenance

- Source lineage: clipped from a prior BTCUSDT Binance USD-M capture; the committed `input_clip.ndjson` is the authoritative replay input for this pack
- Selected window: BTCUSDT `exchangeInfo`, the second recorded snapshot, and the next 78 recorded events
- Committed clip time range: `ts_local` `1772633472.3978999` to `1772633474.824`

The original larger raw file is not committed in this pack. The committed input here is the clipped replay window used to regenerate the case outputs.
