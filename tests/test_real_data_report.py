from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.replay.inspection import file_sha256
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from scripts.run_real_data_report import (
    RAW_DATA_POLICY,
    REAL_DATA_EVIDENCE_SCHEMA_VERSION,
    REAL_DATA_REPORT_SCHEMA_VERSION,
    REPO_ROOT,
    _build_evidence_quality,
    _fill_source_context,
    run_report,
)


FIXTURE = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "input_fixture.ndjson"


def _write_schema_v3_manifest(directory: Path) -> Path:
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    with SegmentedCaptureWriter(directory, "capture-test", compression="none") as writer:
        for sequence, row in enumerate(rows, start=1):
            timestamp_ns = int(float(row["ts_local"]) * 1_000_000_000)
            writer.write(
                EventEnvelope(
                    capture_id="capture-test",
                    schema_version=SCHEMA_V3,
                    venue="BINANCE_USDM",
                    instrument="BTCUSDT",
                    event_kind=str(row["type"]),
                    route="market" if row["type"] == "aggTrade" else "public",
                    recv_seq=sequence,
                    recv_wall_ns=timestamp_ns,
                    recv_monotonic_ns=timestamp_ns,
                    stream_epoch=1,
                    sync_epoch=1,
                    payload=dict(row["data"]),
                )
            )
    return directory / "capture-test.manifest.json"


def test_real_data_report_generation_writes_schema_and_report_only_publish(tmp_path: Path) -> None:
    publish_dir = tmp_path / "published"

    paths = run_report(
        input_path=FIXTURE,
        env_path=".env.example",
        out_dir=tmp_path / "outputs",
        label="tiny_fixture",
        runs=1,
        publish_dir=publish_dir,
    )

    assert paths["pack_dir"].is_dir()
    assert paths["simulation_run_dir"].is_dir()
    assert paths["inspection_json"].is_file()
    assert paths["audit_json"].is_file()
    assert paths["benchmark_json"].is_file()
    assert paths["published_report_md"] == publish_dir / "tiny_fixture.md"
    assert paths["published_report_json"] == publish_dir / "tiny_fixture.json"
    assert sorted(path.name for path in publish_dir.iterdir()) == ["tiny_fixture.json", "tiny_fixture.md"]

    payload = json.loads(paths["published_report_json"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == REAL_DATA_REPORT_SCHEMA_VERSION
    assert payload["schema_version"] == "lob_sim.real_data_report.v3"
    assert payload["raw_data_policy"] == RAW_DATA_POLICY
    assert payload["input"]["sha256"] == file_sha256(FIXTURE)
    assert payload["input"]["file_size_bytes"] == FIXTURE.stat().st_size
    assert payload["input"]["symbol"] == "BTCUSDT"
    assert len(payload["provenance"]["config_sha256"]) == 64
    assert payload["provenance"]["code_identity"]["complete"] is True
    assert payload["event_counts"]["records_processed"] == 6
    assert payload["public_trade_source_counts"] == {"unknown": 1}
    assert payload["fills"]["fill_count"] == 1
    assert set(payload["fills"]["fill_source_counts"]) == {"depth_update", "agg_trade", "taker_order"}
    assert "quote_fill_probability" in payload["fills"]
    assert "fills_per_quote_request" in payload["fills"]
    assert "fills_per_arrived_order" in payload["fills"]
    assert set(payload["fills"]["fill_source_context"]["shares"]) == {
        "depth_update",
        "agg_trade",
        "taker_order",
    }
    assert "gross_total_pnl" in payload["risk"]
    assert "total_fees" in payload["risk"]
    assert "valuation_complete" in payload["risk"]
    assert set(payload["markout_by_fill_source"]) == {"depth_update", "agg_trade", "taker_order"}
    assert payload["target_window"]["meets_target"] is False
    assert payload["target_window"]["env_overrides"]["COLLECT_SECONDS"] == "1800"
    assert payload["target_window"]["longer_run_commands"]
    assert payload["audit"]["ok"] is True
    assert payload["audit"]["mode"] == "bounded_streaming"
    assert payload["audit"]["memory_contract"]["memory_bounded_by_tape_duration"] is True
    assert len(payload["audit"]["artifact_bundle_sha256"]) == 64
    assert payload["simulation_export"]["mode"] == "bounded_streaming"
    assert payload["benchmark"]["schema_version"] == "lob_sim.reviewer_benchmark.v2"
    assert payload["validity"]["integrity"]["capture_schema_version"] == 1
    assert payload["evidence_quality"]["schema_version"] == REAL_DATA_EVIDENCE_SCHEMA_VERSION
    assert payload["evidence_quality"]["execution_claim_ready"] is False
    assert payload["evidence_quality"]["markout_claim_ready"] is False
    assert payload["evidence_quality"]["checks"]["schema_v3_receipt_identity"] is False
    assert payload["evidence_quality"]["claim_matrix"]["modeled_pnl"]["status"] == "diagnostic_only"
    assert (
        "missing_schema_v3_receipt_identity"
        in payload["evidence_quality"]["claim_matrix"]["capture_receipt_and_validity"]["reason_codes"]
    )

    pack_files = {path.name for path in paths["pack_dir"].iterdir()}
    assert {
        "event_trace.csv",
        "markouts.csv",
        "trades.csv",
        "summary.json",
        "summary.csv",
        "manifest.json",
    } <= pack_files
    assert "_INCOMPLETE.json" not in pack_files
    assert not any(path.name.endswith(".partial") for path in paths["pack_dir"].iterdir())
    pack_summary = json.loads((paths["pack_dir"] / "summary.json").read_text(encoding="utf-8"))
    pack_manifest = json.loads((paths["pack_dir"] / "manifest.json").read_text(encoding="utf-8"))
    assert pack_manifest["artifact_bundle"]["complete"] is True
    assert len(pack_manifest["artifact_bundle"]["sha256"]) == 64
    assert pack_summary["simulation_export"]["mode"] == "bounded_streaming"
    assert pack_summary["audit_retention"]["memory_bounded_by_tape_duration"] is True
    assert pack_summary["event_trace_retention"]["memory_bounded_by_tape_duration"] is True
    assert pack_summary["fills"] is None
    assert pack_summary["markout_events"] is None

    markdown = paths["published_report_md"].read_text(encoding="utf-8")
    assert "Plain Interpretation" in markdown
    assert "Negative or positive PnL is not the point" in markdown
    assert "Raw public trade event types inside `aggTrade` records" in markdown
    assert "raw NDJSON tape is not committed" in markdown
    assert "Meets 10-30 minute target: `false`" in markdown
    assert "## Evidence Gate" in markdown
    assert "Execution claim-ready: `false`" in markdown
    assert "python scripts/run_real_data_report.py" in markdown
    assert not any(path.suffix in {".csv", ".ndjson", ".gz"} for path in publish_dir.rglob("*"))


def test_fill_source_context_exposes_inferred_taker_mix_without_claiming_fill_truth() -> None:
    context = _fill_source_context({"fill_source_counts": {"depth_update": 2, "agg_trade": 1, "taker_order": 7}})

    assert context["count_total"] == 10
    assert context["shares"] == {"agg_trade": 0.1, "depth_update": 0.2, "taker_order": 0.7}
    assert context["taker_order_fraction"] == 0.7
    assert context["taker_order_dominated"] is True

    empty = _fill_source_context({"fill_source_counts": {"taker_order": 0}})
    assert empty["count_total"] == 0
    assert empty["shares"] == {"taker_order": None}
    assert empty["taker_order_fraction"] is None
    assert empty["taker_order_dominated"] is False


def test_real_data_report_validates_input_and_keeps_local_packs_out_of_docs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Real-data input does not exist"):
        run_report(
            input_path=tmp_path / "missing.ndjson",
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="missing",
            runs=1,
        )

    wrong_suffix = tmp_path / "raw.txt"
    wrong_suffix.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="NDJSON"):
        run_report(
            input_path=wrong_suffix,
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="wrong_suffix",
            runs=1,
        )

    with pytest.raises(ValueError, match="--out-dir writes local audit packs"):
        run_report(
            input_path=FIXTURE,
            env_path=".env.example",
            out_dir=REPO_ROOT / "docs" / "real_data_runs" / "bad_local_pack",
            label="bad",
            runs=1,
        )


def test_real_data_report_marks_pack_incomplete_when_independent_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.run_real_data_report.audit_futures_pack",
        lambda _: {"ok": False, "issue_count": 1, "issues": ["injected independent-audit failure"]},
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        run_report(
            input_path=FIXTURE,
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="audit_failure",
            runs=1,
        )

    sentinel = tmp_path / "outputs" / "audit_failure" / "pack" / "_INCOMPLETE.json"
    assert sentinel.is_file()
    payload = json.loads(sentinel.read_text(encoding="utf-8"))
    assert payload["reason"] == "derived pack failed its independent streaming audit"
    assert payload["audit_issue_count"] == 1


def test_real_data_report_keeps_pack_sentinel_when_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("injected copy failure")

    monkeypatch.setattr("scripts.run_real_data_report._atomic_copy", fail_copy)

    with pytest.raises(OSError, match="injected copy failure"):
        run_report(
            input_path=FIXTURE,
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="copy_failure",
            runs=1,
        )

    pack_dir = tmp_path / "outputs" / "copy_failure" / "pack"
    assert (pack_dir / "_INCOMPLETE.json").is_file()
    assert not (pack_dir / "trades.csv").is_file()


def test_real_data_report_accepts_segmented_schema_v3_capture_manifest(tmp_path: Path) -> None:
    manifest = _write_schema_v3_manifest(tmp_path / "capture")

    paths = run_report(
        input_path=manifest,
        env_path=".env.example",
        out_dir=tmp_path / "outputs",
        label="schema_v3",
        runs=1,
    )

    report = json.loads(paths["report_json"].read_text(encoding="utf-8"))
    audit = json.loads(paths["audit_json"].read_text(encoding="utf-8"))
    assert report["input"]["sha256"] == file_sha256(manifest)
    assert report["audit"]["mode"] == "bounded_streaming"
    assert report["simulation_export"]["mode"] == "bounded_streaming"
    assert audit["ok"] is True


def test_evidence_quality_keeps_clean_schema_v3_execution_distinct_from_research_claim() -> None:
    quality = _build_evidence_quality(
        summary={
            "integrity": {
                "capture_schema_version": 3,
                "clock": "receive_time",
                "last_receive_sequence": 12,
                "clock_invalidated": False,
                "clock_regressions_clamped": 0,
                "receive_clock_regressions": 0,
                "capture_trailer_seen": True,
                "capture_valid": True,
                "all_required_execution_inputs_valid_at_end": True,
                "book_invalidations": 0,
                "claim_ready": True,
            },
            "evidence_quality": {"markouts": "claim_ready"},
            "claim_matrix": {"fill_truth": "scenario_envelope_only"},
            "trade_stream_invalidation_count": 0,
            "valuation_complete": True,
        },
        audit_result={"ok": True},
        meets_target=True,
    )

    assert quality["execution_claim_ready"] is True
    assert quality["markout_claim_ready"] is True
    assert quality["claim_matrix"]["capture_receipt_and_validity"]["status"] == "claim_ready"
    assert quality["claim_matrix"]["subsecond_markouts"]["status"] == "claim_ready"
    assert quality["claim_matrix"]["modeled_pnl"]["status"] == "diagnostic_only"
    assert quality["claim_matrix"]["strategy_or_profitability"]["status"] == "diagnostic_only"


def test_evidence_quality_reports_each_failed_validity_dimension() -> None:
    quality = _build_evidence_quality(
        summary={
            "integrity": {
                "capture_schema_version": 3,
                "clock": "receive_time",
                "last_receive_sequence": 12,
                "clock_invalidated": True,
                "clock_regressions_clamped": 1,
                "receive_clock_regressions": 1,
                "capture_trailer_seen": False,
                "capture_valid": False,
                "all_required_execution_inputs_valid_at_end": False,
                "book_invalidations": 1,
                "claim_ready": False,
            },
            "evidence_quality": {"markouts": "diagnostic_only", "markout_reason": "gap boundary"},
            "trade_stream_invalidation_count": 1,
            "valuation_complete": False,
        },
        audit_result={"ok": False},
        meets_target=False,
    )

    reasons = quality["claim_matrix"]["capture_receipt_and_validity"]["reason_codes"]
    assert quality["execution_claim_ready"] is False
    assert quality["checks"]["independent_pack_audit"] is False
    assert {
        "invalid_or_regressing_receive_clock",
        "capture_trailer_missing",
        "capture_invalidated",
        "execution_inputs_invalid_at_end",
        "book_invalidations_observed",
        "trade_stream_invalidations_observed",
        "independent_pack_audit_failed",
    } <= set(reasons)
    assert "gap boundary" in quality["claim_matrix"]["subsecond_markouts"]["reason_codes"]
    assert "valuation_incomplete_or_missing_marks" in quality["claim_matrix"]["modeled_pnl"]["reason_codes"]
