# Futures overlap-reconciliation sensitivity

Public L2 cannot prove private fills. This compact fixture isolates one observable question: when an aggregate trade
print is followed by a same-price displayed reduction, how much of the depth
reduction is treated as corroborating evidence under a local 0/125/250 ms
reconciliation window?

It is deliberately non-economic (`MM_ENABLED=0`). It does not establish private
order identity, participant FIFO, hidden liquidity, or historical execution
truth. The selected `SIM_FILL_MODEL=trade` signal remains mutually exclusive;
the depth route is used only as corroborating public evidence.
The study is not claim-ready.

Refresh the committed reference from a clean tree:

```bash
python scripts/refresh_futures_overlap_sensitivity.py
```

Outputs:

- [`futures_overlap_sensitivity.json`](futures_overlap_sensitivity.json)
- [`futures_overlap_sensitivity.csv`](futures_overlap_sensitivity.csv)
- [`futures_overlap_sensitivity.md`](futures_overlap_sensitivity.md)
- [`futures_overlap_sensitivity_registry.json`](futures_overlap_sensitivity_registry.json)
