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
