# Futures Parameter Sweep

- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`
- Input SHA-256: `e69072b718b871a4437e321dbd9fb26892ab08e97543f42f9661f2bc39af5a26`
- Config digest: `c44933922b7655a7dab3d4e006c19a3ca492a458bf0f8f97d763fa433064f34a`
- Feed adapter: `binance_usdm` (`BINANCE_USDM`)
- Public-L2 fill model: `trade` (mutually exclusive scenario)
- Profiles: `baseline, layered_mm, research_mm`
- Half-spread bps grid: `0.05, 0.10, 0.25`
- Queue repost lots grid: `0, 5, 99`
- Git commit at run time: `a3e8f8ddb482fec62b77b783492602de2ac02562`
- Git dirty at run time: `False`

Exact command:

```bash
python scripts/refresh_futures_parameter_sweep_reference.py
```

- Ranking score is diagnostic only; it is not an alpha or profitability claim.
- `quote_fill_probability` is bounded by arrived orders; `fills_per_quote_request` can exceed one when a single order has multiple partial fills.
- Use this table to inspect how queue refresh, spread width, fill quality, adverse markout, and inventory variance move together on one deterministic fixture.
- This tiny committed clip produced no confirmed-trade fills; it is a zero-fill diagnostic, not economic evidence.

| Rank | Profile | Half-spread bps | Queue repost lots | Score | Fills | Quote-fill probability | Fills / quote request | Avg spread | Adverse 1s | Inventory stdev | Max drawdown |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `layered_mm` | 0.10 | 0 | -0.000500 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 2 | `layered_mm` | 0.10 | 5 | -0.000500 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 3 | `layered_mm` | 0.10 | 99 | -0.000500 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 4 | `baseline` | 0.05 | 0 | -0.000750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 5 | `baseline` | 0.05 | 5 | -0.000750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 6 | `baseline` | 0.05 | 99 | -0.000750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 7 | `layered_mm` | 0.05 | 0 | -0.000875 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 8 | `layered_mm` | 0.05 | 5 | -0.000875 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 9 | `layered_mm` | 0.05 | 99 | -0.000875 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 10 | `baseline` | 0.10 | 0 | -0.001000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 11 | `baseline` | 0.10 | 5 | -0.001000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 12 | `baseline` | 0.10 | 99 | -0.001000 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 13 | `layered_mm` | 0.25 | 0 | -0.001125 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 14 | `layered_mm` | 0.25 | 5 | -0.001125 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 15 | `layered_mm` | 0.25 | 99 | -0.001125 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 16 | `baseline` | 0.25 | 0 | -0.002250 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 17 | `baseline` | 0.25 | 5 | -0.002250 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 18 | `baseline` | 0.25 | 99 | -0.002250 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 19 | `research_mm` | 0.05 | 0 | -0.034625 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 20 | `research_mm` | 0.05 | 5 | -0.034625 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 21 | `research_mm` | 0.05 | 99 | -0.034625 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 22 | `research_mm` | 0.10 | 0 | -0.092750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 23 | `research_mm` | 0.10 | 5 | -0.092750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 24 | `research_mm` | 0.10 | 99 | -0.092750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 25 | `research_mm` | 0.25 | 0 | -0.112750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 26 | `research_mm` | 0.25 | 5 | -0.112750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| 27 | `research_mm` | 0.25 | 99 | -0.112750 | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
