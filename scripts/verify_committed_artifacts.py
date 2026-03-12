from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = REPO_ROOT / "docs" / "sample_outputs"
CASE_STUDY_DIR = SAMPLE_ROOT / "toxic_flow_seed7"
SCENARIO_MATRIX_DIR = SAMPLE_ROOT / "scenario_matrix_seed7"
SENSITIVITY_DIR = SAMPLE_ROOT / "toxicity_spread_sensitivity_seed7"
CASE_STUDY_SUMMARY = CASE_STUDY_DIR / "summary.json"
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]+\]\(([^)]+)\)")
MALFORMED_OUT_DIR_PATTERN = re.compile(r"--out-dir(?:\s+|\s*=\s*)(?:--|\r?\n|$)")
TEMP_PATH_MARKERS = ("AppData", "Temp\\", "/tmp/", "lob_sim_options_sample_")

MARKDOWN_AUDIT_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "options_mm_demo_guide.md",
    REPO_ROOT / "docs" / "sample_outputs" / "README.md",
    REPO_ROOT / "docs" / "interview_talk_track.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "interview_brief.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "demo_report.md",
    REPO_ROOT / "docs" / "sample_outputs" / "scenario_matrix_seed7" / "scenario_matrix.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxicity_spread_sensitivity_seed7" / "toxicity_spread_sensitivity.md",
]

CASE_STUDY_CORE_FILES = [
    "interview_brief.md",
    "demo_report.md",
    "summary.json",
    "overview_dashboard.png",
    "position_surface_heatmap.png",
    "vega_surface_heatmap.png",
    "fills_head.csv",
    "checkpoints_head.csv",
]

SCENARIO_MATRIX_CORE_FILES = [
    "scenario_matrix.md",
    "scenario_matrix.csv",
    "scenario_comparison.png",
]

SENSITIVITY_CORE_FILES = [
    "toxicity_spread_sensitivity.md",
    "toxicity_spread_sensitivity.csv",
    "toxicity_spread_heatmap.png",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section_text(text: str, start_marker: str, end_marker: str | None = None) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise ValueError(f"missing section marker: {start_marker}")
    start += len(start_marker)
    if end_marker is None:
        return text[start:]
    end = text.find(end_marker, start)
    if end < 0:
        raise ValueError(f"missing section end marker: {end_marker}")
    return text[start:end]


def _repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _iter_repo_relative_links(path: Path) -> list[str]:
    links: list[str] = []
    for link in MARKDOWN_LINK_PATTERN.findall(_read_text(path)):
        if "://" in link or link.startswith("#"):
            continue
        links.append(link)
    return links


def _resolve_repo_relative_link(path: Path, link: str) -> Path:
    link_target = link.split("#", 1)[0]
    target = (path.parent / link_target).resolve()
    return target


def _verify_markdown_links() -> list[str]:
    issues: list[str] = []
    for path in MARKDOWN_AUDIT_FILES:
        for link in _iter_repo_relative_links(path):
            target = _resolve_repo_relative_link(path, link)
            if not target.exists():
                issues.append(
                    f"Broken markdown link in {_repo_relative(path)}: {link}"
                )
                continue
            try:
                target.relative_to(REPO_ROOT.resolve())
            except ValueError:
                issues.append(
                    f"Markdown link escapes repository in {_repo_relative(path)}: {link}"
                )
    return issues


def _verify_summary_output_files() -> list[str]:
    issues: list[str] = []
    summary = json.loads(_read_text(CASE_STUDY_SUMMARY))
    for label, relative_path in summary["output_files"].items():
        target = REPO_ROOT / relative_path
        if not target.exists():
            issues.append(f"summary.json output_files[{label}] is missing: {relative_path}")
    return issues


def _verify_core_files() -> list[str]:
    issues: list[str] = []
    for directory, expected_names in [
        (CASE_STUDY_DIR, CASE_STUDY_CORE_FILES),
        (SCENARIO_MATRIX_DIR, SCENARIO_MATRIX_CORE_FILES),
        (SENSITIVITY_DIR, SENSITIVITY_CORE_FILES),
    ]:
        for name in expected_names:
            if not (directory / name).exists():
                issues.append(f"Missing committed artifact: {_repo_relative(directory / name)}")
    return issues


def _verify_implied_vol_snapshot_references() -> list[str]:
    issues: list[str] = []
    referenced = False
    for path in MARKDOWN_AUDIT_FILES:
        if "implied_vol_surface_snapshot.png" in _read_text(path):
            referenced = True
            break
    if referenced and not (CASE_STUDY_DIR / "implied_vol_surface_snapshot.png").exists():
        issues.append(
            "implied_vol_surface_snapshot.png is referenced in committed docs but missing from docs/sample_outputs/toxic_flow_seed7/"
        )
    return issues


def _verify_no_temp_paths() -> list[str]:
    issues: list[str] = []
    for path in [
        CASE_STUDY_DIR / "interview_brief.md",
        CASE_STUDY_DIR / "demo_report.md",
        CASE_STUDY_DIR / "summary.json",
    ]:
        text = _read_text(path)
        for marker in TEMP_PATH_MARKERS:
            if marker in text:
                issues.append(f"Temporary path marker '{marker}' leaked into {_repo_relative(path)}")
    return issues


def _verify_no_malformed_cli_fragments() -> list[str]:
    issues: list[str] = []
    for path in MARKDOWN_AUDIT_FILES:
        text = _read_text(path)
        if "<temp_dir>" in text:
            issues.append(f"Placeholder CLI path leaked into {_repo_relative(path)}: <temp_dir>")
        if MALFORMED_OUT_DIR_PATTERN.search(text):
            issues.append(f"Malformed --out-dir CLI fragment found in {_repo_relative(path)}")
    return issues


def _verify_screen_share_order() -> list[str]:
    issues: list[str] = []
    expectations = [
        (
            REPO_ROOT / "README.md",
            "## 2-Minute Screen-Share Path",
            "If you are browsing on GitHub and not running the code, use the committed sample pack in",
            [
                "1. `interview_brief.md`",
                "2. `overview_dashboard.png`",
                "3. `implied_vol_surface_snapshot.png`",
                "4. `position_surface_heatmap.png`",
                "5. `vega_surface_heatmap.png`",
                "6. representative fill in `interview_brief.md`",
                "7. `scenario_matrix.md`",
                "8. `toxicity_spread_sensitivity.md`",
            ],
        ),
        (
            REPO_ROOT / "docs" / "options_mm_demo_guide.md",
            "## Best screen-share order",
            "If you want to show the case study is not one cherry-picked path, run:",
            [
                "1. Open `interview_brief.md`.",
                "2. Open `overview_dashboard.png`.",
                "3. Open `implied_vol_surface_snapshot.png`.",
                "4. Open `position_surface_heatmap.png`.",
                "5. Open `vega_surface_heatmap.png`.",
                "6. Open the representative fill in `interview_brief.md`.",
                "7. Open `scenario_matrix.md`.",
                "8. Open `toxicity_spread_sensitivity.md`.",
            ],
        ),
        (
            REPO_ROOT / "docs" / "sample_outputs" / "README.md",
            "Canonical screen-share order:",
            "Cross-scenario credibility check:",
            [
                "1. [`toxic_flow_seed7/interview_brief.md`](toxic_flow_seed7/interview_brief.md)",
                "2. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)",
                "3. [`toxic_flow_seed7/implied_vol_surface_snapshot.png`](toxic_flow_seed7/implied_vol_surface_snapshot.png)",
                "4. [`toxic_flow_seed7/position_surface_heatmap.png`](toxic_flow_seed7/position_surface_heatmap.png)",
                "5. [`toxic_flow_seed7/vega_surface_heatmap.png`](toxic_flow_seed7/vega_surface_heatmap.png)",
                "6. representative fill in [`toxic_flow_seed7/interview_brief.md#representative-fill`](toxic_flow_seed7/interview_brief.md#representative-fill)",
                "7. [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)",
                "8. [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)",
            ],
        ),
        (
            REPO_ROOT / "run_options_mm_interview_mode.bat",
            "echo [options] Screen-share order:",
            "echo [options] Open %OUT_DIR%\\interview_brief.md first.",
            [
                "echo [options]   1. %OUT_DIR%\\interview_brief.md",
                "echo [options]   2. %OUT_DIR%\\overview_dashboard.png",
                "echo [options]   3. %OUT_DIR%\\implied_vol_surface_snapshot.png",
                "echo [options]   4. %OUT_DIR%\\position_surface_heatmap.png",
                "echo [options]   5. %OUT_DIR%\\vega_surface_heatmap.png",
                "echo [options]   6. representative fill in %OUT_DIR%\\interview_brief.md",
                "echo [options]   7. docs\\sample_outputs\\scenario_matrix_seed7\\scenario_matrix.md",
                "echo [options]   8. docs\\sample_outputs\\toxicity_spread_sensitivity_seed7\\toxicity_spread_sensitivity.md",
            ],
        ),
        (
            CASE_STUDY_DIR / "interview_brief.md",
            "## Files to open next",
            None,
            [
                "- `interview_brief.md`: docs/sample_outputs/toxic_flow_seed7/interview_brief.md",
                "- `overview_dashboard.png`: docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png",
                "- `implied_vol_surface_snapshot.png`: docs/sample_outputs/toxic_flow_seed7/implied_vol_surface_snapshot.png",
                "- `position_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/position_surface_heatmap.png",
                "- `vega_surface_heatmap.png`: docs/sample_outputs/toxic_flow_seed7/vega_surface_heatmap.png",
                "- representative fill: see the `Representative Fill` section in this file",
                "- `scenario_matrix.md`: docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md",
                "- `toxicity_spread_sensitivity.md`: docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md",
            ],
        ),
    ]
    for path, start_marker, end_marker, ordered_lines in expectations:
        text = _read_text(path)
        try:
            section = _section_text(text, start_marker, end_marker)
        except ValueError as exc:
            issues.append(f"{_repo_relative(path)}: {exc}")
            continue
        last_index = -1
        for line in ordered_lines:
            index = section.find(line)
            if index < 0:
                issues.append(f"Missing screen-share item in {_repo_relative(path)}: {line}")
                continue
            if index <= last_index:
                issues.append(f"Out-of-order screen-share item in {_repo_relative(path)}: {line}")
            last_index = index
    return issues


def collect_artifact_issues() -> list[str]:
    issues: list[str] = []
    issues.extend(_verify_markdown_links())
    issues.extend(_verify_summary_output_files())
    issues.extend(_verify_core_files())
    issues.extend(_verify_implied_vol_snapshot_references())
    issues.extend(_verify_no_temp_paths())
    issues.extend(_verify_no_malformed_cli_fragments())
    issues.extend(_verify_screen_share_order())
    return issues


def assert_no_artifact_issues() -> None:
    issues = collect_artifact_issues()
    if issues:
        raise AssertionError("\n".join(f"- {issue}" for issue in issues))


def main() -> int:
    issues = collect_artifact_issues()
    if issues:
        print("Committed artifact verification failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Committed artifact verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
