# Futures Latency Sensitivity Sweep

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Base config digest: `25e9c2a87b55856ccad243903af02df1eeea9c70c8729fd5e64a36838152a42b`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Public-L2 fill models: `trade`, `depth` (mutually exclusive scenarios)
- Strategy profile: `baseline`
- Order latency grid ms: `0, 10, 50`
- Cancel latency grid ms: `0, 10, 50`
- Frozen research registry SHA-256: `bbad043b990515781c2392faeabd7e696be7c459e28b4a322c591488630c0ea2`
- Registry sidecar: `futures_latency_sweep_reference_registry.json`
- Git commit at run time: `02b699f94d967bd961b23cd231261951ad0b706b`
- Git dirty at run time: `False`

Exact command:

```bash
python scripts/refresh_futures_latency_sweep_reference.py
```

- Latency values are modeled order-arrival and cancel-ack delays inside the replay simulator, not measured gateway, colocated, or exchange latency.
- Ranking score is diagnostic only; it is not a latency-arbitrage, alpha, or profitability claim.
- `quote_fill_probability` is bounded by arrived orders; `fills_per_quote_request` can exceed one when a single order has multiple partial fills.
- Fill models are mutually exclusive public-L2 execution signals; the matrix is a scenario envelope, not a true fill bound.
- Use this table to inspect how queue position, fill quality, adverse markout, and cancel races respond to explicit latency assumptions on one deterministic fixture.
- The sweep uses aggregate-only metrics with event and audit rows disabled in memory; use the bounded streaming runner when individual audit rows are required.

| Rank | Profile | Fill model | Order latency ms | Cancel latency ms | Score | Fills | Quote-fill probability | Fills / quote request | Avg spread | Adverse 1s | Avg wait ms | Inventory stdev | Max drawdown |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `baseline` | `depth` | 0 | 0 | 26.986765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000380 | 0.000000 |
| 2 | `baseline` | `depth` | 10 | 0 | 26.896765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000380 | 0.000000 |
| 3 | `baseline` | `depth` | 0 | 10 | 26.886765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000380 | 0.000000 |
| 4 | `baseline` | `depth` | 10 | 10 | 26.796765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000380 | 0.000000 |
| 5 | `baseline` | `depth` | 0 | 50 | 26.486765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000380 | 0.000000 |
| 6 | `baseline` | `depth` | 10 | 50 | 26.396765 | 1 | 0.500000 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000380 | 0.000000 |
| 7 | `baseline` | `trade` | 0 | 0 | 0.000000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 8 | `baseline` | `trade` | 0 | 10 | -0.100000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 9 | `baseline` | `trade` | 10 | 0 | -0.100000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 10 | `baseline` | `trade` | 10 | 10 | -0.200000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 11 | `baseline` | `trade` | 0 | 50 | -0.500000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 12 | `baseline` | `trade` | 50 | 0 | -0.500000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 13 | `baseline` | `depth` | 50 | 0 | -0.500000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 14 | `baseline` | `trade` | 10 | 50 | -0.600000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 15 | `baseline` | `trade` | 50 | 10 | -0.600000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 16 | `baseline` | `depth` | 50 | 10 | -0.600000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 17 | `baseline` | `trade` | 50 | 50 | -1.000000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 18 | `baseline` | `depth` | 50 | 50 | -1.000000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
