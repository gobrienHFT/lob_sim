# Public-L2 overlap-reconciliation sensitivity

Public L2 cannot prove private fills. Public Binance market-by-price data contains two related observations: depth
reductions and aggregate trade prints. A local reconciliation window can label a
reduction as corroborating evidence for a nearby print without claiming that it
identifies a private participant or a historical FIFO event.

Run the deterministic diagnostic on the committed compact fixture:

```bash
python experiments/sweep_futures_overlap.py \
  --file docs/sample_outputs/futures_overlap_sensitivity/input_fixture.ndjson \
  --env .env.example \
  --out-dir outputs/futures_overlap_sensitivity \
  --windows-ms 0,125,250
```

The reference uses `SIM_FILL_MODEL=trade` and disables the market-making
strategy. The economic fill signal is therefore trade-only; depth reductions
are never independently credited as fills. Only local corroboration statistics
change with the 0/125/250 ms window. This is a microstructure diagnostic, not
economic evidence, a private-fill estimate, or a true Binance FIFO claim. The
scenario is mutually exclusive and not claim-ready.

The committed reference is in
[`sample_outputs/futures_overlap_sensitivity/README.md`](sample_outputs/futures_overlap_sensitivity/README.md).
