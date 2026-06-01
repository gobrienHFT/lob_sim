# Futures Latency Sensitivity Sweep

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `729d4ed0bd5afc0ea7d8594fbefe64cc055be2d2b16c3d992babed6cf814c3f4`
- Base config digest: `b7cc4431f0c85321260b1751767c35baafe00fe86de609cc4d97ac57227ef658`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Strategy profile: `baseline`
- Order latency grid ms: `0, 10, 50`
- Cancel latency grid ms: `0, 10, 50`
- Git commit at run time: `2d1c18d777f04a66e7030b90231cd492260ea02b`
- Git dirty at run time: `False`

Exact command:

```bash
python scripts/refresh_futures_latency_sweep_reference.py
```

- Latency values are modeled order-arrival and cancel-ack delays inside the replay simulator, not measured gateway, colocated, or exchange latency.
- Ranking score is diagnostic only; it is not a latency-arbitrage, alpha, or profitability claim.
- Use this table to inspect how queue position, fill quality, adverse markout, and cancel races respond to explicit latency assumptions on one deterministic fixture.

| Rank | Profile | Order latency ms | Cancel latency ms | Score | Fills | Fill rate | Avg spread | Adverse 1s | Avg wait ms | Inventory stdev | Max drawdown |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `baseline` | 0 | 0 | 26.986765 | 1 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000382 | 0.000000 |
| 2 | `baseline` | 10 | 0 | 26.896765 | 1 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000382 | 0.000000 |
| 3 | `baseline` | 0 | 10 | 26.886765 | 1 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000382 | 0.000000 |
| 4 | `baseline` | 10 | 10 | 26.796765 | 1 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000382 | 0.000000 |
| 5 | `baseline` | 0 | 50 | 26.486765 | 1 | 0.500000 | 13.550000 | 0.000000 | 113.139868 | 0.000382 | 0.000000 |
| 6 | `baseline` | 10 | 50 | 26.396765 | 1 | 0.500000 | 13.550000 | 0.000000 | 103.139877 | 0.000382 | 0.000000 |
| 7 | `baseline` | 50 | 0 | -19.308867 | 1 | 0.500000 | -0.050000 | 1.000000 | 0.000000 | 0.000466 | 0.008750 |
| 8 | `baseline` | 50 | 10 | -19.408867 | 1 | 0.500000 | -0.050000 | 1.000000 | 0.000000 | 0.000466 | 0.008750 |
| 9 | `baseline` | 50 | 50 | -19.808867 | 1 | 0.500000 | -0.050000 | 1.000000 | 0.000000 | 0.000466 | 0.008750 |
