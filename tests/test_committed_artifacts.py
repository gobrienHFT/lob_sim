from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.refresh_sample_outputs import DOCUMENTED_SAMPLE_COMMANDS
from scripts import verify_committed_artifacts as verifier
from scripts.verify_committed_artifacts import collect_artifact_issues


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_committed_artifacts.py"
README = REPO_ROOT / "README.md"
WALKTHROUGH = REPO_ROOT / "WALKTHROUGH.md"
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SAMPLE_OUTPUTS_README = REPO_ROOT / "docs" / "sample_outputs" / "README.md"
FUTURES_VALIDATION = REPO_ROOT / "docs" / "futures_validation.md"
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
FUTURES_BENCHMARK_REFERENCE = REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md"
REPLAY_CONTRACT = REPO_ROOT / "docs" / "replay_contract.md"
HFT_REVIEWER_GUIDE = REPO_ROOT / "docs" / "hft_reviewer_guide.md"
FUTURES_STRATEGY_PROFILES = REPO_ROOT / "docs" / "futures_strategy_profiles.md"
FUTURES_STRATEGY_REFERENCE = REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md"
INTERVIEW_PACKET = REPO_ROOT / "docs" / "interview_packet.md"
REAL_DATA_RUNBOOK = REPO_ROOT / "docs" / "real_data_runbook.md"
REAL_DATA_RESULTS_TEMPLATE = REPO_ROOT / "docs" / "real_data_results_template.md"
REAL_DATA_RUNS_README = REPO_ROOT / "docs" / "real_data_runs" / "README.md"
PUBLISHED_REAL_DATA_REPORT = REPO_ROOT / "docs" / "real_data_runs" / "raw_1772633471.md"
PUBLISHED_REAL_DATA_REPORT_JSON = REPO_ROOT / "docs" / "real_data_runs" / "raw_1772633471.json"
REAL_DATA_REPORT_SCRIPT = REPO_ROOT / "scripts" / "run_real_data_report.py"
FUTURES_PARAMETER_SWEEP_REFERENCE = REPO_ROOT / "docs" / "strategy_results" / "futures_parameter_sweep_reference.md"
FUTURES_PARAMETER_SWEEP_REFERENCE_CSV = (
    REPO_ROOT / "docs" / "strategy_results" / "futures_parameter_sweep_reference.csv"
)
FUTURES_LATENCY_SWEEP_REFERENCE = REPO_ROOT / "docs" / "strategy_results" / "futures_latency_sweep_reference.md"
FUTURES_LATENCY_SWEEP_REFERENCE_CSV = REPO_ROOT / "docs" / "strategy_results" / "futures_latency_sweep_reference.csv"
FUTURES_STRESS_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_stress_case"
FUTURES_STRESS_SUMMARY = FUTURES_STRESS_DIR / "summary.json"
FUTURES_STRATEGY_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_strategy_profile_reference.py"
FUTURES_PARAMETER_SWEEP_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_parameter_sweep_reference.py"
FUTURES_LATENCY_SWEEP_REFRESH = REPO_ROOT / "scripts" / "refresh_futures_latency_sweep_reference.py"
FUTURES_DETERMINISM_CHECK = REPO_ROOT / "scripts" / "check_futures_determinism.py"
FUTURES_PACK_AUDIT = REPO_ROOT / "scripts" / "audit_futures_pack.py"
REVIEWER_GATE = REPO_ROOT / "scripts" / "reviewer_gate.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMMITTED_STRATEGY_INPUT = "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson"
FUTURES_WALKTHROUGH_README = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "README.md"
FUTURES_WALKTHROUGH_NOTES = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "walkthrough.md"
RECORDED_CASE_README = REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "README.md"
RECORDED_CASE_NOTES = REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "case_notes.md"
COMMITTED_CASE_BRIEF = REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "case_brief.md"
COMMITTED_DEMO_REPORT = REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "demo_report.md"
ROOT_OPTIONS_LAUNCHERS = [
    REPO_ROOT / "run_options_case_study.bat",
    REPO_ROOT / "run_options_case_study.sh",
    REPO_ROOT / "run_options_mm_case.bat",
    REPO_ROOT / "run_options_mm_case.sh",
    REPO_ROOT / "run_options_mm_quick.bat",
    REPO_ROOT / "run_options_mm_walkthrough_mode.bat",
]
CANONICAL_OPTIONS_LAUNCHERS = [
    REPO_ROOT / "scripts" / "launchers" / "run_options_case_study.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_case_study.sh",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_case.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_case.sh",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_quick.bat",
    REPO_ROOT / "scripts" / "launchers" / "run_options_mm_walkthrough_mode.bat",
]


def test_committed_artifacts_have_no_integrity_issues() -> None:
    issues = collect_artifact_issues()
    assert not issues, "\n".join(f"- {issue}" for issue in issues)


def test_verify_committed_artifacts_script_runs_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Committed artifact verification passed." in result.stdout


def test_futures_manifest_verifier_rejects_dirty_source_provenance(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    showcase.mkdir()
    recorded.mkdir()
    clean_source = {"git_commit": "abc123", "git_branch": "master", "git_dirty": False}
    dirty_source = {"git_commit": "def456", "git_branch": "master", "git_dirty": True}

    (showcase / "manifest.json").write_text(json.dumps({"source": dirty_source}), encoding="utf-8")
    (recorded / "manifest.json").write_text(json.dumps({"source": clean_source}), encoding="utf-8")
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_manifest_source_provenance()

    assert issues == [f"{showcase / 'manifest.json'} should be refreshed from a clean source tree"]


def test_futures_retention_verifier_rejects_tampered_audit_chain(tmp_path, monkeypatch) -> None:
    copied_dirs = []
    for source in (
        verifier.FUTURES_SHOWCASE_DIR,
        verifier.RECORDED_CLIP_DIR,
        verifier.FUTURES_STRESS_DIR,
    ):
        target = tmp_path / source.name
        shutil.copytree(source, target)
        copied_dirs.append(target)
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", copied_dirs[0])
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", copied_dirs[1])
    monkeypatch.setattr(verifier, "FUTURES_STRESS_DIR", copied_dirs[2])
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))
    summary_path = copied_dirs[0] / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["audit_retention"]["markout_audit_sha256"] = "f" * 64
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    issues = verifier._verify_futures_audit_retention()

    assert any("audit_retention.markout_audit_sha256" in issue for issue in issues)


def test_futures_feed_adapter_verifier_rejects_stale_metadata(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    showcase.mkdir()
    recorded.mkdir()
    expected = verifier.EXPECTED_FUTURES_FEED_ADAPTER
    stale = {**expected, "venue_label": "OTHER_VENUE"}

    (showcase / "manifest.json").write_text(json.dumps({"feed_adapter": stale}), encoding="utf-8")
    (showcase / "summary.json").write_text(json.dumps({"feed_adapter": expected}), encoding="utf-8")
    (recorded / "manifest.json").write_text(json.dumps({"feed_adapter": expected}), encoding="utf-8")
    (recorded / "summary.json").write_text(json.dumps({"feed_adapter": expected}), encoding="utf-8")
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_futures_feed_adapter_metadata()

    assert issues == [
        f"{showcase / 'manifest.json'} has missing or stale feed_adapter metadata",
        f"{showcase / 'manifest.json'} feed_adapter does not match summary.json",
    ]


def _write_instrument_spec_case(
    directory: Path,
    *,
    input_name: str,
    manifest_specs: dict,
    summary_specs: dict,
) -> None:
    directory.mkdir()
    input_row = {
        "ts_local": 1.0,
        "symbol": "BTCUSDT",
        "type": "exchangeInfo",
        "data": {
            "symbol": "BTCUSDT",
            "tickSize": "0.10",
            "stepSize": "0.001",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "venue": "BINANCE_USDM",
        },
    }
    (directory / input_name).write_text(json.dumps(input_row) + "\n", encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"instrument_specs": manifest_specs}),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps({"instrument_specs": summary_specs}),
        encoding="utf-8",
    )
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["instrument_specs"])
        writer.writeheader()
        writer.writerow({"instrument_specs": json.dumps(summary_specs, sort_keys=True)})


def test_futures_instrument_spec_verifier_rejects_stale_metadata(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    expected = {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "venue": "BINANCE_USDM",
            "price_currency": "USDT",
            "quantity_unit": "BTC",
            "tick_size": "0.10",
            "step_size": "0.001",
            "contract_multiplier": "1",
        }
    }
    stale = {
        "BTCUSDT": {
            **expected["BTCUSDT"],
            "contract_multiplier": "100",
        }
    }

    _write_instrument_spec_case(
        showcase,
        input_name="input_fixture.ndjson",
        manifest_specs=stale,
        summary_specs=expected,
    )
    _write_instrument_spec_case(
        recorded,
        input_name="input_clip.ndjson",
        manifest_specs=expected,
        summary_specs=expected,
    )
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_futures_instrument_specs_metadata()

    assert issues == [
        f"{showcase / 'manifest.json'} instrument_specs does not match summary.json",
        f"{showcase / 'manifest.json'} instrument_specs does not match replay input metadata",
    ]


def _valid_simulation_assumptions() -> dict:
    return {
        "schema_version": verifier.EXPECTED_SIMULATION_ASSUMPTIONS_SCHEMA_VERSION,
        "fill_assumption_profile": "base",
        "fill_assumption": {
            "profile": "base",
            "depth_reductions_consume_queue": False,
            "agg_trades_consume_queue": True,
            "overlap_netting_enabled": True,
            "overlap_window_seconds": verifier.EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS,
            "uncorroborated_depth_reduction_mode": "consume_fifo_queue",
        },
        "data_scope": "public_l2_order_book_and_agg_trade_records",
        "private_exchange_execution_reports": False,
        "queue_priority_model": "synthetic_queue_ahead_by_price_level",
        "snapshot_seed": "snapshot queue",
        "depth_increase": "depth increases append",
        "depth_decrease": "depth reductions consume",
        "agg_trade_consumption": "aggTrade consumes",
        "overlap_netting": {
            "enabled": True,
            "window_seconds": verifier.EXPECTED_PUBLIC_CONSUMPTION_OVERLAP_WINDOW_SECONDS,
            "purpose": "dedupe public consumption",
        },
        "cancel_model": "cancel latency",
        "same_timestamp_ordering": "event-time ordering",
        "marketable_limits": "taker execution",
        "self_trade_prevention": "stop before own liquidity",
        "markout": "mid-price markout",
        "limitations": sorted(verifier.EXPECTED_SIMULATION_LIMITATIONS),
    }


def _write_simulation_assumption_case(
    directory: Path,
    *,
    manifest_assumptions: dict,
    summary_assumptions: dict,
) -> None:
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps({"simulation_assumptions": manifest_assumptions}),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps({"simulation_assumptions": summary_assumptions}),
        encoding="utf-8",
    )
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["simulation_assumptions"])
        writer.writeheader()
        writer.writerow({"simulation_assumptions": json.dumps(summary_assumptions, sort_keys=True)})


def test_futures_simulation_assumption_verifier_rejects_private_fill_claim(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    expected = _valid_simulation_assumptions()
    stale = {**expected, "private_exchange_execution_reports": True}

    _write_simulation_assumption_case(
        showcase,
        manifest_assumptions=stale,
        summary_assumptions=expected,
    )
    _write_simulation_assumption_case(
        recorded,
        manifest_assumptions=expected,
        summary_assumptions=expected,
    )
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_futures_simulation_assumptions_metadata()

    assert issues == [
        f"{showcase / 'manifest.json'} simulation_assumptions must not claim private exchange execution reports",
        f"{showcase / 'manifest.json'} simulation_assumptions does not match summary.json",
    ]


def test_futures_trade_audit_verifier_requires_economics_and_provenance(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    stress = tmp_path / "stress"
    showcase.mkdir()
    recorded.mkdir()
    stress.mkdir()

    with (showcase / "trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["fill_source", "fee_bps", "fee", "fee_currency"])
        writer.writeheader()
        writer.writerow({"fill_source": "depth_update", "fee_bps": "0", "fee": "0", "fee_currency": "USDT"})
    with (recorded / "trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(verifier.FUTURES_TRADE_AUDIT_FIELDS))
        writer.writeheader()
    with (stress / "trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(verifier.FUTURES_TRADE_AUDIT_FIELDS))
        writer.writeheader()
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "FUTURES_STRESS_DIR", stress)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_futures_trade_audit_fields()
    provided = {"fill_source", "fee_bps", "fee", "fee_currency"}
    missing = ", ".join(sorted(verifier.FUTURES_TRADE_AUDIT_FIELDS - provided))

    assert issues == [f"{showcase / 'trades.csv'} is missing trade audit column(s): {missing}"]


def test_fill_provenance_verifier_rejects_inconsistent_validity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))
    row = _event_trace_row(
        event_type="fill",
        side="bid",
        qty_lots="2",
        fill_source="agg_trade",
    )
    details = {
        "provenance_schema_version": "lob_sim.fill_provenance.v1",
        "scenario_id": "public_l2:base:signal=trade:overlap_us=0",
        "evidence_ids": ["input_row:4"],
        "validity": {
            "book_valid": True,
            "trade_stream_valid": True,
            "clock_valid": True,
            "capture_valid": True,
            "trade_stream_required": True,
            "execution_valid": False,
            "reason": None,
        },
        "queue_trajectory": {
            "queue_ahead_before_trigger_lots": 3,
            "queue_ahead_at_fill_lots": 0,
            "queue_consumed_before_fill_lots": 3,
            "public_consumption_trigger_lots": 5,
            "fill_lots": 2,
            "remaining_order_lots_after_fill": 0,
        },
        "latency_draws_ms": {"new_order": 1.0, "cancel": None},
        "latency_model": {
            "mode": "fixed",
            "seed": 7,
            "source": "configured_scenario",
            "measured": False,
        },
        "order_state_at_fill": "live",
        "fee_model_id": "static_config_bps",
    }

    issues = verifier._verify_fill_provenance_details(tmp_path / "event_trace.csv", 2, row, details)

    assert issues == [f"{tmp_path / 'event_trace.csv'}:2 fill row validity has inconsistent execution_valid"]


def _write_public_consumption_case(directory: Path, diagnostics: dict) -> None:
    directory.mkdir(exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps({"public_consumption_summary": diagnostics}),
        encoding="utf-8",
    )
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["public_consumption_summary"])
        writer.writeheader()
        writer.writerow({"public_consumption_summary": json.dumps(diagnostics, sort_keys=True)})


def test_public_consumption_verifier_rejects_inconsistent_totals(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    good_diagnostics = {
        "overlap_window_seconds": 0.125,
        "sources": {
            "depth_update": {
                "observed_lots": 2,
                "modeled_lots": 1,
                "overlap_netted_lots": 1,
                "queue_consumed_lots": 1,
                "unmatched_lots": 0,
            },
            "agg_trade": {
                "observed_lots": 3,
                "modeled_lots": 3,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 3,
                "unmatched_lots": 0,
            },
        },
        "total_observed_lots": 5,
        "total_modeled_lots": 4,
        "total_overlap_netted_lots": 1,
        "total_queue_consumed_lots": 4,
        "total_unmatched_lots": 0,
    }
    stale_diagnostics = {**good_diagnostics, "total_observed_lots": 99}

    _write_public_consumption_case(showcase, stale_diagnostics)
    _write_public_consumption_case(recorded, good_diagnostics)
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_SUMMARY", showcase / "summary.json")
    monkeypatch.setattr(verifier, "RECORDED_CLIP_SUMMARY", recorded / "summary.json")
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_public_consumption_diagnostics()

    assert issues == [f"{showcase / 'summary.json'} public_consumption_summary.total_observed_lots is inconsistent"]


def test_public_consumption_verifier_rejects_inconsistent_unmatched_lots(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    good_diagnostics = {
        "overlap_window_seconds": 0.125,
        "sources": {
            "depth_update": {
                "observed_lots": 3,
                "modeled_lots": 2,
                "overlap_netted_lots": 1,
                "queue_consumed_lots": 1,
                "unmatched_lots": 1,
            },
            "agg_trade": {
                "observed_lots": 0,
                "modeled_lots": 0,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 0,
                "unmatched_lots": 0,
            },
        },
        "total_observed_lots": 3,
        "total_modeled_lots": 2,
        "total_overlap_netted_lots": 1,
        "total_queue_consumed_lots": 1,
        "total_unmatched_lots": 1,
    }
    stale_diagnostics = json.loads(json.dumps(good_diagnostics))
    stale_diagnostics["sources"]["depth_update"]["unmatched_lots"] = 0

    _write_public_consumption_case(showcase, stale_diagnostics)
    _write_public_consumption_case(recorded, good_diagnostics)
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_SUMMARY", showcase / "summary.json")
    monkeypatch.setattr(verifier, "RECORDED_CLIP_SUMMARY", recorded / "summary.json")
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_public_consumption_diagnostics()

    assert issues == [
        f"{showcase / 'summary.json'} public_consumption_summary[depth_update] has inconsistent unmatched lots",
        f"{showcase / 'summary.json'} public_consumption_summary.total_unmatched_lots is inconsistent",
    ]


def _write_markout_by_source_case(directory: Path, diagnostics: dict, events: list[dict]) -> None:
    directory.mkdir()
    (directory / "summary.json").write_text(
        json.dumps({"markout_by_fill_source": diagnostics, "markout_events": events}),
        encoding="utf-8",
    )
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["markout_by_fill_source"])
        writer.writeheader()
        writer.writerow({"markout_by_fill_source": json.dumps(diagnostics, sort_keys=True)})


def test_markout_by_source_verifier_rejects_event_mismatch(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_SUMMARY", showcase / "summary.json")
    monkeypatch.setattr(verifier, "RECORDED_CLIP_SUMMARY", recorded / "summary.json")
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    good = {
        "depth_update": {
            "samples": 0,
            "adverse_samples": 0,
            "qty": 0.0,
            "avg_markout_1s": 0.0,
            "adverse_fill_rate_1s": 0.0,
        },
        "agg_trade": {
            "samples": 1,
            "adverse_samples": 1,
            "qty": 0.001,
            "avg_markout_1s": -2.0,
            "adverse_fill_rate_1s": 1.0,
        },
        "taker_order": {
            "samples": 0,
            "adverse_samples": 0,
            "qty": 0.0,
            "avg_markout_1s": 0.0,
            "adverse_fill_rate_1s": 0.0,
        },
    }
    events = [{"fill_source": "agg_trade", "qty": "0.001", "markout": "-2", "adverse": True}]
    stale = {**good, "agg_trade": {**good["agg_trade"], "avg_markout_1s": 0.0}}
    _write_markout_by_source_case(showcase, stale, events)
    _write_markout_by_source_case(recorded, good, events)

    issues = verifier._verify_futures_markout_by_source()

    assert issues == [
        f"{showcase / 'summary.json'} markout_by_fill_source[agg_trade].avg_markout_1s does not match markout_events"
    ]


def _event_trace_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in verifier.FUTURES_EVENT_TRACE_FIELDS}
    row.update(
        {
            "ts_local": "1.0",
            "seq": "0",
            "symbol": "BTCUSDT",
            "event_type": "market_record",
            "source": "snapshot",
            "details": '{"record_type":"snapshot"}',
        }
    )
    row.update(overrides)
    return row


def _lifecycle_counts(**overrides: int) -> dict[str, int]:
    counts = {key: 0 for key in verifier.FUTURES_ORDER_LIFECYCLE_KEYS}
    counts.update(overrides)
    return counts


def _write_event_trace_case(
    directory: Path,
    *,
    event_trace_count: int,
    fill_count: int,
    rows: list[dict[str, str]],
    lifecycle_counts: dict[str, int] | None = None,
) -> None:
    directory.mkdir()
    arrival_queue_samples = 0
    arrival_with_queue_ahead_count = 0
    arrival_queue_ahead_sum = 0
    max_arrival_queue_ahead_lots = 0
    for row in rows:
        if row.get("event_type") != "order_arrival":
            continue
        try:
            details = json.loads(row.get("details") or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(details, dict) or details.get("resting_after_arrival") is not True:
            continue
        queue_ahead = details.get("queue_ahead_lots_after_arrival", 0)
        if isinstance(queue_ahead, int) and queue_ahead >= 0:
            arrival_queue_samples += 1
            arrival_queue_ahead_sum += queue_ahead
            if queue_ahead > 0:
                arrival_with_queue_ahead_count += 1
            max_arrival_queue_ahead_lots = max(max_arrival_queue_ahead_lots, queue_ahead)
    avg_arrival_queue_ahead_lots = arrival_queue_ahead_sum / arrival_queue_samples if arrival_queue_samples else 0.0
    counts = lifecycle_counts or _lifecycle_counts()
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "event_trace_count": event_trace_count,
                "fill_count": fill_count,
                "quote_count": counts["arrived"],
                "cancel_count": counts["cancel_requested"],
                "self_trade_prevention_count": counts["self_trade_prevented"],
                "order_lifecycle_counts": counts,
                "resting_arrival_queue_samples": arrival_queue_samples,
                "arrival_with_queue_ahead_count": arrival_with_queue_ahead_count,
                "avg_arrival_queue_ahead_lots": avg_arrival_queue_ahead_lots,
                "max_arrival_queue_ahead_lots": max_arrival_queue_ahead_lots,
            }
        ),
        encoding="utf-8",
    )
    with (directory / "event_trace.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=verifier.FUTURES_EVENT_TRACE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch) -> tuple[Path, Path]:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))
    return showcase, recorded


def test_futures_event_trace_verifier_rejects_summary_count_mismatch(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=2,
        fill_count=0,
        rows=[_event_trace_row()],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert issues == [f"{showcase / 'event_trace.csv'} has 1 row(s), expected 2 from summary"]


def test_futures_event_trace_verifier_rejects_unstructured_fill_rows(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=1,
        rows=[
            _event_trace_row(
                event_type="fill",
                source="fill_model",
                side="bid",
                price_tick="1000",
                qty_lots="1",
                order_id="order-1",
                fill_source="mystery",
                details='["not", "an", "object"]',
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 details must be a JSON object" in issues
    assert f"{showcase / 'event_trace.csv'}:2 has invalid fill_source: 'mystery'" in issues


def test_futures_event_trace_verifier_rejects_malformed_fill_economics(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=1,
        rows=[
            _event_trace_row(
                event_type="fill",
                source="fill_model",
                side="bid",
                price_tick="1000",
                qty_lots="1",
                order_id="order-1",
                fill_source="depth_update",
                details=(
                    '{"maker":true,"queue_ahead_lots":0,"created_ts":1.0,'
                    '"price":"100","qty":"0.001","notional":"oops",'
                    '"contract_multiplier":"1","fee_bps":"0","fee":"0",'
                    '"fee_currency":"USDT","mid_at_fill":"100.05",'
                    '"spread_capture":"0.05","spread_capture_value":"0.00005",'
                    '"time_in_book_ms":1000.0,"markout_horizon":1.0,'
                    '"regime":"tight","book_bid_tick":1000,"book_ask_tick":1001}'
                ),
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 fill row has invalid notional" in issues


def test_futures_event_trace_verifier_rejects_lifecycle_summary_mismatch(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="order_arrival_scheduled",
                source="engine",
                details='{"arrival_ts":1.0}',
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'} lifecycle arrival_scheduled=1 does not match summary value 0" in issues


def test_futures_event_trace_verifier_rejects_arrival_queue_mismatch(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="order_arrival",
                source="engine",
                details='{"resting_after_arrival":true,"queue_ahead_lots_after_arrival":3,"immediate_fills":0}',
            )
        ],
        lifecycle_counts=_lifecycle_counts(arrived=1, rested_after_arrival=1),
    )
    summary = json.loads((showcase / "summary.json").read_text(encoding="utf-8"))
    summary["max_arrival_queue_ahead_lots"] = 0
    (showcase / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'} max_arrival_queue_ahead_lots=3 does not match summary value 0" in issues


def test_futures_event_trace_verifier_rejects_unstructured_risk_halt(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="risk_halt",
                source="risk",
                details='{"reason":"","phase":"mystery","canceled_order_count":-1}',
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 risk_halt row is missing reason" in issues
    assert f"{showcase / 'event_trace.csv'}:2 risk_halt row has invalid phase" in issues
    assert f"{showcase / 'event_trace.csv'}:2 risk_halt row has invalid canceled_order_count" in issues


def test_futures_event_trace_verifier_rejects_malformed_queue_consumption(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="queue_consumption",
                source="depth_update",
                side="bid",
                price_tick="1000",
                qty_lots="5",
                details=(
                    '{"observed_lots":4,"modeled_lots":5,"overlap_netted_lots":0,'
                    '"queue_consumed_lots":6,"unmatched_lots":0,"overlap_window_seconds":999,'
                    '"fill_assumption_profile":"base"}'
                ),
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 queue_consumption qty_lots does not match observed_lots" in issues
    assert f"{showcase / 'event_trace.csv'}:2 queue_consumption models more lots than observed" in issues
    assert f"{showcase / 'event_trace.csv'}:2 queue_consumption consumes more queue than modeled" in issues
    assert f"{showcase / 'event_trace.csv'}:2 queue_consumption has unexpected overlap window" in issues


def test_futures_event_trace_verifier_rejects_queue_consumption_summary_mismatch(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="queue_consumption",
                source="depth_update",
                side="ask",
                price_tick="1001",
                qty_lots="2",
                details=(
                    '{"observed_lots":2,"modeled_lots":1,"overlap_netted_lots":1,'
                    '"queue_consumed_lots":1,"unmatched_lots":0,"overlap_window_seconds":0.125,'
                    '"fill_assumption_profile":"base"}'
                ),
            )
        ],
    )
    summary = json.loads((showcase / "summary.json").read_text(encoding="utf-8"))
    summary["public_consumption_summary"] = {
        "sources": {
            "depth_update": {
                "observed_lots": 0,
                "modeled_lots": 0,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 0,
                "unmatched_lots": 0,
            },
            "agg_trade": {
                "observed_lots": 0,
                "modeled_lots": 0,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 0,
                "unmatched_lots": 0,
            },
        }
    }
    (showcase / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert (
        f"{showcase / 'event_trace.csv'} queue_consumption depth_update.observed_lots=2 does not match summary value 0"
    ) in issues


def test_futures_event_trace_verifier_rejects_malformed_markout(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="markout",
                source="engine",
                side="buy",
                price_tick="0",
                qty_lots="0",
                fill_source="mystery",
                details=(
                    '{"fill_ts_local":1.0,"deadline_ts":2.0,"horizon":1.0,'
                    '"fill_price":"100","qty":"0.001","fill_mid":"100.5",'
                    '"mid_after":"99","markout":"-1","contract_multiplier":"1",'
                    '"adverse":"yes","regime":"tight","status":"resolved",'
                    '"invalid_reason":null}'
                ),
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid source" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid fill_source" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid side" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row is missing order_id" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid price_tick" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid qty_lots" in issues
    assert f"{showcase / 'event_trace.csv'}:2 markout row has invalid adverse" in issues


def test_markout_trace_verifier_accepts_invalidated_null_result(tmp_path) -> None:
    row = _event_trace_row(
        event_type="markout",
        source="metrics",
        side="bid",
        price_tick="1000",
        qty_lots="1",
        order_id="order-1",
        fill_source="agg_trade",
    )
    details = {
        "fill_ts_local": 1.0,
        "deadline_ts": 2.0,
        "horizon": 1.0,
        "fill_price": "100",
        "qty": "0.001",
        "fill_mid": "100.5",
        "mid_after": None,
        "markout": None,
        "contract_multiplier": "1",
        "adverse": None,
        "regime": "tight",
        "status": "invalidated",
        "invalid_reason": "depth_gap",
    }

    assert verifier._verify_markout_trace_details(tmp_path / "event_trace.csv", 2, row, details) == []


def test_futures_event_trace_verifier_rejects_markout_summary_mismatch(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    markout_details = (
        '{"fill_ts_local":1.0,"deadline_ts":2.0,"horizon":1.0,'
        '"fill_price":"100","qty":"0.001","fill_mid":"100.5",'
        '"mid_after":"99","markout":"-1","contract_multiplier":"1",'
        '"adverse":true,"regime":"tight","status":"resolved",'
        '"invalid_reason":null}'
    )
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="markout",
                source="metrics",
                side="bid",
                price_tick="1000",
                qty_lots="1",
                order_id="order-1",
                fill_source="agg_trade",
                details=markout_details,
            )
        ],
    )
    summary = json.loads((showcase / "summary.json").read_text(encoding="utf-8"))
    summary["markout_events"] = [{"fill_source": "agg_trade", "qty": "0.001", "markout": "-1", "adverse": True}]
    summary["markout_by_fill_source"] = {
        source: {
            "samples": 0,
            "adverse_samples": 0,
            "qty": 0.0,
            "avg_markout_1s": 0.0,
            "adverse_fill_rate_1s": 0.0,
        }
        for source in verifier.FUTURES_FILL_SOURCES
    }
    (showcase / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert (f"{showcase / 'event_trace.csv'} markout agg_trade.samples=1 does not match summary value 0") in issues


def test_futures_event_trace_verifier_rejects_missing_decision_diagnostics(tmp_path, monkeypatch) -> None:
    showcase, recorded = _point_event_trace_verifier_at_tmp_cases(tmp_path, monkeypatch)
    _write_event_trace_case(
        showcase,
        event_trace_count=1,
        fill_count=0,
        rows=[
            _event_trace_row(
                event_type="decision",
                source="strategy",
                details='{"strategy_profile":"baseline","quote_count":2}',
            )
        ],
    )
    _write_event_trace_case(
        recorded,
        event_trace_count=1,
        fill_count=0,
        rows=[_event_trace_row()],
    )

    issues = verifier._verify_futures_event_trace_contract()

    assert f"{showcase / 'event_trace.csv'}:2 decision row is missing strategy diagnostics" in issues


def test_ci_runs_supported_python_matrix_and_artifact_verifier() -> None:
    assert CI_WORKFLOW.exists()
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    mypy_targets = (
        "lob_sim/book lob_sim/replay lob_sim/record lob_sim/binance/ws.py lob_sim/cli.py lob_sim/config.py "
        "lob_sim/oracle_kernel.py "
        "lob_sim/util.py "
        "lob_sim/sim/fill_model.py "
        "lob_sim/sim/engine.py lob_sim/sim/export.py lob_sim/sim/runner.py "
        "lob_sim/sim/metrics.py lob_sim/sim/run_manifest.py lob_sim/sim/mm_strategy.py"
    )
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in pyproject
        assert f'"{version}"' in workflow
    assert "python -m pip install -r requirements.txt" in workflow
    assert "python -m pip check" in workflow
    assert "python -m lob_sim.cli --help" in workflow
    assert "make reviewer-gate" in workflow
    assert "MPLBACKEND: Agg" in workflow
    assert "ci: reviewer-gate" in makefile
    assert "reviewer-gate:" in makefile
    assert "type-check:" in makefile
    assert f"MYPY_TARGETS ?= {mypy_targets}" in makefile
    assert "$(PY) -m mypy $(MYPY_TARGETS)" in makefile
    assert "$(PY) scripts/reviewer_gate.py --python $(PY)" in makefile
    assert "lint:" in makefile
    assert "format-check:" in makefile
    assert "$(PY) -m ruff check ." in makefile
    assert "$(PY) -m ruff format --check ." in makefile
    assert "refresh-artifacts:" in makefile
    assert "determinism-fixture:" in makefile
    assert "audit-fixture:" in makefile
    assert "audit-futures-packs:" in makefile
    assert "latency-sweep-fixture:" in makefile
    assert "scripts/check_futures_determinism.py" in makefile
    assert "scripts/audit_futures_pack.py" in makefile
    assert "experiments/benchmark_futures_replay.py" in makefile
    assert "--mode all --pack docs/sample_outputs/futures_stress_case" in makefile
    assert "experiments/sweep_futures_latency.py" in makefile
    assert "scripts/refresh_futures_reviewer_artifacts.py" in makefile
    assert "scripts/refresh_futures_stress_case.py" in (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "mypy>=1.10" in pyproject
    assert FUTURES_DETERMINISM_CHECK.exists()
    assert FUTURES_PACK_AUDIT.exists()
    assert REVIEWER_GATE.exists()
    assert REAL_DATA_REPORT_SCRIPT.exists()
    assert INTERVIEW_PACKET.exists()
    assert REAL_DATA_RUNBOOK.exists()
    assert REAL_DATA_RESULTS_TEMPLATE.exists()
    assert REAL_DATA_RUNS_README.exists()
    assert PUBLISHED_REAL_DATA_REPORT.exists()
    assert PUBLISHED_REAL_DATA_REPORT_JSON.exists()
    assert "python scripts/reviewer_gate.py" in README.read_text(encoding="utf-8")
    assert "python scripts/reviewer_gate.py" in HFT_REVIEWER_GUIDE.read_text(encoding="utf-8")
    assert "make reviewer-gate" in README.read_text(encoding="utf-8")
    assert "make reviewer-gate" in HFT_REVIEWER_GUIDE.read_text(encoding="utf-8")
    assert "docs/interview_packet.md" in README.read_text(encoding="utf-8")
    assert "docs/interview_packet.md" in WALKTHROUGH.read_text(encoding="utf-8")
    assert "docs/real_data_runbook.md" in README.read_text(encoding="utf-8")
    assert "docs/real_data_results_template.md" in README.read_text(encoding="utf-8")
    readme_front_door = README.read_text(encoding="utf-8")[:1200]
    assert "docs/real_data_runs/raw_1772633471.md" not in readme_front_door
    assert "docs/real_data_runs/raw_1780500354_10m.md" not in readme_front_door


def test_published_real_data_report_contains_required_evidence() -> None:
    markdown = PUBLISHED_REAL_DATA_REPORT.read_text(encoding="utf-8")
    payload = json.loads(PUBLISHED_REAL_DATA_REPORT_JSON.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "lob_sim.real_data_report.v1"
    assert payload["raw_data_policy"] == "local-only raw data; raw NDJSON is not committed"
    assert payload["input"]["sha256"] == "520e65919c86c552162028c52da92b642018daf69b4bdb8ca8a9d1626eecb5c8"
    assert payload["input"]["file_size_bytes"] > 1_000_000
    assert payload["input"]["symbol"] == "BTCUSDT"
    assert payload["target_window"]["requested"] == "10-30 minutes"
    assert payload["target_window"]["meets_target"] is False
    assert payload["target_window"]["label"] == "short local public tape"
    assert payload["target_window"]["env_overrides"]["COLLECT_SECONDS"] == "1800"
    assert payload["target_window"]["longer_run_commands"]
    assert payload["local_artifacts"]["report_only_docs_safe"] is True
    assert payload["event_counts"]["records_processed"] == 1997
    assert payload["event_counts"]["book_gap_count"] == 1
    assert payload["fills"]["fill_count"] == 20
    assert payload["fills"]["quote_fill_probability"] == payload["fills"]["fills_per_quote_request"]
    assert payload["fills"]["fills_per_quote_request"] == payload["fills"]["fills_per_arrived_order"]
    assert set(payload["fills"]["fill_source_counts"]) == {"depth_update", "agg_trade", "taker_order"}
    assert set(payload["markout_by_fill_source"]) == {"depth_update", "agg_trade", "taker_order"}
    assert "inventory_by_symbol" in payload["risk"]
    assert "max_drawdown" in payload["risk"]
    assert payload["audit"] == {
        "ok": True,
        "issue_count": 0,
        "event_trace_rows": 35561,
        "queue_consumption_rows": 30986,
    }
    assert payload["benchmark"]["replay_only"]["events_per_second"] > 0

    for token in [
        "Input SHA-256",
        "Event Counts",
        "Fill-source mix",
        "Markouts",
        "Inventory And Drawdown",
        "Benchmark",
        "Plain Interpretation",
        "Negative or positive PnL is not the point",
        "Meets 10-30 minute target: `false`",
        "Longer Target Run",
        "python scripts/run_real_data_report.py",
        "local-only raw data",
    ]:
        assert token in markdown


def test_committed_stress_fill_includes_units_explanation() -> None:
    case_brief = COMMITTED_CASE_BRIEF.read_text(encoding="utf-8")
    demo_report = COMMITTED_DEMO_REPORT.read_text(encoding="utf-8")

    assert "This is not a units mismatch" in case_brief
    assert "This is not a units mismatch" in demo_report


def test_futures_stress_pack_covers_reviewer_edge_cases() -> None:
    summary = json.loads(FUTURES_STRESS_SUMMARY.read_text(encoding="utf-8"))
    coverage = summary["stress_coverage"]

    assert summary["fixture_provenance"]["source"] == "synthetic_exchange_shaped"
    assert all(
        coverage[key] is True
        for key in [
            "queue_ahead",
            "partial_fills",
            "exclusive_trade_fill_attribution",
            "signed_markout_accounting",
            "cancel_latency",
            "same_timestamp_cancel_before_trade",
            "arrival_time_post_only_rejection",
            "self_trade_prevention",
        ]
    )
    assert coverage["book_gap_count"] == 0
    assert summary["fill_source_counts"]["depth_update"] == 0
    assert summary["fill_source_counts"]["agg_trade"] > 0
    assert summary["fill_source_counts"]["taker_order"] == 0
    assert summary["public_consumption_summary"]["sources"]["depth_update"]["unmatched_lots"] > 0
    assert summary["order_lifecycle_counts"]["self_trade_prevented"] == 1


def test_sample_output_commands_match_refresh_source_of_truth() -> None:
    readme = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")

    assert "<temp_dir>" not in readme
    for command in DOCUMENTED_SAMPLE_COMMANDS.values():
        assert command in readme, f"missing documented command: {command}"


def test_futures_walkthrough_pack_is_linked_from_front_door_docs() -> None:
    readme = README.read_text(encoding="utf-8")
    walkthrough = WALKTHROUGH.read_text(encoding="utf-8")
    sample_outputs = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")
    readme_walkthrough = readme.split("## Walkthrough Path", 1)[1]
    walkthrough_five_minute = walkthrough.split("## 5-Minute Walkthrough", 1)[1].split("## Core Talking Points", 1)[0]

    assert "docs/sample_outputs/futures_replay_walkthrough/README.md" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/summary.json" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/trades.csv" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/event_trace.csv" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md" in readme
    assert "docs/sample_outputs/futures_recorded_clip_case/README.md" in readme
    assert "docs/sample_outputs/futures_stress_case/README.md" in readme
    assert "docs/reviewer_results_memo.md" in readme
    assert "docs/futures_strategy_profiles.md" in readme
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in readme
    assert "docs/strategy_results/futures_parameter_sweep_reference.md" in readme
    assert "docs/benchmark_results/futures_replay_reference.md" in readme
    assert readme_walkthrough.index(
        "docs/sample_outputs/futures_recorded_clip_case/README.md"
    ) < readme_walkthrough.index("docs/futures_strategy_profiles.md")
    assert readme_walkthrough.index("docs/futures_strategy_profiles.md") < readme_walkthrough.index(
        "docs/strategy_results/futures_strategy_profile_reference.md"
    )

    assert "docs/sample_outputs/futures_replay_walkthrough/README.md" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/summary.json" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/trades.csv" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/event_trace.csv" in walkthrough
    assert "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md" in walkthrough
    assert "docs/sample_outputs/futures_recorded_clip_case/README.md" in walkthrough
    assert "docs/sample_outputs/futures_recorded_clip_case/case_notes.md" in walkthrough
    assert "futures_stress_case/README.md" in sample_outputs
    assert "futures_stress_case/summary.json" in sample_outputs
    assert "futures_stress_case/trades.csv" in sample_outputs
    assert "futures_stress_case/event_trace.csv" in sample_outputs
    assert "docs/futures_strategy_profiles.md" in walkthrough
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in walkthrough
    assert "docs/strategy_results/futures_parameter_sweep_reference.md" in walkthrough
    assert "docs/benchmark_results/futures_replay_reference.md" in walkthrough
    assert walkthrough_five_minute.index(
        "docs/sample_outputs/futures_recorded_clip_case/README.md"
    ) < walkthrough_five_minute.index("docs/futures_strategy_profiles.md")
    assert walkthrough_five_minute.index("docs/futures_strategy_profiles.md") < walkthrough_five_minute.index(
        "docs/strategy_results/futures_strategy_profile_reference.md"
    )

    assert "futures_replay_walkthrough/README.md" in sample_outputs
    assert "futures_replay_walkthrough/summary.json" in sample_outputs
    assert "futures_replay_walkthrough/manifest.json" in sample_outputs
    assert "futures_replay_walkthrough/trades.csv" in sample_outputs
    assert "futures_replay_walkthrough/event_trace.csv" in sample_outputs
    assert "futures_replay_walkthrough/walkthrough.md" in sample_outputs
    assert "futures_recorded_clip_case/README.md" in sample_outputs
    assert "futures_recorded_clip_case/summary.json" in sample_outputs
    assert "futures_recorded_clip_case/manifest.json" in sample_outputs
    assert "futures_recorded_clip_case/trades.csv" in sample_outputs
    assert "futures_recorded_clip_case/event_trace.csv" in sample_outputs
    assert "futures_recorded_clip_case/case_notes.md" in sample_outputs


def test_futures_walkthrough_refresh_command_is_documented_consistently() -> None:
    sample_outputs = SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")
    futures_pack = FUTURES_WALKTHROUGH_README.read_text(encoding="utf-8")
    futures_notes = FUTURES_WALKTHROUGH_NOTES.read_text(encoding="utf-8")
    recorded_pack = RECORDED_CASE_README.read_text(encoding="utf-8")
    recorded_notes = RECORDED_CASE_NOTES.read_text(encoding="utf-8")

    assert "python scripts/refresh_futures_showcase.py" in sample_outputs
    assert "python scripts/refresh_futures_showcase.py" in futures_pack
    assert "python scripts/refresh_futures_showcase.py" in futures_notes
    assert "python scripts/refresh_futures_recorded_case.py" in sample_outputs
    assert "python scripts/refresh_futures_recorded_case.py" in recorded_pack
    assert "python scripts/refresh_futures_recorded_case.py" in recorded_notes

    assert "refresh_futures_replay_summary.py" not in sample_outputs
    assert "refresh_futures_replay_summary.py" not in futures_pack
    assert "refresh_futures_replay_summary.py" not in futures_notes
    assert "refresh_futures_replay_summary.py" not in recorded_pack
    assert "refresh_futures_replay_summary.py" not in recorded_notes


def test_futures_determinism_checker_is_documented() -> None:
    expected_command = (
        "python scripts/check_futures_determinism.py --file "
        "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example"
    )

    assert FUTURES_DETERMINISM_CHECK.exists()
    for path in (
        README,
        WALKTHROUGH,
        FUTURES_VALIDATION,
        REPLAY_CONTRACT,
        HFT_REVIEWER_GUIDE,
        FUTURES_BENCHMARKS,
        SAMPLE_OUTPUTS_README,
    ):
        text = path.read_text(encoding="utf-8")
        assert "scripts/check_futures_determinism.py" in text
    assert expected_command in README.read_text(encoding="utf-8")
    assert expected_command in FUTURES_VALIDATION.read_text(encoding="utf-8")
    assert expected_command in REPLAY_CONTRACT.read_text(encoding="utf-8")
    assert expected_command in HFT_REVIEWER_GUIDE.read_text(encoding="utf-8")
    assert expected_command in SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")


def test_futures_pack_auditor_is_documented() -> None:
    expected_command = "python scripts/audit_futures_pack.py --committed-futures"

    assert FUTURES_PACK_AUDIT.exists()
    for path in (
        README,
        WALKTHROUGH,
        FUTURES_VALIDATION,
        REPLAY_CONTRACT,
        HFT_REVIEWER_GUIDE,
        SAMPLE_OUTPUTS_README,
    ):
        text = path.read_text(encoding="utf-8")
        assert "scripts/audit_futures_pack.py" in text
    assert expected_command in REPLAY_CONTRACT.read_text(encoding="utf-8")
    assert expected_command in HFT_REVIEWER_GUIDE.read_text(encoding="utf-8")
    assert expected_command in SAMPLE_OUTPUTS_README.read_text(encoding="utf-8")


def test_options_launchers_live_under_scripts_launchers() -> None:
    readme = README.read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "options_mm_demo_guide.md").read_text(encoding="utf-8")

    for path in ROOT_OPTIONS_LAUNCHERS:
        assert not path.exists()
    for path in CANONICAL_OPTIONS_LAUNCHERS:
        assert path.exists()

    assert r"scripts\launchers\run_options_case_study.bat" in readme
    assert "bash scripts/launchers/run_options_case_study.sh" in readme
    assert r"scripts\launchers\run_options_mm_case.bat" in readme
    assert r"scripts\launchers\run_options_mm_walkthrough_mode.bat" in readme

    assert "scripts/launchers/run_options_mm_walkthrough_mode.bat" in guide
    assert "bash scripts/launchers/run_options_mm_case.sh" in guide
    assert "scripts/launchers/run_options_mm_case.bat" in guide
    assert "scripts/launchers/run_options_case_study.bat" in guide
    assert "scripts/launchers/run_options_mm_quick.bat" in guide


def test_futures_benchmark_reference_is_published() -> None:
    benchmarks = FUTURES_BENCHMARKS.read_text(encoding="utf-8")
    benchmark_json = json.loads(FUTURES_BENCHMARK_REFERENCE.with_suffix(".json").read_text(encoding="utf-8"))

    assert FUTURES_BENCHMARK_REFERENCE.exists()
    assert "## Published Reference Run" in benchmarks
    assert "## Benchmark Tool" in benchmarks
    published_section = benchmarks.split("## Published Reference Run", 1)[1].split("## Benchmark Tool", 1)[0]
    assert "TBD" not in published_section
    assert "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" in published_section
    assert "benchmark_results/futures_replay_reference.md" in benchmarks
    assert "Instrument specs: `BTCUSDT`" in benchmarks
    assert "--mode all" in benchmarks
    assert "simulation without writing artifacts" in benchmarks
    assert "bounded simulation plus streamed event/fill/markout audits" in benchmarks
    assert "docs/sample_outputs/futures_stress_case" in benchmarks
    assert benchmark_json["schema_version"] == verifier.EXPECTED_BENCHMARK_SCHEMA_VERSION
    assert "config" in benchmark_json["metadata"]
    assert "instrument_specs" in benchmark_json["metadata"]
    assert benchmark_json["metadata"]["feed_adapter"] == verifier.EXPECTED_FUTURES_FEED_ADAPTER


def test_futures_strategy_profile_docs_are_published() -> None:
    profiles = FUTURES_STRATEGY_PROFILES.read_text(encoding="utf-8")
    reference = FUTURES_STRATEGY_REFERENCE.read_text(encoding="utf-8")
    sweep_reference = FUTURES_PARAMETER_SWEEP_REFERENCE.read_text(encoding="utf-8")

    assert FUTURES_STRATEGY_PROFILES.exists()
    assert FUTURES_STRATEGY_REFERENCE.exists()
    assert FUTURES_STRATEGY_REFRESH.exists()
    assert FUTURES_PARAMETER_SWEEP_REFERENCE.exists()
    assert FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.exists()
    assert FUTURES_PARAMETER_SWEEP_REFRESH.exists()
    assert FUTURES_LATENCY_SWEEP_REFERENCE.exists()
    assert FUTURES_LATENCY_SWEEP_REFERENCE_CSV.exists()
    assert FUTURES_LATENCY_SWEEP_REFRESH.exists()
    assert "baseline" in profiles
    assert "layered_mm" in profiles
    assert "research_mm" in profiles
    assert "futures_parameter_sweep_reference.md" in profiles
    assert "futures_parameter_sweep_reference.csv" in profiles
    assert "futures_latency_sweep_reference.md" in profiles
    assert "futures_latency_sweep_reference.csv" in profiles
    assert COMMITTED_STRATEGY_INPUT in reference
    assert "research_mm" in reference
    assert "python scripts/refresh_futures_strategy_profile_reference.py" in reference
    assert "local-only" not in reference
    assert "data/raw_1772633471.ndjson" not in reference
    assert "strategy-profile comparison" in reference
    assert COMMITTED_STRATEGY_INPUT in sweep_reference
    assert "python scripts/refresh_futures_parameter_sweep_reference.py" in sweep_reference
    assert "not an alpha or profitability claim" in sweep_reference
    assert "Git dirty at run time: `False`" in sweep_reference
    assert "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" in sweep_reference
    assert "Public-L2 fill model: `trade`" in sweep_reference
    assert "zero-fill diagnostic, not economic evidence" in sweep_reference
    latency_reference = FUTURES_LATENCY_SWEEP_REFERENCE.read_text(encoding="utf-8")
    assert COMMITTED_STRATEGY_INPUT in latency_reference
    assert "python scripts/refresh_futures_latency_sweep_reference.py" in latency_reference
    assert "modeled order-arrival and cancel-ack delays" in latency_reference
    assert "not a latency-arbitrage, alpha, or profitability claim" in latency_reference
    assert "Git dirty at run time: `False`" in latency_reference
    assert "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" in latency_reference
    assert "Public-L2 fill model: `trade`" in latency_reference
    assert "zero-fill diagnostic, not economic evidence" in latency_reference

    with FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with FUTURES_LATENCY_SWEEP_REFERENCE_CSV.open("r", encoding="utf-8", newline="") as handle:
        latency_rows = list(csv.DictReader(handle))

    assert len(rows) == 27
    assert [int(row["rank"]) for row in rows] == list(range(1, 28))
    assert {"baseline", "layered_mm", "research_mm"} <= {row["strategy_profile"] for row in rows}
    assert max(int(row["fill_count"]) for row in rows) == 0
    assert "fill_source_counts" in rows[0]
    assert "order_lifecycle_counts" in rows[0]
    assert len(latency_rows) == 9
    assert [int(row["rank"]) for row in latency_rows] == list(range(1, 10))
    assert {float(row["order_latency_ms"]) for row in latency_rows} == {0.0, 10.0, 50.0}
    assert {float(row["cancel_latency_ms"]) for row in latency_rows} == {0.0, 10.0, 50.0}
    assert max(int(row["fill_count"]) for row in latency_rows) == 0
    assert "avg_fill_wait_ms" in latency_rows[0]
    assert "fill_source_counts" in latency_rows[0]
    assert "order_lifecycle_counts" in latency_rows[0]
