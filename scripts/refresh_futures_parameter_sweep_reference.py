from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator


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
REFERENCE_ENV = {
    "SIM_FILL_MODEL": "trade",
    "SIM_ORDER_LATENCY_MS": "0",
    "SIM_CANCEL_LATENCY_MS": "0",
    "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
    "SIM_KILL_SWITCH_ENABLED": "0",
    "SIM_KILL_MAX_DRAWDOWN": "0",
    "SIM_KILL_MAX_CONSECUTIVE_LOSSES": "0",
    "MM_ENABLED": "1",
    "MM_REQUOTE_MS": "1000",
    "MM_ORDER_QTY": "0.001",
    "MM_MAX_POSITION": "0.01",
    "MM_VOLATILITY_WINDOW": "30",
    "MM_VOLATILITY_SPREAD_FACTOR": "0",
    "MM_SKEW_BPS_PER_UNIT": "0",
    "FEES_MAKER_BPS": "0",
    "FEES_TAKER_BPS": "0",
    "LOG_LEVEL": "ERROR",
}
COMMAND = "python scripts/refresh_futures_parameter_sweep_reference.py"


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def refresh_futures_parameter_sweep_reference() -> dict[str, Path]:
    previous_cwd = Path.cwd()
    try:
        os.chdir(REPO_ROOT)
        with _temporary_env(REFERENCE_ENV):
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
