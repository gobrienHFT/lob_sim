# Contributing

## Setup

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the full local evidence gate before publishing changes:

```bash
python scripts/reviewer_gate.py
```

If `make` is available, the equivalent target is:

```bash
make reviewer-gate
```

For the gradual core type gate alone:

```bash
python -m mypy lob_sim/book lob_sim/replay lob_sim/record lob_sim/sim/fill_model.py lob_sim/sim/engine.py lob_sim/sim/metrics.py lob_sim/sim/run_manifest.py lob_sim/sim/mm_strategy.py
```

The current mypy gate covers book sync, replay normalization/inspection, record schema/writing, passive-fill mechanics, simulation engine/metrics, run manifests, and the market-making strategy layer. Options demos, plotting-heavy experiments, artifact refresh scripts, and tests remain outside the gradual type gate because they are more dynamic and less central to replay/fill correctness; they still run under pytest, ruff, artifact verification, and CI.

## Artifact Refresh Policy

Committed futures reviewer artifacts must be refreshed from a clean source tree so manifests carry useful provenance. Use:

```bash
python scripts/refresh_futures_reviewer_artifacts.py
```

This refreshes the walkthrough pack, recorded clip pack, synthetic stress pack, strategy profile reference, parameter sweep reference, latency sweep reference, and benchmark reference under one source-state snapshot.

For only the synthetic stress pack, run:

```bash
python scripts/refresh_futures_stress_case.py
```

## Committed Fixture Policy

- Prefer small real recorded public-market-data clips when they are available and redistributable.
- Synthetic fixtures are allowed only when they are clearly labeled and cover mechanics that are hard to observe compactly in a real clip.
- Synthetic rows should remain exchange-shaped: `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` with valid sequencing and tick/lot metadata.
- Every committed futures pack must include input, summary JSON/CSV, trades CSV, event trace CSV, manifest, and notes.
- `scripts/audit_futures_pack.py --committed-futures` must pass after every artifact refresh.

## Claim Discipline

Keep the repo honest:

- No alpha claims.
- No profitability claims from deterministic fixtures.
- No production latency claims from Python benchmark numbers.
- No private exchange fill truth claims from public L2 and aggregate-trade data.
- No production gateway readiness claims.

The strongest signal is careful replay, queue/fill assumptions, event-time traceability, and reproducibility.
