from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from lob_sim.options.demo import format_demo_report, format_interview_brief


SCENARIO = "toxic_flow"
STEPS = 180
SEED = 7
SAMPLE_ROOT = Path("docs") / "sample_outputs"
CASE_STUDY_DIR = SAMPLE_ROOT / f"{SCENARIO}_seed{SEED}"
MATRIX_DIR = SAMPLE_ROOT / f"scenario_matrix_seed{SEED}"
SENSITIVITY_DIR = SAMPLE_ROOT / f"toxicity_spread_sensitivity_seed{SEED}"


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
        "--interview-mode",
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


def _run_sensitivity(out_dir: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "experiments.run_options_toxicity_spread_sensitivity",
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


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_head_csv(src: Path, dst: Path, rows: int = 25) -> None:
    data_rows = _read_csv_rows(src)
    fieldnames = list(data_rows[0].keys()) if data_rows else []
    with dst.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if data_rows:
            writer.writerows(data_rows[:rows])


def _select_worked_fill(rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None

    def _score(row: dict[str, str]) -> tuple[int, float]:
        toxic = str(row.get("toxic_flow", "")).lower() == "true"
        hedged = abs(float(row.get("hedge_qty", "0") or 0.0)) > 0.0
        markout = abs(float(row.get("signed_markout", "0") or 0.0))
        return (2 if toxic and hedged else 1 if hedged else 0, markout)

    return max(rows, key=_score)


def _sample_case_output_files() -> dict[str, str]:
    case_dir = CASE_STUDY_DIR.as_posix()
    return {
        "summary": f"{case_dir}/summary.json",
        "fills": f"{case_dir}/fills_head.csv",
        "checkpoints": f"{case_dir}/checkpoints_head.csv",
        "pnl_timeseries": f"{case_dir}/pnl_timeseries_head.csv",
        "positions_final": f"{case_dir}/positions_final.csv",
        "report": f"{case_dir}/demo_report.md",
        "interview_brief": f"{case_dir}/interview_brief.md",
        "overview_dashboard_plot": f"{case_dir}/overview_dashboard.png",
        "pnl_over_time_plot": f"{case_dir}/pnl_over_time.png",
        "inventory_over_time_plot": f"{case_dir}/inventory_over_time.png",
        "net_delta_over_time_plot": f"{case_dir}/net_delta_over_time.png",
        "toxic_vs_nontoxic_plot": f"{case_dir}/toxic_vs_nontoxic_markout.png",
        "position_surface_heatmap_plot": f"{case_dir}/position_surface_heatmap.png",
        "vega_surface_heatmap_plot": f"{case_dir}/vega_surface_heatmap.png",
    }


def _write_sanitized_case_artifacts(case_tmp: Path, case_study_dir: Path) -> None:
    summary = json.loads((case_tmp / "summary.json").read_text(encoding="utf-8"))
    fills_rows = _read_csv_rows(case_tmp / "fills.csv")
    worked_fill = _select_worked_fill(fills_rows)
    summary["output_files"] = _sample_case_output_files()

    (case_study_dir / "demo_report.md").write_text(format_demo_report(summary), encoding="utf-8")
    (case_study_dir / "interview_brief.md").write_text(
        format_interview_brief(summary, worked_fill),
        encoding="utf-8",
    )
    with (case_study_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)


def main() -> None:
    repo_root = _repo_root()
    case_study_dir = repo_root / CASE_STUDY_DIR
    matrix_dir = repo_root / MATRIX_DIR
    sensitivity_dir = repo_root / SENSITIVITY_DIR
    _clear_directory(case_study_dir)
    _clear_directory(matrix_dir)
    _clear_directory(sensitivity_dir)

    with tempfile.TemporaryDirectory(prefix="lob_sim_options_sample_") as tmp_name:
        tmp_dir = Path(tmp_name)
        case_study_tmp = tmp_dir / "case_study"
        matrix_tmp = tmp_dir / "scenario_matrix"
        sensitivity_tmp = tmp_dir / "toxicity_spread"
        _run_demo(case_study_tmp)
        _run_matrix(matrix_tmp)
        _run_sensitivity(sensitivity_tmp)

        _copy_file(case_study_tmp / "overview_dashboard.png", case_study_dir / "overview_dashboard.png")
        _copy_file(case_study_tmp / "pnl_over_time.png", case_study_dir / "pnl_over_time.png")
        _copy_file(case_study_tmp / "inventory_over_time.png", case_study_dir / "inventory_over_time.png")
        _copy_file(case_study_tmp / "net_delta_over_time.png", case_study_dir / "net_delta_over_time.png")
        _copy_file(
            case_study_tmp / "toxic_vs_nontoxic_markout.png",
            case_study_dir / "toxic_vs_nontoxic_markout.png",
        )
        _copy_file(
            case_study_tmp / "position_surface_heatmap.png",
            case_study_dir / "position_surface_heatmap.png",
        )
        _copy_file(case_study_tmp / "vega_surface_heatmap.png", case_study_dir / "vega_surface_heatmap.png")
        _copy_file(case_study_tmp / "positions_final.csv", case_study_dir / "positions_final.csv")
        _write_head_csv(case_study_tmp / "fills.csv", case_study_dir / "fills_head.csv", rows=25)
        _write_head_csv(case_study_tmp / "checkpoints.csv", case_study_dir / "checkpoints_head.csv", rows=25)
        _write_head_csv(case_study_tmp / "pnl_timeseries.csv", case_study_dir / "pnl_timeseries_head.csv", rows=25)
        _write_sanitized_case_artifacts(case_study_tmp, case_study_dir)

        _copy_file(matrix_tmp / "scenario_matrix.csv", matrix_dir / "scenario_matrix.csv")
        _copy_file(matrix_tmp / "scenario_matrix.md", matrix_dir / "scenario_matrix.md")
        _copy_file(matrix_tmp / "scenario_comparison.png", matrix_dir / "scenario_comparison.png")

        _copy_file(
            sensitivity_tmp / "toxicity_spread_sensitivity.csv",
            sensitivity_dir / "toxicity_spread_sensitivity.csv",
        )
        _copy_file(
            sensitivity_tmp / "toxicity_spread_sensitivity.md",
            sensitivity_dir / "toxicity_spread_sensitivity.md",
        )
        _copy_file(
            sensitivity_tmp / "toxicity_spread_heatmap.png",
            sensitivity_dir / "toxicity_spread_heatmap.png",
        )

    print(f"Refreshed sample outputs in {repo_root / SAMPLE_ROOT}")


if __name__ == "__main__":
    main()
