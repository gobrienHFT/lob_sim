"""Reproducible raw-market-tape recovery, not a strategy profitability study."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.config import load_config
from lob_sim.regime.fit import FitConfig
from lob_sim.regime.recovery import format_recovery_report, run_synthetic_recovery
from lob_sim.regime.synthetic import SyntheticTapeConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True, help="new directory; never overwrite existing evidence")
    parser.add_argument("--env", default=str(REPO_ROOT / ".env.example"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--days", type=int, default=5, help="short distinct UTC snippets, not certified full days")
    parser.add_argument("--seconds-per-day", type=int, default=480)
    parser.add_argument("--restarts", type=int, default=10)
    parser.add_argument("--max-iterations", type=int, default=300)
    args = parser.parse_args(argv)
    report = run_synthetic_recovery(
        args.out_dir,
        load_config(args.env, inherit_environment=False),
        tape_config=SyntheticTapeConfig(seed=args.seed, days=args.days, seconds_per_day=args.seconds_per_day),
        fit_config=FitConfig(seed=args.seed, restarts=args.restarts, max_iterations=args.max_iterations),
    )
    print(format_recovery_report(report))
    print(f"Reproduction bundle: {args.out_dir.resolve()}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
