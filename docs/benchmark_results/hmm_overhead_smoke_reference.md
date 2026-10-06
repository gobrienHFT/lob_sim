# HMM overhead: synthetic diagnostic smoke run

**Policy mode sent no quotes. Its shorter runtime than observation mode is not
evidence of faster quoting or a better strategy.** This single-repetition run
exercises the benchmark and reveals a workload/configuration caveat; it is not
the representative release benchmark or an economic study.

The [compact machine-readable record](hmm_overhead_smoke_reference.json) retains
raw timings, workload counts, effective configuration, environment and original
report/input/model/source identities. The complete local report is
`outputs/hmm_overhead_20261005_probe.json`; its semantic report SHA-256 is
`eba768f8b228ce0fa8883a3403dac8ff0443a1ec2d4f8e1484ac6fec64891a74`.
The compact record is a labeled projection, not that full report.

## Workload and measurements

All modes replayed the same 11,623 records / 4,521,291 bytes with fresh engines,
fixed 10 ms new/cancel latency, the same fees/execution assumptions, simulation
seed 1, a 500 ms quote cadence, and null/aggregate sinks. No event, fill or
markout detail rows were retained in memory. The generated tape has five
eight-minute snippets on different UTC dates with compressed native logical
gaps; it is not five complete valid days or a real-market execution sample.

| Mode | Untraced replay seconds | Relative runtime | Traced replay peak bytes | Quote requests | Cancels | Modeled fills |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 29.496655 | 1.000 | 3,345,142 | 19,167 | 3,520 | 8,952 |
| Observation | 257.156042 | 8.718 | 4,658,626 | 19,167 | 3,520 | 8,952 |
| Policy | 83.612853 | 2.835 | 4,274,288 | 0 | 0 | 0 |

There was one warmup, one measured repetition and one **separate** traced-memory
run per mode. Ratios pair the single measured rounds; they have no inferential
confidence interval. With one sample, calculated p95/p99 values equal the
sample and say nothing useful about runtime tails or per-event latency.

Baseline and observation had identical bounded core-summary/book/fill/markout/
latency identities:
`30e9beeb5fe3ccd96436d471d94810eab44961f20daba8ab3f9240a7d9f1cc23`.
Each mode reproduced its probe across warmup, timing and memory runs. Both HMM
modes performed 2,350 valid inferences, with 50 warming and eight invalid-book
samples. The policy was not merely an inactive filter. This probe is not a full
event-by-event oracle; the independent trace and checkpoint tests are separate.

## Why policy mode did no quoting work

The reference size is `0.001` with a `0.001` lot step: one lot. The frozen
training-only state risk scores are `0.15/0.15/0.55/0.75/0.90`. Even before
uncertainty reductions, the best permitted size multiplier is
`1 - 0.75 * 0.15 = 0.8875`; flooring a reduced one-lot quantity yields zero.
Confidence, entropy and stand-aside rules can also inhibit quotes. This run
does not claim a per-reason attribution of all rejected quote opportunities.

The conservative lot/risk rules must not be rounded up or relaxed to make the
benchmark look active. A multi-lot configuration, if studied, must be explicit,
shared with the baseline and registered before test evaluation. This diagnostic
does not select such a quantity or establish a profitable policy.

Observation's substantial measured overhead is an engineering issue to
investigate, not an HFT-latency claim. The measurement includes causal features,
inference, diagnostics and validation—not isolated posterior math. Policy
changes the quote, fill and pending-horizon workload, so its ratio cannot
isolate inference overhead.

## Environment and measurement limits

Historical source was clean `4cde350a83030d5ba630a8a467d7bd85a07b8f48` on
`codex/hmm-regime-layer`; package identity and the benchmark script's own hash
are retained. Python 3.13.1 on Windows 11 build 26200 used NumPy 2.2.4,
hmmlearn 0.3.3, SciPy 1.18.1 and scikit-learn 1.9.1. The local host was an Intel
i5-1035G7, four physical cores / eight logical CPUs. It was unpinned, with
uncontrolled CPU frequency, power policy and other load. An approximately
12-second diagnostic profiler ran concurrently with part of the measured
round; this is not a dedicated-host comparison.

Timing includes engine construction, input reading/hashing/decoding, simulation
and EOF handling. Model loading, GC preconditioning, bounded diagnostic
reduction and publication are outside timing; ordinary runtime GC remains on.
The separate tracemalloc peaks start after the frozen model is loaded. They
are replay allocations, not total model memory, process RSS, steady-state
allocations per event or proof of bounded memory during a 24-hour soak.

## Reproduce

Use the historical source/environment and model/input hashes to reproduce this
workload; timings naturally vary. The frozen model came from the separately
documented [synthetic recovery](../strategy_results/hmm_synthetic_recovery_reference.md)
run at `60b0696`. Regeneration under a different revision changes model/source
identities even if the market tape is identical. Never overwrite old evidence.

```bash
python experiments/benchmark_hmm_overhead.py \
  --file outputs/hmm_synthetic_recovery_20261005_reference/tape/market.ndjson \
  --model outputs/hmm_synthetic_recovery_20261005_reference/model.json \
  --json-out outputs/hmm_overhead_new_smoke.json --requote-ms 500 \
  --warmups 1 --repetitions 1 --memory-runs 1
```

The benchmark now prints quote/cancel/fill workload and a warning when policy
issues zero quotes; that console-only change is later than this measured source.
The default protocol remains three warmups and thirty measured repetitions.
Representative data, an adequately active and explicitly declared policy
workload, dedicated-host performance work and the real registered economic
study remain outstanding. Offline Python replay throughput is not exchange or
trading latency.
