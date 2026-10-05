# Known-regime synthetic market-tape recovery

This diagnostic checks raw-tape reconstruction, causal features, train-only
model selection and forward inference together. It does not test a market-making
policy or show that Binance has these regimes. The fixture is market-by-price;
the exact synthetic market-by-order matching venue is separate.

## Reproduce

At source commit `60b0696`, in the pinned environment recorded in the
[machine-readable reference](hmm_synthetic_recovery_reference.json):

```bash
python -m pip install ".[hmm]"
python experiments/run_hmm_synthetic_recovery.py --out-dir outputs/hmm_synthetic_recovery
python -m pytest -q tests/test_hmm_recovery.py
```

Use a new output directory. The command never overwrites previous evidence.
Regeneration at another source revision changes the source/model identity even
when the generated market tape is identical. Floating fitting equality is only
promised in a pinned environment, not across arbitrary libraries/CPUs.

Seed 7 produces 11,623 ordinary schema-v3 market/control records and 2,400 hidden
labels, stored separately. Five eight-minute snippets occupy distinct UTC days;
both streams disconnect at each boundary. They are not five complete joint-valid
days. The authoritative engine emits 2,408 feature rows: 2,350 valid, 50 warming
up and eight invalid-book samples. Quoting is disabled during extraction.

The chronological split uses three calibration snippets, one validation snippet
and one diagnostic test snippet. The existing registry freezes the entire
specification before extraction. Scaling, candidate fitting and state signatures
use calibration only. Validation chooses K; neither test features nor test truth
can change model selection or the subsequent training-only label alignment.

## Selection and recovery

All forty deterministic restart attempts were retained and passed their
configured numerical/convergence gates. The best train-likelihood restart for
each K was scored on validation; the frozen validation/BIC rule selected K=5.

| K | Validation log-likelihood per observation | Training BIC |
| --- | ---: | ---: |
| 2 | -8.4527 | 23,222.33 |
| 3 | -6.5754 | 17,016.15 |
| 4 | -0.0291 | -1,256.27 |
| 5 | 0.1915 | -1,709.01 |

The hidden process has two generating states, but rolling-window emissions are
mixed near switches and are not a conditionally Gaussian Markov realization.
Selection is not forced to return the known generating state count. The selected
states map `[0, 0, 1, 1, 1]` using calibration forward predictions only. This is
explicitly **many-to-one classification**, not a permutation or perfect cluster
recovery. Native adjusted Rand index exposes the additional fitted clusters.

| Partition | Valid samples | Native raw ARI | Mapped raw accuracy | Active accuracy, all samples |
| --- | ---: | ---: | ---: | ---: |
| Calibration | 1,410 | 0.4947 | 100.00% | 82.34% |
| Validation | 470 | 0.4677 | 100.00% | 82.13% |
| Diagnostic test | 470 | 0.4672 | 100.00% | 84.47% |

On the test snippet, raw classification confirmed 27 of 30 known switches with
a mean **confirmation-available** lag of 2.50 s. It requires three consecutive
mapped matches, so short episodes can remain undetected. Active hysteretic labels
matched 22 of 30 switches, with mean first-available matching lag 2.64 s among
detected switches. Undetected/cutoff switches remain censored; their delay is not
zero and they are not dropped from the switch denominator. Active accuracy also
keeps initially unavailable labels in the all-sample denominator.

The full generated report contains each switch and its cutoff. The committed
reference contains the switch census, confusion matrices, sequence-separated
transition counts/errors, complete/censored episode summaries and parent hashes.
There is no direct two-state permutation comparison of fitted transition
parameters for this selected five-state model. Its five geometric durations stay
separate from the generating two-state durations (25 s and 12.5 s). Mapped
empirical transitions are descriptive, not proof the collapsed process is Markov.

## Evidence and limits

Two complete default runs produced byte-identical model, fit report, recovery
report, registry, alignment, feature manifest and synthetic-tape manifest. The
semantic model identity is
`8617536945f704aed7d5ab9e456c4b2d2cb11983f52df552e1637df1e4fd6109`;
the raw tape identity is
`dcf4615f83a9e8ce851fea144bb7631b248fff5d7334d15b6735d7b33366505d`.
Semantic content identities and full-file byte checksums are different contracts.

Forty-seven new regressions check an independent integer book reduction,
pair-count ARI against scikit-learn, frozen test access, training-only alignment,
unavailable/censored denominators, causal availability lag, disjoint sequences,
corrupt future-test/truth data, deterministic generation and no-clobber output.
The focused recovery/dataset/fit/golden run passes 106 tests; repository
lint/format and full-package typing also pass. The full release gate is recorded
separately in the implementation ledger after verification.

One generation/extraction/fitting/evaluation run took 19.14 s while the reviewer
gate was also running. This single wall-clock diagnostic is **not** a baseline /
observation / policy overhead benchmark, a dedicated-host measurement or trading
latency. Offline analysis retains only a capped fixture; ordinary runtime windows
and audit export keep their separate bounded-memory contracts.

The strong mapped score is expected from deliberately separated visible states.
It does not establish hidden exchange truth, true Binance FIFO, economic benefit,
profitable alpha or production readiness. Registered paired market-data research
and representative three-mode overhead remain required. Imperfect clustering,
late detection and missed short states are valid findings, not errors to tune
away using the diagnostic test.
