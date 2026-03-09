from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.verify_committed_artifacts import collect_artifact_issues


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_committed_artifacts.py"
COMMITTED_INTERVIEW_BRIEF = (
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "interview_brief.md"
)
COMMITTED_DEMO_REPORT = REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "demo_report.md"


def test_committed_artifacts_have_no_integrity_issues() -> None:
    issues = collect_artifact_issues()
    assert not issues, "\n".join(f"- {issue}" for issue in issues)


def test_verify_committed_artifacts_script_runs_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Committed artifact verification passed." in result.stdout


def test_committed_stress_fill_includes_units_explanation() -> None:
    interview_brief = COMMITTED_INTERVIEW_BRIEF.read_text(encoding="utf-8")
    demo_report = COMMITTED_DEMO_REPORT.read_text(encoding="utf-8")

    assert "This is not a units mismatch" in interview_brief
    assert "This is not a units mismatch" in demo_report
