from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from scripts.audit_futures_pack import (
    PACK_AUDIT_SCHEMA_VERSION,
    audit_futures_pack,
    audit_futures_packs,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_PACK = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough"
RECORDED_PACK = REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case"
STRESS_PACK = REPO_ROOT / "docs" / "sample_outputs" / "futures_stress_case"


def test_committed_futures_showcase_pack_audit_passes() -> None:
    result = audit_futures_pack(SHOWCASE_PACK)

    assert result["schema_version"] == PACK_AUDIT_SCHEMA_VERSION
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["counts"]["fill_rows"] == 1
    assert result["counts"]["queue_consumption_rows"] == 2
    assert result["counts"]["markout_rows"] == 1
    assert result["summary"]["feed_adapter"] == {
        "name": "binance_usdm",
        "venue_label": "BINANCE_USDM",
        "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
    }


def test_committed_futures_pack_collection_audit_passes() -> None:
    result = audit_futures_packs([SHOWCASE_PACK, RECORDED_PACK, STRESS_PACK])

    assert result["schema_version"] == PACK_AUDIT_SCHEMA_VERSION
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["pack_count"] == 3
    assert [pack["pack_dir"] for pack in result["packs"]] == [
        "docs/sample_outputs/futures_replay_walkthrough",
        "docs/sample_outputs/futures_recorded_clip_case",
        "docs/sample_outputs/futures_stress_case",
    ]
    assert [pack["counts"]["fill_rows"] for pack in result["packs"]] == [1, 0, 1]
    assert result["packs"][1]["counts"]["queue_consumption_rows"] > 1000
    assert result["packs"][2]["counts"]["event_type_counts"]["cancel_ack"] == 2
    assert result["packs"][2]["counts"]["event_type_counts"]["markout"] == 1


def test_futures_pack_collection_audit_rejects_empty_input() -> None:
    result = audit_futures_packs([])

    assert result == {
        "schema_version": PACK_AUDIT_SCHEMA_VERSION,
        "ok": False,
        "pack_count": 0,
        "packs": [],
        "issues": ["No futures packs supplied"],
    }


def test_futures_pack_audit_rejects_stale_summary_csv(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_csv_path = copied_pack / "summary.csv"
    with summary_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    rows[0]["fill_count"] = "99"
    with summary_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any("summary.csv fill_count='99' does not match summary value 1" in issue for issue in result["issues"])
    assert any("output_artifacts[summary_csv].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_stale_replay_event_count(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["event_counts"]["agg_trade"] = 99
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert "event_counts.agg_trade=99 does not match trace source aggTrade count 1" in result["issues"]
    assert "event_counts.agg_trade=99 does not match replay input count 1" in result["issues"]
    assert any("output_artifacts[summary].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_stale_trade_export(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    trades_path = copied_pack / "trades.csv"
    with trades_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    rows[0]["fee"] = "999"
    with trades_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "trades.csv:2 fee='999' does not match summary.fills[0].fee='0.0000'" in issue for issue in result["issues"]
    )
    assert any("output_artifacts[trades].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_stale_fill_trace_economics(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    trace_path = copied_pack / "event_trace.csv"
    with trace_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    fill_row = next(row for row in rows if row["event_type"] == "fill")
    details = json.loads(fill_row["details"])
    details["spread_capture"] = "999"
    fill_row["details"] = json.dumps(details, sort_keys=True)
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "details.spread_capture='999' does not match summary.fills[0].spread_capture='0.05'" in issue
        for issue in result["issues"]
    )
    assert any("output_artifacts[event_trace].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_unresolved_fill_evidence(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills"][0]["evidence_ids"] = ["input_row:999999"]
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert "summary.fills[0] has unresolved evidence_ids: ['input_row:999999']" in result["issues"]


def test_futures_pack_audit_rejects_scenario_drift(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills"][0]["scenario_id"] = "public_l2:unknown"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "summary.fills[0] scenario_id='public_l2:unknown' does not match run assumptions" in issue
        for issue in result["issues"]
    )


def test_futures_pack_audit_rejects_inconsistent_fill_validity(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills"][0]["validity"]["execution_valid"] = False
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert "summary.fills[0] validity has inconsistent execution_valid" in result["issues"]
    assert "summary.fill_provenance.execution_valid does not match summary.fills" in result["issues"]


def test_futures_pack_audit_rejects_measured_latency_claim(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills"][0]["latency_model"]["measured"] = True
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert "summary.fills[0] latency_model must not claim measurement" in result["issues"]


def test_futures_pack_audit_rejects_stale_summary_markout_event(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["markout_events"][0]["mid_after"] = "999"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "details.mid_after='100.05' does not match summary.markout_events[0].mid_after='999'" in issue
        for issue in result["issues"]
    )
    assert any("output_artifacts[summary].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_tampered_audit_chain_identity(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["audit_retention"]["fill_audit_sha256"] = "0" * 64
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "summary.audit_retention.fill_audit_sha256=" in issue and "does not match expected" in issue
        for issue in result["issues"]
    )


def test_futures_pack_audit_rejects_stale_markout_trace_event(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    trace_path = copied_pack / "event_trace.csv"
    with trace_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    markout_row = next(row for row in rows if row["event_type"] == "markout")
    details = json.loads(markout_row["details"])
    details["markout"] = "999"
    markout_row["details"] = json.dumps(details, sort_keys=True)
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "details.markout='999' does not match summary.markout_events[0].markout='0.05'" in issue
        for issue in result["issues"]
    )
    assert any("output_artifacts[event_trace].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_private_execution_assumption(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    for filename in ("summary.json", "manifest.json"):
        path = copied_pack / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["simulation_assumptions"]["private_exchange_execution_reports"] = True
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any(
        "simulation_assumptions must not claim private exchange execution reports" in issue
        for issue in result["issues"]
    )
    assert any("summary.csv simulation_assumptions does not match summary.json" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_stale_summary_count(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fill_count"] = 99
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert "trades.csv has 1 row(s), summary expected 99" in result["issues"]
    assert "event_trace.csv has 1 fill row(s), summary expected 99" in result["issues"]
    assert any("output_artifacts[summary].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_ambiguous_fill_rate_metric(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fill_rate"] = 99.0
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any("uses ambiguous fill_rate" in issue for issue in result["issues"])


def test_futures_pack_audit_allows_explicitly_deprecated_fill_rate_marker(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fill_rate"] = summary["quote_fill_probability"]
    summary["deprecated_fields"] = {
        "fill_rate": {
            "status": "deprecated",
            "replacement_fields": [
                "quote_fill_probability",
                "fills_per_quote_request",
                "fills_per_arrived_order",
            ],
        }
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert not any("uses ambiguous fill_rate" in issue for issue in result["issues"])
    assert any("output_artifacts[summary].sha256 is stale" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_stale_fixture_provenance(tmp_path: Path) -> None:
    copied_pack = tmp_path / "futures_recorded_clip_case"
    shutil.copytree(RECORDED_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fixture_provenance"]["data_class"] = "synthetic"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert any("fixture_provenance.data_class must be recorded_public_data" in issue for issue in result["issues"])


def test_futures_pack_audit_rejects_queue_consumption_mismatch(tmp_path: Path) -> None:
    copied_pack = tmp_path / "pack"
    shutil.copytree(SHOWCASE_PACK, copied_pack)
    summary_path = copied_pack / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["public_consumption_summary"]["sources"]["agg_trade"]["queue_consumed_lots"] = 99
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    result = audit_futures_pack(copied_pack)

    assert result["ok"] is False
    assert (
        "public_consumption_summary.agg_trade.queue_consumed_lots=99 does not match trace value 3" in result["issues"]
    )
