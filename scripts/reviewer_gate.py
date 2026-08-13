from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = Path("docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson")
DEFAULT_RECORDED_FIXTURE = Path("docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson")
DEFAULT_ENV = Path(".env.example")
DEFAULT_DETERMINISM_JSON = Path("outputs/futures_determinism.json")
DEFAULT_BENCHMARK_JSON = Path("outputs/futures_benchmark.json")
DEFAULT_REPORT_JSON = Path("outputs/reviewer_gate_report.json")
REVIEWER_REPORT_SCHEMA = "lob_sim.reviewer_gate_report.v1"
MYPY_TARGETS = (
    "lob_sim/book",
    "lob_sim/replay",
    "lob_sim/record",
    "lob_sim/binance/ws.py",
    "lob_sim/cli.py",
    "lob_sim/config.py",
    "lob_sim/oracle_kernel.py",
    "lob_sim/util.py",
    "lob_sim/sim/fill_model.py",
    "lob_sim/sim/engine.py",
    "lob_sim/sim/export.py",
    "lob_sim/sim/runner.py",
    "lob_sim/sim/metrics.py",
    "lob_sim/sim/run_manifest.py",
    "lob_sim/sim/mm_strategy.py",
    "lob_sim/sim/contracts.py",
    "lob_sim/sim/latency.py",
    "lob_sim/sim/sinks.py",
    "lob_sim/sim/synthetic_exchange.py",
    "lob_sim/sim/synthetic_demo.py",
    "lob_sim/audit",
    "lob_sim/research",
)


@dataclass(frozen=True)
class GateStep:
    name: str
    command: tuple[str, ...]


Runner = Callable[..., subprocess.CompletedProcess]


def _path_arg(path: Path) -> str:
    return path.as_posix()


def _display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_command_output(cwd: Path, command: Sequence[str]) -> str | None:
    """Read small provenance commands without making the gate depend on them."""

    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _repo_runtime_metadata(cwd: Path) -> dict[str, object]:
    status = _read_command_output(cwd, ("git", "status", "--porcelain"))
    return {
        "root": str(cwd.resolve()),
        "git_commit": _read_command_output(cwd, ("git", "rev-parse", "HEAD")),
        "git_branch": _read_command_output(cwd, ("git", "branch", "--show-current")),
        "git_dirty": bool(status),
        "python_executable": sys.executable,
        "python_version": sys.version.splitlines()[0],
        "platform": platform.platform(),
        "cargo_version": _read_command_output(cwd, ("cargo", "--version")),
        "rustc_version": _read_command_output(cwd, ("rustc", "--version")),
        "os_name": os.name,
    }


def _write_report(path: Path, payload: Mapping[str, object], *, cwd: Path) -> Path:
    target = path if path.is_absolute() else cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(target)
    return target


def build_reviewer_gate_steps(
    python_executable: str = sys.executable,
    *,
    fixture: Path = DEFAULT_FIXTURE,
    recorded_fixture: Path = DEFAULT_RECORDED_FIXTURE,
    env_path: Path = DEFAULT_ENV,
    determinism_json: Path = DEFAULT_DETERMINISM_JSON,
    benchmark_json: Path = DEFAULT_BENCHMARK_JSON,
    include_benchmark: bool = True,
    include_rust: bool = True,
    cargo_executable: str = "cargo",
) -> list[GateStep]:
    steps = [
        GateStep("unit and invariant tests", (python_executable, "-m", "pytest", "-q")),
        GateStep(
            "type check core replay, record, and simulation modules",
            (python_executable, "-m", "mypy", *MYPY_TARGETS),
        ),
        GateStep("ruff lint", (python_executable, "-m", "ruff", "check", ".")),
        GateStep("ruff format check", (python_executable, "-m", "ruff", "format", "--check", ".")),
    ]
    if include_rust:
        steps.extend(
            [
                GateStep("rust format check", (cargo_executable, "fmt", "--all", "--", "--check")),
                GateStep("rust kernel tests", (cargo_executable, "test", "--workspace")),
                GateStep(
                    "rust clippy all features",
                    (
                        cargo_executable,
                        "clippy",
                        "--workspace",
                        "--all-targets",
                        "--all-features",
                        "--",
                        "-D",
                        "warnings",
                    ),
                ),
                GateStep(
                    "python/rust differential parity",
                    (
                        python_executable,
                        "scripts/check_rust_python_parity.py",
                        "--cargo",
                        cargo_executable,
                        "--cases",
                        "10000",
                        "--expected",
                        "docs/differential_results/rust_python_parity_v3.json",
                    ),
                ),
            ]
        )
    steps.extend(
        [
            GateStep(
                "committed artifact verification",
                (python_executable, "scripts/verify_committed_artifacts.py"),
            ),
            GateStep("whitespace check", ("git", "diff", "--check")),
            GateStep(
                "committed fixture determinism",
                (
                    python_executable,
                    "scripts/check_futures_determinism.py",
                    "--file",
                    _path_arg(fixture),
                    "--env",
                    _path_arg(env_path),
                    "--json-out",
                    _path_arg(determinism_json),
                ),
            ),
            GateStep(
                "committed futures pack audit",
                (python_executable, "scripts/audit_futures_pack.py", "--committed-futures"),
            ),
            GateStep(
                "fault-injection fail-closed matrix",
                (python_executable, "scripts/check_fault_injection.py"),
            ),
        ]
    )
    if include_benchmark:
        steps.append(
            GateStep(
                "recorded clip benchmark",
                (
                    python_executable,
                    "experiments/benchmark_futures_replay.py",
                    "--file",
                    _path_arg(recorded_fixture),
                    "--env",
                    _path_arg(env_path),
                    "--mode",
                    "all",
                    "--pack",
                    "docs/sample_outputs/futures_stress_case",
                    "--json-out",
                    _path_arg(benchmark_json),
                ),
            )
        )
    return steps


def run_steps(
    steps: Sequence[GateStep],
    *,
    cwd: Path = REPO_ROOT,
    runner: Runner = subprocess.run,
    report_path: Path | None = None,
    report_metadata: Mapping[str, object] | None = None,
) -> int:
    started_at = _utc_now()
    started_clock = time.perf_counter()
    step_results: list[dict[str, object]] = []
    failed_step: str | None = None
    exit_code = 0
    for index, step in enumerate(steps, start=1):
        print(f"[{index}/{len(steps)}] {step.name}", flush=True)
        print(f"$ {_display_command(step.command)}", flush=True)
        step_started_at = _utc_now()
        step_started_clock = time.perf_counter()
        result = runner(list(step.command), cwd=cwd, check=False)
        returncode = int(result.returncode)
        step_results.append(
            {
                "index": index,
                "name": step.name,
                "command": list(step.command),
                "status": "passed" if returncode == 0 else "failed",
                "returncode": returncode,
                "started_at_utc": step_started_at,
                "elapsed_seconds": round(time.perf_counter() - step_started_clock, 6),
            }
        )
        if returncode != 0:
            print(f"Reviewer gate failed at step {index}: {step.name}", file=sys.stderr)
            failed_step = step.name
            exit_code = returncode
            break
    complete = failed_step is None and len(step_results) == len(steps)
    if report_path is not None:
        report = {
            "schema_version": REVIEWER_REPORT_SCHEMA,
            "status": "passed" if complete else "failed",
            "complete": complete,
            "exit_code": exit_code,
            "started_at_utc": started_at,
            "finished_at_utc": _utc_now(),
            "elapsed_seconds": round(time.perf_counter() - started_clock, 6),
            "repo_runtime": _repo_runtime_metadata(cwd),
            "invocation": dict(report_metadata or {}),
            "step_count": len(steps),
            "completed_step_count": len(step_results),
            "failed_step": failed_step,
            "steps": step_results,
        }
        report_target = _write_report(report_path, report, cwd=cwd)
        print(f"Reviewer report: {report_target}", flush=True)
    if complete:
        print("Reviewer gate passed.", flush=True)
        return 0
    return exit_code or 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the cross-platform reviewer evidence gate without requiring make."
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable used for Python steps")
    parser.add_argument("--file", type=Path, default=DEFAULT_FIXTURE, help="Committed determinism fixture")
    parser.add_argument(
        "--recorded-file",
        type=Path,
        default=DEFAULT_RECORDED_FIXTURE,
        help="Committed recorded clip used for the benchmark",
    )
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV, help="Environment file for replay commands")
    parser.add_argument(
        "--determinism-json",
        type=Path,
        default=DEFAULT_DETERMINISM_JSON,
        help="Machine-readable determinism report path",
    )
    parser.add_argument(
        "--benchmark-json",
        type=Path,
        default=DEFAULT_BENCHMARK_JSON,
        help="Machine-readable benchmark report path",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=DEFAULT_REPORT_JSON,
        help="Machine-readable reviewer-gate release report path",
    )
    parser.add_argument(
        "--skip-benchmark",
        action="store_true",
        help="Run the non-benchmark evidence path only",
    )
    parser.add_argument("--cargo", default="cargo", help="Cargo executable used for Rust kernel steps")
    parser.add_argument("--skip-rust", action="store_true", help="Skip Rust fmt, test, and Clippy steps")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.determinism_json.parent.mkdir(parents=True, exist_ok=True)
    if not args.skip_benchmark:
        args.benchmark_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    steps = build_reviewer_gate_steps(
        args.python,
        fixture=args.file,
        recorded_fixture=args.recorded_file,
        env_path=args.env,
        determinism_json=args.determinism_json,
        benchmark_json=args.benchmark_json,
        include_benchmark=not args.skip_benchmark,
        include_rust=not args.skip_rust,
        cargo_executable=args.cargo,
    )
    return run_steps(
        steps,
        cwd=REPO_ROOT,
        report_path=args.report_out,
        report_metadata={
            "python_argument": args.python,
            "cargo_argument": args.cargo,
            "fixture": _path_arg(args.file),
            "recorded_fixture": _path_arg(args.recorded_file),
            "environment": _path_arg(args.env),
            "determinism_json": _path_arg(args.determinism_json),
            "benchmark_json": _path_arg(args.benchmark_json),
            "include_benchmark": not args.skip_benchmark,
            "include_rust": not args.skip_rust,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
