from __future__ import annotations

import argparse
import json
from pathlib import Path

from lob_sim.options.demo import DEFAULT_OPTIONS_SCENARIO, OptionsMarketMakerDemo, build_options_config, options_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the options MM case-study demo")
    parser.add_argument("--out-dir", default="outputs")
    parser.add_argument("--steps", type=int, default=450)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scenario", choices=options_scenarios(), default=DEFAULT_OPTIONS_SCENARIO)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    config = build_options_config(steps=args.steps, seed=args.seed, scenario=args.scenario)
    summary = OptionsMarketMakerDemo(config).run(
        Path(args.out_dir),
        verbose=args.verbose,
        progress_every=args.progress_every,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
