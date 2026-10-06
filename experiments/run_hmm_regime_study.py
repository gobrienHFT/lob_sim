"""Compare registered baseline/observe/policy and declared risk/cadence ablations."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.config import load_config
from lob_sim.regime.fit import FitConfig
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.study import run_regime_study, format_study_report
from lob_sim.regime.synthetic import SyntheticTapeConfig, generate_synthetic_sources
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.run_manifest import source_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument(
        "--input",
        action="append",
        type=Path,
        help="independent single-UTC-day tape; repeat for >=3 dates; native clocks stay separate",
    )
    sources.add_argument(
        "--synthetic-demo",
        action="store_true",
        help="generate independent BTCUSDT snippets, not real data; common 0.010 quantity/500ms reference workload",
    )
    parser.add_argument("--synthetic-days", type=int, help="demo only: distinct short UTC snippets (default 5)")
    parser.add_argument("--synthetic-seconds-per-day", type=int, help="demo only: snippet duration (default 180)")
    parser.add_argument("--out-dir", type=Path, required=True, help="new evidence directory; never overwrite")
    parser.add_argument("--env", default=str(REPO_ROOT / ".env.example"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--requote-ms",
        type=float,
        help="explicit common reference quote cadence, registered for every variant; default uses env",
    )
    parser.add_argument("--restarts", type=int, default=10)
    parser.add_argument("--max-iterations", type=int, default=300)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args(argv)
    if not args.synthetic_demo and (args.synthetic_days is not None or args.synthetic_seconds_per_day is not None):
        parser.error("--synthetic-days/--synthetic-seconds-per-day require --synthetic-demo")
    if args.synthetic_demo and args.symbol != "BTCUSDT":
        parser.error("the synthetic diagnostic generator supports BTCUSDT only")
    cfg = replace(
        load_config(args.env, inherit_environment=False),
        symbols=(args.symbol,),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        sim_seed=args.seed,
    )
    if args.synthetic_demo:
        cfg = replace(cfg, mm_order_qty=Decimal("0.010"), mm_requote_ms=500)
    if args.requote_ms is not None:
        cfg = replace(cfg, mm_requote_ms=args.requote_ms)
    fit_config = FitConfig(seed=args.seed, restarts=args.restarts, max_iterations=args.max_iterations)
    root = args.out_dir
    generated = None
    if args.synthetic_demo:
        tape_config = SyntheticTapeConfig(
            seed=args.seed,
            days=args.synthetic_days if args.synthetic_days is not None else 5,
            seconds_per_day=args.synthetic_seconds_per_day if args.synthetic_seconds_per_day is not None else 180,
        )
        root.mkdir(parents=True, exist_ok=False)
        publish_json(root / "_INCOMPLETE.json", {"synthetic": True, "config": tape_config.as_dict()})
        generated = generate_synthetic_sources(root / "inputs", tape_config)
        inputs = tuple(root / "inputs" / entry["path"] for entry in generated["sources"])
        study_directory = root / "study"
    else:
        inputs = tuple(args.input)
        study_directory = root
    report = run_regime_study(
        inputs,
        study_directory,
        cfg,
        symbol=args.symbol,
        fit_config=fit_config,
        bootstrap_replicates=args.bootstrap_replicates,
    )
    if generated is not None:
        manifest = {
            "schema_version": "lob_sim.synthetic_regime_study_demo.v1",
            "synthetic": True,
            "producer": {
                "source": source_state(),
                "script_sha256": file_sha256(Path(__file__)),
                "package": report["code_identity"],
            },
            "status": report["status"],
            "claim_ready": False,
            "input_manifest": {"path": "inputs/manifest.json", "sha256": file_sha256(root / "inputs/manifest.json")},
            "study_report": {
                "path": "study/study_report.json",
                "sha256": file_sha256(study_directory / "study_report.json"),
            },
            "registry_sha256": report["registry_sha256"],
            "generator_manifest_sha256": generated["manifest_sha256"],
            "common_demo_quote_overrides": {"quantity": str(cfg.mm_order_qty), "requote_ms": cfg.mm_requote_ms},
            "scope": "generated synthetic snippets; actual registered native study; not real-market calibration, full valid days, private fills or policy benefit",
        }
        manifest["manifest_sha256"] = identity(manifest)
        publish_json(root / "manifest.json", manifest)
        if report["status"] == "completed":
            (root / "_INCOMPLETE.json").unlink()
    print(format_study_report(report))
    print(f"Evidence bundle: {args.out_dir.resolve()}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
