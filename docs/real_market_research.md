# Real-market evidence workflow

This workflow separates capture reliability, data admission and economic
research. A successful capture is not an eligible UTC day; an eligible day is
not an execution model validation or a profitable strategy result.

The implementation specification is [real_market_evidence_design.md](real_market_evidence_design.md).
The existing short HMM study remains a diagnostic; this work does not relabel
it as held-out evidence.

## Available commands

Run from the standalone repository with the storage dependencies installed:

```powershell
python -m pip install -e ".[dev,storage,hmm]"
python scripts/reviewer_gate.py
```

Capture public BTCUSDT and ETHUSDT together. The default is 25 hours; use a
fresh directory for every attempt. No credentials or order entry are involved.

```powershell
python -m lob_sim.cli --env .env.example capture-soak --out outputs/paired_day_01
python -m lob_sim.cli capture-soak-verify --bundle outputs/paired_day_01
```

The runner fixes the paired universe and public production endpoints. It
preserves the complete initial symbol metadata and filters, sampled host RSS,
disk availability, event-loop lag and writer statistics. Sampling does not
establish a continuous memory peak. Limits default to 2 GiB free disk and 1 GiB
process RSS, with five-second samples and a 48-hour maximum per run. These
limits bound the measurement workflow, not real exchange latency.

An interrupted run keeps its request, failure receipt, reserved capture identity
and partial tails. Restart separately; never append the old tape:

```powershell
python -m lob_sim.cli --env .env.example capture-soak --out outputs/paired_day_01_restart --restart-from outputs/paired_day_01
```

The interruption gap stays unknown. The original directory is neither repaired
nor overwritten. Windows shutdown, power loss or forced termination may leave
no failure JSON; the missing completion/manifest or partial tail still fails
admission. A one-minute run tests mechanics only and cannot pass a 24-hour soak.

The evidence commands select BTCUSDT+ETHUSDT and require HMM disabled. Use
`.env.example` (or an explicit equivalent configuration) for native extraction
and admission. Extraction itself disables quoting:

```powershell
python -m lob_sim.cli --env .env.example capture-audit --file outputs/paired_day_01/CAPTURE.manifest.json --out outputs/day_01_audit
python -m lob_sim.cli --env .env.example research-days --file outputs/paired_day_01/CAPTURE.manifest.json --file outputs/paired_day_02/CAPTURE.manifest.json --out outputs/day_admission
python -m lob_sim.cli research-days-verify --bundle outputs/day_admission --file outputs/paired_day_01/CAPTURE.manifest.json --file outputs/paired_day_02/CAPTURE.manifest.json
```

Replace each `CAPTURE.manifest.json` with the actual finalized filename. Repeated
`--file` arguments supply exact sources; no glob silently omits a failed tape.
Verification accepts explicit source paths so a bundle can be moved without
rewriting evidence. All outputs are exclusive and preserve unsuccessful
attempts. Do not reuse an output directory.
For a one-symbol diagnostic, use `capture-audit --symbol BTCUSDT`; it cannot
provide paired research-day eligibility.

## Admission rule

The fixed `lob_sim.utc_research_eligibility.v1` rule requires both linear USD-M
instruments, stable full units/grid identities, declared public-network source,
complete writer/finalization/trailer and continuous receipt identities.
Depth/marks expire after five seconds; trades expire after sixty seconds.
Native book and independent stream epochs remain authoritative.

Every UTC day has an 86,400-second denominator. Admission needs at least 99%
captured coverage, 95% joint-valid time and 95% joint mark coverage. Missing
spans, stale data and invalid epochs are explicit. Overlapping independent
captures are rejected instead of deduplicated or counted twice. A short tape
with an excellent within-tape fraction remains an ineligible day.

The clock is a fixed first-receipt wall/monotonic projection. Later receipts
validate it within a declared 50 ms tolerance; they never fit a future anchor.
Absolute UTC accuracy is unmeasured. Missing provenance is not retroactively
inferred from prices or a plausible filename. Synthetic fixtures never earn
empirical admission.

The independent reader reopens source bytes, checks receipt identities and
metadata, integrates serialized interval geometry and re-applies the day rule.
It does not reconstruct a second exchange book or authenticate an author's
declared provenance. A checksum proves identity, not economic realism.

## Registration

Only after at least ten eligible days with stable instrument identities:

```powershell
python -m lob_sim.cli --env .env.example research-register --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --out outputs/frozen_registry.json
```

Supply every source bound into the day bundle, not just the first two examples.
The registrar re-verifies admission and freezes source/clock identities,
chronological 60/20/20 dates, causal features, K=2..5, ten seeded restarts,
calibration-only preprocessing, validation selection, baseline/observe/policy,
fill scenarios, fixed latency scenarios, fees/risk, outcomes, bootstrap settings,
resource caps and exclusions. The 1/2/5-second cadence grid is an explicit
registered sensitivity, not a latency measurement.
All cadences retain the same ten-second trailing feature window. Hysteresis is
frozen explicitly, with its three-second requirement rounded up to whole
samples. The scenario registry freezes the effective execution flags, not just
profile labels: conservative uses confirmed trades, while base/aggressive use
displayed decreases. The latter share the same passive matching rule in this
core; reconciliation diagnostics can differ. They are not three independent
execution bounds. Replay must report that equivalence rather than manufacture
three distinct fill models.

Registry values cannot be mutated through returned dictionaries. Supported
partition access requires the explicit expected digest before opening a target
file and checks the registered day/role and physical partition. These checks
prevent accidental API leakage; they do not stop arbitrary filesystem access
or prove that an author never looked at a holdout elsewhere.

## Outstanding release gates

The infrastructure is not yet a completed real-market research pipeline.
Physical causal feature preparation, the registered fitting/scenario runner,
whole-study statistical aggregation and its independent full-bundle verifier
remain separate implementation gates. The commands above do not claim those
stages are available. The existing diagnostic HMM tools must not be used to
circumvent real-data admission or to declare a held-out result.

Required empirical evidence also remains: a finalized audited 24-hour-plus
soak, at least ten eligible UTC days, untouched test results and a dedicated
benchmark host. Retain failed captures/fits/runs and publish all registered
variants. Missing valuations keep PnL null; funding remains excluded. The
headline research question is robustness of incremental regime information,
not the best simulated PnL cell.

## Current admission evidence

The [2026-10-07 public smoke report](capture_results/real_market_admission_smoke_20261007.json)
binds the actual two-symbol capture, host telemetry, raw manifests and the
independently re-read interval/day reports. It contains 1,562 records and
58.284 seconds of joint-valid time, but **zero eligible UTC days**. Capture
startup and the explicit stopped/shutdown tail are retained in the denominator.
This proves a short public-network workflow, not a 24-hour soak or an empirical
strategy result. Raw tapes remain external/local rather than Git artifacts.
