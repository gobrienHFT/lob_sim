from __future__ import annotations

import argparse
import json
from pathlib import Path

from lob_sim.options.demo import OptionsMMConfig, OptionsMarketMakerDemo


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the options MM case-study demo")
    parser.add_argument("--out-dir", default="data/options_demo")
    parser.add_argument("--steps", type=int, default=450)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    config = OptionsMMConfig(steps=args.steps, seed=args.seed)
    summary = OptionsMarketMakerDemo(config).run(
        Path(args.out_dir),
        verbose=args.verbose,
        progress_every=args.progress_every,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
