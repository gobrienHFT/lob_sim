## Reproduction commands

Run only from `C:/bitbucket/kibert/lob_sim`, with the pinned reviewer dependencies
installed. Every output path below must be new; preserve failed attempts rather
than clearing or reusing directories. Do not edit/stage/commit source while a
native export or source-guarded measurement is running.

Prepare one synthetic input and reuse its exact bytes for matched trials:

```powershell
python experiments/prepare_research_scale.py --out outputs/repro-scale-input --rounds 100000 --levels 20
$scaleFixture = Get-Content outputs/repro-scale-input/fixture.json -Raw | ConvertFrom-Json
$scaleInput = 'outputs/repro-scale-input/' + $scaleFixture.view.path
$scaleContract = 'outputs/repro-scale-input/contract.json'
$scaleModel = 'docs/benchmark_inputs/hmm_public_btcusdt_20261005/synthetic_frozen_model.json'
python experiments/benchmark_research_scale.py --file $scaleInput --contract $scaleContract --model $scaleModel --out outputs/repro-scale-raw --mode baseline --workload raw --warmups 1 --repetitions 3
foreach ($scaleMode in @('baseline','observe','policy')) {
    python experiments/benchmark_research_scale.py --file $scaleInput --contract $scaleContract --model $scaleModel --out "outputs/repro-scale-null-$scaleMode" --mode $scaleMode --workload null --warmups 0 --repetitions 1
    if ($LASTEXITCODE -ne 0) { throw 'Retain and inspect the failed case; do not publish a surviving subset' }
}
```

Use 25,000 rounds for the 100,012-record view or 4,000 for the 16,012-record
view. Raw generation has four rows per round plus ten controls; normalization
adds two explicit controls. Event content is deterministic; creation metadata
and source-bound normalized controls mean separately generated file/run hashes
need not equal the archived measurement's hashes. Generate once, freeze and
reuse files rather than claiming a freshly created tape is byte-identical.
The five-state model is frozen and synthetic-trained, not calibrated to these
performance fixtures or the short public capture. Policy inactivity must remain
visible in the report rather than being tuned away.

The interleaved comparison isolates the selected old/new hot functions inside
one current engine, not an old whole binary. Use the full trusted local commit:

```powershell
python experiments/prepare_research_scale.py --out outputs/repro-scale-short --rounds 4000 --levels 20
$scaleShort = Get-Content outputs/repro-scale-short/fixture.json -Raw | ConvertFrom-Json
$scaleShortInput = 'outputs/repro-scale-short/' + $scaleShort.view.path
$scaleShortContract = 'outputs/repro-scale-short/contract.json'
python experiments/paired_research_scale.py --file $scaleShortInput --contract $scaleShortContract --model $scaleModel --before-commit f18c458e37d24a1606a7cc831f78ea64507dec29 --out outputs/repro-scale-paired --warmups 1 --repetitions 3
```

Preserve all warm and measured rows. Run profiling separately from ordinary
timing, using this same short fixture:

```powershell
python experiments/benchmark_research_scale.py --file $scaleShortInput --contract $scaleShortContract --model $scaleModel --out outputs/repro-scale-profile --mode observe --workload null --warmups 0 --repetitions 1 --profile
```

Full audit export also independently reopens native artifacts and reduces
clock tables. Budget retained outputs and temporary indexes before running it:

```powershell
python experiments/prepare_research_scale.py --out outputs/repro-scale-audit-input --rounds 25000 --levels 20
$scaleAudit = Get-Content outputs/repro-scale-audit-input/fixture.json -Raw | ConvertFrom-Json
$scaleAuditInput = 'outputs/repro-scale-audit-input/' + $scaleAudit.view.path
python experiments/benchmark_research_scale.py --file $scaleAuditInput --contract outputs/repro-scale-audit-input/contract.json --model $scaleModel --out outputs/repro-scale-audit --mode observe --workload audit --warmups 0 --repetitions 1
```

This full-audit recipe deliberately uses 100,012 records, not the larger
400,012-record null-sink input. The latter has not been demonstrated to fit
the available disk budget. For the smaller monitored resource experiment,
prepare 800 rounds at 50 ms (3,212 normalized records over 40 seconds). The
controller uses a fresh child and managed temporary directory. Run allocation
tracing separately on this same small fixture:

```powershell
python experiments/prepare_research_scale.py --out outputs/repro-scale-resource-input --rounds 800 --period-ns 50000000 --levels 20
$scaleSmall = Get-Content outputs/repro-scale-resource-input/fixture.json -Raw | ConvertFrom-Json
$scaleSmallInput = 'outputs/repro-scale-resource-input/' + $scaleSmall.view.path
python experiments/research_scale_resources.py --file $scaleSmallInput --contract outputs/repro-scale-resource-input/contract.json --model $scaleModel --out outputs/repro-scale-resources --mode observe --timeout-seconds 900
python experiments/benchmark_research_scale.py --file $scaleSmallInput --contract outputs/repro-scale-resource-input/contract.json --model $scaleModel --out outputs/repro-scale-memory --mode observe --workload null --warmups 0 --repetitions 1 --memory
```

To compare two archived worker reports on identical input/configuration/worker
bytes:

```powershell
python experiments/compare_research_scale.py --before BEFORE/report.json --after AFTER/report.json --out outputs/repro-scale-comparison.json --file MEASURED_VIEW.manifest.json
```

`--file` is mandatory for a full-audit comparison: every native bundle is
reopened and compared, not just its summary probe. The before/after library
identities are expected to differ; changing the measured worker, host identity,
input, model, configuration or instrumentation invalidates the comparison.

The public short-capture diagnostic is deliberately outside the registered
study admission API. Supply a finalized, provenance-declared paired raw source
and a fresh native audit produced by the same library version:

```powershell
python -m lob_sim.cli --env .env.example capture-audit --file PUBLIC_CAPTURE.manifest.json --out outputs/repro-short-audit
python experiments/diagnose_short_capture_execution.py --file PUBLIC_CAPTURE.manifest.json --audit outputs/repro-short-audit --model $scaleModel --out outputs/repro-short-cases
```

It retains all six predefined cells and cannot create an admitted day, fitted
model, holdout, private execution observation or informative confidence interval.

For read-only structural forensics of the existing incomplete attempt:

```powershell
python experiments/audit_incomplete_capture.py --source outputs/real_market_soak_25h_c88970e_20261007 --out outputs/repro-incomplete-forensics.json
python -m lob_sim.cli capture-soak-verify --bundle outputs/real_market_soak_25h_c88970e_20261007
```

The second command is expected to reject the incomplete attempt with exit code
2. Do not finalize/rename its partial files. Fresh forensic timestamps and
processor identity differ from the retained original census; original source
bytes must not change.

## Next continuous public capture — not launched by this review

A 48-hour attempt contains at least one whole intervening UTC date regardless
of its start phase. That is geometry, not a guarantee of 99% captured coverage,
95% joint validity, stable instruments or absolute UTC-clock accuracy. Check
local processes manually for an existing collector and wait for research jobs
to finish. The recorded C: free space was insufficient for the approximately
5.62 GB planning allowance (observed rate plus 30%, retaining a 2 GiB floor).
The 6 GiB starting-free-space guard below adds headroom; it is not a bound on
future message rates or unrelated machine writes.

```powershell
Set-Location -LiteralPath C:\bitbucket\kibert\lob_sim
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | Select-Object ProcessId,CommandLine | Format-List
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory,FreeVirtualMemory
Get-PSDrive -Name C
if ((Get-PSDrive -Name C).Free -lt 6GB) { throw 'Insufficient planning headroom; preserve old evidence and arrange more space first' }
python -m lob_sim.cli --env .env.example capture-soak --seconds 172800 --out outputs/paired_48h_scale_01
python -m lob_sim.cli capture-soak-verify --bundle outputs/paired_48h_scale_01
```

Do not launch if another collector is present or resource headroom is unclear.
Use a fresh directory for every attempt; an interruption restarts separately
with the documented `--restart-from` workflow, never appends or finalizes the
old attempt. The two-day duration does not replace the ten admitted-day rule.

Extended correctness and workload checks remain separate from the front door:

```powershell
python scripts/reviewer_gate.py
python scripts/core_regression_probe.py --expected docs/regression_results/repaired_core_baseline.json --write outputs/repro-corrected-core.json
python -m pytest tests/test_research_study_graph.py tests/test_research_study_recovery.py -m long_research -q
```

The extended workload workflow runs bounded synthetic decoding and full audit
on Windows/Linux without noisy throughput thresholds. Its separate corrected-core
job checks the immutable 112-case/648-checkpoint reference, never refreshes it.
Use the [registered workflow](real_market_research.md) for the actual immutable
ten-day protocol and durable `--case-store` / `--resume-store` commands. Short
diagnostics are not a substitute for that admission path.
