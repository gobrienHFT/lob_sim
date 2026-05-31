from __future__ import annotations

import csv
import json
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
FUTURES_BENCHMARKS = REPO_ROOT / "docs" / "futures_benchmarks.md"
FUTURES_BENCHMARK_REFERENCE = (
    REPO_ROOT / "docs" / "benchmark_results" / "futures_replay_reference.md"
)
FUTURES_STRATEGY_PROFILES = REPO_ROOT / "docs" / "futures_strategy_profiles.md"
FUTURES_STRATEGY_REFERENCE = (
    REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md"
)
FUTURES_PARAMETER_SWEEP_REFERENCE = (
    REPO_ROOT / "docs" / "strategy_results" / "futures_parameter_sweep_reference.md"
)
FUTURES_PARAMETER_SWEEP_REFERENCE_CSV = (
    REPO_ROOT / "docs" / "strategy_results" / "futures_parameter_sweep_reference.csv"
)
FUTURES_STRATEGY_REFRESH = (
    REPO_ROOT / "scripts" / "refresh_futures_strategy_profile_reference.py"
)
FUTURES_PARAMETER_SWEEP_REFRESH = (
    REPO_ROOT / "scripts" / "refresh_futures_parameter_sweep_reference.py"
)
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMMITTED_STRATEGY_INPUT = "docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson"
FUTURES_WALKTHROUGH_README = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "README.md"
)
FUTURES_WALKTHROUGH_NOTES = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "walkthrough.md"
)
RECORDED_CASE_README = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "README.md"
)
RECORDED_CASE_NOTES = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "case_notes.md"
)
COMMITTED_CASE_BRIEF = (
    REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "case_brief.md"
)
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
        "data_scope": "public_l2_order_book_and_agg_trade_records",
        "private_exchange_execution_reports": False,
        "queue_priority_model": "visible_price_time_fifo",
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


def test_futures_trade_audit_verifier_requires_notional_and_multiplier(tmp_path, monkeypatch) -> None:
    showcase = tmp_path / "showcase"
    recorded = tmp_path / "recorded"
    showcase.mkdir()
    recorded.mkdir()

    with (showcase / "trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["fill_source", "fee_bps", "fee", "fee_currency"])
        writer.writeheader()
        writer.writerow({"fill_source": "depth_update", "fee_bps": "0", "fee": "0", "fee_currency": "USDT"})
    with (recorded / "trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(verifier.FUTURES_TRADE_AUDIT_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                "fill_source": "agg_trade",
                "notional": "100.0",
                "contract_multiplier": "1",
                "fee_bps": "0",
                "fee": "0",
                "fee_currency": "USDT",
            }
        )
    monkeypatch.setattr(verifier, "FUTURES_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(verifier, "RECORDED_CLIP_DIR", recorded)
    monkeypatch.setattr(verifier, "_repo_relative", lambda path: str(path))

    issues = verifier._verify_futures_trade_audit_fields()

    assert issues == [
        f"{showcase / 'trades.csv'} is missing trade audit column(s): contract_multiplier, notional"
    ]


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

    assert issues == [
        f"{showcase / 'summary.json'} public_consumption_summary.total_observed_lots is inconsistent"
    ]


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
    avg_arrival_queue_ahead_lots = (
        arrival_queue_ahead_sum / arrival_queue_samples if arrival_queue_samples else 0.0
    )
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

    assert (
        f"{showcase / 'event_trace.csv'} lifecycle arrival_scheduled=1 does not match summary value 0"
        in issues
    )


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

    assert (
        f"{showcase / 'event_trace.csv'} max_arrival_queue_ahead_lots=3 does not match summary value 0"
        in issues
    )


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
                    '"queue_consumed_lots":6,"unmatched_lots":0,"overlap_window_seconds":999}'
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
                    '"queue_consumed_lots":1,"unmatched_lots":0,"overlap_window_seconds":0.125}'
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
        f"{showcase / 'event_trace.csv'} queue_consumption depth_update.observed_lots=2 "
        "does not match summary value 0"
    ) in issues


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
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in pyproject
        assert f'"{version}"' in workflow
    assert "python -m pip install -r requirements.txt" in workflow
    assert "python -m pip check" in workflow
    assert "python -m lob_sim.cli --help" in workflow
    assert "python -m pytest -q" in workflow
    assert "python scripts/verify_committed_artifacts.py" in workflow
    assert "git diff --check" in workflow
    assert "MPLBACKEND: Agg" in workflow
    assert "ci: test verify-artifacts check-whitespace" in makefile
    assert "refresh-artifacts:" in makefile
    assert "scripts/refresh_futures_reviewer_artifacts.py" in makefile


def test_committed_stress_fill_includes_units_explanation() -> None:
    case_brief = COMMITTED_CASE_BRIEF.read_text(encoding="utf-8")
    demo_report = COMMITTED_DEMO_REPORT.read_text(encoding="utf-8")

    assert "This is not a units mismatch" in case_brief
    assert "This is not a units mismatch" in demo_report


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
    walkthrough_five_minute = walkthrough.split("## 5-Minute Walkthrough", 1)[1].split(
        "## Core Talking Points", 1
    )[0]

    assert "docs/sample_outputs/futures_replay_walkthrough/README.md" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/summary.json" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/trades.csv" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/event_trace.csv" in readme
    assert "docs/sample_outputs/futures_replay_walkthrough/walkthrough.md" in readme
    assert "docs/sample_outputs/futures_recorded_clip_case/README.md" in readme
    assert "docs/futures_strategy_profiles.md" in readme
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in readme
    assert "docs/strategy_results/futures_parameter_sweep_reference.md" in readme
    assert "docs/benchmark_results/futures_replay_reference.md" in readme
    assert readme_walkthrough.index("docs/sample_outputs/futures_recorded_clip_case/README.md") < readme_walkthrough.index(
        "docs/futures_strategy_profiles.md"
    )
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
    assert "docs/futures_strategy_profiles.md" in walkthrough
    assert "docs/strategy_results/futures_strategy_profile_reference.md" in walkthrough
    assert "docs/strategy_results/futures_parameter_sweep_reference.md" in walkthrough
    assert "docs/benchmark_results/futures_replay_reference.md" in walkthrough
    assert walkthrough_five_minute.index("docs/sample_outputs/futures_recorded_clip_case/README.md") < walkthrough_five_minute.index(
        "docs/futures_strategy_profiles.md"
    )
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
    assert "baseline" in profiles
    assert "layered_mm" in profiles
    assert "research_mm" in profiles
    assert "futures_parameter_sweep_reference.md" in profiles
    assert "futures_parameter_sweep_reference.csv" in profiles
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

    with FUTURES_PARAMETER_SWEEP_REFERENCE_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 27
    assert [int(row["rank"]) for row in rows] == list(range(1, 28))
    assert {"baseline", "layered_mm", "research_mm"} <= {row["strategy_profile"] for row in rows}
    assert max(int(row["fill_count"]) for row in rows) > 0
    assert "fill_source_counts" in rows[0]
    assert "order_lifecycle_counts" in rows[0]
