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
    result = audit_futures_packs([SHOWCASE_PACK, RECORDED_PACK])

    assert result["schema_version"] == PACK_AUDIT_SCHEMA_VERSION
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["pack_count"] == 2
    assert [pack["pack_dir"] for pack in result["packs"]] == [
        "docs/sample_outputs/futures_replay_walkthrough",
        "docs/sample_outputs/futures_recorded_clip_case",
    ]
    assert [pack["counts"]["fill_rows"] for pack in result["packs"]] == [1, 1]
    assert result["packs"][1]["counts"]["queue_consumption_rows"] > 1000


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
    assert any(
        "summary.csv fill_count='99' does not match summary value 1" in issue
        for issue in result["issues"]
    )
    assert any("output_artifacts[summary_csv].sha256 is stale" in issue for issue in result["issues"])


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
        "trades.csv:2 fee='999' does not match summary.fills[0].fee='0.0000'" in issue
        for issue in result["issues"]
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
        "public_consumption_summary.agg_trade.queue_consumed_lots=99 does not match trace value 2"
        in result["issues"]
    )
