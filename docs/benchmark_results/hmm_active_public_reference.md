# HMM overhead on a short public BTCUSDT capture

This completed engineering benchmark exercises accepted policy quotes on an
unchanged public tape. It does **not** establish HMM policy benefit, passive
fill quality, real-market model calibration or trading latency. Observation
overhead remains substantial; this is not a production-HFT performance result.

The [full native JSON report](hmm_active_public_reference.json) includes every
timing sample, effective configuration, lifecycle count and source/model/input
identity. It is not a compact projection. Its semantic report SHA-256 is
`b2bb3fe11e8530afed71de63dc82d2d4846587eef865a494d06f85ab8797303b`.
The measured source was clean `8c2a0040e1bfb007c5b681d9081bc4391b0b351f`.

## Workload and results

All modes replayed the same 2,364 records from a finalized 130-second capture:
1,161 depth updates and 1,196 aggregate trades, plus metadata/control records.
The [portable input bundle](../benchmark_inputs/hmm_public_btcusdt_20261005/README.md)
contains the original 750,136-byte segment, manifest and frozen five-state
**synthetic-trained** model. That model was not fitted or selected on this tape.

The shared [environment](hmm_active_public_reference.env) declares ten lots
(`0.010` BTC), 500 ms reference quoting, the unchanged `0.05` BTC hard cap,
10 ms fixed new/cancel latency, seed 1, identical fees and execution assumptions.
Quantity/cadence were declared to exercise quoting, not selected against PnL.

| Mode | Median seconds | Run p95 seconds | Run p99 seconds | Median matched-round runtime | Traced replay peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 9.328034 | 12.712412 | 14.345012 | 1.000 | 28,384,126 |
| Observation | 17.435596 | 24.772904 | 25.576566 | 1.879 | 29,319,105 |
| Policy | 16.179587 | 21.985630 | 24.614644 | 1.699 | 29,281,218 |

Ratios are medians of the thirty matched-round ratios, not ratios of medians.
Observation's descriptive matched overhead is **87.90%**; policy's is **69.94%**.
Run quantiles are not per-event latency. There is no inferential performance
confidence interval or dedicated-host threshold result.

| Mode | Native quote count | Accepted resting arrivals | Cancels | Modeled fills | Immediate-fill arrivals |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1,024 | 288 | 288 | 394 | 394 |
| Observation | 1,024 | 288 | 288 | 394 | 394 |
| Policy | 473 | 229 | 229 | 218 | 218 |

The accepted-resting gate passes in every replay; fills were not required by
that gate. Immediate-fill arrivals account for the fill activity shown here:
this is not a demonstration of passive historical queue fills. Policy changes
quote, cancellation and fill work. Its lower runtime than observation mode
does not isolate inference cost, show faster quoting, or establish superiority.
No PnL or economic-benefit result is inferred from these counts.

Both HMM modes produced 130 samples: 112 valid, 10 warming, six stale and two
invalid-book samples. That is sample-grid activity, **not** joint-valid wall
coverage or a 95%-valid-day certificate. Invalid samples are not silently
promoted into valid inference. Eleven confirmed switches occurred in each mode.

## Correctness and measurement contract

There were three warmups, thirty untraced measured repetitions and one separate
traced-memory replay **per mode**: 102 fresh-engine replays in total. All six
mode permutations occur five times in the measured phase. Every replay must
reproduce its bounded probe; HMM inference must be active, and policy must have
both positive native quote count and accepted resting arrivals.

Baseline and observation reproduce the same bounded core summary, final book,
fill/markout hash chains and latency sampler state. Their core SHA-256 is
`f5c89ef631378d90c5424f778f82e1d8b79d5e9e3562310e47b479858bc8aec1`.
This is not an event-by-event trace oracle; independent observer/golden tests
remain a separate proof. Input, model, package and benchmark-script identities
were unchanged at publication. Neither the tape nor the model was rewritten.

Timing includes engine construction, input read/hash/decode, simulation and EOF
draining. Native input/metadata preflight, frozen-model loading, GC
preconditioning, bounded summary/hash reduction and publication are outside.
Normal GC remains enabled. Null/aggregate sinks retain no event/fill/markout
detail rows. Memory tracing is disabled during timing; its separate peak
excludes the already-loaded frozen model and is not process RSS, steady-state
allocations per event or long-soak evidence.

The host was an unpinned Intel i5-1035G7 / eight logical CPUs, Windows 11 build
26200, Python 3.13.1. The report records exact package versions. Power/frequency
and ordinary app load were uncontrolled; intermittent Git/GitHub inspection
occurred. No local tests or profiler ran during the measurement. Hosted CI ran
on other machines. These limitations preclude an HFT latency/release-threshold
claim. Public L2 still cannot identify private FIFO, hidden liquidity or fills.

## Reproduce and preserve earlier attempts

Use the recorded revision and input/model identities; timing will vary. Install
the normal development dependencies, then use a **new** output path:

```bash
python experiments/benchmark_hmm_overhead.py \
  --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json \
  --model docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json \
  --env docs/benchmark_results/hmm_active_public_reference.env \
  --json-out outputs/hmm_public_overhead_new.json \
  --require-active-policy --warmups 3 --repetitions 30 --memory-runs 1
```

The [first attempt](hmm_public_overhead_attempt_e259673.json) failed on an
insignificant Decimal grid-spelling mismatch. The
[second attempt](hmm_public_overhead_attempt_5951541.json) was deliberately
stopped for CRC32C implementation work. Neither published timings, and neither
supports a before/after speedup claim. The table-based checksum implementation
has an independent bit-stream oracle and validates the original stored CRCs.
The older [inactive synthetic smoke](hmm_overhead_smoke_reference.md) is preserved
with its original source and limitations.

Remaining release work includes broader representative workloads, profiling
the material HMM overhead, dedicated-host performance and eligible registered
multi-day economic evaluation. A completed short engineering benchmark does
not finish the original HFT-platform roadmap.
