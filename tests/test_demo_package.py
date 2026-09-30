from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from lob_sim.cli import _deterministic_run
from lob_sim.config import ConfigError, load_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_bundled_demo_preserves_walkthrough_data_and_config() -> None:
    resources = files("lob_sim").joinpath("resources")
    original_rows = [
        json.loads(line)
        for line in (REPO_ROOT / "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson")
        .read_text()
        .splitlines()
    ]
    bundled_rows = [json.loads(line) for line in resources.joinpath("demo_fixture.ndjson").read_text().splitlines()]
    assert bundled_rows == original_rows
    with as_file(resources.joinpath("demo.env")) as bundled_env:
        bundled_config = load_config(str(bundled_env), inherit_environment=False)
    original_config = load_config(str(REPO_ROOT / ".env.example"), inherit_environment=False)
    assert bundled_config == original_config
    with as_file(resources.joinpath("demo_fixture.ndjson")) as fixture:
        assert _deterministic_run(bundled_config, str(fixture)) == _deterministic_run(
            original_config,
            str(REPO_ROOT / "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson"),
        )


def test_isolated_demo_config_does_not_read_or_modify_shell_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_SEED", "invalid-shell-seed")
    environment_before = dict(os.environ)
    with as_file(files("lob_sim").joinpath("resources", "demo.env")) as env_path:
        assert load_config(str(env_path), inherit_environment=False).sim_seed == 1
        assert dict(os.environ) == environment_before
        # Supplying --env keeps the pre-existing environment precedence.
        with pytest.raises(ConfigError, match="SIM_SEED"):
            load_config(str(env_path))


def test_offline_demo_works_outside_checkout_and_ignores_local_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SIM_SEED=bad-local-seed\n", encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(REPO_ROOT), SIM_SEED="bad-shell-seed")
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "demo"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["input"]["records"] == 6
    assert payload["deterministic_run"]["fill_count"] == 1
    assert payload["synthetic_exchange"]["fifo_ground_truth"]["matches"] is True
    assert list(tmp_path.iterdir()) == [tmp_path / ".env"]


def test_demo_honors_explicit_file_and_env(tmp_path: Path) -> None:
    fixture = REPO_ROOT / "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lob_sim.cli",
            "--env",
            str(REPO_ROOT / ".env.example"),
            "demo",
            "--file",
            str(fixture),
        ],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["input"]["records"] == 80
    assert Path(payload["input"]["path"]) == fixture
