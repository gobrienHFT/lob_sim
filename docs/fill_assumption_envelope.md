# Fill Assumption Envelope

Public L2 cannot prove private fills. The fill profiles are assumption bounds around public depth and `aggTrade` evidence, not private execution truth.

Use the envelope runner when a replay conclusion depends on passive fills, PnL, markouts, or queue-consumption attribution:

```bash
python experiments/run_fill_assumption_envelope.py --file <file> --env .env.example --out-dir outputs/fill_envelope
```

The runner executes the same replay input and the same normalized simulation config three times. The
`FILL_PROFILE` value changes the envelope profile. Each individual simulation still uses one
mutually exclusive public-consumption signal selected by `SIM_FILL_MODEL`:

- `SIM_FILL_MODEL=trade`: `aggTrade` prints are the queue-consumption signal; depth reductions are not independently credited.
- `SIM_FILL_MODEL=depth`: depth reductions are the queue-consumption signal; `aggTrade` prints are not independently credited.

This separation matters: profile rows are sensitivity cases, not a claim that both signals identify
the same private FIFO execution. The effective assumption written into each run manifest is the
post-`SIM_FILL_MODEL` configuration.

At the profile level, the envelope is:

- `conservative`: `aggTrade` prints may consume visible FIFO queue. Depth-only reductions are recorded as unknown/cancel-like public consumption unless same-price trade prints corroborate them inside the overlap window.
- `base`: enables both observed signals for profile construction with the existing overlap-netting window; the per-run `SIM_FILL_MODEL` filter above still makes the executed scenario mutually exclusive.
- `aggressive`: depth reductions and `aggTrade` prints can both consume queue without overlap netting. This is an upper-bound model, not truth.

Outputs:

- `fill_envelope_summary.json`
- `fill_envelope_summary.csv`
- `fill_envelope_report.md`

Before running any profile, the runner freezes a content-addressed research
registry containing every profile and its normalized configuration. The
registry is written to `research_registry` in the JSON output and its
`registry_sha256` is printed in the Markdown report. This makes it auditable
that no profile was added or changed after the study started. Each JSON/CSV
run row also carries `registry_variant_id`, binding the result to the exact
registered profile/configuration identity.

The summary compares `fill_count`, realized and unrealized PnL, fees, average spread captured, adverse-fill rate, fill-source counts, public-consumption totals, max inventory, kill-switch state/reason, input digest, raw config digest, normalized config digest, the frozen registry identity, and runtime metadata.

Robust conclusions should survive conservative/base/aggressive. Conclusions that only work under aggressive assumptions are weak.

See the committed sample in [`sample_outputs/futures_fill_assumption_envelope/README.md`](sample_outputs/futures_fill_assumption_envelope/README.md).
