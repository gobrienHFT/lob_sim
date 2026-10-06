# Short public BTCUSDT overhead fixture

This is a finalized 130-second Binance USD-M public capture from 2026-10-05:
2,364 records, one 750,136-byte Zstandard segment, and its original manifest.
The files are copied byte-for-byte from the capture, without invented clocks,
renumbered receipts, repaired books or a fabricated completion trailer.
Its validation covers schema and receipt integrity; simulation still performs
normal book synchronization and execution-validity handling.

This compact fixture is for **offline cost measurement**, not a soak, complete
UTC research day, held-out economics, private fill truth or exchange latency.
Zero recorded internal writer overflows is not proof of zero venue-side loss.

`synthetic_frozen_model.json` is the unchanged five-state model from the
[known-regime synthetic recovery](../../strategy_results/hmm_synthetic_recovery_reference.md)
study. Its original training provenance and source identity remain inside the
artifact. It was not fitted or selected on this public capture. Loading it here
exercises causal inference and conservative policy work, not calibrated real-
market state inference or evidence of economic benefit.

Hashes (SHA-256):

| Artifact | SHA-256 |
| --- | --- |
| Manifest file | `a60ded6cea347f0396fe01a4a423f1a31f60bd7c3ab4be8c2f4b976ba8b5a7aa` |
| Compressed segment | `514d464398c4ce2d1951a542c62be71681815935bdf68744921658ca11a0ce0a` |
| Frozen model file | `bacd6ec0631f89d66c02cddeb4177d28be390a8fbfef7a7731462fab52524a40` |
| Model semantic identity | `8617536945f704aed7d5ab9e456c4b2d2cb11983f52df552e1637df1e4fd6109` |

The declared shared environment uses ten lots (`0.010` BTC at a `0.001` step)
and a 500 ms reference quote cadence. It keeps the same hard `0.05` BTC position
cap, fees, execution assumptions, seeds and latency in every mode. This size was
chosen to exercise multi-lot quoting, not optimized against PnL. The original
one-lot/inactive-policy smoke remains separately documented and unchanged.

```bash
python -m lob_sim.cli validate --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json
python experiments/benchmark_hmm_overhead.py \
  --file docs/benchmark_inputs/hmm_public_btcusdt_20261005/capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json \
  --model docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json \
  --env docs/benchmark_results/hmm_active_public_reference.env \
  --json-out outputs/hmm_public_overhead_new.json \
  --require-active-policy --warmups 3 --repetitions 30 --memory-runs 1
```

Use a fresh output name. The v2 benchmark checks valid HMM inference and, when
requested, at least one actual accepted resting policy quote in every replay.
Fills are not required. It records lifecycle/workload counts, untraced timings,
separate traced-memory runs and baseline/observation core identity. A failed
active-policy check must be reported, not bypassed to make a benchmark pass.
The capture and model identify a short workload, not the whole market.
