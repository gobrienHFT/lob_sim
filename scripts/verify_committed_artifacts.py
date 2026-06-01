from __future__ import annotations

import csv
import json
import math
import re
import sys
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = REPO_ROOT / "docs" / "sample_outputs"
BENCHMARK_RESULTS_DIR = REPO_ROOT / "docs" / "benchmark_results"
STRATEGY_RESULTS_DIR = REPO_ROOT / "docs" / "strategy_results"
FUTURES_STRATEGY_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_strategy_profile_reference.py"
FUTURES_SHOWCASE_DIR = SAMPLE_ROOT / "futures_replay_walkthrough"
RECORDED_CLIP_DIR = SAMPLE_ROOT / "futures_recorded_clip_case"
FUTURES_STRESS_DIR = SAMPLE_ROOT / "futures_stress_case"
CASE_STUDY_DIR = SAMPLE_ROOT / "toxic_flow_seed7"
SCENARIO_MATRIX_DIR = SAMPLE_ROOT / "scenario_matrix_seed7"
SENSITIVITY_DIR = SAMPLE_ROOT / "toxicity_spread_sensitivity_seed7"
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
FUTURES_BENCHMARK_REFERENCE = BENCHMARK_RESULTS_DIR / "futures_replay_reference.md"
FUTURES_BENCHMARK_REFERENCE_JSON = BENCHMARK_RESULTS_DIR / "futures_replay_reference.json"
FUTURES_STRATEGY_PROFILES = REPO_ROOT / "docs" / "futures_strategy_profiles.md"
FUTURES_STRATEGY_REFERENCE = STRATEGY_RESULTS_DIR / "futures_strategy_profile_reference.md"
FUTURES_PARAMETER_SWEEP_REFERENCE = STRATEGY_RESULTS_DIR / "futures_parameter_sweep_reference.md"
FUTURES_PARAMETER_SWEEP_REFERENCE_CSV = STRATEGY_RESULTS_DIR / "futures_parameter_sweep_reference.csv"
FUTURES_PARAMETER_SWEEP_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_parameter_sweep_reference.py"
FUTURES_LATENCY_SWEEP_REFERENCE = STRATEGY_RESULTS_DIR / "futures_latency_sweep_reference.md"
FUTURES_LATENCY_SWEEP_REFERENCE_CSV = STRATEGY_RESULTS_DIR / "futures_latency_sweep_reference.csv"
FUTURES_LATENCY_SWEEP_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_latency_sweep_reference.py"
REPLAY_CONTRACT = REPO_ROOT / "docs" / "replay_contract.md"
HFT_REVIEWER_GUIDE = REPO_ROOT / "docs" / "hft_reviewer_guide.md"
EXTENSION_POINTS = REPO_ROOT / "docs" / "extension_points.md"
TOKENIZED_ASSETS_ROADMAP = REPO_ROOT / "docs" / "tokenized_assets_roadmap.md"
REVIEWER_GATE = REPO_ROOT / "scripts" / "reviewer_gate.py"
COMMITTED_STRATEGY_PROFILE_INPUTS = (
    "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson",
    "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson",
)
FUTURES_SHOWCASE_SUMMARY = FUTURES_SHOWCASE_DIR / "summary.json"
RECORDED_CLIP_SUMMARY = RECORDED_CLIP_DIR / "summary.json"
FUTURES_STRESS_SUMMARY = FUTURES_STRESS_DIR / "summary.json"
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
        "docs/sample_outputs/futures_stress_case/README.md",
        "docs/reviewer_results_memo.md",
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
        "futures_stress_case/README.md",
        "futures_stress_case/summary.json",
        "futures_stress_case/manifest.json",
        "futures_stress_case/trades.csv",
        "futures_stress_case/event_trace.csv",
        "futures_stress_case/case_notes.md",
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
        "docs/strategy_results/futures_parameter_sweep_reference.md",
        "docs/strategy_results/futures_latency_sweep_reference.md",
    ],
    REPO_ROOT / "WALKTHROUGH.md": [
        "docs/futures_strategy_profiles.md",
        "docs/strategy_results/futures_strategy_profile_reference.md",
        "docs/strategy_results/futures_parameter_sweep_reference.md",
        "docs/strategy_results/futures_latency_sweep_reference.md",
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
    REPO_ROOT / "docs" / "strategy_results" / "futures_parameter_sweep_reference.md",
    REPO_ROOT / "docs" / "strategy_results" / "futures_latency_sweep_reference.md",
    REPO_ROOT / "docs" / "futures_benchmarks.md",
    REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md",
    REPO_ROOT / "docs" / "reviewer_results_memo.md",
    REPO_ROOT / "docs" / "architecture_decisions.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "options_mm_demo_guide.md",
    REPO_ROOT / "docs" / "sample_outputs" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "walkthrough.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "case_notes.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_stress_case" / "README.md",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_stress_case" / "case_notes.md",
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

FUTURES_STRESS_CORE_FILES = [
    "README.md",
    "case_notes.md",
    "input_stress.ndjson",
    "summary.json",
    "summary.csv",
    "manifest.json",
    "trades.csv",
    "event_trace.csv",
]

FUTURES_FILL_SOURCES = {"depth_update", "agg_trade", "taker_order"}
FUTURES_ORDER_LIFECYCLE_KEYS = {
    "arrival_scheduled",
    "arrived",
    "rested_after_arrival",
    "immediate_fill_arrivals",
    "expired_unfilled_arrivals",
    "cancel_requested",
    "cancel_acknowledged",
    "self_trade_prevented",
}
FUTURES_TRADE_AUDIT_FIELDS = {
    "fill_source",
    "notional",
    "contract_multiplier",
    "fee_bps",
    "fee",
    "fee_currency",
    "spread_capture",
    "spread_capture_value",
}
FUTURES_EVENT_TRACE_FIELDS = [
    "ts_local",
    "seq",
    "symbol",
    "event_type",
    "source",
    "side",
    "quote_slot",
    "price_tick",
    "qty_lots",
    "order_id",
    "fill_source",
    "details",
]
EXPECTED_FUTURES_FEED_ADAPTER = {
    "name": "binance_usdm",
    "venue_label": "BINANCE_USDM",
    "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
}
EXPECTED_BENCHMARK_SCHEMA_VERSION = "lob_sim.replay_benchmark.v2"
EXPECTED_SIMULATION_ASSUMPTIONS_SCHEMA_VERSION = "lob_sim.simulation_assumptions.v1"
EXPECTED_INSTRUMENT_SPEC_FIELDS = {
    "symbol",
    "venue",
    "price_currency",
    "quantity_unit",
    "tick_size",
    "step_size",
    "contract_multiplier",
}
EXPECTED_SIMULATION_ASSUMPTION_FIELDS = {
    "schema_version",
    "data_scope",
    "private_exchange_execution_reports",
    "queue_priority_model",
    "snapshot_seed",
    "depth_increase",
    "depth_decrease",
    "agg_trade_consumption",
    "overlap_netting",
    "cancel_model",
    "same_timestamp_ordering",
    "marketable_limits",
    "self_trade_prevention",
    "markout",
    "limitations",
}
EXPECTED_SIMULATION_LIMITATIONS = {
    "no_private_queue_ids",
    "no_hidden_liquidity",
    "not_private_exchange_fill_truth",
    "public_l2_cannot_distinguish_all_cancels_from_trades",
}
EXPECTED_PUBLIC_CONSUMPTION_SOURCES = {"depth_update", "agg_trade"}
EXPECTED_PUBLIC_CONSUMPTION_FIELDS = {
    "observed_lots",
    "modeled_lots",
    "overlap_netted_lots",
    "queue_consumed_lots",
    "unmatched_lots",
}
EXPECTED_QUEUE_CONSUMPTION_TRACE_FIELDS = EXPECTED_PUBLIC_CONSUMPTION_FIELDS | {
    "overlap_window_seconds",
}
EXPECTED_MARKOUT_BY_SOURCE_FIELDS = {
    "samples",
    "adverse_samples",
    "qty",
    "avg_markout_1s",
    "adverse_fill_rate_1s",
}
EXPECTED_MARKOUT_TRACE_FIELDS = {
    "fill_ts_local",
    "deadline_ts",
    "horizon",
    "fill_price",
    "qty",
    "fill_mid",
    "mid_after",
    "markout",
    "contract_multiplier",
    "adverse",
    "regime",
}
EXPECTED_FILL_TRACE_FIELDS = {
    "maker",
    "queue_ahead_lots",
    "created_ts",
    "price",
    "qty",
    "notional",
    "contract_multiplier",
    "fee_bps",
    "fee",
    "fee_currency",
    "mid_at_fill",
    "spread_capture",
    "spread_capture_value",
    "time_in_book_ms",
    "markout_horizon",
    "regime",
    "book_bid_tick",
    "book_ask_tick",
}
EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS = 0.125
EXPECTED_STRATEGY_PROFILE_NAMES = {"baseline", "layered_mm", "research_mm"}
REQUIRED_DECISION_DIAGNOSTIC_KEYS = {
    "profile",
    "best_bid_tick",
    "best_ask_tick",
    "mid_ticks",
    "mid_price",
    "inventory_qty",
    "size_lots",
    "volatility",
    "skew_ticks",
    "top_of_book_imbalance",
    "recent_trade_imbalance",
}
PROFILE_DECISION_DIAGNOSTIC_KEYS = {
    "baseline": {"spread_scale", "half_spread_bps", "half_spread_ticks"},
    "layered_mm": {"spread_scale", "inner_spread_ticks", "outer_spread_ticks", "gate_label", "gate_ticks"},
    "research_mm": {
        "spread_scale",
        "combined_imbalance",
        "toxicity_bps",
        "base_half_spread_bps",
        "fee_floor_bps",
        "half_spread_bps",
        "half_spread_ticks",
        "reservation_ticks",
        "reservation_tick",
        "gate_label",
        "gate_reason",
        "gate_ticks",
        "threshold",
        "book_imbalance",
        "trade_imbalance",
        "bid_extra_ticks",
        "ask_extra_ticks",
        "outer_spread_ticks",
    },
}

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


def _config_digest(config: dict) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


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
                issues.append(f"Broken markdown link in {_repo_relative(path)}: {link}")
                continue
            try:
                target.relative_to(REPO_ROOT.resolve())
            except ValueError:
                issues.append(f"Markdown link escapes repository in {_repo_relative(path)}: {link}")
    return issues


def _verify_summary_output_files() -> list[str]:
    issues: list[str] = []
    for summary_path in [FUTURES_SHOWCASE_SUMMARY, RECORDED_CLIP_SUMMARY, CASE_STUDY_SUMMARY]:
        summary = json.loads(_read_text(summary_path))
        for label, relative_path in summary["output_files"].items():
            target = REPO_ROOT / relative_path
            if not target.exists():
                issues.append(f"{_repo_relative(summary_path)} output_files[{label}] is missing: {relative_path}")
    return issues


def _verify_core_files() -> list[str]:
    issues: list[str] = []
    for directory, expected_names in [
        (FUTURES_SHOWCASE_DIR, FUTURES_SHOWCASE_CORE_FILES),
        (RECORDED_CLIP_DIR, RECORDED_CLIP_CORE_FILES),
        (FUTURES_STRESS_DIR, FUTURES_STRESS_CORE_FILES),
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
                issues.append(
                    f"{_repo_relative(manifest_path)} output_artifacts[{label}] target is missing: {relative_path}"
                )
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


def _verify_manifest_source_provenance() -> list[str]:
    issues: list[str] = []
    for manifest_path in [FUTURES_SHOWCASE_DIR / "manifest.json", RECORDED_CLIP_DIR / "manifest.json"]:
        manifest = json.loads(_read_text(manifest_path))
        source = manifest.get("source")
        if not isinstance(source, dict):
            issues.append(f"{_repo_relative(manifest_path)} is missing source provenance")
            continue
        git_commit = source.get("git_commit")
        git_branch = source.get("git_branch")
        if not isinstance(git_commit, str) or not git_commit:
            issues.append(f"{_repo_relative(manifest_path)} source.git_commit is missing")
        if not isinstance(git_branch, str) or not git_branch:
            issues.append(f"{_repo_relative(manifest_path)} source.git_branch is missing")
        if source.get("git_dirty") is not False:
            issues.append(f"{_repo_relative(manifest_path)} should be refreshed from a clean source tree")
    return issues


def _verify_futures_feed_adapter_metadata() -> list[str]:
    issues: list[str] = []
    for directory in [FUTURES_SHOWCASE_DIR, RECORDED_CLIP_DIR]:
        manifest_path = directory / "manifest.json"
        summary_path = directory / "summary.json"
        manifest = json.loads(_read_text(manifest_path))
        summary = json.loads(_read_text(summary_path))
        if manifest.get("feed_adapter") != EXPECTED_FUTURES_FEED_ADAPTER:
            issues.append(f"{_repo_relative(manifest_path)} has missing or stale feed_adapter metadata")
        if summary.get("feed_adapter") != EXPECTED_FUTURES_FEED_ADAPTER:
            issues.append(f"{_repo_relative(summary_path)} has missing or stale feed_adapter metadata")
        if manifest.get("feed_adapter") != summary.get("feed_adapter"):
            issues.append(f"{_repo_relative(manifest_path)} feed_adapter does not match summary.json")
    return issues


def _format_positive_decimal(value: object) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"expected a positive decimal, got {value!r}") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError(f"expected a positive decimal, got {value!r}")
    return str(decimal_value)


def _instrument_specs_from_replay_input(input_path: Path) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {}
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") != "exchangeInfo":
                continue
            data = record.get("data")
            if not isinstance(data, dict):
                raise ValueError(f"{_repo_relative(input_path)}:{line_number} exchangeInfo data must be an object")
            symbol = str(record.get("symbol", "")).strip()
            if not symbol:
                raise ValueError(f"{_repo_relative(input_path)}:{line_number} exchangeInfo symbol is missing")
            specs[symbol] = {
                "symbol": symbol,
                "venue": str(data.get("venue") or EXPECTED_FUTURES_FEED_ADAPTER["venue_label"]),
                "price_currency": str(data.get("quoteAsset", "")),
                "quantity_unit": str(data.get("baseAsset", "")),
                "tick_size": _format_positive_decimal(data.get("tickSize")),
                "step_size": _format_positive_decimal(data.get("stepSize")),
                "contract_multiplier": _format_positive_decimal(data.get("contractMultiplier", "1")),
            }
    return specs


def _validate_instrument_specs_shape(path: Path, specs: object) -> list[str]:
    issues: list[str] = []
    if not isinstance(specs, dict) or not specs:
        return [f"{_repo_relative(path)} is missing instrument_specs"]
    for symbol, spec in specs.items():
        if not isinstance(symbol, str) or not symbol:
            issues.append(f"{_repo_relative(path)} instrument_specs has an invalid symbol key")
            continue
        if not isinstance(spec, dict):
            issues.append(f"{_repo_relative(path)} instrument_specs[{symbol}] must be an object")
            continue
        if set(spec) != EXPECTED_INSTRUMENT_SPEC_FIELDS:
            issues.append(f"{_repo_relative(path)} instrument_specs[{symbol}] has unexpected fields")
            continue
        if spec.get("symbol") != symbol:
            issues.append(f"{_repo_relative(path)} instrument_specs[{symbol}].symbol does not match its key")
        for field in ["tick_size", "step_size", "contract_multiplier"]:
            try:
                _format_positive_decimal(spec.get(field))
            except ValueError:
                issues.append(f"{_repo_relative(path)} instrument_specs[{symbol}].{field} is invalid")
    return issues


def _verify_futures_instrument_specs_metadata() -> list[str]:
    issues: list[str] = []
    for directory, input_name in [
        (FUTURES_SHOWCASE_DIR, "input_fixture.ndjson"),
        (RECORDED_CLIP_DIR, "input_clip.ndjson"),
    ]:
        manifest_path = directory / "manifest.json"
        summary_path = directory / "summary.json"
        summary_csv_path = directory / "summary.csv"
        input_path = directory / input_name
        manifest = json.loads(_read_text(manifest_path))
        summary = json.loads(_read_text(summary_path))
        manifest_specs = manifest.get("instrument_specs")
        summary_specs = summary.get("instrument_specs")

        issues.extend(_validate_instrument_specs_shape(manifest_path, manifest_specs))
        issues.extend(_validate_instrument_specs_shape(summary_path, summary_specs))
        if manifest_specs != summary_specs:
            issues.append(f"{_repo_relative(manifest_path)} instrument_specs does not match summary.json")

        try:
            expected_specs = _instrument_specs_from_replay_input(input_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            issues.append(f"{_repo_relative(input_path)} could not be inspected for instrument metadata: {exc}")
            continue
        if manifest_specs != expected_specs:
            issues.append(f"{_repo_relative(manifest_path)} instrument_specs does not match replay input metadata")
        if summary_specs != expected_specs:
            issues.append(f"{_repo_relative(summary_path)} instrument_specs does not match replay input metadata")

        with summary_csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or "instrument_specs" not in rows[0]:
            issues.append(f"{_repo_relative(summary_csv_path)} is missing instrument_specs")
            continue
        try:
            csv_specs = json.loads(rows[0]["instrument_specs"])
        except json.JSONDecodeError as exc:
            issues.append(f"{_repo_relative(summary_csv_path)} instrument_specs is not valid JSON: {exc}")
        else:
            if csv_specs != summary_specs:
                issues.append(f"{_repo_relative(summary_csv_path)} instrument_specs does not match summary.json")
    return issues


def _validate_simulation_assumptions_shape(path: Path, assumptions: object) -> list[str]:
    issues: list[str] = []
    if not isinstance(assumptions, dict):
        return [f"{_repo_relative(path)} is missing simulation_assumptions"]
    if set(assumptions) != EXPECTED_SIMULATION_ASSUMPTION_FIELDS:
        issues.append(f"{_repo_relative(path)} simulation_assumptions has unexpected fields")
        return issues
    if assumptions.get("schema_version") != EXPECTED_SIMULATION_ASSUMPTIONS_SCHEMA_VERSION:
        issues.append(f"{_repo_relative(path)} simulation_assumptions has unexpected schema_version")
    if assumptions.get("data_scope") != "public_l2_order_book_and_agg_trade_records":
        issues.append(f"{_repo_relative(path)} simulation_assumptions has unexpected data_scope")
    if assumptions.get("private_exchange_execution_reports") is not False:
        issues.append(
            f"{_repo_relative(path)} simulation_assumptions must not claim private exchange execution reports"
        )
    if assumptions.get("queue_priority_model") != "visible_price_time_fifo":
        issues.append(f"{_repo_relative(path)} simulation_assumptions has unexpected queue priority model")

    overlap = assumptions.get("overlap_netting")
    if not isinstance(overlap, dict):
        issues.append(f"{_repo_relative(path)} simulation_assumptions.overlap_netting must be an object")
    else:
        if overlap.get("enabled") is not True:
            issues.append(f"{_repo_relative(path)} simulation_assumptions overlap netting must be enabled")
        if overlap.get("window_seconds") != EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS:
            issues.append(f"{_repo_relative(path)} simulation_assumptions has unexpected overlap window")

    limitations = assumptions.get("limitations")
    if not isinstance(limitations, list):
        issues.append(f"{_repo_relative(path)} simulation_assumptions.limitations must be a list")
    elif not EXPECTED_SIMULATION_LIMITATIONS <= set(limitations):
        issues.append(f"{_repo_relative(path)} simulation_assumptions is missing required limitation token(s)")
    return issues


def _verify_futures_simulation_assumptions_metadata() -> list[str]:
    issues: list[str] = []
    for directory in [FUTURES_SHOWCASE_DIR, RECORDED_CLIP_DIR]:
        manifest_path = directory / "manifest.json"
        summary_path = directory / "summary.json"
        summary_csv_path = directory / "summary.csv"
        manifest = json.loads(_read_text(manifest_path))
        summary = json.loads(_read_text(summary_path))
        manifest_assumptions = manifest.get("simulation_assumptions")
        summary_assumptions = summary.get("simulation_assumptions")

        issues.extend(_validate_simulation_assumptions_shape(manifest_path, manifest_assumptions))
        issues.extend(_validate_simulation_assumptions_shape(summary_path, summary_assumptions))
        if manifest_assumptions != summary_assumptions:
            issues.append(f"{_repo_relative(manifest_path)} simulation_assumptions does not match summary.json")

        with summary_csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or "simulation_assumptions" not in rows[0]:
            issues.append(f"{_repo_relative(summary_csv_path)} is missing simulation_assumptions")
            continue
        try:
            csv_assumptions = json.loads(rows[0]["simulation_assumptions"])
        except json.JSONDecodeError as exc:
            issues.append(f"{_repo_relative(summary_csv_path)} simulation_assumptions is not valid JSON: {exc}")
        else:
            if csv_assumptions != summary_assumptions:
                issues.append(f"{_repo_relative(summary_csv_path)} simulation_assumptions does not match summary.json")
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
                        issues.append(f"{_repo_relative(path)}:{row_index} has empty trade audit field: {field}")
    return issues


def _verify_futures_fill_source_counts() -> list[str]:
    issues: list[str] = []
    for path in [FUTURES_SHOWCASE_SUMMARY, RECORDED_CLIP_SUMMARY]:
        summary = json.loads(_read_text(path))
        counts = summary.get("fill_source_counts")
        if not isinstance(counts, dict):
            issues.append(f"{_repo_relative(path)} is missing fill_source_counts")
            continue
        if set(counts) != FUTURES_FILL_SOURCES:
            issues.append(f"{_repo_relative(path)} has unexpected fill_source_counts keys: {sorted(counts)}")
            continue
        if any(not isinstance(counts[source], int) or counts[source] < 0 for source in FUTURES_FILL_SOURCES):
            issues.append(f"{_repo_relative(path)} has invalid fill_source_counts values")
            continue
        if sum(counts.values()) != summary.get("fill_count"):
            issues.append(f"{_repo_relative(path)} fill_source_counts do not sum to fill_count")
    return issues


def _verify_futures_markout_by_source() -> list[str]:
    issues: list[str] = []
    for summary_path, summary_csv_path in [
        (FUTURES_SHOWCASE_SUMMARY, FUTURES_SHOWCASE_DIR / "summary.csv"),
        (RECORDED_CLIP_SUMMARY, RECORDED_CLIP_DIR / "summary.csv"),
    ]:
        summary = json.loads(_read_text(summary_path))
        diagnostics = summary.get("markout_by_fill_source")
        if not isinstance(diagnostics, dict) or set(diagnostics) != FUTURES_FILL_SOURCES:
            issues.append(f"{_repo_relative(summary_path)} has invalid markout_by_fill_source")
            continue

        expected = {
            source: {
                "samples": 0,
                "adverse_samples": 0,
                "qty": Decimal("0"),
                "markout_sum": Decimal("0"),
            }
            for source in FUTURES_FILL_SOURCES
        }
        markout_events = summary.get("markout_events", [])
        if isinstance(markout_events, list):
            for event in markout_events:
                if not isinstance(event, dict):
                    issues.append(f"{_repo_relative(summary_path)} markout_events contains a non-object entry")
                    continue
                source = event.get("fill_source")
                if source not in FUTURES_FILL_SOURCES:
                    issues.append(f"{_repo_relative(summary_path)} markout event has invalid fill_source: {source!r}")
                    continue
                try:
                    qty = Decimal(str(event.get("qty")))
                    markout = Decimal(str(event.get("markout")))
                except (InvalidOperation, TypeError):
                    issues.append(f"{_repo_relative(summary_path)} markout event has invalid qty/markout")
                    continue
                expected[source]["samples"] += 1
                expected[source]["qty"] += qty
                expected[source]["markout_sum"] += markout * qty
                if event.get("adverse") is True:
                    expected[source]["adverse_samples"] += 1
        else:
            issues.append(f"{_repo_relative(summary_path)} has invalid markout_events")

        for source, stats in diagnostics.items():
            if not isinstance(stats, dict) or set(stats) != EXPECTED_MARKOUT_BY_SOURCE_FIELDS:
                issues.append(f"{_repo_relative(summary_path)} markout_by_fill_source[{source}] has unexpected fields")
                continue
            samples = stats.get("samples")
            adverse_samples = stats.get("adverse_samples")
            if not isinstance(samples, int) or samples < 0:
                issues.append(f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].samples is invalid")
                continue
            if not isinstance(adverse_samples, int) or adverse_samples < 0 or adverse_samples > samples:
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].adverse_samples is invalid"
                )
                continue
            for field in ["qty", "avg_markout_1s", "adverse_fill_rate_1s"]:
                value = stats.get(field)
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    issues.append(f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].{field} is invalid")
            if (
                not isinstance(stats.get("adverse_fill_rate_1s"), (int, float))
                or not 0 <= float(stats["adverse_fill_rate_1s"]) <= 1
            ):
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].adverse_fill_rate_1s is out of range"
                )

            expected_stats = expected[source]
            expected_samples = int(expected_stats["samples"])
            expected_adverse = int(expected_stats["adverse_samples"])
            expected_qty = float(expected_stats["qty"])
            expected_avg = (
                float(expected_stats["markout_sum"] / expected_stats["qty"]) if expected_stats["qty"] > 0 else 0.0
            )
            expected_rate = float(Decimal(expected_adverse) / Decimal(expected_samples)) if expected_samples else 0.0
            if samples != expected_samples:
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].samples "
                    f"does not match markout_events"
                )
            if adverse_samples != expected_adverse:
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].adverse_samples "
                    f"does not match markout_events"
                )
            if not math.isclose(float(stats.get("qty", 0)), expected_qty, rel_tol=1e-12, abs_tol=1e-12):
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].qty does not match markout_events"
                )
            if not math.isclose(float(stats.get("avg_markout_1s", 0)), expected_avg, rel_tol=1e-12, abs_tol=1e-12):
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].avg_markout_1s "
                    f"does not match markout_events"
                )
            if not math.isclose(
                float(stats.get("adverse_fill_rate_1s", 0)), expected_rate, rel_tol=1e-12, abs_tol=1e-12
            ):
                issues.append(
                    f"{_repo_relative(summary_path)} markout_by_fill_source[{source}].adverse_fill_rate_1s "
                    f"does not match markout_events"
                )

        with summary_csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or "markout_by_fill_source" not in rows[0]:
            issues.append(f"{_repo_relative(summary_csv_path)} is missing markout_by_fill_source")
            continue
        try:
            csv_diagnostics = json.loads(rows[0]["markout_by_fill_source"])
        except json.JSONDecodeError as exc:
            issues.append(f"{_repo_relative(summary_csv_path)} markout_by_fill_source is not valid JSON: {exc}")
        else:
            if csv_diagnostics != diagnostics:
                issues.append(f"{_repo_relative(summary_csv_path)} markout_by_fill_source does not match summary.json")
    return issues


def _verify_public_consumption_diagnostics() -> list[str]:
    issues: list[str] = []
    for summary_path, summary_csv_path in [
        (FUTURES_SHOWCASE_SUMMARY, FUTURES_SHOWCASE_DIR / "summary.csv"),
        (RECORDED_CLIP_SUMMARY, RECORDED_CLIP_DIR / "summary.csv"),
    ]:
        summary = json.loads(_read_text(summary_path))
        diagnostics = summary.get("public_consumption_summary")
        if not isinstance(diagnostics, dict):
            issues.append(f"{_repo_relative(summary_path)} is missing public_consumption_summary")
            continue
        if diagnostics.get("overlap_window_seconds") != EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS:
            issues.append(f"{_repo_relative(summary_path)} has unexpected public-consumption overlap window")

        sources = diagnostics.get("sources")
        if not isinstance(sources, dict) or set(sources) != EXPECTED_PUBLIC_CONSUMPTION_SOURCES:
            issues.append(f"{_repo_relative(summary_path)} has unexpected public-consumption sources")
            continue

        source_totals = {
            "observed_lots": 0,
            "modeled_lots": 0,
            "overlap_netted_lots": 0,
            "queue_consumed_lots": 0,
            "unmatched_lots": 0,
        }
        for source, stats in sources.items():
            if not isinstance(stats, dict) or set(stats) != EXPECTED_PUBLIC_CONSUMPTION_FIELDS:
                issues.append(
                    f"{_repo_relative(summary_path)} public_consumption_summary[{source}] has unexpected fields"
                )
                continue
            for field in EXPECTED_PUBLIC_CONSUMPTION_FIELDS:
                value = stats.get(field)
                if not isinstance(value, int) or value < 0:
                    issues.append(
                        f"{_repo_relative(summary_path)} public_consumption_summary[{source}].{field} is invalid"
                    )
                    continue
                source_totals[field] += value
            if stats.get("observed_lots", 0) < stats.get("modeled_lots", 0):
                issues.append(
                    f"{_repo_relative(summary_path)} public_consumption_summary[{source}] models more lots than observed"
                )
            if stats.get("overlap_netted_lots") != stats.get("observed_lots", 0) - stats.get("modeled_lots", 0):
                issues.append(
                    f"{_repo_relative(summary_path)} public_consumption_summary[{source}] has inconsistent netted lots"
                )
            if stats.get("queue_consumed_lots", 0) > stats.get("modeled_lots", 0):
                issues.append(
                    f"{_repo_relative(summary_path)} public_consumption_summary[{source}] consumes more queue than modeled"
                )
            if stats.get("unmatched_lots") != stats.get("modeled_lots", 0) - stats.get("queue_consumed_lots", 0):
                issues.append(
                    f"{_repo_relative(summary_path)} public_consumption_summary[{source}] has inconsistent unmatched lots"
                )

        expected_totals = {
            "total_observed_lots": source_totals["observed_lots"],
            "total_modeled_lots": source_totals["modeled_lots"],
            "total_overlap_netted_lots": source_totals["overlap_netted_lots"],
            "total_queue_consumed_lots": source_totals["queue_consumed_lots"],
            "total_unmatched_lots": source_totals["unmatched_lots"],
        }
        for field, expected_value in expected_totals.items():
            if diagnostics.get(field) != expected_value:
                issues.append(f"{_repo_relative(summary_path)} public_consumption_summary.{field} is inconsistent")

        with summary_csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or "public_consumption_summary" not in rows[0]:
            issues.append(f"{_repo_relative(summary_csv_path)} is missing public_consumption_summary")
            continue
        try:
            csv_diagnostics = json.loads(rows[0]["public_consumption_summary"])
        except json.JSONDecodeError as exc:
            issues.append(f"{_repo_relative(summary_csv_path)} public_consumption_summary is not valid JSON: {exc}")
        else:
            if csv_diagnostics != diagnostics:
                issues.append(
                    f"{_repo_relative(summary_csv_path)} public_consumption_summary does not match summary.json"
                )
    return issues


def _verify_futures_self_trade_prevention_counts() -> list[str]:
    issues: list[str] = []
    for path in [FUTURES_SHOWCASE_SUMMARY, RECORDED_CLIP_SUMMARY]:
        summary = json.loads(_read_text(path))
        count = summary.get("self_trade_prevention_count")
        if not isinstance(count, int) or count < 0:
            issues.append(f"{_repo_relative(path)} has invalid self_trade_prevention_count")
    return issues


def _verify_decision_trace_details(path: Path, row_index: int, details: dict[str, object] | None) -> list[str]:
    issues: list[str] = []
    if details is None:
        issues.append(f"{_repo_relative(path)}:{row_index} decision row is missing details")
        return issues
    diagnostics = details.get("diagnostics")
    if not isinstance(diagnostics, dict):
        issues.append(f"{_repo_relative(path)}:{row_index} decision row is missing strategy diagnostics")
        return issues

    profile = diagnostics.get("profile")
    if profile not in EXPECTED_STRATEGY_PROFILE_NAMES:
        issues.append(f"{_repo_relative(path)}:{row_index} decision diagnostics has invalid profile: {profile!r}")
        return issues
    if details.get("strategy_profile") != profile:
        issues.append(
            f"{_repo_relative(path)}:{row_index} decision diagnostics profile does not match strategy_profile"
        )

    required_keys = REQUIRED_DECISION_DIAGNOSTIC_KEYS | PROFILE_DECISION_DIAGNOSTIC_KEYS[str(profile)]
    missing_keys = sorted(required_keys - set(diagnostics))
    if missing_keys:
        issues.append(
            f"{_repo_relative(path)}:{row_index} decision diagnostics missing key(s): {', '.join(missing_keys)}"
        )

    for key in ["best_bid_tick", "best_ask_tick", "size_lots"]:
        value = diagnostics.get(key)
        if not isinstance(value, int) or value <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} decision diagnostics has invalid {key}")
    return issues


def _verify_risk_halt_trace_details(path: Path, row_index: int, details: dict[str, object] | None) -> list[str]:
    issues: list[str] = []
    if details is None:
        return [f"{_repo_relative(path)}:{row_index} risk_halt row is missing details"]
    reason = details.get("reason")
    if not isinstance(reason, str) or not reason:
        issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row is missing reason")
    if details.get("phase") not in {"market_record", "shutdown"}:
        issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has invalid phase")
    canceled_count = details.get("canceled_order_count")
    if not isinstance(canceled_count, int) or canceled_count < 0:
        issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has invalid canceled_order_count")
    canceled_by_symbol = details.get("canceled_orders_by_symbol")
    if not isinstance(canceled_by_symbol, dict):
        issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has invalid canceled_orders_by_symbol")
    for field in [
        "cleared_pending_cancel_ack_count",
        "cleared_pending_replacement_slot_count",
        "max_consecutive_loss_count",
    ]:
        value = details.get(field)
        if not isinstance(value, int) or value < 0:
            issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has invalid {field}")
    for field in ["realized_pnl", "unrealized_pnl", "max_drawdown"]:
        try:
            value = float(str(details.get(field)))
        except (TypeError, ValueError):
            issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has invalid {field}")
            continue
        if not math.isfinite(value):
            issues.append(f"{_repo_relative(path)}:{row_index} risk_halt row has non-finite {field}")
    return issues


def _verify_queue_consumption_trace_details(
    path: Path,
    row_index: int,
    row: dict[str, str],
    details: dict[str, object] | None,
) -> list[str]:
    issues: list[str] = []
    if details is None:
        return [f"{_repo_relative(path)}:{row_index} queue_consumption row is missing details"]

    if row.get("source") not in EXPECTED_PUBLIC_CONSUMPTION_SOURCES:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid source")
    if row.get("side") not in {"bid", "ask"}:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid side")

    try:
        price_tick = int(row.get("price_tick", ""))
    except (TypeError, ValueError):
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid price_tick")
    else:
        if price_tick <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid price_tick")

    try:
        qty_lots = int(row.get("qty_lots", ""))
    except (TypeError, ValueError):
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid qty_lots")
        qty_lots = None
    else:
        if qty_lots <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption row has invalid qty_lots")

    if set(details) != EXPECTED_QUEUE_CONSUMPTION_TRACE_FIELDS:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption details have unexpected fields")
        return issues

    parsed: dict[str, int] = {}
    for field in EXPECTED_PUBLIC_CONSUMPTION_FIELDS:
        value = details.get(field)
        if not isinstance(value, int) or value < 0:
            issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption has invalid {field}")
        else:
            parsed[field] = value

    if details.get("overlap_window_seconds") != EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption has unexpected overlap window")

    if len(parsed) != len(EXPECTED_PUBLIC_CONSUMPTION_FIELDS):
        return issues
    observed = parsed["observed_lots"]
    modeled = parsed["modeled_lots"]
    queue_consumed = parsed["queue_consumed_lots"]
    if qty_lots is not None and qty_lots != observed:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption qty_lots does not match observed_lots")
    if observed < modeled:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption models more lots than observed")
    if parsed["overlap_netted_lots"] != observed - modeled:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption has inconsistent netted lots")
    if queue_consumed > modeled:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption consumes more queue than modeled")
    if parsed["unmatched_lots"] != modeled - queue_consumed:
        issues.append(f"{_repo_relative(path)}:{row_index} queue_consumption has inconsistent unmatched lots")
    return issues


def _verify_fill_trace_details(
    path: Path,
    row_index: int,
    row: dict[str, str],
    details: dict[str, object] | None,
) -> list[str]:
    issues: list[str] = []
    if details is None:
        return [f"{_repo_relative(path)}:{row_index} fill row is missing details"]

    if set(details) != EXPECTED_FILL_TRACE_FIELDS:
        issues.append(f"{_repo_relative(path)}:{row_index} fill details have unexpected fields")
        return issues

    if row.get("fill_source") not in FUTURES_FILL_SOURCES:
        issues.append(f"{_repo_relative(path)}:{row_index} has invalid fill_source: {row.get('fill_source')!r}")
    if row.get("side") not in {"bid", "ask"}:
        issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid side")
    for field in ["price_tick", "qty_lots"]:
        try:
            value = int(row.get(field, ""))
        except (TypeError, ValueError):
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
            continue
        if value <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
    if not row.get("order_id"):
        issues.append(f"{_repo_relative(path)}:{row_index} fill row is missing order_id")

    if not isinstance(details.get("maker"), bool):
        issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid maker")
    queue_ahead_lots = details.get("queue_ahead_lots")
    if not isinstance(queue_ahead_lots, int) or queue_ahead_lots < 0:
        issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid queue_ahead_lots")

    for field in ["created_ts", "time_in_book_ms", "markout_horizon"]:
        value = details.get(field)
        if value is None and field == "created_ts":
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")

    for field in [
        "price",
        "qty",
        "notional",
        "contract_multiplier",
        "fee_bps",
        "fee",
        "spread_capture",
        "spread_capture_value",
    ]:
        value = details.get(field)
        if value is None and field in {"spread_capture", "spread_capture_value"}:
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError):
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
            continue
        if not parsed.is_finite():
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
        if field in {"price", "qty", "contract_multiplier"} and parsed <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
        if field == "notional" and parsed < 0:
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")

    mid_at_fill = details.get("mid_at_fill")
    if mid_at_fill is not None:
        try:
            value = Decimal(str(mid_at_fill))
        except (InvalidOperation, TypeError):
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid mid_at_fill")
        else:
            if not value.is_finite() or value <= 0:
                issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid mid_at_fill")
    fee_currency = details.get("fee_currency")
    if not isinstance(fee_currency, str):
        issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid fee_currency")
    if not isinstance(details.get("regime"), str) or not details.get("regime"):
        issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid regime")
    for field in ["book_bid_tick", "book_ask_tick"]:
        value = details.get(field)
        if value is not None and (not isinstance(value, int) or value <= 0):
            issues.append(f"{_repo_relative(path)}:{row_index} fill row has invalid {field}")
    return issues


def _verify_markout_trace_details(
    path: Path,
    row_index: int,
    row: dict[str, str],
    details: dict[str, object] | None,
) -> list[str]:
    issues: list[str] = []
    if details is None:
        return [f"{_repo_relative(path)}:{row_index} markout row is missing details"]

    if row.get("source") != "metrics":
        issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid source")
    if row.get("fill_source") not in FUTURES_FILL_SOURCES:
        issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid fill_source")
    if row.get("side") not in {"bid", "ask"}:
        issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid side")
    if not row.get("order_id"):
        issues.append(f"{_repo_relative(path)}:{row_index} markout row is missing order_id")

    for field in ["price_tick", "qty_lots"]:
        try:
            value = int(row.get(field, ""))
        except (TypeError, ValueError):
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")
            continue
        if value <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")

    if set(details) != EXPECTED_MARKOUT_TRACE_FIELDS:
        issues.append(f"{_repo_relative(path)}:{row_index} markout details have unexpected fields")
        return issues

    for field in ["fill_ts_local", "deadline_ts", "horizon"]:
        try:
            value = float(str(details.get(field)))
        except (TypeError, ValueError):
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")
            continue
        if not math.isfinite(value) or value < 0:
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")

    for field in ["fill_price", "qty", "mid_after", "markout", "contract_multiplier"]:
        try:
            value = Decimal(str(details.get(field)))
        except (InvalidOperation, TypeError):
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")
            continue
        if not value.is_finite():
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")
        if field in {"fill_price", "qty", "contract_multiplier"} and value <= 0:
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid {field}")

    fill_mid = details.get("fill_mid")
    if fill_mid is not None:
        try:
            value = Decimal(str(fill_mid))
        except (InvalidOperation, TypeError):
            issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid fill_mid")
        else:
            if not value.is_finite():
                issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid fill_mid")

    if not isinstance(details.get("adverse"), bool):
        issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid adverse")
    if not isinstance(details.get("regime"), str) or not details.get("regime"):
        issues.append(f"{_repo_relative(path)}:{row_index} markout row has invalid regime")
    return issues


def _verify_futures_event_trace_contract() -> list[str]:
    issues: list[str] = []
    for directory in [FUTURES_SHOWCASE_DIR, RECORDED_CLIP_DIR]:
        summary_path = directory / "summary.json"
        trace_path = directory / "event_trace.csv"
        summary = json.loads(_read_text(summary_path))
        expected_event_count = summary.get("event_trace_count")
        expected_fill_count = summary.get("fill_count")
        lifecycle_counts = summary.get("order_lifecycle_counts")
        lifecycle_counts_valid = isinstance(lifecycle_counts, dict)
        if not isinstance(expected_event_count, int) or expected_event_count < 0:
            issues.append(f"{_repo_relative(summary_path)} has invalid event_trace_count")
            continue
        if not isinstance(expected_fill_count, int) or expected_fill_count < 0:
            issues.append(f"{_repo_relative(summary_path)} has invalid fill_count")
            continue
        if not lifecycle_counts_valid:
            issues.append(f"{_repo_relative(summary_path)} is missing order_lifecycle_counts")
        elif set(lifecycle_counts) != FUTURES_ORDER_LIFECYCLE_KEYS:
            issues.append(
                f"{_repo_relative(summary_path)} has unexpected order_lifecycle_counts keys: {sorted(lifecycle_counts)}"
            )
            lifecycle_counts_valid = False
        elif any(
            not isinstance(lifecycle_counts[key], int) or lifecycle_counts[key] < 0
            for key in FUTURES_ORDER_LIFECYCLE_KEYS
        ):
            issues.append(f"{_repo_relative(summary_path)} has invalid order_lifecycle_counts values")
            lifecycle_counts_valid = False
        elif lifecycle_counts["arrived"] != summary.get("quote_count"):
            issues.append(f"{_repo_relative(summary_path)} order_lifecycle_counts.arrived does not match quote_count")
        elif lifecycle_counts["cancel_requested"] != summary.get("cancel_count"):
            issues.append(
                f"{_repo_relative(summary_path)} order_lifecycle_counts.cancel_requested does not match cancel_count"
            )
        elif lifecycle_counts["self_trade_prevented"] != summary.get("self_trade_prevention_count"):
            issues.append(
                f"{_repo_relative(summary_path)} order_lifecycle_counts.self_trade_prevented does not match self_trade_prevention_count"
            )

        with trace_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            missing = [field for field in FUTURES_EVENT_TRACE_FIELDS if field not in fieldnames]
            if missing:
                issues.append(f"{_repo_relative(trace_path)} is missing event trace column(s): {', '.join(missing)}")
                continue
            rows = list(reader)

        if len(rows) != expected_event_count:
            issues.append(
                f"{_repo_relative(trace_path)} has {len(rows)} row(s), expected {expected_event_count} from summary"
            )

        previous_key: tuple[float, int] | None = None
        fill_rows = []
        lifecycle_from_trace = {key: 0 for key in FUTURES_ORDER_LIFECYCLE_KEYS}
        public_consumption_from_trace = {
            source: {field: 0 for field in EXPECTED_PUBLIC_CONSUMPTION_FIELDS}
            for source in EXPECTED_PUBLIC_CONSUMPTION_SOURCES
        }
        markout_from_trace = {
            source: {
                "samples": 0,
                "adverse_samples": 0,
                "qty": Decimal("0"),
                "markout_sum": Decimal("0"),
            }
            for source in FUTURES_FILL_SOURCES
        }
        markout_row_count = 0
        arrival_queue_samples = 0
        arrival_with_queue_ahead_count = 0
        arrival_queue_ahead_sum = 0
        max_arrival_queue_ahead_lots = 0
        for row_index, row in enumerate(rows, start=2):
            seq_value = row.get("seq", "")
            try:
                seq = int(seq_value)
            except (TypeError, ValueError):
                issues.append(f"{_repo_relative(trace_path)}:{row_index} has invalid seq: {seq_value!r}")
                continue
            expected_seq = row_index - 2
            if seq != expected_seq:
                issues.append(f"{_repo_relative(trace_path)}:{row_index} has seq {seq}, expected {expected_seq}")

            ts_value = row.get("ts_local", "")
            try:
                ts_local = float(ts_value)
            except (TypeError, ValueError):
                issues.append(f"{_repo_relative(trace_path)}:{row_index} has invalid ts_local: {ts_value!r}")
                continue
            if not math.isfinite(ts_local):
                issues.append(f"{_repo_relative(trace_path)}:{row_index} has non-finite ts_local: {ts_value!r}")
                continue

            key = (ts_local, seq)
            if previous_key is not None and key < previous_key:
                issues.append(f"{_repo_relative(trace_path)}:{row_index} is out of event-time order")
            previous_key = key

            details_raw = (row.get("details") or "").strip()
            details: dict[str, object] | None = None
            if details_raw:
                try:
                    decoded_details = json.loads(details_raw)
                except json.JSONDecodeError as exc:
                    issues.append(f"{_repo_relative(trace_path)}:{row_index} has invalid details JSON: {exc.msg}")
                else:
                    if not isinstance(decoded_details, dict):
                        issues.append(f"{_repo_relative(trace_path)}:{row_index} details must be a JSON object")
                    else:
                        details = decoded_details

            event_type = row.get("event_type")
            if event_type == "order_arrival_scheduled":
                lifecycle_from_trace["arrival_scheduled"] += 1
            elif event_type == "order_arrival":
                lifecycle_from_trace["arrived"] += 1
                if details is not None:
                    if details.get("resting_after_arrival") is True:
                        lifecycle_from_trace["rested_after_arrival"] += 1
                        queue_ahead = details.get("queue_ahead_lots_after_arrival")
                        if not isinstance(queue_ahead, int) or queue_ahead < 0:
                            issues.append(
                                f"{_repo_relative(trace_path)}:{row_index} order_arrival has invalid queue_ahead_lots_after_arrival"
                            )
                        else:
                            arrival_queue_samples += 1
                            arrival_queue_ahead_sum += queue_ahead
                            if queue_ahead > 0:
                                arrival_with_queue_ahead_count += 1
                            max_arrival_queue_ahead_lots = max(max_arrival_queue_ahead_lots, queue_ahead)
                    immediate_fills = details.get("immediate_fills")
                    if isinstance(immediate_fills, int) and immediate_fills > 0:
                        lifecycle_from_trace["immediate_fill_arrivals"] += 1
                    remaining_lots = details.get("remaining_lots_after_arrival")
                    if (
                        details.get("resting_after_arrival") is False
                        and isinstance(remaining_lots, int)
                        and remaining_lots > 0
                    ):
                        lifecycle_from_trace["expired_unfilled_arrivals"] += 1
                    if details.get("self_trade_prevented") is True:
                        lifecycle_from_trace["self_trade_prevented"] += 1
            elif event_type == "cancel_requested":
                lifecycle_from_trace["cancel_requested"] += 1
            elif event_type == "cancel_ack":
                lifecycle_from_trace["cancel_acknowledged"] += 1
            elif event_type == "decision":
                issues.extend(_verify_decision_trace_details(trace_path, row_index, details))
            elif event_type == "risk_halt":
                issues.extend(_verify_risk_halt_trace_details(trace_path, row_index, details))
            elif event_type == "queue_consumption":
                issue_count_before = len(issues)
                issues.extend(_verify_queue_consumption_trace_details(trace_path, row_index, row, details))
                if len(issues) == issue_count_before and details is not None:
                    source = row["source"]
                    for field in EXPECTED_PUBLIC_CONSUMPTION_FIELDS:
                        public_consumption_from_trace[source][field] += int(details[field])
            elif event_type == "markout":
                issue_count_before = len(issues)
                issues.extend(_verify_markout_trace_details(trace_path, row_index, row, details))
                if len(issues) == issue_count_before and details is not None:
                    fill_source = row["fill_source"]
                    qty = Decimal(str(details["qty"]))
                    markout = Decimal(str(details["markout"]))
                    markout_from_trace[fill_source]["samples"] += 1
                    markout_from_trace[fill_source]["qty"] += qty
                    markout_from_trace[fill_source]["markout_sum"] += markout * qty
                    if details.get("adverse") is True:
                        markout_from_trace[fill_source]["adverse_samples"] += 1
                    markout_row_count += 1

            if event_type == "fill":
                issues.extend(_verify_fill_trace_details(trace_path, row_index, row, details))
                fill_rows.append((row_index, row))

        if len(fill_rows) != expected_fill_count:
            issues.append(
                f"{_repo_relative(trace_path)} has {len(fill_rows)} fill row(s), expected {expected_fill_count} from summary"
            )
        for row_index, row in fill_rows:
            fill_source = row.get("fill_source")
            if fill_source not in FUTURES_FILL_SOURCES:
                issues.append(f"{_repo_relative(trace_path)}:{row_index} has invalid fill_source: {fill_source!r}")
            for field in ["side", "price_tick", "qty_lots", "order_id"]:
                if not row.get(field):
                    issues.append(f"{_repo_relative(trace_path)}:{row_index} fill row is missing {field}")
        if lifecycle_counts_valid:
            for key in sorted(FUTURES_ORDER_LIFECYCLE_KEYS):
                if lifecycle_counts[key] != lifecycle_from_trace[key]:
                    issues.append(
                        f"{_repo_relative(trace_path)} lifecycle {key}={lifecycle_from_trace[key]} "
                        f"does not match summary value {lifecycle_counts[key]}"
                    )
        expected_arrival_queue_fields = {
            "resting_arrival_queue_samples": arrival_queue_samples,
            "arrival_with_queue_ahead_count": arrival_with_queue_ahead_count,
            "max_arrival_queue_ahead_lots": max_arrival_queue_ahead_lots,
        }
        for field, expected_value in expected_arrival_queue_fields.items():
            if summary.get(field) != expected_value:
                issues.append(
                    f"{_repo_relative(trace_path)} {field}={expected_value} "
                    f"does not match summary value {summary.get(field)!r}"
                )
        expected_avg_queue = arrival_queue_ahead_sum / arrival_queue_samples if arrival_queue_samples else 0.0
        observed_avg_queue = summary.get("avg_arrival_queue_ahead_lots")
        if not isinstance(observed_avg_queue, (int, float)) or not math.isclose(
            float(observed_avg_queue),
            expected_avg_queue,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            issues.append(
                f"{_repo_relative(trace_path)} avg_arrival_queue_ahead_lots={expected_avg_queue} "
                f"does not match summary value {observed_avg_queue!r}"
            )
        public_consumption_summary = summary.get("public_consumption_summary")
        if isinstance(public_consumption_summary, dict):
            public_sources = public_consumption_summary.get("sources")
            if isinstance(public_sources, dict):
                for source in EXPECTED_PUBLIC_CONSUMPTION_SOURCES:
                    summary_stats = public_sources.get(source)
                    if not isinstance(summary_stats, dict):
                        continue
                    for field in EXPECTED_PUBLIC_CONSUMPTION_FIELDS:
                        if summary_stats.get(field) != public_consumption_from_trace[source][field]:
                            issues.append(
                                f"{_repo_relative(trace_path)} queue_consumption {source}.{field}="
                                f"{public_consumption_from_trace[source][field]} does not match summary value "
                                f"{summary_stats.get(field)!r}"
                            )
        markout_events = summary.get("markout_events")
        if isinstance(markout_events, list) and len(markout_events) != markout_row_count:
            issues.append(
                f"{_repo_relative(trace_path)} has {markout_row_count} markout row(s), "
                f"expected {len(markout_events)} from summary markout_events"
            )
        markout_by_source = summary.get("markout_by_fill_source")
        if isinstance(markout_by_source, dict):
            for source in FUTURES_FILL_SOURCES:
                summary_stats = markout_by_source.get(source)
                if not isinstance(summary_stats, dict):
                    continue
                expected_stats = markout_from_trace[source]
                expected_samples = int(expected_stats["samples"])
                expected_adverse = int(expected_stats["adverse_samples"])
                expected_qty = float(expected_stats["qty"])
                expected_avg = (
                    float(expected_stats["markout_sum"] / expected_stats["qty"]) if expected_stats["qty"] > 0 else 0.0
                )
                expected_rate = (
                    float(Decimal(expected_adverse) / Decimal(expected_samples)) if expected_samples else 0.0
                )
                expected_values = {
                    "samples": expected_samples,
                    "adverse_samples": expected_adverse,
                    "qty": expected_qty,
                    "avg_markout_1s": expected_avg,
                    "adverse_fill_rate_1s": expected_rate,
                }
                for field, expected_value in expected_values.items():
                    observed = summary_stats.get(field)
                    if isinstance(expected_value, float):
                        if not isinstance(observed, (int, float)) or not math.isclose(
                            float(observed),
                            expected_value,
                            rel_tol=1e-12,
                            abs_tol=1e-12,
                        ):
                            issues.append(
                                f"{_repo_relative(trace_path)} markout {source}.{field}="
                                f"{expected_value} does not match summary value {observed!r}"
                            )
                    elif observed != expected_value:
                        issues.append(
                            f"{_repo_relative(trace_path)} markout {source}.{field}="
                            f"{expected_value} does not match summary value {observed!r}"
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
        FUTURES_STRESS_DIR / "README.md",
        FUTURES_STRESS_DIR / "case_notes.md",
        FUTURES_STRESS_DIR / "summary.json",
        FUTURES_STRESS_DIR / "summary.csv",
        FUTURES_STRESS_DIR / "manifest.json",
        FUTURES_BENCHMARKS,
        FUTURES_BENCHMARK_REFERENCE,
        FUTURES_STRATEGY_PROFILES,
        FUTURES_STRATEGY_REFERENCE,
        FUTURES_PARAMETER_SWEEP_REFERENCE,
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
        issues.append(f"Missing published benchmark artifact: {_repo_relative(FUTURES_BENCHMARK_REFERENCE)}")
    if not FUTURES_BENCHMARK_REFERENCE_JSON.exists():
        issues.append(f"Missing published benchmark JSON artifact: {_repo_relative(FUTURES_BENCHMARK_REFERENCE_JSON)}")
    else:
        try:
            result = json.loads(_read_text(FUTURES_BENCHMARK_REFERENCE_JSON))
        except json.JSONDecodeError as exc:
            issues.append(f"Benchmark JSON artifact is not valid JSON: {exc}")
        else:
            expected_input = "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson"
            expected_sha = _file_sha256(REPO_ROOT / expected_input)
            if result.get("schema_version") != EXPECTED_BENCHMARK_SCHEMA_VERSION:
                issues.append("Benchmark JSON artifact has an unexpected schema_version")
            metadata = result.get("metadata", {})
            if metadata.get("input_file") != expected_input:
                issues.append("Benchmark JSON artifact does not reference the committed recorded clip input")
            if metadata.get("input_sha256") != expected_sha:
                issues.append("Benchmark JSON artifact input_sha256 does not match the committed recorded clip")
            if metadata.get("feed_adapter") != EXPECTED_FUTURES_FEED_ADAPTER:
                issues.append("Benchmark JSON artifact has missing or stale feed_adapter metadata")
            config = metadata.get("config")
            if not isinstance(config, dict) or not config:
                issues.append("Benchmark JSON artifact is missing non-secret config metadata")
            elif metadata.get("config_digest") != _config_digest(config):
                issues.append("Benchmark JSON artifact config_digest does not match config metadata")
            try:
                expected_specs = _instrument_specs_from_replay_input(REPO_ROOT / expected_input)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                issues.append(f"Benchmark replay input could not be inspected for instrument metadata: {exc}")
            else:
                if metadata.get("instrument_specs") != expected_specs:
                    issues.append("Benchmark JSON artifact instrument_specs does not match replay input metadata")
            if metadata.get("source", {}).get("git_dirty") is not False:
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
            if "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" not in published:
                issues.append(
                    "docs/futures_benchmarks.md published benchmark section is missing feed adapter provenance"
                )

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


def _verify_reviewer_gate_publication() -> list[str]:
    issues: list[str] = []
    if not REVIEWER_GATE.exists():
        issues.append(f"Missing reviewer gate script: {_repo_relative(REVIEWER_GATE)}")
        return issues

    script = _read_text(REVIEWER_GATE)
    required_tokens = [
        "build_reviewer_gate_steps",
        "ruff",
        "format",
        "scripts/verify_committed_artifacts.py",
        "git",
        "diff",
        "--check",
        "scripts/check_futures_determinism.py",
        "scripts/audit_futures_pack.py",
        "--committed-futures",
        "experiments/benchmark_futures_replay.py",
        "--mode",
        "all",
        "docs/sample_outputs/futures_stress_case",
    ]
    for token in required_tokens:
        if token not in script:
            issues.append(f"Reviewer gate script is missing expected token: {token}")

    for path in [REPO_ROOT / "README.md", HFT_REVIEWER_GUIDE]:
        text = _read_text(path)
        if "python scripts/reviewer_gate.py" not in text:
            issues.append(f"Missing portable reviewer gate command in {_repo_relative(path)}")
        if "make reviewer-gate" not in text:
            issues.append(f"Missing Makefile reviewer gate command in {_repo_relative(path)}")
    return issues


def _verify_futures_stress_pack_publication() -> list[str]:
    issues: list[str] = []
    if not FUTURES_STRESS_DIR.exists():
        issues.append(f"Missing futures stress pack: {_repo_relative(FUTURES_STRESS_DIR)}")
        return issues
    summary = json.loads(_read_text(FUTURES_STRESS_SUMMARY))
    provenance = summary.get("fixture_provenance")
    if not isinstance(provenance, dict) or provenance.get("source") != "synthetic_exchange_shaped":
        issues.append("futures_stress_case summary must label the fixture as synthetic_exchange_shaped")
    coverage = summary.get("stress_coverage")
    required_coverage = {
        "queue_ahead",
        "partial_fills",
        "depth_agg_trade_overlap_netting",
        "adverse_and_non_adverse_markouts",
        "cancel_latency",
        "same_timestamp_cancel_before_trade",
        "marketable_taker_fill",
        "self_trade_prevention",
    }
    if not isinstance(coverage, dict):
        issues.append("futures_stress_case summary is missing stress_coverage")
    else:
        missing = sorted(key for key in required_coverage if coverage.get(key) is not True)
        if missing:
            issues.append("futures_stress_case stress_coverage missing true flag(s): " + ", ".join(missing))
        if coverage.get("book_gap_count") != 0:
            issues.append("futures_stress_case should be a no-gap stress fixture")
    fill_sources = summary.get("fill_source_counts")
    if not isinstance(fill_sources, dict) or not all(
        fill_sources.get(source, 0) > 0 for source in FUTURES_FILL_SOURCES
    ):
        issues.append("futures_stress_case must include depth_update, agg_trade, and taker_order fills")
    public = summary.get("public_consumption_summary", {})
    if not isinstance(public, dict) or public.get("total_overlap_netted_lots", 0) <= 0:
        issues.append("futures_stress_case must include overlap-netted public consumption")
    markout_by_source = summary.get("markout_by_fill_source", {})
    if not isinstance(markout_by_source, dict):
        issues.append("futures_stress_case is missing markout_by_fill_source")
    else:
        adverse = sum(int(data.get("adverse_samples", 0)) for data in markout_by_source.values())
        non_adverse = sum(
            int(data.get("samples", 0)) - int(data.get("adverse_samples", 0)) for data in markout_by_source.values()
        )
        if adverse <= 0 or non_adverse <= 0:
            issues.append("futures_stress_case must include both adverse and non-adverse markouts")

    text = _read_text(FUTURES_STRESS_DIR / "README.md")
    for token in ["synthetic-but-exchange-shaped", "self-trade prevention", "Same-timestamp"]:
        if token not in text:
            issues.append(f"futures_stress_case README is missing token: {token}")
    return issues


def _verify_strategy_profile_publication() -> list[str]:
    issues: list[str] = []
    if not FUTURES_STRATEGY_PROFILES.exists():
        issues.append(f"Missing futures strategy profile doc: {_repo_relative(FUTURES_STRATEGY_PROFILES)}")
    if not FUTURES_STRATEGY_REFERENCE.exists():
        issues.append(f"Missing futures strategy reference doc: {_repo_relative(FUTURES_STRATEGY_REFERENCE)}")
    if not FUTURES_STRATEGY_REFRESH.exists():
        issues.append(f"Missing futures strategy refresh script: {_repo_relative(FUTURES_STRATEGY_REFRESH)}")
    if not FUTURES_PARAMETER_SWEEP_REFERENCE.exists():
        issues.append(
            f"Missing futures parameter sweep reference doc: {_repo_relative(FUTURES_PARAMETER_SWEEP_REFERENCE)}"
        )
    if not FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.exists():
        issues.append(
            f"Missing futures parameter sweep reference CSV: {_repo_relative(FUTURES_PARAMETER_SWEEP_REFERENCE_CSV)}"
        )
    if not FUTURES_PARAMETER_SWEEP_REFRESH.exists():
        issues.append(
            f"Missing futures parameter sweep refresh script: {_repo_relative(FUTURES_PARAMETER_SWEEP_REFRESH)}"
        )
    if not FUTURES_LATENCY_SWEEP_REFERENCE.exists():
        issues.append(f"Missing futures latency sweep reference doc: {_repo_relative(FUTURES_LATENCY_SWEEP_REFERENCE)}")
    if not FUTURES_LATENCY_SWEEP_REFERENCE_CSV.exists():
        issues.append(
            f"Missing futures latency sweep reference CSV: {_repo_relative(FUTURES_LATENCY_SWEEP_REFERENCE_CSV)}"
        )
    if not FUTURES_LATENCY_SWEEP_REFRESH.exists():
        issues.append(f"Missing futures latency sweep refresh script: {_repo_relative(FUTURES_LATENCY_SWEEP_REFRESH)}")

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
        issues.append("docs/strategy_results/futures_strategy_profile_reference.md is missing the refresh command")
    if "research_mm" not in reference:
        issues.append(
            "docs/strategy_results/futures_strategy_profile_reference.md must include the research_mm profile"
        )

    if FUTURES_PARAMETER_SWEEP_REFERENCE.exists() and FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.exists():
        sweep_doc = _read_text(FUTURES_PARAMETER_SWEEP_REFERENCE)
        if not any(path in sweep_doc for path in COMMITTED_STRATEGY_PROFILE_INPUTS):
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.md must reference a committed replay input"
            )
        if "python scripts/refresh_futures_parameter_sweep_reference.py" not in sweep_doc:
            issues.append("docs/strategy_results/futures_parameter_sweep_reference.md is missing the refresh command")
        if "not an alpha or profitability claim" not in sweep_doc:
            issues.append("docs/strategy_results/futures_parameter_sweep_reference.md is missing the no-alpha caveat")
        if "Git dirty at run time: `False`" not in sweep_doc:
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.md must be refreshed from a clean source tree"
            )
        if "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" not in sweep_doc:
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.md is missing feed adapter provenance"
            )
        if "local-only" in sweep_doc or "data/raw_1772633471.ndjson" in sweep_doc:
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.md still depends on a local-only input"
            )

        with FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        required_columns = {
            "rank",
            "diagnostic_score",
            "strategy_profile",
            "half_spread_bps",
            "queue_repost_lots",
            "fill_count",
            "adverse_fill_rate_1s",
            "markout_by_fill_source",
            "inventory_stdev",
            "max_drawdown",
            "fill_from_top_rate",
            "avg_queue_ahead_lots",
            "resting_arrival_queue_samples",
            "arrival_with_queue_ahead_count",
            "avg_arrival_queue_ahead_lots",
            "max_arrival_queue_ahead_lots",
            "fill_source_counts",
            "order_lifecycle_counts",
        }
        fieldnames = set(rows[0].keys()) if rows else set()
        missing_columns = sorted(required_columns - fieldnames)
        if missing_columns:
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.csv missing column(s): "
                + ", ".join(missing_columns)
            )
        if len(rows) < 3:
            issues.append(
                "docs/strategy_results/futures_parameter_sweep_reference.csv must include multiple sweep rows"
            )
        else:
            try:
                ranks = [int(row["rank"]) for row in rows]
            except (KeyError, ValueError):
                issues.append("docs/strategy_results/futures_parameter_sweep_reference.csv has invalid ranks")
            else:
                if ranks != list(range(1, len(rows) + 1)):
                    issues.append(
                        "docs/strategy_results/futures_parameter_sweep_reference.csv ranks are not contiguous"
                    )
            profiles = {row.get("strategy_profile") for row in rows}
            if not {"baseline", "layered_mm", "research_mm"} <= profiles:
                issues.append(
                    "docs/strategy_results/futures_parameter_sweep_reference.csv is missing a strategy profile"
                )
            try:
                fill_counts = [int(row["fill_count"]) for row in rows]
            except (KeyError, ValueError):
                issues.append(
                    "docs/strategy_results/futures_parameter_sweep_reference.csv has invalid fill_count values"
                )
            else:
                if max(fill_counts, default=0) <= 0:
                    issues.append("docs/strategy_results/futures_parameter_sweep_reference.csv has no filled sweep run")

    if FUTURES_LATENCY_SWEEP_REFERENCE.exists() and FUTURES_LATENCY_SWEEP_REFERENCE_CSV.exists():
        latency_doc = _read_text(FUTURES_LATENCY_SWEEP_REFERENCE)
        if not any(path in latency_doc for path in COMMITTED_STRATEGY_PROFILE_INPUTS):
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.md must reference a committed replay input"
            )
        if "python scripts/refresh_futures_latency_sweep_reference.py" not in latency_doc:
            issues.append("docs/strategy_results/futures_latency_sweep_reference.md is missing the refresh command")
        if "not a latency-arbitrage, alpha, or profitability claim" not in latency_doc:
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.md is missing the latency/no-alpha caveat"
            )
        if "modeled order-arrival and cancel-ack delays" not in latency_doc:
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.md must describe modeled latency scope"
            )
        if "Git dirty at run time: `False`" not in latency_doc:
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.md must be refreshed from a clean source tree"
            )
        if "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" not in latency_doc:
            issues.append("docs/strategy_results/futures_latency_sweep_reference.md is missing feed adapter provenance")

        with FUTURES_LATENCY_SWEEP_REFERENCE_CSV.open("r", encoding="utf-8", newline="") as handle:
            latency_rows = list(csv.DictReader(handle))
        required_latency_columns = {
            "rank",
            "diagnostic_score",
            "strategy_profile",
            "order_latency_ms",
            "cancel_latency_ms",
            "fill_count",
            "adverse_fill_rate_1s",
            "markout_by_fill_source",
            "inventory_stdev",
            "max_drawdown",
            "avg_fill_wait_ms",
            "fill_source_counts",
            "order_lifecycle_counts",
        }
        latency_fieldnames = set(latency_rows[0].keys()) if latency_rows else set()
        missing_latency_columns = sorted(required_latency_columns - latency_fieldnames)
        if missing_latency_columns:
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.csv missing column(s): "
                + ", ".join(missing_latency_columns)
            )
        if len(latency_rows) < 3:
            issues.append(
                "docs/strategy_results/futures_latency_sweep_reference.csv must include multiple latency rows"
            )
        else:
            try:
                ranks = [int(row["rank"]) for row in latency_rows]
                order_latencies = {float(row["order_latency_ms"]) for row in latency_rows}
                cancel_latencies = {float(row["cancel_latency_ms"]) for row in latency_rows}
                fill_counts = [int(row["fill_count"]) for row in latency_rows]
            except (KeyError, ValueError):
                issues.append("docs/strategy_results/futures_latency_sweep_reference.csv has invalid numeric values")
            else:
                if ranks != list(range(1, len(latency_rows) + 1)):
                    issues.append("docs/strategy_results/futures_latency_sweep_reference.csv ranks are not contiguous")
                if 0.0 not in order_latencies or max(order_latencies, default=0.0) <= 0.0:
                    issues.append(
                        "docs/strategy_results/futures_latency_sweep_reference.csv must include zero and positive order latency"
                    )
                if 0.0 not in cancel_latencies or max(cancel_latencies, default=0.0) <= 0.0:
                    issues.append(
                        "docs/strategy_results/futures_latency_sweep_reference.csv must include zero and positive cancel latency"
                    )
                if max(fill_counts, default=0) <= 0:
                    issues.append("docs/strategy_results/futures_latency_sweep_reference.csv has no filled sweep run")

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
                issues.append(f"Strategy-profile walkthrough order is incorrect in {_repo_relative(path)}: {token}")
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
    issues.extend(_verify_manifest_source_provenance())
    issues.extend(_verify_futures_feed_adapter_metadata())
    issues.extend(_verify_futures_instrument_specs_metadata())
    issues.extend(_verify_futures_simulation_assumptions_metadata())
    issues.extend(_verify_core_files())
    issues.extend(_verify_futures_trade_audit_fields())
    issues.extend(_verify_futures_fill_source_counts())
    issues.extend(_verify_futures_markout_by_source())
    issues.extend(_verify_public_consumption_diagnostics())
    issues.extend(_verify_futures_self_trade_prevention_counts())
    issues.extend(_verify_futures_event_trace_contract())
    issues.extend(_verify_implied_vol_snapshot_references())
    issues.extend(_verify_no_temp_paths())
    issues.extend(_verify_no_malformed_cli_fragments())
    issues.extend(_verify_futures_showcase_front_door_links())
    issues.extend(_verify_launcher_layout())
    issues.extend(_verify_strategy_profile_publication())
    issues.extend(_verify_benchmark_publication())
    issues.extend(_verify_replay_contract_publication())
    issues.extend(_verify_reviewer_gate_publication())
    issues.extend(_verify_futures_stress_pack_publication())
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
