# Contributing

## Setup

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install Rust through rustup as well. `rust-toolchain.toml` pins version 1.82.0,
rustfmt, and Clippy. Windows builds require the MSVC C++ build tools.

Run the full local verification gate before publishing changes:

```bash
python scripts/reviewer_gate.py
```

If `make` is available, the equivalent target is:

```bash
make reviewer-gate
```

For the gradual core type gate alone:

```bash
python -m mypy lob_sim/book lob_sim/replay lob_sim/record lob_sim/cli.py lob_sim/config.py lob_sim/util.py lob_sim/sim/fill_model.py lob_sim/sim/engine.py lob_sim/sim/metrics.py lob_sim/sim/run_manifest.py lob_sim/sim/mm_strategy.py
```

The current mypy gate covers book sync, replay normalization/inspection, record schema/writing, core CLI/config/util helpers, passive-fill mechanics, simulation engine/metrics, run manifests, and the market-making strategy layer. Options demos, plotting-heavy experiments, artifact refresh scripts, and tests remain outside the gradual type gate because they are more dynamic and less central to replay/fill correctness; they still run under pytest, ruff, artifact verification, and CI.

## Git workflow

Before branching, check the Git root, remote, and local changes:

```bash
git rev-parse --show-toplevel
git remote get-url origin
git status --short
```

The remote should be `https://github.com/gobrienHFT/lob_sim.git`, and the Git
root should be this checkout, not a parent directory. Preserve local work
before switching branches. From a clean checkout:

```bash
git switch master
git pull --ff-only origin master
git switch -c docs/describe-your-change
```

Use `fix/` or `feat/` for implementation changes. Keep each commit about one
change, with titles such as `fix(book): reject crossed snapshots` or
`docs: explain the offline demo`. Stage named paths and inspect
`git diff --cached` and `git diff --cached --check` before committing.

A pull request should explain the problem, resulting behavior, and checks run.
Keep generated reports separate from the implementation that produced them.
Preserve published history: existing manifests may reference those commits.

## Refreshing recorded outputs

Refresh committed futures packs from a clean source tree so their manifests
identify the source revision. Use:

```bash
python scripts/refresh_futures_reviewer_artifacts.py
```

This refreshes the walkthrough pack, recorded clip pack, synthetic stress pack, strategy profile reference, parameter sweep reference, latency sweep reference, and benchmark reference under one source-state snapshot.

For only the synthetic stress pack, run:

```bash
python scripts/refresh_futures_stress_case.py
```

## Committed fixtures

- Prefer small real recorded public-market-data clips when they are available and redistributable.
- Synthetic fixtures are allowed only when they are clearly labeled and cover mechanics that are hard to observe compactly in a real clip.
- Synthetic rows should remain exchange-shaped: `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` with valid sequencing and tick/lot metadata.
- Every committed futures pack must include input, summary JSON/CSV, trades CSV, event trace CSV, manifest, and notes.
- `scripts/audit_futures_pack.py --committed-futures` must pass after every artifact refresh.

## Describing results

Keep the repo honest:

- No alpha claims.
- No profitability claims from deterministic fixtures.
- No production latency claims from Python benchmark numbers.
- No private exchange fill truth claims from public L2 and aggregate-trade data.
- No production gateway readiness claims.

Describe the input, assumptions, failure cases, and reproduction command. Keep
the distinction between modeled public-L2 fills and synthetic matching visible.
