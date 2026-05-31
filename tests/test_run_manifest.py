from __future__ import annotations

import json

import pytest

from lob_sim.sim import run_manifest


def test_source_state_uses_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {"git_commit": "abc123", "git_branch": "master", "git_dirty": False}
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, json.dumps(source))

    assert run_manifest.source_state() == source


def test_source_state_rejects_non_object_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, '["not", "an", "object"]')

    with pytest.raises(ValueError, match=run_manifest.SOURCE_STATE_OVERRIDE_ENV):
        run_manifest.source_state()
