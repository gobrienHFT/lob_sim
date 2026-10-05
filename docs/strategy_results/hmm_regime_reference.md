# Registered HMM study: real-data eligibility audit

This is a diagnostic failure report, not a fitted real-market model, held-out
PnL result or evidence that a regime policy improves trading. The executable
comparison path is covered by synthetic/native-clock integration tests. Its
real-data prerequisites are not yet satisfied.

The [machine-readable record](hmm_regime_reference.json) binds the frozen
registry, actual package source, input/feature hashes, capture receipt checks,
and both failed cadence variants. Raw tapes remain local/external.

## What was registered

Before opening test features: baseline `research_mm`, observation-only HMM,
posterior-weighted policy, hard-active-state policy, and 250 ms policy sampling.
The primary sampling cadence is one second. Both feature windows are ten
seconds; confirmation/minimum age are three seconds. All variants share the
caller-selected 500 ms quote cadence, fixed 10 ms new/cancel latency, fees,
execution assumptions and seed 7. Quote cadence is a diagnostic configuration,
not a tuned optimum or measured exchange latency.

K=2..5 and ten restarts were registered. No fitting attempt was executed:
the eligible whole-day partition could not be formed. The failure must not be
described as a zero-return strategy, a failed alpha model or a negative paired
policy result—those require actual model/run evaluation.

## What the tapes support

The one-second feature census is:

| Source | Valid | Other feature statuses |
| --- | ---: | --- |
| February 26, legacy clip | 0 | 18 invalid clock, 5 invalid book |
| March 4, first legacy clip | 0 | 28 invalid clock, 2 invalid book |
| March 4, second legacy clip | 0 | 28 invalid clock, 2 invalid book |
| March 4, third legacy clip | 0 | 28 invalid clock, 2 invalid book |
| September 30, schema-v3 diagnostic | 8 | 10 warming, 4 stale, 2 invalid book |
| October 5, fresh schema-v3 diagnostic | 112 | 10 warming, 6 stale, 2 invalid book |

Only two dates have admissible features. The reader therefore rejects the
three-way chronological split. Dates containing invalid records are not silently
promoted to training days, and short snippets are not complete joint-valid days.
The 250 ms extraction independently reaches the same eligibility failure.
Its valid rows total 30 on September 30 and 451 on October 5.

An earlier, preserved attempt also stopped on the June tape's zero-quantity
trade prints, which are inadmissible under this feature contract. Neither the
prints nor their timestamps were rewritten, rounded or silently dropped. A
separate older February file contains a grossly inconsistent timestamp and was
not admitted to the registered universe. These findings reinforce why old PnL
reports remain superseded rather than headline economic evidence.

All observed sources also have changing wall/logical offsets, including small
legacy float-conversion differences. Native risk/economic audits can still be
verified, but the conservative fixed-UTC clock comparison does not assert an
exact projection for those sources. No 30/5/60-minute interval is published from
this audit. The statistical implementation is tested on fixed-clock fixtures;
that is a different proof from a real-data confidence interval.

## Fresh capture evidence

The October 5 public BTCUSDT capture finalized one Zstandard segment containing
2,364 records: 1,196 aggregate trades, 1,161 depth updates, one snapshot, one
instrument record, four capture controls and one capture metadata record.
Configured observation time was 120 seconds; receipt span including shutdown
was 130.02 seconds. It used public streams and no credentials or order entry.

Receipt/schema validation passed, with zero sequence gaps/regressions, zero
monotonic regressions, a visible trailer and no capture invalidation events.
The bounded writer recorded zero overflows and all 2,364 enqueued/written
records; high-water was seven and measured maximum writer lag was 27.701 ms.
Hashes and writer metadata are retained in the machine-readable record.
This is not a 24-hour soak, certified joint-valid coverage, measured trading
latency or profitability evidence.

The local Windows environment had optional `aiodns` installed; its default
Proactor event loop could not initialize the resolver. Capture was rerun using
Python's explicit selector-loop factory. The failed startup produced no tape
and is not counted as a successful capture. A clean dependency environment is
still the recommended reproduction path.

## Reproduction and remaining release work

```bash
python -m pip install ".[hmm]"
python experiments/run_hmm_regime_study.py --help
python -m pytest -q tests/test_hmm_study.py tests/test_hmm_collection.py \
  tests/test_clock_bootstrap.py tests/test_hmm_artifact_cache.py
python scripts/reviewer_gate.py
```

The JSON record contains the exact real-data command and input identities.
That command needs the external tapes and a new output directory. The tests
exercise the full successful comparison path with generated independent tapes,
including baseline/observe parity and every registered variant; they do not
depend on local raw captures.

Required remaining proof includes a sufficiently large admissible chronological
universe, the actual real-data paired policy study, execution/economic confidence
intervals where justified, and representative baseline/observe/policy overhead.
The original platform's end-to-end Rust parity, long fuzz/soak, ten-day holdout
and dedicated-host performance gates are separately incomplete.

The technical work sample is stronger because it makes this boundary observable:
a study cannot manufacture eligibility, confidence or a favourable result from
broken or insufficient inputs.
