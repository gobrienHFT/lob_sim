# Registered synthetic HMM comparison

This is a workflow diagnostic, not a held-out market study. The command below
completed at clean source `0c8d505f9c419c3d6d12a155be6297b296ee08b3` using the
actual registered runner, fitter, simulator and serialized audit verifiers.
It generates five independent three-minute UTC snippets. There is no exchange
authentication, supplied data file or private execution information.

```powershell
python -m pip install -e ".[hmm]"
python experiments/run_hmm_regime_study.py --synthetic-demo --out-dir outputs/hmm_paired_demo
```

Use a new output path for every run. A partial or failed attempt remains visible
and cannot be overwritten. The
[compact reference](hmm_synthetic_study_reference.json) binds the original clean
producer, raw input hashes, native manifest and full native study-report hash.
It is a projection, not a replacement for the generated audit bundle. Native
run-directory timestamps and source/dependency identities need not reproduce
byte-for-byte after a code or environment change. The five raw input sources
are deterministic; a regression regenerates and hashes them independently.

## Frozen design and model selection

The first three dates calibrate, the fourth validates, and only the fifth is
evaluated. These are synthetic snippets, **not** five complete joint-valid days.
The registry is frozen before extraction/test access. Each source starts its
own native clock, engine and zero inventory. Hidden generator labels never
enter the study's input files or model-selection criteria.

All variants share seed 7, fixed latency, fees, public-L2 execution assumptions
and hard risk limits. The declared common diagnostic workload uses 0.010 order
quantity and 500 ms requoting. It does not round suppressed sub-lot quotes upward
or loosen the 0.05 position cap. Empirical latency draws are not paired by merely
reusing a seed; this runner explicitly requires the fixed-latency scenario.

| Model | Calibration / validation rows | K | Selected restart | Attempts / failures |
| --- | --- | --- | --- | --- |
| Primary, 1 s samples | 510 / 170 | 5 | 9 | 40 / 0 |
| Cadence sensitivity, 250 ms samples | 2,037 / 679 | 5 | 8 | 40 / 3 |

Both evaluate K=2..5 with ten deterministic restarts. The cadence failures remain
in the ledger: two likelihood/convergence failures and one indistinguishable-
emission-means rejection. K=5 is not evidence of five true market regimes; the
generator has two hidden states. Calibration signatures and emission means are
model characterization, not execution-outcome training targets.

```powershell
python -m lob_sim.cli regime-inspect --model outputs/hmm_paired_demo/study/primary/model.json
python -m lob_sim.cli regime-inspect --model outputs/hmm_paired_demo/study/cadence_250ms/model.json
```

## Test-snippet outcomes, without a benefit claim

| Variant | Fill events | Fees, quote units | Final inventory, lots | Final marked net PnL |
| --- | --- | --- | --- | --- |
| Baseline | 477 | 15.879825 | -50 | Unknown |
| Observation only | 477 | 15.879825 | -50 | Unknown |
| Posterior-risk policy | 408 | 11.14377024 | -40 | Unknown |
| Hard-active risk sensitivity | 396 | 10.987771 | -41 | Unknown |
| 250 ms cadence sensitivity | 413 | 11.38776252 | -39 | Unknown |

Baseline and observer have exactly the same 9,569 core event rows, fill bytes
and markout bytes. Observation-only diagnostics are measured through sidecars
only after this identity is established. Policy results are deliberately not
required to match baseline actions. The reference does not select the best PnL
variant or omit unsuccessful fits.

All final marked PnLs remain null: the capture ends with open inventory and
200 ms of unpriced held-inventory time. A previous valid mark is not silently
carried through that invalid tail. State-level observed drawdown maxima are
descriptive and non-additive, not causal drawdown contributions. The serialized
reports retain the exact cash/fee ledger, missing-mark durations and zero gap
bridges separately.

Each comparison has only two common complete eligible UTC minutes. The 30-minute
primary and 5/60-minute sensitivity intervals are all null, with the explicit
short-contiguous-stratum reason. Point estimates of mean eligible-minute equity
changes are not whole-run PnL, full-path drawdown intervals or statistically
supported improvement. Fill activity is reported as taker-order arrivals in
these runs; this fixture does not prove passive queue-fill quality.

## Independently verify the generated run reports

The following discovers the actual timestamped directories rather than inventing
their names. The report command verifies all serialized regime, quote,
execution, risk and economic parents before printing per-state results. All four
commands completed successfully for the recorded reference.

```powershell
$hmmStudy = Get-Content outputs/hmm_paired_demo/study/study_report.json -Raw | ConvertFrom-Json
foreach ($hmmRun in $hmmStudy.results) {
    if ($hmmRun.variant -ne 'baseline') {
        python -m lob_sim.cli regime-report --run-dir (Join-Path outputs/hmm_paired_demo/study $hmmRun.run_dir)
    }
}
```

The full bundle preserves fitted models, all 80 attempts, registry, source
identities, event/fill/markout traces, frozen decision/arrival/pre-fill state
labels, quote denominators, state-conditioned outcomes, integer time-weighted
exposure, exact economics and clock-period comparisons. It is generated locally;
large raw inputs and audit traces are not committed.

For a real-data study use repeated `--input` arguments with independent immutable
single-UTC-day tapes; do not rewrite their receipt clocks using the synthetic
exporter. The [real-data eligibility audit](hmm_regime_reference.md) currently
fails before a real-model fit. Representative valid days, real calibration and
untouched held-out comparisons remain necessary before making economic claims.
