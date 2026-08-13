from __future__ import annotations

import sys
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.sweep_futures_overlap import (
    DEFAULT_FILL_MODELS,
    DEFAULT_OVERLAP_WINDOWS_MS,
    run_overlap_sweep,
    write_overlap_outputs,
)


INPUT_FILE = Path("docs/sample_outputs/futures_overlap_sensitivity/input_fixture.ndjson")
OUTPUT_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_overlap_sensitivity"
ENV_PATH = ".env.example"
REFERENCE_ENV = {
    "SIM_FILL_MODEL": "trade",
    "FILL_PROFILE": "base",
    "MM_ENABLED": "0",
    "LOG_LEVEL": "ERROR",
}


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, previous_value in previous.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


def refresh_futures_overlap_sensitivity() -> dict[str, Path]:
    previous_cwd = Path.cwd()
    try:
        os.chdir(REPO_ROOT)
        with _temporary_env(REFERENCE_ENV):
            payload = run_overlap_sweep(
                INPUT_FILE,
                ENV_PATH,
                fill_models=DEFAULT_FILL_MODELS,
                windows_ms=DEFAULT_OVERLAP_WINDOWS_MS,
            )
        return write_overlap_outputs(
            payload,
            OUTPUT_DIR,
            output_stem="futures_overlap_sensitivity",
            command="python scripts/refresh_futures_overlap_sensitivity.py",
        )
    finally:
        os.chdir(previous_cwd)


if __name__ == "__main__":
    paths = refresh_futures_overlap_sensitivity()
    print(f"Refreshed overlap sensitivity reference in {OUTPUT_DIR}")
    for label, path in paths.items():
        print(f"- {label}: {path}")
