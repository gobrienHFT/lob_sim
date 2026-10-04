# Project status

The current artifact is an offline market-by-price replay and
execution-sensitivity laboratory. Its strongest evidence is runnable mechanics:
validity boundaries, causal order lifecycles, risk reservations, accounting,
checkpoint continuation, and an independently tested synthetic matching venue.
It is useful as a technical work sample without claiming that the original
HFT-platform roadmap is complete.

## What can be reviewed now

- Schema-v3 receipt identity, segmented capture integrity, book synchronization,
  and independent depth/trade validity epochs.
- Named public-L2 execution and latency scenarios, with auditable fill evidence.
- Pending-cancel fills, arrival-time post-only checks, reserved exposure,
  missing-mark handling, and gross/fee/net accounting.
- JSON checkpoint continuation and bounded streaming audit exports. Checkpoint
  loading rejects input, configuration, Python source and adapter identity
  drift before restoring state. Resume does not append an existing streaming
  export and requires the same pinned environment.
- Exact synthetic participant/order-level price-time matching, kept separate
  from inferred historical execution.
- Python/Rust differential checks for the published kernel primitives and a
  composed smoke sequence. The parity report explicitly keeps
  `full_engine_parity: false`.

The [claim matrix](claims.md) specifies the limits of each capability. The
[results memo](reviewer_results_memo.md) explains the committed fixtures;
historical real-data PnL reports are not current economic evidence.
An [extended verification job](long_verification.md) exercises larger kernel
corpora with multiple seeds independently of the fast release gate.
The [live capture diagnostic](capture_results/live_smoke_20260930.json)
records a short before/after receipt-order check; it is not soak or research
evidence, and the raw tapes remain local.

## What still separates this from the original HFT-platform goal

| Requirement | Evidence still needed |
| --- | --- |
| End-to-end Rust hot path | Integrated engine-level differential traces for every execution scenario, accounting state, checkpoint, and manifest; the million-event valid/invalid corpus and long fuzz evidence. |
| Representative capture reliability | External 24-hour BTCUSDT/ETHUSDT capture, independently audited drops/liveness/invalid intervals, at least 95% joint-valid wall time, and bounded-memory soak results. |
| Defensible strategy research | Ten joint-valid UTC days, chronologically frozen calibration/validation/test partitions, registered variants, paired scenario/latency comparisons, and the held-out report. |
| Release-grade performance | Documented dedicated host, representative and ten-million-event corpora, warmups/repetitions, tail timings, allocation/RSS measurements, and a profile. Targets must be met or explicitly reported as missed. |
| Review ownership | Human review of venue semantics, causal ordering, risk policy, statistical choices, and public claims. Automated tests do not replace this signoff. |

## Why it is useful as a work sample

For market-data and quantitative-development roles, the project makes difficult
engineering choices inspectable: validating unreliable inputs, preserving
causality, recovering state, accounting for partial fills, and distinguishing
observations from assumptions. The Python/Rust boundary, independent oracles,
regression tests, installable demo and release checks show how those choices
can be turned into maintainable software.

For research-facing roles, its value is reproducible experiments and explicit
execution uncertainty—not evidence of profitable alpha. A reviewer can change
an execution or latency assumption and inspect what changed on the same tape.
The project complements interview performance and domain experience; it does
not by itself establish production experience or readiness for a particular
employer.

There is no defensible single “percent finished” for these independent gates.
A green fixture suite is not soak evidence, a throughput target is not a
measurement, and synthetic FIFO is not historical Binance FIFO.

## Causal regime research in progress

An optional HMM foundation now provides twelve fixed-grid microstructure
features, bounded trailing windows, train-only clipping/scaling, immutable
diagonal Gaussian parameters, a custom log-space forward filter, confidence/
entropy and hysteresis, and checksummed no-clobber JSON model artifacts.
Independent latent-path enumeration tests distinguish filtering from smoothing;
prefix-mutation tests check that future observations cannot rewrite earlier
features or inference. The pre-HMM summary/trace behavioral hashes are frozen.

Validated raw-tape extraction now uses read-only observations from the existing
engine and writes checksummed, no-clobber UTC-day feature files. Calibration and
validation readers do not open test row files. The optional fitter supports
K=2..5, seeded restarts, failure ledgers, train-only preprocessing, explicit
convergence/numerical gates, validation/BIC selection and training-only canonical
state signatures. `regime-features`, `regime-fit` and `regime-inspect` are available.
Synthetic numeric fitting tests are not representative exchange/economic evidence.

Observation-only inference now runs through the authoritative simulation clock
without changing the paired strategy's actions, fills, queue model or accounting.
It streams samples and immediate feed invalidations, exposes decision diagnostics,
and includes the frozen model in a verified audit bundle. Bounded feature/filter/
hysteresis checkpoints resume identically through market, control and outage
boundaries. Disabled configurations retain their pre-HMM behavioral hashes.

Decision/arrival/fill attribution, state-conditioned execution metrics,
hard-risk-dominated policy adaptation, paired economic evaluation and overhead
evidence remain required. No trained real-market model, alpha or holdout claim
is made. See the [model specification](hmm_regime_model.md) and
[acceptance ledger](hmm_implementation_plan.md).

## Integrity repairs in this revision

Adversarial review identified cases absent from the previous regression suite:

- Reading a capture through its manifest must enforce the same market-payload
  schema validation as reading a segment directly. A valid checksum does not
  make a malformed trade or depth payload valid.
- Checkpoint decoding must restore shared mutable order and overlap-credit
  references. A matching queue and its lookup index cannot hold independent
  copies of the same partially filled order.
- Checkpoints bind their Python source files and declared adapter contract,
  including source in an installed package without Git. Older unbound schemas
  are rejected instead of silently resumed under changed code.
- Requote timers faster than new-order transit must retain one outbound intent
  per quote slot. A delayed duplicate cannot silently cancel a live or
  pending-cancel quote; replacing it requires its modeled cancel acknowledgement.
- A capture identifier must not reuse or overwrite an existing tape. Failed
  and unfinished captures remain visible rather than being silently replaced.
- Websocket cancellation housekeeping cannot split receipt timestamping from
  global sequence assignment. A live two-symbol smoke test exposed that race;
  the regression checks the boundary without clamping or inventing timestamps.
- An installed-wheel demo must carry its own offline fixture and configuration;
  it cannot depend on an adjacent repository or the caller's environment.

Each repair has a regression test. Existing evidence packs are retained rather
than regenerated into new economic claims.

The subsequent storage-boundary review adds adversarial cases for malformed
segment/manifest JSON roots, unknown recovery records and schema versions,
invalid UTF-8 and corrupt compression. Recovery is prefix-only: it does not
skip damage to yield later observations. Arrow normalization now preserves
existing final/partial outputs, rejects source/destination aliasing, fails
closed on publication races, and checks input-file identity before publication.
These are integrity improvements, not new economic or performance evidence.

## Reproduce the release checks

From a checkout with the development dependencies and pinned Rust toolchain:

```bash
python scripts/reviewer_gate.py
```

The gate also builds a Python wheel and runs its demo from an empty directory
in a separate environment. To repeat just that installation check, use
`python scripts/check_installed_package.py`.

The report at `outputs/reviewer_gate_report.json` records the commit, dirty-tree
state, environment, executed commands, and results. GitHub Actions runs the
gate on Linux/Python 3.11–3.13 and a Windows/Python 3.13 smoke path. Neither CI
nor the fixture benchmark certifies production readiness or trading latency.
