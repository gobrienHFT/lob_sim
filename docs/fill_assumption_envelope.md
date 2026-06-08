# Fill Assumption Envelope

Public L2 cannot prove private fills. The fill profiles are assumption bounds around public depth and `aggTrade` evidence, not private execution truth.

Use the envelope runner when a replay conclusion depends on passive fills, PnL, markouts, or queue-consumption attribution:

```bash
python experiments/run_fill_assumption_envelope.py --file <file> --env .env.example --out-dir outputs/fill_envelope
```

The runner executes the same replay input and the same normalized simulation config three times. Only the fill-assumption profile changes:

- `conservative`: `aggTrade` prints may consume visible FIFO queue. Depth-only reductions are recorded as unknown/cancel-like public consumption unless same-price trade prints corroborate them inside the overlap window.
- `base`: current passive-fill model behavior. Depth reductions and `aggTrade` prints both consume visible FIFO queue with the existing overlap-netting window.
- `aggressive`: depth reductions and `aggTrade` prints can both consume queue without overlap netting. This is an upper-bound model, not truth.

Outputs:

- `fill_envelope_summary.json`
- `fill_envelope_summary.csv`
- `fill_envelope_report.md`

The summary compares `fill_count`, realized and unrealized PnL, fees, average spread captured, adverse-fill rate, fill-source counts, public-consumption totals, max inventory, kill-switch state/reason, input digest, raw config digest, normalized config digest, and runtime metadata.

Robust conclusions should survive conservative/base/aggressive. Conclusions that only work under aggressive assumptions are weak.

See the committed sample in [`sample_outputs/futures_fill_assumption_envelope/README.md`](sample_outputs/futures_fill_assumption_envelope/README.md).
