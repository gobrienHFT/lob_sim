from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lob_sim.sim.run_manifest import SOURCE_STATE_OVERRIDE_ENV
from scripts import refresh_futures_reviewer_artifacts as reviewer_refresh


def test_reviewer_refresh_uses_one_source_state_override(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {"git_commit": "abc123", "git_branch": "master", "git_dirty": False}
    calls: list[str] = []

    def _step(label: str):
        def run() -> dict[str, Path]:
            calls.append(label)
            assert json.loads(os.environ[SOURCE_STATE_OVERRIDE_ENV]) == source
            return {"artifact": Path(f"{label}.txt")}

        return run

    monkeypatch.setattr(reviewer_refresh, "source_state", lambda: source)
    monkeypatch.setattr(
        reviewer_refresh,
        "_refresh_steps",
        lambda: [
            ("showcase", _step("showcase")),
            ("recorded", _step("recorded")),
            ("sweep", _step("sweep")),
        ],
    )
    monkeypatch.delenv(SOURCE_STATE_OVERRIDE_ENV, raising=False)

    refreshed = reviewer_refresh.refresh_futures_reviewer_artifacts()

    assert calls == ["showcase", "recorded", "sweep"]
    assert set(refreshed) == {"showcase", "recorded", "sweep"}
    assert SOURCE_STATE_OVERRIDE_ENV not in os.environ


def test_reviewer_refresh_refuses_dirty_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reviewer_refresh,
        "source_state",
        lambda: {"git_commit": "abc123", "git_branch": "master", "git_dirty": True},
    )

    with pytest.raises(RuntimeError, match="dirty source tree"):
        reviewer_refresh.refresh_futures_reviewer_artifacts()
