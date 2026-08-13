# Futures Overlap-Reconciliation Sensitivity

- Input file: `docs/sample_outputs/futures_overlap_sensitivity/input_fixture.ndjson`
- Input SHA-256: `436b3461558917a6cd879d5a1a7f09f09d250517f2ba681ac6adeb6aecd8774a`
- Base config digest: `d0113e3f7c6eb76aed72eafff912cd592769d134a24582330db0cc3d6aa2d8a8`
- Public-L2 economic signal: `trade` (mutually exclusive scenario)
- Frozen research registry SHA-256: `c44430c7df603b6907b5d5e3e1b62134ad4a17e92364a032a32087e20cba7a0c`
- Registry sidecar: `futures_overlap_sensitivity_registry.json`

Exact command:

```bash
python scripts/refresh_futures_overlap_sensitivity.py
```

Public L2 cannot prove private fills. This is a local corroboration diagnostic, not a private FIFO or execution-truth claim.
The selected trade/depth signal remains mutually exclusive; the window only controls whether the other public feed is treated as corroborating evidence.
The study is intentionally non-economic (`MM_ENABLED=0`) and is not claim-ready even if the input has no detected gap.

| Window ms | Fills | Total PnL | Fees | Overlap-netted lots | Corroborated depth lots | Uncorroborated depth lots | State SHA-256 |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 2 | `89196218a1fa5e0259440a32d38a6981b0d269763b33f032408eef7a33288918` |
| 125 | 0 | 0 | 0 | 2 | 2 | 0 | `6d1b08a30899fc0ca54a2779af24f41dbf12fe2c11b694d5bd11519efeeacbc9` |
| 250 | 0 | 0 | 0 | 2 | 2 | 0 | `1df752cd21aeca70f6e097a1c42b3630ca4d2e6970c7fa96ac6d2dafa0cbc04b` |
