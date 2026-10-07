# Extended verification

The fast reviewer gate compares 10,000 generated cases per tested kernel
primitive against the committed reference report. A separate GitHub Actions
workflow, **Extended kernel differential**, runs 100,000 cases per primitive
for seeds 17, 73 and 101. It can be dispatched manually and runs weekly at
05:00 UTC on Sunday. Each job publishes corpus, transition and final-state
hashes as an artifact attached to its tested source revision.

Run one extended corpus locally:

```bash
python scripts/check_rust_python_parity.py --cases 100000 --seed 73 --json-out outputs/kernel_parity_seed_73.json
```

The seed controls generated transitions and latency draws. The public-queue,
portfolio-notional and accounting corpora use distinct derived streams, so
adding a case family does not advance another family's random generator. The
default seed remains 17 and retains the committed fast-gate hashes.

## Scope

This compares independent Python and Rust implementations of the published
book, queue, synthetic matching, scheduler, reservation, accounting, markout
and latency primitives. It includes deliberate invalid operations and periodic
checkpoint/state comparisons. The composed engine sequence remains a fixed
smoke test, not a generated full-engine tape.

Counting multiple primitive families is not a claim of a million-event
end-to-end replay. The report keeps `full_engine_parity: false`. These jobs are
not coverage-guided parser fuzzing, a 24-hour capture soak, representative-data
research, or a dedicated-host performance release gate. Those remain explicit
roadmap requirements in [project status](project_status.md).

## Handling a failure

Retain the source revision, seed, case count and first differing transition
from the failed job. Reproduce it locally, reduce the failing sequence, and
commit a small regression fixture with independently specified expected
behavior before changing the implementation. Do not update the committed
expected hashes merely to make a divergence disappear. A green extended job
only validates its tested corpus and the scope stated in its report.

## Serialized research graph

The separate **Extended research verification** workflow runs manually and
weekly on Windows and Linux. Run its native integration locally with:

```bash
python -m pytest tests/test_research_study_graph.py -m long_research -q --durations=10
```

This reduced synthetic grid executes matching, bounded exports, scenario
configuration and independent serialized economic/risk/statistical audits.
It retains failures rather than rewriting their files. The hand models and
pytest-only admission/fitting substitutions test graph mechanics, not real
day admission or fitted market regimes. Production exposes no bypass flag.
The fast reviewer gate explicitly excludes `long_research`; its short fault,
partition, model, replay and hand-calculated statistical tests still run.

Actual public capture and full admitted research are separate local jobs in
[the research guide](real_market_research.md). Do not download large tapes into
Git or substitute this harness for the required empirical evidence.
