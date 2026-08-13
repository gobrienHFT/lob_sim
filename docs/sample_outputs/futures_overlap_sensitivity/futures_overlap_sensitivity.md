# Futures Overlap-Reconciliation Sensitivity

- Input file: `docs/sample_outputs/futures_overlap_sensitivity/input_fixture.ndjson`
- Input SHA-256: `bb736b95a92486a8b573955e8a171385bf639dbfc8e2ee9eef2543f486d6210d`
- Base config digest: `d0113e3f7c6eb76aed72eafff912cd592769d134a24582330db0cc3d6aa2d8a8`
- Public-L2 signals: `trade, depth` (mutually exclusive scenarios)
- Frozen research registry SHA-256: `ebc51abd73f97b04aee20adb4862a6d434659d3a58b475d68d77a9a9652a7514`
- Registry sidecar: `futures_overlap_sensitivity_registry.json`

Exact command:

```bash
python scripts/refresh_futures_overlap_sensitivity.py
```

Public L2 cannot prove private fills. This is a local corroboration diagnostic, not a private FIFO or execution-truth claim.
Trade-only and depth-only signals are run separately; the window only controls whether the other public feed is treated as corroborating evidence.
The sweep uses aggregate-only metrics with event and audit rows disabled in memory; use bounded streaming export when individual audit rows are required.
The study is intentionally non-economic (`MM_ENABLED=0`) and is not claim-ready even if the input has no detected gap.

| Signal | Scenario ID | Window ms | Fills | Total PnL | Fees | Overlap-netted lots | Corroborated depth lots | Uncorroborated depth lots | State SHA-256 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| trade | `public_l2:profile=base:signal=trade:overlap_window_ms=0` | 0 | 0 | 0 | 0 | 0 | 0 | 4 | `b94650751a62aca5cb7800af30c969d8c2093398c8a54b89165899835a9c344a` |
| trade | `public_l2:profile=base:signal=trade:overlap_window_ms=125` | 125 | 0 | 0 | 0 | 2 | 2 | 2 | `c473969bcdf2e1688736e30b9dd9ec6395bf504de000ec90b146abf337559b0d` |
| trade | `public_l2:profile=base:signal=trade:overlap_window_ms=250` | 250 | 0 | 0 | 0 | 2 | 2 | 2 | `04664a71804358a7f3dd61532c02ac2f3fc6059d97b374d02176abc81d15f595` |
| depth | `public_l2:profile=base:signal=depth:overlap_window_ms=0` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | `83219f1514eafa55e04a847fa09670a0066bf11e844b595adf2aad63bd265704` |
| depth | `public_l2:profile=base:signal=depth:overlap_window_ms=125` | 125 | 0 | 0 | 0 | 2 | 0 | 0 | `12911ce21eac2bc07275f4c6ee11428d11f2a33afba2a5c6f87cdc23534ad480` |
| depth | `public_l2:profile=base:signal=depth:overlap_window_ms=250` | 250 | 0 | 0 | 0 | 2 | 0 | 0 | `156fc6f5e1c5763ce2af59140ba8c816ac1cbd16b733afe0039f6578c5cea0a5` |
