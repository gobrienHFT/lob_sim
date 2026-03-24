from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.refresh_sample_outputs import DOCUMENTED_SAMPLE_COMMANDS
from scripts.verify_committed_artifacts import collect_artifact_issues


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_committed_artifacts.py"
README = REPO_ROOT / "README.md"
WALKTHROUGH = REPO_ROOT / "WALKTHROUGH.md"
SAMPLE_OUTPUTS_README = REPO_ROOT / "docs" / "sample_outputs" / "README.md"
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
FUTURES_BENCHMARK_REFERENCE = (
    REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md"
)
FUTURES_STRATEGY_PROFILES = REPO_ROOT / "docs" / "futures_strategy_profiles.md"
FUTURES_STRATEGY_REFERENCE = (
    REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md"
)
FUTURES_STRATEGY_REFRESH = (
    REPO_ROOT / "scripts" / "refresh_futures_strategy_profile_reference.py"
)
FUTURES_WALKTHROUGH_README = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "README.md"
)
FUTURES_WALKTHROUGH_NOTES = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "walkthrough.md"
)
RECORDED_CASE_README = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "README.md"
)
RECORDED_CASE_NOTES = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "case_notes.md"
)
COMMITTED_CASE_BRIEF = (
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "case_brief.md"
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
    case_brief = COMMITTED_CASE_BRIEF.read_text(encoding="utf-8")
    demo_report = COMMITTED_DEMO_REPORT.read_text(encoding="utf-8")

    assert "This is not a units mismatch" in case_brief
    assert "This is not a units mismatch" in demo_report


def test_sample_output_commands_match_refresh_source_of_truth() -> None:
    readme = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")

    assert "<temp_dir>" not in readme
    for command in DOCUMENTED_SAMPLE_COMMANDS.values():
        assert command in readme, f"missing documented command: {command}"


def test_futures_walkthrough_pack_is_linked_from_front_door_docs() -> None:
    readme = README.read_text(encoding="utf-8")
    walkthrough = WALKTHROUGH.read_text(encoding="utf-8")
    sample_outputs = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")

    assert "docs/sample_outputs/futures_replay_walkthrough/README.md" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/summary.json" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/trades.csv" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md" in readme
    assert "docs/sample_outputs/futures_recorded_clip_case/README.md" in readme
    assert "docs/futures_strategy_profiles.md" in readme
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in readme
    assert "docs/benchmark_results/futures_replay_reference.md" in readme

    assert "docs/sample_outputs/futures_replay_walkthrough/README.md" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/summary.json" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/trades.csv" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md" in walkthrough
    assert "docs/sample_outputs/futures_recorded_clip_case/README.md" in walkthrough
    assert "docs/sample_outputs/futures_recorded_clip_case/case_notes.md" in walkthrough
    assert "docs/futures_strategy_profiles.md" in walkthrough
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in walkthrough
    assert "docs/benchmark_results/futures_replay_reference.md" in walkthrough

    assert "futures_replay_walkthrough/README.md" in sample_outputs
    assert "futures_replay_walkthrough/summary.json" in sample_outputs
    assert "futures_replay_walkthrough/trades.csv" in sample_outputs
    assert "futures_replay_walkthrough/walkthrough.md" in sample_outputs
    assert "futures_recorded_clip_case/README.md" in sample_outputs
    assert "futures_recorded_clip_case/summary.json" in sample_outputs
    assert "futures_recorded_clip_case/trades.csv" in sample_outputs
    assert "futures_recorded_clip_case/case_notes.md" in sample_outputs


def test_futures_walkthrough_refresh_command_is_documented_consistently() -> None:
    sample_outputs = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")
    futures_pack = FUTURES_WALKTHROUGH_README.read_text(encoding="utf-8")
    futures_notes = FUTURES_WALKTHROUGH_NOTES.read_text(encoding="utf-8")
    recorded_pack = RECORDED_CASE_README.read_text(encoding="utf-8")
    recorded_notes = RECORDED_CASE_NOTES.read_text(encoding="utf-8")

    assert "python scripts/refresh_futures_showcase.py" in sample_outputs
    assert "python scripts/refresh_futures_showcase.py" in futures_pack
    assert "python scripts/refresh_futures_showcase.py" in futures_notes
    assert "python scripts/refresh_futures_recorded_case.py" in sample_outputs
    assert "python scripts/refresh_futures_recorded_case.py" in recorded_pack
    assert "python scripts/refresh_futures_recorded_case.py" in recorded_notes

    assert "refresh_futures_replay_summary.py" not in sample_outputs
    assert "refresh_futures_replay_summary.py" not in futures_pack
    assert "refresh_futures_replay_summary.py" not in futures_notes
    assert "refresh_futures_replay_summary.py" not in recorded_pack
    assert "refresh_futures_replay_summary.py" not in recorded_notes


def test_futures_benchmark_reference_is_published() -> None:
    benchmarks = FUTURES_BENCHMARKS.read_text(encoding="utf-8")

    assert FUTURES_BENCHMARK_REFERENCE.exists()
    assert "## Published Reference Run" in benchmarks
    assert "## Benchmark Tool" in benchmarks
    published_section = benchmarks.split("## Published Reference Run", 1)[1].split("## Benchmark Tool", 1)[0]
    assert "TBD" not in published_section
    assert "benchmark_results/futures_replay_reference.md" in benchmarks


def test_futures_strategy_profile_docs_are_published() -> None:
    profiles = FUTURES_STRATEGY_PROFILES.read_text(encoding="utf-8")
    reference = FUTURES_STRATEGY_REFERENCE.read_text(encoding="utf-8")

    assert FUTURES_STRATEGY_PROFILES.exists()
    assert FUTURES_STRATEGY_REFERENCE.exists()
    assert FUTURES_STRATEGY_REFRESH.exists()
    assert "baseline" in profiles
    assert "layered_mm" in profiles
    assert "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson" in reference
    assert "local-only" not in reference
    assert "data/raw_1772633471.ndjson" not in reference
    assert "strategy-profile comparison" in reference
