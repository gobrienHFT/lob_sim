from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.audit.streaming_bundle import STREAMING_BUNDLE_AUDIT_SCHEMA_VERSION, audit_streaming_bundle
from lob_sim.config import load_config
from lob_sim.sim.export import (
    EVENT_TRACE_FIELDS as EXPORT_EVENT_TRACE_FIELDS,
    MARKOUT_AUDIT_FIELDS as EXPORT_MARKOUT_AUDIT_FIELDS,
    TRADE_AUDIT_FIELDS as EXPORT_TRADE_AUDIT_FIELDS,
)
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.audit import streaming_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "input_fixture.ndjson"


def test_depth_only_disabled_trade_is_ignored_not_falsely_reported_as_netted(tmp_path):
    config = replace(
        load_config(str(REPO_ROOT / ".env.example"), inherit_environment=False),
        sim_fill_model="depth",
        record_dir=tmp_path,
    )
    files, summary = run_bounded_simulation(config, FIXTURE)
    assert config.hmm is None and summary["fill_assumption"]["agg_trades_consume_queue"] is False
    with files["event_trace"].open(encoding="utf-8", newline="") as handle:
        ignored = [
            json.loads(row["details"])
            for row in csv.DictReader(handle)
            if row["event_type"] == "queue_consumption" and row["source"] == "agg_trade"
        ]
    assert any(row["observed_lots"] > row["overlap_netted_lots"] and row["modeled_lots"] == 0 for row in ignored)
    assert audit_streaming_bundle(files["manifest"].parent)["ok"]


@pytest.mark.parametrize(
    "changes",
    [
        {"modeled_lots": 1},
        {"queue_consumed_lots": 1},
        {"unmatched_lots": 1},
        {"overlap_netted_lots": 21},
    ],
)
def test_disabled_trade_audit_still_rejects_consumption_or_impossible_overlap(changes):
    issues = streaming_bundle._Issues()
    details = {
        "observed_lots": 20,
        "modeled_lots": 0,
        "queue_consumed_lots": 0,
        "unmatched_lots": 0,
        "overlap_netted_lots": 4,
    }
    details.update(changes)
    streaming_bundle._audit_queue_consumption(
        Path("trace.csv"), 2, "agg_trade", details, streaming_bundle._TraceState(), issues, trade_consumes_queue=False
    )
    assert issues.total > 0


def test_enabled_trade_audit_does_not_accept_the_disabled_signal_exception():
    issues = streaming_bundle._Issues()
    streaming_bundle._audit_queue_consumption(
        Path("trace.csv"),
        2,
        "agg_trade",
        {
            "observed_lots": 20,
            "modeled_lots": 0,
            "queue_consumed_lots": 0,
            "unmatched_lots": 0,
            "overlap_netted_lots": 4,
        },
        streaming_bundle._TraceState(),
        issues,
    )
    assert any("netted lots" in message for message in issues.messages)


@pytest.mark.parametrize("oversized", [False, True])
def test_aggregate_summary_csv_has_a_finite_budget_and_restores_global_limit(tmp_path, oversized):
    from lob_sim.audit.csv_limits import MAX_AGGREGATE_FIELD_CHARS

    summary = {
        name: "same"
        for name in ("strategy_profile", "fill_assumption_profile", "run_id", "input_sha256", "config_sha256")
    }
    summary.update(
        {
            name: 0
            for name in (
                "fill_count",
                "quote_count",
                "cancel_count",
                "self_trade_prevention_count",
                "event_trace_count",
            )
        }
    )
    row = {**summary, "bounded_aggregate": "x" * (MAX_AGGREGATE_FIELD_CHARS + 1 if oversized else 140_000)}
    with (tmp_path / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    before = csv.field_size_limit()
    issues = streaming_bundle._Issues()
    streaming_bundle._audit_summary_csv(tmp_path, summary, issues)
    assert csv.field_size_limit() == before
    assert any("could not be read" in message for message in issues.messages) == oversized


def _create_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "records"))
    paths, summary = run_bounded_simulation(load_config(str(REPO_ROOT / ".env.example")), FIXTURE)
    return paths["manifest"].parent, summary


def test_independent_auditor_schema_matches_published_export_schema() -> None:
    assert streaming_bundle.EVENT_TRACE_FIELDS == EXPORT_EVENT_TRACE_FIELDS
    assert streaming_bundle.TRADE_AUDIT_FIELDS == EXPORT_TRADE_AUDIT_FIELDS
    assert streaming_bundle.MARKOUT_AUDIT_FIELDS == EXPORT_MARKOUT_AUDIT_FIELDS


def test_streaming_bundle_audit_recomputes_exact_contract_without_detail_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, summary = _create_bundle(tmp_path, monkeypatch)

    result = audit_streaming_bundle(bundle)

    assert result["schema_version"] == STREAMING_BUNDLE_AUDIT_SCHEMA_VERSION
    assert result["audit_mode"] == "bounded_streaming"
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["issue_count"] == 0
    assert result["memory_contract"] == {
        "schema_version": "lob_sim.streaming_audit_memory.v1",
        "detail_rows_retained": 0,
        "csv_processing": "sequential_rows",
        "exact_set_storage": "temporary_on_disk_sqlite",
        "diagnostic_limit": 250,
        "memory_bounded_by_tape_duration": True,
    }
    assert result["counts"]["event_trace_rows"] == summary["event_trace_count"]
    assert result["counts"]["trade_rows"] == summary["fill_count"]
    assert result["counts"]["fill_rows"] == summary["fill_count"]
    assert result["counts"]["markout_rows"] == summary["audit_retention"]["markout_rows_emitted"]
    assert result["hashes"]["fill_audit_sha256"] == summary["audit_retention"]["fill_audit_sha256"]
    assert result["hashes"]["markout_audit_sha256"] == summary["audit_retention"]["markout_audit_sha256"]
    assert len(result["hashes"]["artifact_bundle_sha256"]) == 64


def test_streaming_bundle_audit_fails_closed_on_incomplete_or_partial_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _create_bundle(tmp_path, monkeypatch)
    (bundle / "_INCOMPLETE.json").write_text("{}\n", encoding="utf-8")
    (bundle / "trades.csv.partial").write_text("unfinished\n", encoding="utf-8")

    result = audit_streaming_bundle(bundle)

    assert result["ok"] is False
    assert any("is incomplete" in issue for issue in result["issues"])
    assert any("unfinished artifact trades.csv.partial" in issue for issue in result["issues"])


def test_streaming_bundle_audit_detects_serialized_fill_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _create_bundle(tmp_path, monkeypatch)
    trades_path = bundle / "trades.csv"
    with trades_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = tuple(rows[0])
    rows[0]["fee_model_id"] = "tampered"
    with trades_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_streaming_bundle(bundle)

    assert result["ok"] is False
    assert any("invalid fee_model_id" in issue for issue in result["issues"])
    assert any("fill_audit_sha256" in issue for issue in result["issues"])
    assert any("output_artifacts[trades].sha256 is stale" in issue for issue in result["issues"])
    assert any("artifact_bundle" in issue for issue in result["issues"])


def test_streaming_bundle_audit_rejects_manifest_claim_gate_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _create_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["claim_gate"]["execution_claim_ready"] = not manifest["claim_gate"]["execution_claim_ready"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_streaming_bundle(bundle)

    assert result["ok"] is False
    assert any("claim_gate does not match summary.json" in issue for issue in result["issues"])


def test_streaming_bundle_audit_caps_corruption_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _create_bundle(tmp_path, monkeypatch)
    trace_path = bundle / "event_trace.csv"
    with trace_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = tuple(rows[0])
    for row in rows:
        row["seq"] = "broken"
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_streaming_bundle(bundle, max_issues=5)

    assert result["ok"] is False
    assert len(result["issues"]) == 5
    assert result["issue_count"] > len(result["issues"])
    assert result["issues_omitted"] == result["issue_count"] - len(result["issues"])


def test_streaming_bundle_audit_rejects_summary_detail_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _create_bundle(tmp_path, monkeypatch)
    summary_path = bundle / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills"] = [{"fabricated": True}]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = audit_streaming_bundle(bundle)

    assert result["ok"] is False
    assert any("must not retain fill or markout detail rows" in issue for issue in result["issues"])


def test_streaming_bundle_audit_accepts_null_markout_invalidated_by_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "records"))
    rows = FIXTURE.read_text(encoding="utf-8").splitlines()[:5]
    rows.append(
        json.dumps(
            {
                "ts_local": 2.5,
                "symbol": "BTCUSDT",
                "type": "depthUpdate",
                "data": {"U": 999, "u": 999, "pu": 998, "b": [], "a": []},
            }
        )
    )
    fixture = tmp_path / "gap_invalidated_markout.ndjson"
    fixture.write_text("\n".join(rows) + "\n", encoding="utf-8")
    paths, summary = run_bounded_simulation(load_config(str(REPO_ROOT / ".env.example")), fixture)

    result = audit_streaming_bundle(paths["manifest"].parent)

    assert summary["markout_invalidated_count"] == 1
    assert summary["markout_resolved_count"] == 0
    assert result["ok"] is True
    assert result["counts"]["invalidated_markouts"] == 1
    assert result["counts"]["resolved_markouts"] == 0
