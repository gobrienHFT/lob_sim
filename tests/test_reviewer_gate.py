from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import reviewer_gate


def test_reviewer_gate_steps_match_local_evidence_path() -> None:
    steps = reviewer_gate.build_reviewer_gate_steps("python")

    assert [step.name for step in steps] == [
        "unit and invariant tests",
        "type check core replay, record, and simulation modules",
        "ruff lint",
        "ruff format check",
        "rust format check",
        "rust kernel tests",
        "rust clippy all features",
        "python/rust differential parity",
        "committed artifact verification",
        "whitespace check",
        "committed fixture determinism",
        "committed futures pack audit",
        "fault-injection fail-closed matrix",
        "recorded clip benchmark",
    ]
    assert steps[0].command == ("python", "-m", "pytest", "-q")
    assert steps[1].command[:3] == ("python", "-m", "mypy")
    assert "lob_sim/record" in steps[1].command
    assert "lob_sim/binance/ws.py" in steps[1].command
    assert "lob_sim/cli.py" in steps[1].command
    assert "lob_sim/config.py" in steps[1].command
    assert "lob_sim/oracle_kernel.py" in steps[1].command
    assert "lob_sim/util.py" in steps[1].command
    assert "lob_sim/sim/engine.py" in steps[1].command
    assert "lob_sim/sim/run_manifest.py" in steps[1].command
    assert "lob_sim/sim/mm_strategy.py" in steps[1].command
    assert steps[2].command == ("python", "-m", "ruff", "check", ".")
    assert steps[3].command == ("python", "-m", "ruff", "format", "--check", ".")
    assert steps[4].command == ("cargo", "fmt", "--all", "--", "--check")
    assert steps[5].command == ("cargo", "test", "--workspace")
    assert steps[6].command[-2:] == ("-D", "warnings")
    assert "scripts/check_rust_python_parity.py" in steps[7].command
    assert "--expected" in steps[7].command
    assert "docs/differential_results/rust_python_parity_v3.json" in steps[7].command
    assert steps[8].command == ("python", "scripts/verify_committed_artifacts.py")
    assert steps[9].command == ("git", "diff", "--check")
    assert "scripts/check_futures_determinism.py" in steps[10].command
    assert "scripts/audit_futures_pack.py" in steps[11].command
    assert "--committed-futures" in steps[11].command
    assert steps[12].command == ("python", "scripts/check_fault_injection.py")
    assert "experiments/benchmark_futures_replay.py" in steps[13].command
    assert "--mode" in steps[13].command
    assert "all" in steps[13].command
    assert "--pack" in steps[13].command
    assert "docs/sample_outputs/futures_stress_case" in steps[13].command
    assert "--json-out" in steps[13].command


def test_reviewer_gate_mypy_targets_match_makefile() -> None:
    makefile = (reviewer_gate.REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target_line = next(line for line in makefile.splitlines() if line.startswith("MYPY_TARGETS ?="))
    makefile_targets = tuple(target_line.split("?=", 1)[1].strip().split())

    assert makefile_targets == reviewer_gate.MYPY_TARGETS


def test_reviewer_gate_can_skip_benchmark_for_narrower_local_checks() -> None:
    steps = reviewer_gate.build_reviewer_gate_steps("python", include_benchmark=False, include_rust=False)

    commands = [" ".join(step.command) for step in steps]
    assert len(steps) == 9
    assert not any("benchmark_futures_replay.py" in command for command in commands)


def test_reviewer_gate_stops_on_first_failed_step() -> None:
    steps = [
        reviewer_gate.GateStep("ok", ("python", "--version")),
        reviewer_gate.GateStep("bad", ("python", "-m", "missing")),
        reviewer_gate.GateStep("later", ("git", "status")),
    ]
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path, check: bool) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=1 if command == ["python", "-m", "missing"] else 0)

    code = reviewer_gate.run_steps(steps, cwd=reviewer_gate.REPO_ROOT, runner=fake_runner)

    assert code == 1
    assert calls == [["python", "--version"], ["python", "-m", "missing"]]


def test_reviewer_gate_writes_machine_readable_success_report(tmp_path: Path) -> None:
    report_path = tmp_path / "outputs" / "reviewer_gate_report.json"
    steps = [
        reviewer_gate.GateStep("first", ("python", "--version")),
        reviewer_gate.GateStep("second", ("python", "-c", "pass")),
    ]

    def fake_runner(command: list[str], *, cwd: Path, check: bool) -> SimpleNamespace:
        return SimpleNamespace(returncode=0)

    code = reviewer_gate.run_steps(
        steps,
        cwd=tmp_path,
        runner=fake_runner,
        report_path=report_path,
        report_metadata={"include_benchmark": False},
    )

    assert code == 0
    assert not list(report_path.parent.glob("*.partial"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "lob_sim.reviewer_gate_report.v1"
    assert report["status"] == "passed"
    assert report["complete"] is True
    assert report["exit_code"] == 0
    assert report["step_count"] == 2
    assert report["completed_step_count"] == 2
    assert report["failed_step"] is None
    assert report["invocation"] == {"include_benchmark": False}
    assert [step["status"] for step in report["steps"]] == ["passed", "passed"]
    assert all(step["elapsed_seconds"] >= 0 for step in report["steps"])


def test_reviewer_gate_writes_failure_report_before_returning(tmp_path: Path) -> None:
    report_path = tmp_path / "reviewer_gate_report.json"
    steps = [
        reviewer_gate.GateStep("ok", ("python", "--version")),
        reviewer_gate.GateStep("bad", ("python", "-m", "missing")),
        reviewer_gate.GateStep("not run", ("git", "status")),
    ]

    def fake_runner(command: list[str], *, cwd: Path, check: bool) -> SimpleNamespace:
        return SimpleNamespace(returncode=1 if command == ["python", "-m", "missing"] else 0)

    code = reviewer_gate.run_steps(steps, cwd=tmp_path, runner=fake_runner, report_path=report_path)

    assert code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["complete"] is False
    assert report["exit_code"] == 1
    assert report["failed_step"] == "bad"
    assert report["completed_step_count"] == 2
    assert [step["name"] for step in report["steps"]] == ["ok", "bad"]
