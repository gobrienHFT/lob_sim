from __future__ import annotations

import json
import re
import sys
import csv
from hashlib import sha256
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = REPO_ROOT / "docs" / "sample_outputs"
BENCHMARK_RESULTS_DIR = REPO_ROOT / "docs" / "benchmark_results"
STRATEGY_RESULTS_DIR = REPO_ROOT / "docs" / "strategy_results"
FUTURES_STRATEGY_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_strategy_profile_reference.py"
FUTURES_SHOWCASE_DIR = SAMPLE_ROOT / "futures_replay_walkthrough"
RECORDED_CLIP_DIR = SAMPLE_ROOT / "futures_recorded_clip_case"
CASE_STUDY_DIR = SAMPLE_ROOT / "toxic_flow_seed7"
SCENARIO_MATRIX_DIR = SAMPLE_ROOT / "scenario_matrix_seed7"
SENSITIVITY_DIR = SAMPLE_ROOT / "toxicity_spread_sensitivity_seed7"
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
FUTURES_BENCHMARK_REFERENCE = BENCHMARK_RESULTS_DIR / "futures_replay_reference.md"
FUTURES_BENCHMARK_REFERENCE_JSON = BENCHMARK_RESULTS_DIR / "futures_replay_reference.json"
FUTURES_STRATEGY_PROFILES = REPO_ROOT / "docs" / "futures_strategy_profiles.md"
FUTURES_STRATEGY_REFERENCE = STRATEGY_RESULTS_DIR / "futures_strategy_profile_reference.md"
REPLAY_CONTRACT = REPO_ROOT / "docs" / "replay_contract.md"
HFT_REVIEWER_GUIDE = REPO_ROOT / "docs" / "hft_reviewer_guide.md"
EXTENSION_POINTS = REPO_ROOT / "docs" / "extension_points.md"
TOKENIZED_ASSETS_ROADMAP = REPO_ROOT / "docs" / "tokenized_assets_roadmap.md"
COMMITTED_STRATEGY_PROFILE_INPUTS = (
    "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson",
    "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson",
)
FUTURES_SHOWCASE_SUMMARY = FUTURES_SHOWCASE_DIR / "summary.json"
RECORDED_CLIP_SUMMARY = RECORDED_CLIP_DIR / "summary.json"
CASE_STUDY_SUMMARY = CASE_STUDY_DIR / "summary.json"
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]+\]\(([^)]+)\)")
MALFORMED_OUT_DIR_PATTERN = re.compile(r"--out-dir(?:\s+|\s*=\s*)(?:--|\r?\n|$)")
TEMP_PATH_MARKERS = ("AppData", "Temp\\", "/tmp/", "lob_sim_options_sample_")
FUTURES_SHOWCASE_FRONT_DOOR_LINKS = {
    REPO_ROOT / "README.md": [
        "docs/sample_outputs/futures_replay_walkthrough/README.md",
        "docs/sample_outputs/futures_replay_walkthrough/summary.json",
        "docs/sample_outputs/futures_replay_walkthrough/trades.csv",
        "docs/sample_outputs/futures_replay_walkthrough/event_trace.csv",
        "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md",
        "docs/sample_outputs/futures_recorded_clip_case/README.md",
    ],
    REPO_ROOT / "WALKTHROUGH.md": [
        "docs/sample_outputs/futures_replay_walkthrough/README.md",
        "docs/sample_outputs/futures_replay_walkthrough/summary.json",
        "docs/sample_outputs/futures_replay_walkthrough/trades.csv",
        "docs/sample_outputs/futures_replay_walkthrough/event_trace.csv",
        "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md",
        "docs/sample_outputs/futures_recorded_clip_case/README.md",
        "docs/sample_outputs/futures_recorded_clip_case/case_notes.md",
    ],
    REPO_ROOT / "docs" / "sample_outputs" / "README.md": [
        "futures_replay_walkthrough/README.md",
        "futures_replay_walkthrough/summary.json",
        "futures_replay_walkthrough/manifest.json",
        "futures_replay_walkthrough/trades.csv",
        "futures_replay_walkthrough/event_trace.csv",
        "futures_replay_walkthrough/walkthrough.md",
        "futures_recorded_clip_case/README.md",
        "futures_recorded_clip_case/summary.json",
        "futures_recorded_clip_case/manifest.json",
        "futures_recorded_clip_case/trades.csv",
        "futures_recorded_clip_case/event_trace.csv",
        "futures_recorded_clip_case/case_notes.md",
    ],
}

ROOT_LAUNCHER_FILES = [
    REPO_ROOT / "run_demo.bat",
    REPO_ROOT / "run_futures_scenario.bat",
    REPO_ROOT / "run_options_case_study.bat",
    REPO_ROOT / "run_options_case_study.sh",
    REPO_ROOT / "run_options_mm_case.bat",
    REPO_ROOT / "run_options_mm_case.sh",
    REPO_ROOT / "run_options_mm_quick.bat",
    REPO_ROOT / "run_options_mm_walkthrough_mode.bat",
]

CANONICAL_LAUNCHER_FILES = [
    REPO_ROOT / "scripts" / "launchers" / "run_demo.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_futures_scenario.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_case_study.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_case_study.sh",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_case.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_case.sh",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_quick.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_walkthrough_mode.bat",
]

LAUNCHER_DOC_LINKS = {
    REPO_ROOT / "README.md": [
        r"scripts\launchers\run_options_case_study.bat",
        "bash scripts/launchers/run_options_case_study.sh",
        r"scripts\launchers\run_options_mm_case.bat",
        r"scripts\launchers\run_options_mm_walkthrough_mode.bat",
    ],
    REPO_ROOT / "docs" / "options_mm_demo_guide.md": [
        "scripts/launchers/run_options_mm_walkthrough_mode.bat",
        "bash scripts/launchers/run_options_mm_case.sh",
        "scripts/launchers/run_options_mm_case.bat",
        "scripts/launchers/run_options_case_study.bat",
        "scripts/launchers/run_options_mm_quick.bat",
    ],
}

BENCHMARK_FRONT_DOOR_LINKS = {
    REPO_ROOT / "README.md": [
        "docs/benchmark_results/futures_replay_reference.md",
    ],
    REPO_ROOT / "WALKTHROUGH.md": [
        "docs/benchmark_results/futures_replay_reference.md",
    ],
    FUTURES_BENCHMARKS: [
        "benchmark_results/futures_replay_reference.md",
        "benchmark_results/futures_replay_reference.json",
    ],
}

STRATEGY_PROFILE_FRONT_DOOR_LINKS = {
    REPO_ROOT / "README.md": [
        "docs/futures_strategy_profiles.md",
        "docs/strategy_results/futures_strategy_profile_reference.md",
    ],
    REPO_ROOT / "WALKTHROUGH.md": [
        "docs/futures_strategy_profiles.md",
        "docs/strategy_results/futures_strategy_profile_reference.md",
    ],
}

REPLAY_CONTRACT_FRONT_DOOR_LINKS = {
    REPO_ROOT / "README.md": [
        "docs/hft_reviewer_guide.md",
        "docs/replay_contract.md",
        "docs/extension_points.md",
        "docs/tokenized_assets_roadmap.md",
    ],
    REPO_ROOT / "docs" / "futures_validation.md": [
        "tests/test_record_schema.py",
    ],
}

MARKDOWN_AUDIT_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "WALKTHROUGH.md",
    REPO_ROOT / "docs" / "binance_usdm_feed_semantics.md",
    REPO_ROOT / "docs" / "futures_validation.md",
    HFT_REVIEWER_GUIDE,
    REPLAY_CONTRACT,
    EXTENSION_POINTS,
    TOKENIZED_ASSETS_ROADMAP,
    REPO_ROOT / "docs" / "futures_strategy_profiles.md",
    REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md",
    REPO_ROOT / "docs" / "futures_benchmarks.md",
    REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md",
    REPO_ROOT / "docs" / "options_mm_demo_guide.md",
    REPO_ROOT / "docs" / "sample_outputs" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "walkthrough.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "case_notes.md",
    REPO_ROOT / "docs" / "options_case_study_notes.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "case_brief.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "demo_report.md",
    REPO_ROOT / "docs" / "sample_outputs" / "scenario_matrix_seed7" / "scenario_matrix.md",
    REPO_ROOT / "docs" / "sample_outputs" / "toxicity_spread_sensitivity_seed7" / "toxicity_spread_sensitivity.md",
]

FUTURES_SHOWCASE_CORE_FILES = [
    "README.md",
    "walkthrough.md",
    "input_fixture.ndjson",
    "summary.json",
    "summary.csv",
    "manifest.json",
    "trades.csv",
    "event_trace.csv",
]

RECORDED_CLIP_CORE_FILES = [
    "README.md",
    "case_notes.md",
    "input_clip.ndjson",
    "summary.json",
    "summary.csv",
    "manifest.json",
    "trades.csv",
    "event_trace.csv",
]

FUTURES_TRADE_AUDIT_FIELDS = {"fill_source", "fee_bps", "fee", "fee_currency"}

CASE_STUDY_CORE_FILES = [
    "case_brief.md",
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


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    for summary_path in [FUTURES_SHOWCASE_SUMMARY, RECORDED_CLIP_SUMMARY, CASE_STUDY_SUMMARY]:
        summary = json.loads(_read_text(summary_path))
        for label, relative_path in summary["output_files"].items():
            target = REPO_ROOT / relative_path
            if not target.exists():
                issues.append(
                    f"{_repo_relative(summary_path)} output_files[{label}] is missing: {relative_path}"
                )
    return issues


def _verify_core_files() -> list[str]:
    issues: list[str] = []
    for directory, expected_names in [
        (FUTURES_SHOWCASE_DIR, FUTURES_SHOWCASE_CORE_FILES),
        (RECORDED_CLIP_DIR, RECORDED_CLIP_CORE_FILES),
        (CASE_STUDY_DIR, CASE_STUDY_CORE_FILES),
        (SCENARIO_MATRIX_DIR, SCENARIO_MATRIX_CORE_FILES),
        (SENSITIVITY_DIR, SENSITIVITY_CORE_FILES),
    ]:
        for name in expected_names:
            if not (directory / name).exists():
                issues.append(f"Missing committed artifact: {_repo_relative(directory / name)}")
    return issues


def _verify_manifest_output_artifacts() -> list[str]:
    issues: list[str] = []
    for manifest_path in [FUTURES_SHOWCASE_DIR / "manifest.json", RECORDED_CLIP_DIR / "manifest.json"]:
        manifest = json.loads(_read_text(manifest_path))
        if manifest.get("schema_version") != "lob_sim.simulation_run.v2":
            issues.append(f"{_repo_relative(manifest_path)} has unexpected simulation manifest schema_version")
        artifacts = manifest.get("output_artifacts")
        if not isinstance(artifacts, dict):
            issues.append(f"{_repo_relative(manifest_path)} is missing output_artifacts")
            continue
        for label in ["summary", "summary_csv", "trades", "event_trace"]:
            metadata = artifacts.get(label)
            if not isinstance(metadata, dict):
                issues.append(f"{_repo_relative(manifest_path)} output_artifacts[{label}] is missing")
                continue
            relative_path = metadata.get("path")
            if not isinstance(relative_path, str):
                issues.append(f"{_repo_relative(manifest_path)} output_artifacts[{label}].path is missing")
                continue
            target = REPO_ROOT / relative_path
            if not target.exists():
                issues.append(f"{_repo_relative(manifest_path)} output_artifacts[{label}] target is missing: {relative_path}")
                continue
            if metadata.get("size_bytes") != target.stat().st_size:
                issues.append(f"{_repo_relative(manifest_path)} output_artifacts[{label}].size_bytes is stale")
            if metadata.get("sha256") != _file_sha256(target):
                issues.append(f"{_repo_relative(manifest_path)} output_artifacts[{label}].sha256 is stale")
        manifest_artifact = artifacts.get("manifest")
        expected_manifest_path = _repo_relative(manifest_path)
        if not isinstance(manifest_artifact, dict) or manifest_artifact.get("path") != expected_manifest_path:
            issues.append(f"{_repo_relative(manifest_path)} output_artifacts[manifest].path is missing or stale")
    return issues


def _verify_futures_trade_audit_fields() -> list[str]:
    issues: list[str] = []
    for path in [FUTURES_SHOWCASE_DIR / "trades.csv", RECORDED_CLIP_DIR / "trades.csv"]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            missing = sorted(FUTURES_TRADE_AUDIT_FIELDS - fieldnames)
            if missing:
                issues.append(f"{_repo_relative(path)} is missing trade audit column(s): {', '.join(missing)}")
                continue
            for row_index, row in enumerate(reader, start=2):
                for field in FUTURES_TRADE_AUDIT_FIELDS:
                    if row.get(field) in {None, ""}:
                        issues.append(
                            f"{_repo_relative(path)}:{row_index} has empty trade audit field: {field}"
                        )
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
        FUTURES_SHOWCASE_DIR / "README.md",
        FUTURES_SHOWCASE_DIR / "walkthrough.md",
        FUTURES_SHOWCASE_DIR / "summary.json",
        FUTURES_SHOWCASE_DIR / "summary.csv",
        FUTURES_SHOWCASE_DIR / "manifest.json",
        RECORDED_CLIP_DIR / "README.md",
        RECORDED_CLIP_DIR / "case_notes.md",
        RECORDED_CLIP_DIR / "summary.json",
        RECORDED_CLIP_DIR / "summary.csv",
        RECORDED_CLIP_DIR / "manifest.json",
        FUTURES_BENCHMARKS,
        FUTURES_BENCHMARK_REFERENCE,
        FUTURES_STRATEGY_PROFILES,
        FUTURES_STRATEGY_REFERENCE,
        CASE_STUDY_DIR / "case_brief.md",
        CASE_STUDY_DIR / "demo_report.md",
        CASE_STUDY_DIR / "summary.json",
    ]:
        text = _read_text(path)
        if path in {FUTURES_BENCHMARKS, FUTURES_BENCHMARK_REFERENCE}:
            if "local-only" in text or "data/raw_1772633471.ndjson" in text:
                issues.append(f"Benchmark doc still depends on a local-only raw file: {_repo_relative(path)}")
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


def _verify_futures_showcase_front_door_links() -> list[str]:
    issues: list[str] = []
    for path, expected_links in FUTURES_SHOWCASE_FRONT_DOOR_LINKS.items():
        text = _read_text(path)
        for link in expected_links:
            if link not in text:
                issues.append(f"Missing futures walkthrough link in {_repo_relative(path)}: {link}")
    return issues


def _verify_launcher_layout() -> list[str]:
    issues: list[str] = []
    for path in ROOT_LAUNCHER_FILES:
        if path.exists():
            issues.append(f"Launcher should not remain in repo root: {_repo_relative(path)}")
    for path in CANONICAL_LAUNCHER_FILES:
        if not path.exists():
            issues.append(f"Missing canonical launcher: {_repo_relative(path)}")
    for path, expected_links in LAUNCHER_DOC_LINKS.items():
        text = _read_text(path)
        for link in expected_links:
            if link not in text:
                issues.append(f"Missing canonical launcher reference in {_repo_relative(path)}: {link}")
    return issues


def _verify_benchmark_publication() -> list[str]:
    issues: list[str] = []
    if not FUTURES_BENCHMARK_REFERENCE.exists():
        issues.append(
            f"Missing published benchmark artifact: {_repo_relative(FUTURES_BENCHMARK_REFERENCE)}"
        )
    if not FUTURES_BENCHMARK_REFERENCE_JSON.exists():
        issues.append(
            f"Missing published benchmark JSON artifact: {_repo_relative(FUTURES_BENCHMARK_REFERENCE_JSON)}"
        )
    else:
        try:
            result = json.loads(_read_text(FUTURES_BENCHMARK_REFERENCE_JSON))
        except json.JSONDecodeError as exc:
            issues.append(f"Benchmark JSON artifact is not valid JSON: {exc}")
        else:
            expected_input = "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson"
            expected_sha = _file_sha256(REPO_ROOT / expected_input)
            if result.get("schema_version") != "lob_sim.replay_benchmark.v1":
                issues.append("Benchmark JSON artifact has an unexpected schema_version")
            if result.get("metadata", {}).get("input_file") != expected_input:
                issues.append("Benchmark JSON artifact does not reference the committed recorded clip input")
            if result.get("metadata", {}).get("input_sha256") != expected_sha:
                issues.append("Benchmark JSON artifact input_sha256 does not match the committed recorded clip")
            if result.get("metadata", {}).get("source", {}).get("git_dirty") is not False:
                issues.append("Benchmark JSON artifact should be refreshed from a clean source tree")
            for section in ["event_counts", "timing", "memory"]:
                if section not in result:
                    issues.append(f"Benchmark JSON artifact is missing section: {section}")

    text = _read_text(FUTURES_BENCHMARKS)
    if "## Published Reference Run" not in text:
        issues.append("docs/futures_benchmarks.md is missing the published benchmark section")
    else:
        try:
            published = _section_text(text, "## Published Reference Run", "## Benchmark Tool")
        except ValueError as exc:
            issues.append(f"docs/futures_benchmarks.md: {exc}")
        else:
            if "TBD" in published:
                issues.append("docs/futures_benchmarks.md published benchmark section still contains TBD")

    for path, expected_links in BENCHMARK_FRONT_DOOR_LINKS.items():
        path_text = _read_text(path)
        for link in expected_links:
            if link not in path_text:
                issues.append(f"Missing benchmark link in {_repo_relative(path)}: {link}")
    return issues


def _verify_replay_contract_publication() -> list[str]:
    issues: list[str] = []
    if not REPLAY_CONTRACT.exists():
        issues.append(f"Missing replay contract doc: {_repo_relative(REPLAY_CONTRACT)}")
        return issues

    text = _read_text(REPLAY_CONTRACT)
    required_tokens = [
        "Recorded Event Schema",
        "Stream Inspection",
        "Simulation Manifests",
        "python -m lob_sim.cli inspect",
        "SHA-256",
    ]
    for token in required_tokens:
        if token not in text:
            issues.append(f"Replay contract doc is missing expected token: {token}")

    for path, expected_links in REPLAY_CONTRACT_FRONT_DOOR_LINKS.items():
        path_text = _read_text(path)
        for link in expected_links:
            if link not in path_text:
                issues.append(f"Missing replay-contract link in {_repo_relative(path)}: {link}")
    for path in [HFT_REVIEWER_GUIDE, EXTENSION_POINTS, TOKENIZED_ASSETS_ROADMAP]:
        if not path.exists():
            issues.append(f"Missing reviewer/extension doc: {_repo_relative(path)}")
    return issues


def _verify_strategy_profile_publication() -> list[str]:
    issues: list[str] = []
    if not FUTURES_STRATEGY_PROFILES.exists():
        issues.append(f"Missing futures strategy profile doc: {_repo_relative(FUTURES_STRATEGY_PROFILES)}")
    if not FUTURES_STRATEGY_REFERENCE.exists():
        issues.append(f"Missing futures strategy reference doc: {_repo_relative(FUTURES_STRATEGY_REFERENCE)}")
    if not FUTURES_STRATEGY_REFRESH.exists():
        issues.append(f"Missing futures strategy refresh script: {_repo_relative(FUTURES_STRATEGY_REFRESH)}")

    for path, expected_links in STRATEGY_PROFILE_FRONT_DOOR_LINKS.items():
        text = _read_text(path)
        for link in expected_links:
            if link not in text:
                issues.append(f"Missing strategy-profile link in {_repo_relative(path)}: {link}")

    reference = _read_text(FUTURES_STRATEGY_REFERENCE)
    if not any(path in reference for path in COMMITTED_STRATEGY_PROFILE_INPUTS):
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md must reference a committed replay input"
        )
    if "local-only" in reference:
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md still describes the input as local-only"
        )
    if "data/raw_1772633471.ndjson" in reference:
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md still depends on the old local raw file path"
        )
    if "python scripts/refresh_futures_strategy_profile_reference.py" not in reference:
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md is missing the refresh command"
        )
    if "research_mm" not in reference:
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md must include the research_mm profile"
        )

    section_expectations = [
        (
            REPO_ROOT / "README.md",
            "## Walkthrough Path",
            None,
        ),
        (
            REPO_ROOT / "WALKTHROUGH.md",
            "## 5-Minute Walkthrough",
            "## Core Talking Points",
        ),
    ]
    ordered_tokens = [
        "docs/sample_outputs/futures_recorded_clip_case/README.md",
        "docs/futures_strategy_profiles.md",
        "docs/strategy_results/futures_strategy_profile_reference.md",
    ]
    for path, start_marker, end_marker in section_expectations:
        text = _read_text(path)
        try:
            section = _section_text(text, start_marker, end_marker)
        except ValueError as exc:
            issues.append(f"{_repo_relative(path)}: {exc}")
            continue
        last_index = -1
        for token in ordered_tokens:
            index = section.find(token)
            if index < 0:
                issues.append(f"Missing strategy-profile walkthrough item in {_repo_relative(path)}: {token}")
                continue
            if index <= last_index:
                issues.append(
                    f"Strategy-profile walkthrough order is incorrect in {_repo_relative(path)}: {token}"
                )
            last_index = index
    return issues


def _verify_artifact_order() -> list[str]:
    issues: list[str] = []
    expectations = [
        (
            REPO_ROOT / "README.md",
            "## Walkthrough Path",
            None,
            [
                "1. `README.md`",
                "2. `docs/binance_usdm_feed_semantics.md`",
                "3. `docs/futures_validation.md`",
                "4. `docs/sample_outputs/futures_replay_walkthrough/README.md`",
                "5. `docs/sample_outputs/futures_replay_walkthrough/summary.json`",
                "6. `docs/sample_outputs/futures_replay_walkthrough/trades.csv`",
                "7. `docs/sample_outputs/futures_replay_walkthrough/walkthrough.md`",
                "8. `docs/sample_outputs/futures_recorded_clip_case/README.md`",
                "9. `docs/futures_strategy_profiles.md`",
                "10. `docs/strategy_results/futures_strategy_profile_reference.md`",
                "11. `docs/sample_outputs/toxic_flow_seed7/case_brief.md`",
                "12. `docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md`",
                "13. `docs/options_case_study_notes.md`",
            ],
        ),
        (
            REPO_ROOT / "docs" / "options_mm_demo_guide.md",
            "## Recommended artifact order",
            "If you want to show the case study is not one cherry-picked path, run:",
            [
                "1. Open `case_brief.md`.",
                "2. Open `overview_dashboard.png`.",
                "3. Open `implied_vol_surface_snapshot.png`.",
                "4. Open `position_surface_heatmap.png`.",
                "5. Open `vega_surface_heatmap.png`.",
                "6. Open the representative fill in `case_brief.md`.",
                "7. Open `scenario_matrix.md`.",
                "8. Open `toxicity_spread_sensitivity.md`.",
            ],
        ),
        (
            REPO_ROOT / "docs" / "sample_outputs" / "README.md",
            "Recommended artifact order:",
            "Cross-scenario credibility check:",
            [
                "1. [`toxic_flow_seed7/case_brief.md`](toxic_flow_seed7/case_brief.md)",
                "2. [`toxic_flow_seed7/overview_dashboard.png`](toxic_flow_seed7/overview_dashboard.png)",
                "3. [`toxic_flow_seed7/implied_vol_surface_snapshot.png`](toxic_flow_seed7/implied_vol_surface_snapshot.png)",
                "4. [`toxic_flow_seed7/position_surface_heatmap.png`](toxic_flow_seed7/position_surface_heatmap.png)",
                "5. [`toxic_flow_seed7/vega_surface_heatmap.png`](toxic_flow_seed7/vega_surface_heatmap.png)",
                "6. representative fill in [`toxic_flow_seed7/case_brief.md#representative-fill`](toxic_flow_seed7/case_brief.md#representative-fill)",
                "7. [`scenario_matrix_seed7/scenario_matrix.md`](scenario_matrix_seed7/scenario_matrix.md)",
                "8. [`toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md`](toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md)",
            ],
        ),
        (
            REPO_ROOT / "scripts" / "launchers" / "run_options_mm_walkthrough_mode.bat",
            "echo [options] Recommended artifact order:",
            "echo [options] Open %OUT_DIR%\\case_brief.md first.",
            [
                "echo [options]   1. %OUT_DIR%\\case_brief.md",
                "echo [options]   2. %OUT_DIR%\\overview_dashboard.png",
                "echo [options]   3. %OUT_DIR%\\implied_vol_surface_snapshot.png",
                "echo [options]   4. %OUT_DIR%\\position_surface_heatmap.png",
                "echo [options]   5. %OUT_DIR%\\vega_surface_heatmap.png",
                "echo [options]   6. representative fill in %OUT_DIR%\\case_brief.md",
                "echo [options]   7. docs\\sample_outputs\\scenario_matrix_seed7\\scenario_matrix.md",
                "echo [options]   8. docs\\sample_outputs\\toxicity_spread_sensitivity_seed7\\toxicity_spread_sensitivity.md",
            ],
        ),
        (
            CASE_STUDY_DIR / "case_brief.md",
            "## Files to open next",
            None,
            [
                "- `case_brief.md`: docs/sample_outputs/toxic_flow_seed7/case_brief.md",
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
                issues.append(f"Missing artifact-order item in {_repo_relative(path)}: {line}")
                continue
            if index <= last_index:
                issues.append(f"Out-of-order artifact-order item in {_repo_relative(path)}: {line}")
            last_index = index
    return issues


def collect_artifact_issues() -> list[str]:
    issues: list[str] = []
    issues.extend(_verify_markdown_links())
    issues.extend(_verify_summary_output_files())
    issues.extend(_verify_manifest_output_artifacts())
    issues.extend(_verify_core_files())
    issues.extend(_verify_futures_trade_audit_fields())
    issues.extend(_verify_implied_vol_snapshot_references())
    issues.extend(_verify_no_temp_paths())
    issues.extend(_verify_no_malformed_cli_fragments())
    issues.extend(_verify_futures_showcase_front_door_links())
    issues.extend(_verify_launcher_layout())
    issues.extend(_verify_strategy_profile_publication())
    issues.extend(_verify_benchmark_publication())
    issues.extend(_verify_replay_contract_publication())
    issues.extend(_verify_artifact_order())
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
