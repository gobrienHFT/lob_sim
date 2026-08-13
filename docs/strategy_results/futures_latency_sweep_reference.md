# Futures Latency Sensitivity Sweep

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Base config digest: `25e9c2a87b55856ccad243903af02df1eeea9c70c8729fd5e64a36838152a42b`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Public-L2 fill model: `trade` (mutually exclusive scenario)
- Strategy profile: `baseline`
- Order latency grid ms: `0, 10, 50`
- Cancel latency grid ms: `0, 10, 50`
- Frozen research registry SHA-256: `549663c7daa53b1c027aefda3238f4fa466150eafe35b1f7d04fbbe01162e199`
- Registry sidecar: `futures_latency_sweep_reference_registry.json`
- Git commit at run time: `37a9afd34eaee131ecd3aa8df0d08d9386f2d3eb`
- Git dirty at run time: `False`

Exact command:

```bash
python scripts/refresh_futures_latency_sweep_reference.py
```

- Latency values are modeled order-arrival and cancel-ack delays inside the replay simulator, not measured gateway, colocated, or exchange latency.
- Ranking score is diagnostic only; it is not a latency-arbitrage, alpha, or profitability claim.
- `quote_fill_probability` is bounded by arrived orders; `fills_per_quote_request` can exceed one when a single order has multiple partial fills.
- Use this table to inspect how queue position, fill quality, adverse markout, and cancel races respond to explicit latency assumptions on one deterministic fixture.
- This tiny committed clip produced no confirmed-trade fills; it is a zero-fill diagnostic, not economic evidence.

| Rank | Profile | Order latency ms | Cancel latency ms | Score | Fills | Quote-fill probability | Fills / quote request | Avg spread | Adverse 1s | Avg wait ms | Inventory stdev | Max drawdown |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `baseline` | 0 | 0 | 0.000000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 2 | `baseline` | 0 | 10 | -0.100000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 3 | `baseline` | 10 | 0 | -0.100000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 4 | `baseline` | 10 | 10 | -0.200000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 5 | `baseline` | 0 | 50 | -0.500000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 6 | `baseline` | 50 | 0 | -0.500000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 7 | `baseline` | 10 | 50 | -0.600000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 8 | `baseline` | 50 | 10 | -0.600000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 9 | `baseline` | 50 | 50 | -1.000000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
