# Public-L2 overlap-reconciliation sensitivity

Public L2 cannot prove private fills. Public Binance market-by-price data contains two related observations: depth
reductions and aggregate trade prints. A local reconciliation window can label a
reduction as corroborating evidence for a nearby print without claiming that it
identifies a private participant or a historical FIFO event. The reference
matrix runs both mutually exclusive public signals, so the timing assumption is
visible rather than hidden in one default configuration.

Run the deterministic diagnostic on the committed compact fixture:

```bash
python experiments/sweep_futures_overlap.py \
  --file docs/sample_outputs/futures_overlap_sensitivity/input_fixture.ndjson \
  --env .env.example \
  --out-dir outputs/futures_overlap_sensitivity \
  --fill-models trade,depth \
  --windows-ms 0,125,250
```

The reference disables the market-making strategy and runs six non-economic
scenarios: `trade` and `depth` signals crossed with 0/125/250 ms local windows.
The selected signal is the only public route eligible to model queue
consumption; the other route can only be counted as corroborating evidence
inside the configured receipt-time window. This is a microstructure diagnostic,
not economic evidence, a private-fill estimate, or a true Binance FIFO claim.
Every scenario is mutually exclusive and not claim-ready. The matrix publishes aggregate-only metrics with event and audit rows disabled in memory; use bounded streaming export when individual audit rows are required.

The committed reference is in
[`sample_outputs/futures_overlap_sensitivity/README.md`](sample_outputs/futures_overlap_sensitivity/README.md).
