from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.sweep_futures_parameters import build_sweep_metadata, run_sweep, write_sweep_outputs

INPUT_FILE = Path("docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson")
ENV_PATH = ".env.example"
OUTPUT_DIR = REPO_ROOT / "docs" / "strategy_results"
OUTPUT_STEM = "futures_parameter_sweep_reference"
PROFILES = ["baseline", "layered_mm", "research_mm"]
HALF_SPREADS_BPS = [Decimal("0.05"), Decimal("0.10"), Decimal("0.25")]
QUEUE_REPOST_LOTS = [0, 5, 99]
COMMAND = (
    "python experiments/sweep_futures_parameters.py "
    "--file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson "
    "--env .env.example "
    "--out-dir docs/strategy_results "
    "--profiles baseline,layered_mm,research_mm "
    "--half-spreads-bps 0.05,0.10,0.25 "
    "--queue-repost-lots 0,5,99"
)


def refresh_futures_parameter_sweep_reference() -> dict[str, Path]:
    previous_cwd = Path.cwd()
    try:
        os.chdir(REPO_ROOT)
        rows = run_sweep(
            input_file=INPUT_FILE,
            env_path=ENV_PATH,
            profiles=PROFILES,
            half_spreads_bps=HALF_SPREADS_BPS,
            queue_repost_lots=QUEUE_REPOST_LOTS,
        )
        metadata = build_sweep_metadata(
            input_file=INPUT_FILE,
            env_path=ENV_PATH,
            profiles=PROFILES,
            half_spreads_bps=HALF_SPREADS_BPS,
            queue_repost_lots=QUEUE_REPOST_LOTS,
        )
        return write_sweep_outputs(
            rows,
            OUTPUT_DIR,
            INPUT_FILE,
            output_stem=OUTPUT_STEM,
            metadata=metadata,
            command=COMMAND,
        )
    finally:
        os.chdir(previous_cwd)


def main() -> int:
    paths = refresh_futures_parameter_sweep_reference()
    print(f"Refreshed futures parameter sweep reference in {OUTPUT_DIR}")
    for name, path in paths.items():
        print(f"- {name}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
