"""Build a wheel and exercise its CLI without a source checkout on sys.path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"Installed-package check failed ({result.returncode}): {subprocess.list2cmdline(command)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def check_installed_package(wheel: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lob_sim_installed_") as directory:
        temporary = Path(directory)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        if wheel is None:
            source = temporary / "source"
            source.mkdir()
            for name in ("pyproject.toml", "README.md"):
                shutil.copy2(REPO_ROOT / name, source / name)
            shutil.copytree(
                REPO_ROOT / "lob_sim",
                source / "lob_sim",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            wheels = temporary / "wheels"
            _run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(source)],
                cwd=temporary,
                environment=environment,
            )
            built = sorted(wheels.glob("lob_sim-*.whl"))
            if len(built) != 1:
                raise RuntimeError(f"Expected one lob_sim wheel, found {len(built)}")
            wheel = built[0]
        wheel = wheel.resolve()
        with zipfile.ZipFile(wheel) as archive:
            demo_env = archive.read("lob_sim/resources/demo.env").decode("utf-8")
            if not archive.read("lob_sim/resources/demo_fixture.ndjson"):
                raise RuntimeError("The installed demo fixture is empty")
        # Clear only the demo's configuration keys. Dependencies are inherited
        # from the already-installed development environment; the package itself
        # must come from the wheel in this isolated virtual environment.
        for line in demo_env.splitlines():
            if line and not line.startswith("#") and "=" in line:
                environment.pop(line.split("=", 1)[0], None)
        virtualenv = temporary / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(virtualenv)
        executable_dir = virtualenv / ("Scripts" if os.name == "nt" else "bin")
        python = executable_dir / ("python.exe" if os.name == "nt" else "python")
        console = executable_dir / ("lob-sim.exe" if os.name == "nt" else "lob-sim")
        _run(
            [str(python), "-m", "pip", "install", "--no-deps", "--ignore-installed", str(wheel)],
            cwd=temporary,
            environment=environment,
        )
        working = temporary / "empty_working_directory"
        working.mkdir()
        package_file = Path(
            _run(
                [str(python), "-I", "-c", "import lob_sim; print(lob_sim.__file__)"],
                cwd=working,
                environment=environment,
            ).strip()
        ).resolve()
        if not package_file.is_relative_to(virtualenv.resolve()):
            raise RuntimeError("CLI imported lob_sim from outside the installed-wheel environment")
        _run([str(console), "--help"], cwd=working, environment=environment)
        environment["SIM_SEED"] = "unrelated-invalid-shell-value"
        runs = [json.loads(_run([str(console), "demo"], cwd=working, environment=environment)) for _ in range(2)]
        if runs[0] != runs[1]:
            raise RuntimeError("Installed offline demo differs between identical runs")
        payload = runs[0]
        synthetic = payload["synthetic_exchange"]
        if (
            payload["schema_version"] != "lob_sim.reviewer_demo.v1"
            or payload["input"]["records"] != 6
            or payload["deterministic_run"]["fill_count"] != 1
            or payload["deterministic_run"]["valuation_complete"] is not True
            or synthetic["fifo_ground_truth"]["matches"] is not True
            or synthetic["historical_binance_fifo"] is not False
            or synthetic["post_only_rejection_reason"] != "post_only_would_cross"
        ):
            raise RuntimeError("Installed demo no longer satisfies the published walkthrough behavior")
        fixture = str(payload["input"]["path"])
        validation = json.loads(
            _run([str(console), "validate", "--file", fixture], cwd=working, environment=environment)
        )
        if validation["ok"] is not True:
            raise RuntimeError("Installed fixture failed schema validation")
        environment.pop("SIM_SEED")
        comparison = json.loads(
            _run(
                [
                    str(console),
                    "--env",
                    str(package_file.parent / "resources" / "demo.env"),
                    "compare",
                    "--file",
                    fixture,
                    "--repetitions",
                    "2",
                ],
                cwd=working,
                environment=environment,
            )
        )
        if comparison["ok"] is not True or comparison["state_sha256"] != payload["deterministic_run"]["state_sha256"]:
            raise RuntimeError("Installed fixture comparison disagrees with the demo")
        if len(payload["next_commands"]) != 2 or any("reviewer_gate.py" in item for item in payload["next_commands"]):
            raise RuntimeError("Installed demo advertised unavailable repository scripts")
        if list(working.iterdir()):
            raise RuntimeError("Offline demo unexpectedly wrote output into the working directory")
        return {
            "schema_version": "lob_sim.installed_package_check.v1",
            "ok": True,
            "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "imported_from_installed_wheel": True,
            "empty_working_directory": True,
            "ambient_config_ignored_by_default_demo": True,
            "identical_demo_runs": len(runs),
            "fixture_validation_and_comparison_passed": True,
            "public_l2_state_sha256": payload["deterministic_run"]["state_sha256"],
            "synthetic_fifo_matches": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Check an existing wheel instead of building from this source tree")
    args = parser.parse_args()
    print(json.dumps(check_installed_package(args.wheel), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
