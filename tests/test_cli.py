from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_doctor_reports_redacted_config() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "--env", ".env.example", "doctor"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["record_schema_version"] == "lob_sim.record.v1"
    assert payload["symbols"]
    assert "binance_api_key" not in payload["config"]
    assert "binance_api_secret" not in payload["config"]
