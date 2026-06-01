from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import reviewer_gate


def test_reviewer_gate_steps_match_local_evidence_path() -> None:
    steps = reviewer_gate.build_reviewer_gate_steps("python")

    assert [step.name for step in steps] == [
        "unit and invariant tests",
        "type check core replay and simulation modules",
        "ruff lint",
        "ruff format check",
        "committed artifact verification",
        "whitespace check",
        "committed fixture determinism",
        "committed futures pack audit",
        "recorded clip benchmark",
    ]
    assert steps[0].command == ("python", "-m", "pytest", "-q")
    assert steps[1].command[:3] == ("python", "-m", "mypy")
    assert "lob_sim/sim/engine.py" in steps[1].command
    assert steps[2].command == ("python", "-m", "ruff", "check", ".")
    assert steps[3].command == ("python", "-m", "ruff", "format", "--check", ".")
    assert steps[4].command == ("python", "scripts/verify_committed_artifacts.py")
    assert steps[5].command == ("git", "diff", "--check")
    assert "scripts/check_futures_determinism.py" in steps[6].command
    assert "scripts/audit_futures_pack.py" in steps[7].command
    assert "--committed-futures" in steps[7].command
    assert "experiments/benchmark_futures_replay.py" in steps[8].command
    assert "--mode" in steps[8].command
    assert "all" in steps[8].command
    assert "--pack" in steps[8].command
    assert "docs/sample_outputs/futures_stress_case" in steps[8].command
    assert "--json-out" in steps[8].command


def test_reviewer_gate_can_skip_benchmark_for_narrower_local_checks() -> None:
    steps = reviewer_gate.build_reviewer_gate_steps("python", include_benchmark=False)

    commands = [" ".join(step.command) for step in steps]
    assert len(steps) == 8
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
