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

## Physical features and model fitting

Physical causal feature preparation and registered model fitting are available:

```powershell
python -m lob_sim.cli --env .env.example research-prepare --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --out outputs/prepared_features
python -m lob_sim.cli research-prepare-verify --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features
python -m lob_sim.cli --env .env.example research-fit --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --out outputs/registered_models
python -m lob_sim.cli research-fit-verify --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --models outputs/registered_models
```

Use the externally saved 64-character registry digest and every exact parent
manifest. The preparer freezes the first-receipt clock projection, requires
joint validity across the entire trailing feature window and its observation
availability, and excludes UTC-boundary uncertainty. Source/day/epoch and
invalid-sample breaks terminate fit sequences. Row streams are exclusive,
fsynced and bounded; fitting has a frozen one-million-row partition cap.

Preparation records test access before fixed feature extraction. Fitting never
opens test feature files: it accepts only calibration/validation roles, saves
every K's winning model and all restart ledgers, then selects K using the frozen
validation-likelihood/BIC rule. No PnL input is accepted. The model re-reader
recomputes training clipping/scaling and saved winning likelihoods using the
standard-library forward filter, independent of the EM library. It does not
reconstruct discarded restart parameters or authenticate an author's conduct.

## Held-out replay and serialized verification

After every registered symbol/cadence has a valid validation-selected model:

```powershell
python -m lob_sim.cli --env .env.example research-views --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --models outputs/registered_models --out outputs/held_out_views
python -m lob_sim.cli research-views-verify --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --models outputs/registered_models --views outputs/held_out_views
python -m lob_sim.cli --env .env.example research-study --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --models outputs/registered_models --views outputs/held_out_views --out outputs/held_out_study
python -m lob_sim.cli --env .env.example research-study-verify --registry outputs/frozen_registry.json --registry-sha256 REGISTRY_DIGEST --days outputs/day_admission --file FIRST.manifest.json --file SECOND.manifest.json --prepared outputs/prepared_features --models outputs/registered_models --views outputs/held_out_views --study outputs/held_out_study
```

These are long, local evidence jobs, not normal CI or an automatic trading
workflow. Supply all exact parents and the digest saved at registration.
Keep the processing source and configuration unchanged between stages. To
move bundles, pass relocated raw manifests explicitly; do not edit their
stored provenance or child hashes. Keep enough storage for all runs. A full
day/grid runtime and disk cost have not yet been measured; plan conservatively.

The runner executes 162 cases per source/day/instrument unit: three cadences,
three fill profiles, six fixed delays and three variants. The frozen unit cap
is 32; exceeding it fails, rather than choosing a favourable subset. Every
attempt is journalled. A failed case blocks whole-study publication and keeps
its partial artifacts. Restart into new exclusive output directories; this
study wrapper does not resume audit sinks or change the ordinary engine's
checkpoint contract.

Event detail remains streamed; fit rows and offline UTC-minute tables have
fixed caps. A study's complete grid metadata has a separate 64 MiB JSON budget
(ordinary parent readers retain their 8 MiB limit). Disk grows with exported
audit length and case count. Default 40 ms requoting and per-market risk audits
can be expensive across full days: the workflow is not certified for local
storage/runtime at that scale. Do not silently thin events or drop scenarios
to fit a machine. Choose any different quote cadence/configuration before
registration, disclose it, and freeze a new experiment—not after test outcomes.

Each unit starts with zero inventory and quotes just its selected instrument,
while observing both BTC and ETH. Either feed becoming invalid/stale clears
the selected instrument's live/pending orders and pending markouts. The source
prefix reconstructs both books without quoting or crossing a partition with
features. Scoring starts after a fixed 30-second warmup. The end is half-open:
no source observation at the cutoff can fill, and no cutoff or timeout is a
new price observation. Markouts resolve only against validated selected-symbol
depth observations; observation lag and unresolved coverage remain explicit.
Fresh known marks may still value inventory. These are conditional experiments,
not one continuously funded cross-day portfolio or observed exchange cancels.

Baseline and observe-only runs must have identical native core-event, fill and
markout hashes. Only then may the observe-only audit supply measurement tables
for the baseline. Policy-minus-baseline comparisons are separate for each
symbol/cadence/fill/latency cell. Nothing picks the best test PnL cell, refits a
model or merges distinct execution assumptions into a single headline.

`manifest.json` binds every case, model, configuration, parent and comparison.
`attempts.jsonl` accounts for the complete grid. Each run retains native fill,
markout, lifecycle, state, quote, risk and economic audits, plus bounded
`clock_periods.jsonl` tables. `comparisons/*.json` contains exact units,
denominators, exclusions and paired block-bootstrap output. The verifier
reopens this graph, independently reduces the native audits and recomputes the
paired statistics without invoking the matching engine. It is not an
independent exchange implementation or proof of an author's unseen conduct.

## Outcomes and interpretation

The primary maker one-second markout is quantity-weighted signed basis points:
`10000 * signed_markout / original_fill_price`. Takers are separate; unresolved
maker observations are not zeros. Inventory is time-weighted absolute base
quantity. Reserved exposure is gross quote notional including live and pending
orders. Activity distinguishes scheduled new requests and terminal cancel
acknowledgements, not quote targets or cancel requests.

Secondary tables include all four markout horizons, actual observation lag,
coverage, source/queue diagnostics, pending-cancel fills, quote age, turnover,
fees, marked spread capture, maker/taker counts and risk-halt transitions.
State-conditioned distributions remain in each run's independently audited
summary. Gross/net marked equity changes use causal left-limit observations;
missing valuation remains null. Funding is excluded. Full-path drawdown is a
descriptive run diagnostic, not a bootstrap confidence interval.

Uncertainty uses paired UTC-minute sufficient statistics, 2,000 seeded
replications and 30-minute moving blocks, with 5/60-minute sensitivities.
Blocks cannot cross a source, day, epoch or invalid gap. Quiet valid minutes
remain in the denominator. Ratios pool numerators/denominators before
resampling. Insufficient uninterrupted strata produce null intervals, not
shorter undisclosed blocks. These are conditional pointwise intervals, not
multiplicity-adjusted significance across the entire grid. Every bundle keeps
`claim_ready: false`; a successful audit alone cannot justify an economic claim.

## Verification scope and current blockers

The fast reviewer gate covers the new admission, partition, model, replay and
statistical fault boundaries. A separate reduced-grid integration test runs
native matching and reopens its serialized graph:

```powershell
python scripts/reviewer_gate.py
python -m pytest tests/test_research_study_graph.py -m long_research -q --durations=10
```

The latter is explicitly synthetic, with hand-specified models and pytest-only
parent-admission/fitting substitutions. It does not certify a real research
day, recovered regimes, fitted models or held-out performance. Separate tests
exercise the admission and train-only fitting boundaries. Production has no
synthetic bypass or reduced-grid flag. The **Extended research verification**
workflow runs the long graph on Windows/Linux manually and weekly, outside
normal CI. The full real-market sequence above still needs admitted real data.

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
