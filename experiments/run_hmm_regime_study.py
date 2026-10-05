"""Compare registered baseline/observe/policy and declared risk/cadence ablations."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.config import load_config
from lob_sim.regime.fit import FitConfig
from lob_sim.regime.study import run_regime_study, format_study_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        required=True,
        help="independent single-UTC-day tape; repeat for >=3 dates; native clocks stay separate",
    )
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
    cfg = replace(
        load_config(args.env, inherit_environment=False),
        symbols=(args.symbol,),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        sim_seed=args.seed,
    )
    if args.requote_ms is not None:
        cfg = replace(cfg, mm_requote_ms=args.requote_ms)
    report = run_regime_study(
        tuple(args.input),
        args.out_dir,
        cfg,
        symbol=args.symbol,
        fit_config=FitConfig(seed=args.seed, restarts=args.restarts, max_iterations=args.max_iterations),
        bootstrap_replicates=args.bootstrap_replicates,
    )
    print(format_study_report(report))
    print(f"Evidence bundle: {args.out_dir.resolve()}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
