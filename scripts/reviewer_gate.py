from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = Path("docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson")
DEFAULT_RECORDED_FIXTURE = Path("docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson")
DEFAULT_ENV = Path(".env.example")
DEFAULT_DETERMINISM_JSON = Path("outputs/futures_determinism.json")
DEFAULT_BENCHMARK_JSON = Path("outputs/futures_benchmark.json")


@dataclass(frozen=True)
class GateStep:
    name: str
    command: tuple[str, ...]


Runner = Callable[..., subprocess.CompletedProcess]


def _path_arg(path: Path) -> str:
    return path.as_posix()


def _display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def build_reviewer_gate_steps(
    python_executable: str = sys.executable,
    *,
    fixture: Path = DEFAULT_FIXTURE,
    recorded_fixture: Path = DEFAULT_RECORDED_FIXTURE,
    env_path: Path = DEFAULT_ENV,
    determinism_json: Path = DEFAULT_DETERMINISM_JSON,
    benchmark_json: Path = DEFAULT_BENCHMARK_JSON,
    include_benchmark: bool = True,
) -> list[GateStep]:
    steps = [
        GateStep("unit and invariant tests", (python_executable, "-m", "pytest", "-q")),
        GateStep(
            "type check core replay and simulation modules",
            (
                python_executable,
                "-m",
                "mypy",
                "lob_sim/book",
                "lob_sim/replay",
                "lob_sim/sim/fill_model.py",
                "lob_sim/sim/engine.py",
                "lob_sim/sim/metrics.py",
            ),
        ),
        GateStep("ruff lint", (python_executable, "-m", "ruff", "check", ".")),
        GateStep("ruff format check", (python_executable, "-m", "ruff", "format", "--check", ".")),
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
    ]
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
) -> int:
    for index, step in enumerate(steps, start=1):
        print(f"[{index}/{len(steps)}] {step.name}", flush=True)
        print(f"$ {_display_command(step.command)}", flush=True)
        result = runner(list(step.command), cwd=cwd, check=False)
        if result.returncode != 0:
            print(f"Reviewer gate failed at step {index}: {step.name}", file=sys.stderr)
            return int(result.returncode)
    print("Reviewer gate passed.", flush=True)
    return 0


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
        "--skip-benchmark",
        action="store_true",
        help="Run the non-benchmark evidence path only",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.determinism_json.parent.mkdir(parents=True, exist_ok=True)
    if not args.skip_benchmark:
        args.benchmark_json.parent.mkdir(parents=True, exist_ok=True)
    steps = build_reviewer_gate_steps(
        args.python,
        fixture=args.file,
        recorded_fixture=args.recorded_file,
        env_path=args.env,
        determinism_json=args.determinism_json,
        benchmark_json=args.benchmark_json,
        include_benchmark=not args.skip_benchmark,
    )
    return run_steps(steps, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
