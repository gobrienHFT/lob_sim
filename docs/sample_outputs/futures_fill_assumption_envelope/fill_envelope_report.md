# Fill Assumption Envelope

- Input file: `docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson`
- Input digest: `2e70daffc74c03cd6a29fc92832bc8da040893973e35850bf428502a9bbaaff9`
- Normalized config digest: `96a334750a6d40d0084088ba1c252cb54205c395c3310b9ae54db6f6bf4f33f4`
- Profiles: `conservative, base, aggressive`

Exact command:

```bash
python experiments/run_fill_assumption_envelope.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example --out-dir docs/sample_outputs/futures_fill_assumption_envelope
```

Public L2 cannot prove private fills. The profiles are assumption bounds, not private execution truth.
Robust conclusions should survive conservative/base/aggressive. Conclusions that only work under aggressive assumptions are weak.

The runner executes the same replay input and the same normalized simulation config three times; only the fill-assumption profile changes.

| Profile | Fills | Realized PnL | Unrealized PnL | Fees | Avg spread | Adverse 1s | Max inventory | Fill sources | Public consumption |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `conservative` | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0 | `{"agg_trade": 0, "depth_update": 0, "taker_order": 0}` | `{"total_modeled_lots": 3, "total_observed_lots": 3, "total_overlap_netted_lots": 0, "total_queue_consumed_lots": 2, "total_unmatched_lots": 1}` |
| `base` | 1 | 2e-06 | 5e-05 | -2e-06 | 0.05 | 0.000000 | 0.001 | `{"agg_trade": 1, "depth_update": 0, "taker_order": 0}` | `{"total_modeled_lots": 3, "total_observed_lots": 3, "total_overlap_netted_lots": 0, "total_queue_consumed_lots": 3, "total_unmatched_lots": 0}` |
| `aggressive` | 1 | 2e-06 | 5e-05 | -2e-06 | 0.05 | 0.000000 | 0.001 | `{"agg_trade": 1, "depth_update": 0, "taker_order": 0}` | `{"total_modeled_lots": 3, "total_observed_lots": 3, "total_overlap_netted_lots": 0, "total_queue_consumed_lots": 3, "total_unmatched_lots": 0}` |

## Artifact Paths

- `conservative`: summary `docs\sample_outputs\futures_fill_assumption_envelope\conservative\outputs\summary_input_fixture.json`, trades `docs\sample_outputs\futures_fill_assumption_envelope\conservative\outputs\trades_input_fixture.csv`, event trace `docs\sample_outputs\futures_fill_assumption_envelope\conservative\outputs\event_trace_input_fixture.csv`
- `base`: summary `docs\sample_outputs\futures_fill_assumption_envelope\base\outputs\summary_input_fixture.json`, trades `docs\sample_outputs\futures_fill_assumption_envelope\base\outputs\trades_input_fixture.csv`, event trace `docs\sample_outputs\futures_fill_assumption_envelope\base\outputs\event_trace_input_fixture.csv`
- `aggressive`: summary `docs\sample_outputs\futures_fill_assumption_envelope\aggressive\outputs\summary_input_fixture.json`, trades `docs\sample_outputs\futures_fill_assumption_envelope\aggressive\outputs\trades_input_fixture.csv`, event trace `docs\sample_outputs\futures_fill_assumption_envelope\aggressive\outputs\event_trace_input_fixture.csv`
