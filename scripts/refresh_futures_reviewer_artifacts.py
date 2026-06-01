from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.sim.run_manifest import SOURCE_STATE_OVERRIDE_ENV, source_state
from scripts.refresh_futures_benchmark_reference import refresh_futures_benchmark_reference
from scripts.refresh_futures_latency_sweep_reference import refresh_futures_latency_sweep_reference
from scripts.refresh_futures_parameter_sweep_reference import refresh_futures_parameter_sweep_reference
from scripts.refresh_futures_recorded_case import refresh_futures_recorded_case
from scripts.refresh_futures_showcase import refresh_futures_showcase
from scripts.refresh_futures_strategy_profile_reference import refresh_reference


RefreshFunc = Callable[[], dict[str, Path]]


@contextmanager
def _source_state_override(source: dict[str, object]) -> Iterator[None]:
    previous = os.environ.get(SOURCE_STATE_OVERRIDE_ENV)
    os.environ[SOURCE_STATE_OVERRIDE_ENV] = json.dumps(source, sort_keys=True)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(SOURCE_STATE_OVERRIDE_ENV, None)
        else:
            os.environ[SOURCE_STATE_OVERRIDE_ENV] = previous


def _refresh_steps() -> list[tuple[str, RefreshFunc]]:
    return [
        ("futures_showcase", refresh_futures_showcase),
        ("futures_recorded_case", refresh_futures_recorded_case),
        ("futures_strategy_profile_reference", refresh_reference),
        ("futures_parameter_sweep_reference", refresh_futures_parameter_sweep_reference),
        ("futures_latency_sweep_reference", refresh_futures_latency_sweep_reference),
        ("futures_benchmark_reference", refresh_futures_benchmark_reference),
    ]


def refresh_futures_reviewer_artifacts(*, require_clean_source: bool = True) -> dict[str, dict[str, Path]]:
    source = source_state()
    if require_clean_source and source.get("git_dirty"):
        raise RuntimeError(
            "Refusing to refresh committed reviewer artifacts from a dirty source tree. "
            "Commit or stash source changes first so provenance stays meaningful."
        )

    refreshed: dict[str, dict[str, Path]] = {}
    with _source_state_override(source):
        for label, refresh in _refresh_steps():
            refreshed[label] = refresh()
    return refreshed


def main() -> int:
    refreshed = refresh_futures_reviewer_artifacts()
    print("Refreshed futures reviewer artifacts with one clean source provenance snapshot.")
    for label, paths in refreshed.items():
        print(f"- {label}:")
        for name, path in paths.items():
            print(f"  - {name}: {path.resolve().relative_to(REPO_ROOT.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
