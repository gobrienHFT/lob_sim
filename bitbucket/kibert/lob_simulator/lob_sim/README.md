# lob_sim

`lob_sim` is a Python project for collecting Binance USD-M futures order book/aggTrade streams,
reconstructing a local level-II book from snapshot+diff events, and running a basic market-making
simulator on recorded data.

## What is implemented

- Live collectors for Binance combined streams:
  - `depthUpdate` from `<symbol>@depth@100ms`
  - `aggTrade` from `<symbol>@aggTrade`
- Symbol-level book sync using Binance's required algorithm:
  1. Start websocket, buffer depth updates.
  2. Fetch snapshot.
  3. Drop `u < lastUpdateId`.
  4. First usable update must satisfy `U <= lastUpdateId <= u`.
  5. Subsequent updates require `pu == previous u` (unless `RESYNC_ON_GAP=0`).
- NDJSON + optional gzip recording.
- Offline replay by applying the same book sync path.
- Simple passive market making strategy with quote schedule, queue-aware fill approximation, latency model, metrics and output files.

### Limitations

- Binance provides aggregated book levels, so queue position/fills are approximated.
- There is no true per-order FIFO or per-order book-level matching.
- Fill model uses queue-ahead reduction on level decreases and matching aggTrades as a proxy.

## Layout

- `lob_sim/` package with modules as requested.
- `tests/` has unit tests for sync and fill logic.

## Setup

```bash
cd lob_sim
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
python -m lob_sim.cli collect
python -m lob_sim.cli replay --file <path>
python -m lob_sim.cli simulate --file <path>
```

## Recording format

Each NDJSON row has:

```json
{
  "ts_local": float,
  "symbol": "BTCUSDT",
  "type": "depthUpdate | aggTrade | snapshot | exchangeInfo",
  "data": { ... }
}
```

- `exchangeInfo` stores per-symbol `{tickSize, stepSize}`.
- `snapshot` stores top levels from `/fapi/v1/depth` with `lastUpdateId`.
- `depthUpdate` and `aggTrade` store original websocket payloads.

## Simulation and fill model

- Strategy computes mid from best bid/ask every `MM_REQUOTE_MS`.
- Bids/asks are quantized to tick size.
- On each decision cycle:
  - existing orders are canceled with `SIM_CANCEL_LATENCY_MS`,
  - new orders are placed with `SIM_ORDER_LATENCY_MS`.
- For a passive order at level `P`:
  - queue position initialized from current level size at `P`,
  - only decreases at that level (and aggressor aggTrades at `P`) consume queue and fills.
  - size increases at `P` never increase queue-ahead.
- PnL is mark-to-mid for unrealized and fee-adjusted realized PnL for fills.

## Outputs

Simulation writes:
- `RECORD_DIR/outputs/summary_<ts>.json`
- `RECORD_DIR/outputs/trades_<ts>.csv`
