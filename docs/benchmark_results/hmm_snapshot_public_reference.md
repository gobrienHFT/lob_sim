# Snapshot-cache HMM replay measurement

This is a completed offline engineering measurement, not trading latency or
evidence of policy benefit. The [full native report](hmm_snapshot_public_reference.json)
preserves all raw samples from clean source `676ec151e30d329ca0cdca6588bd5120f5de68ba`.
Its semantic SHA-256 is
`f5be34f52507bc0d32edf4c62340a005a61059b56dbc1a02b3fe270967669a43`.

## Result and scope

| Mode | Median seconds | Run p95 seconds | Run p99 seconds | Median matched-round runtime | Traced replay peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 33.999767 | 40.983118 | 60.485719 | 1.000 | 28,383,613 |
| Observation | 61.822969 | 70.583834 | 74.734162 | 1.806 | 29,287,847 |
| Policy | 58.435707 | 67.251024 | 73.575675 | 1.671 | 29,256,016 |

Matched observation overhead is **80.65%**; policy overhead is **67.13%**.
Ratios are the medians of thirty matched-round ratios, not ratios of medians.
The HMM path still has material engineering cost. Policy changes the quoting
and fill workload, so its runtime is not an isolated inference measurement.

The [earlier measurement](hmm_active_public_reference.md) remains unchanged.
Absolute timings in this run are more than three times slower in every mode.
Both runs used an ordinary unpinned host with uncontrolled power/frequency and
application load. Lower descriptive relative overhead is **not a causal cache
speedup**, a dedicated-host regression result or a performance confidence interval.
The independent snapshot tests prove fewer normalizations and unchanged
behavior; these two uncontrolled runs do not quantify that optimization's gain.

## Same inputs, same behavior

The original [public input bundle](../benchmark_inputs/hmm_public_btcusdt_20261005/README.md)
and [shared environment](hmm_active_public_reference.env) are byte-unchanged.
The capture contains 2,364 records over about 130 seconds. Its frozen five-state
model is **synthetic-trained**, not fitted or selected on this public tape.
All modes share ten-lot (`0.010` BTC), 500 ms quoting, a `0.05` BTC hard cap,
fixed 10 ms new/cancel latency, fees, queue assumptions and seed 1.

| Mode | Quotes | Accepted resting arrivals | Cancels | Modeled fills | Immediate-fill arrivals |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1,024 | 288 | 288 | 394 | 394 |
| Observation | 1,024 | 288 | 288 | 394 | 394 |
| Policy | 473 | 229 | 229 | 218 | 218 |

Every configuration and complete bounded replay probe matches its corresponding
mode in the earlier report, including native lifecycle counts, final book/core
identity, fill/markout hashes, latency state and HMM diagnostics. These probes
are not full event-by-event traces; the separate snapshot/observer oracle tests
provide that proof. Baseline/observation core SHA-256 remains
`f5c89ef631378d90c5424f778f82e1d8b79d5e9e3562310e47b479858bc8aec1`.

Accepted resting quotes are required in every policy replay; fills are not.
All reported fill activity comes from immediate-fill arrivals. This does not
prove passive historical queue fills or policy superiority. Both HMM modes have
112 valid, ten warming, six stale and two invalid-book samples, with eleven
confirmed switches. Sample-grid counts do not certify joint-valid wall coverage.

## Measurement contract

The native run completed three warmups, thirty untraced measured repetitions
and one separate traced-memory replay per mode: 102 fresh engines. The six
mode orders each occur five times in the measured phase. Deterministic probes,
active-policy checks and unchanged input/model/package/script checks all passed
before exclusive final publication. No incomplete result was promoted.

Timing includes engine construction, input read/hash/decode, simulation and EOF
draining. Model loading, validated metadata preflight, GC preconditioning,
bounded probe reduction and publication are outside. Normal GC stays enabled;
tracemalloc is disabled during timing. Null/aggregate sinks retain no event,
fill or markout detail rows. The separate traced peak excludes the already-loaded
model and is not RSS, per-event allocation evidence or a long-memory soak.

The host was Windows 11 build 26200, Python 3.13.1 and an unpinned Intel
i5-1035G7 with eight logical CPUs. Exact dependency versions are in the report.
Intermittent read-only repository/GitHub inspection and small ignored draft-file
writes occurred; no local tests, profiler, capture, install or tracked-source
edits ran during measurement. Host activity and power/frequency were uncontrolled.
Run p95/p99 values are not per-event or exchange latency.

The measured package has 81 Python files, SHA-256
`8b2c49e33fe9b6ff97d35bbb0d0d268a66e3d97b3fc23ca608d652d1b945dad8`.
The script SHA-256 remains
`8f16400f61c379f2f6b0dadbf649dce74ac1e649e520c2dd1843a1419c0d53c8`.
Publication is a later commit; neither this nor the earlier producer is relabeled.

## Reproduce

Install development requirements and use the measured revision, same parents
and a fresh output path. Timing will vary:

```bash
python experiments/benchmark_hmm_overhead.py \
  --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json \
  --model docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json \
  --env docs/benchmark_results/hmm_active_public_reference.env \
  --json-out outputs/hmm_snapshot_overhead_new.json \
  --require-active-policy --warmups 3 --repetitions 30 --memory-runs 1
python -m pytest -q tests/test_hmm_benchmark_publication.py tests/test_hmm_snapshot.py
```

Nine publication cases independently verify both reports' canonical identities,
portable parents, configuration/activity, balanced protocol, raw quantiles and
cross-report probe equality. The one-ULP median-oracle bound is arithmetic only;
all hashes and behavior comparisons remain exact. Broader workloads, profiling,
dedicated-host evidence and eligible real-market economics remain separate work.
