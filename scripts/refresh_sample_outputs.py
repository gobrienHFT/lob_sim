from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SCENARIO = "toxic_flow"
STEPS = 180
SEED = 7
SAMPLE_ROOT = Path("docs") / "sample_outputs"
CASE_STUDY_DIR = SAMPLE_ROOT / f"{SCENARIO}_seed{SEED}"
MATRIX_DIR = SAMPLE_ROOT / f"scenario_matrix_seed{SEED}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_demo(out_dir: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "lob_sim.cli",
        "options-demo",
        "--scenario",
        SCENARIO,
        "--steps",
        str(STEPS),
        "--seed",
        str(SEED),
        "--out-dir",
        str(out_dir),
        "--progress-every",
        "30",
        "--log-mode",
        "compact",
    ]
    subprocess.run(cmd, cwd=_repo_root(), check=True)


def _run_matrix(out_dir: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "experiments.run_options_scenario_matrix",
        "--steps",
        str(STEPS),
        "--seed",
        str(SEED),
        "--out-dir",
        str(out_dir),
    ]
    subprocess.run(cmd, cwd=_repo_root(), check=True)


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _write_head_csv(src: Path, dst: Path, rows: int = 25) -> None:
    with src.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        head_rows = []
        for index, row in enumerate(reader):
            if index >= rows:
                break
            head_rows.append(row)

    with dst.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if head_rows:
            writer.writerows(head_rows)


def main() -> None:
    repo_root = _repo_root()
    case_study_dir = repo_root / CASE_STUDY_DIR
    matrix_dir = repo_root / MATRIX_DIR
    _clear_directory(case_study_dir)
    _clear_directory(matrix_dir)

    with tempfile.TemporaryDirectory(prefix="lob_sim_options_sample_") as tmp_name:
        tmp_dir = Path(tmp_name)
        case_study_tmp = tmp_dir / "case_study"
        matrix_tmp = tmp_dir / "scenario_matrix"
        _run_demo(case_study_tmp)
        _run_matrix(matrix_tmp)

        _copy_file(case_study_tmp / "demo_report.md", case_study_dir / "demo_report.md")
        _copy_file(case_study_tmp / "summary.json", case_study_dir / "summary.json")
        _copy_file(case_study_tmp / "overview_dashboard.png", case_study_dir / "overview_dashboard.png")
        _copy_file(case_study_tmp / "pnl_over_time.png", case_study_dir / "pnl_over_time.png")
        _copy_file(case_study_tmp / "inventory_over_time.png", case_study_dir / "inventory_over_time.png")
        _copy_file(case_study_tmp / "net_delta_over_time.png", case_study_dir / "net_delta_over_time.png")
        _copy_file(case_study_tmp / "toxic_vs_nontoxic_markout.png", case_study_dir / "toxic_vs_nontoxic_markout.png")
        _write_head_csv(case_study_tmp / "fills.csv", case_study_dir / "fills_head.csv", rows=25)
        _write_head_csv(case_study_tmp / "checkpoints.csv", case_study_dir / "checkpoints_head.csv", rows=25)

        _copy_file(matrix_tmp / "scenario_matrix.csv", matrix_dir / "scenario_matrix.csv")
        _copy_file(matrix_tmp / "scenario_matrix.md", matrix_dir / "scenario_matrix.md")
        _copy_file(matrix_tmp / "scenario_comparison.png", matrix_dir / "scenario_comparison.png")

    print(f"Refreshed sample outputs in {repo_root / SAMPLE_ROOT}")


if __name__ == "__main__":
    main()
