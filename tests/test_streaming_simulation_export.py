from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from lob_sim.config import Config, load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.export import TRADE_AUDIT_FIELDS, verify_streaming_audit_files
from lob_sim.sim.runner import run_bounded_simulation


REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Config:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    monkeypatch.setenv("MM_REQUOTE_MS", "1000")
    monkeypatch.setenv("SIM_ORDER_LATENCY_MS", "0")
    monkeypatch.setenv("SIM_CANCEL_LATENCY_MS", "0")
    monkeypatch.setenv("SIM_ADVERSE_MARKOUT_SECONDS", "0.5")
    return load_config(str(REPO_ROOT / ".env.example"))


def _write_fill_fixture(path: Path) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.010")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.010"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_bounded_simulation_streams_complete_hashed_audits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(monkeypatch, tmp_path)
    input_path = _write_fill_fixture(tmp_path / "streaming.ndjson")

    output_files, summary = run_bounded_simulation(cfg, input_path)

    assert set(output_files) == {"event_trace", "markouts", "summary", "summary_csv", "trades", "manifest"}
    run_dir = output_files["manifest"].parent
    assert run_dir.name.startswith(f"run_streaming_{summary['run_id']}_")
    assert not (run_dir / "_INCOMPLETE.json").exists()
    assert not list(run_dir.glob("*.partial"))
    assert all(path.is_file() for path in output_files.values())

    on_disk = json.loads(output_files["summary"].read_text(encoding="utf-8"))
    assert on_disk == summary
    assert summary["fills"] is None
    assert summary["markout_events"] is None
    assert summary["simulation_export"] == {
        "schema_version": "lob_sim.simulation_export.v1",
        "mode": "bounded_streaming",
        "memory_bounded_by_tape_duration": True,
        "detail_rows_complete_in_summary": False,
        "detail_rows_streamed": True,
        "markout_audit_file": "markouts",
        "completion_record": "manifest_with_absent_incomplete_sentinel",
        "intended_use": "ordinary_and_large_tape_simulation",
    }
    assert summary["event_trace_retention"]["rows_retained"] == 0
    assert summary["event_trace_retention"]["sink"] == "StreamingCsvSink"
    assert summary["event_trace_retention"]["memory_bounded_by_tape_duration"] is True
    assert summary["audit_retention"]["mode"] == "streaming"
    assert summary["audit_retention"]["fill_rows_retained"] == 0
    assert summary["audit_retention"]["markout_rows_retained"] == 0
    assert summary["audit_retention"]["memory_bounded_by_tape_duration"] is True

    trace_rows = _read_csv(output_files["event_trace"])
    trade_rows = _read_csv(output_files["trades"])
    markout_rows = _read_csv(output_files["markouts"])
    assert len(trace_rows) == summary["event_trace_count"]
    assert len(trade_rows) == summary["fill_count"] == 1
    assert len(markout_rows) == summary["audit_retention"]["markout_rows_emitted"] == 1
    assert [int(row["seq"]) for row in trace_rows] == list(range(len(trace_rows)))
    assert [float(row["ts_local"]) for row in trace_rows] == sorted(float(row["ts_local"]) for row in trace_rows)
    assert json.loads(trade_rows[0]["evidence_ids"])
    assert json.loads(trade_rows[0]["validity"])["execution_valid"] is True
    assert markout_rows[0]["status"] == "resolved"

    manifest = json.loads(output_files["manifest"].read_text(encoding="utf-8"))
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["input"]["sha256"] == file_sha256(input_path)
    assert manifest["config_sha256"] == summary["config_sha256"]
    assert manifest["code_identity"] == summary["code_identity"]
    assert manifest["code_identity"]["complete"] is True
    assert len(manifest["code_identity"]["sha256"]) == 64
    assert manifest["artifact_bundle"]["schema_version"] == "lob_sim.artifact_bundle.v1"
    assert manifest["artifact_bundle"]["complete"] is True
    assert manifest["artifact_bundle"]["artifact_count"] == 5
    assert len(manifest["artifact_bundle"]["sha256"]) == 64
    for label, path in output_files.items():
        if label == "manifest":
            assert manifest["output_artifacts"][label] == {"path": str(path)}
        else:
            assert manifest["output_artifacts"][label]["sha256"] == file_sha256(path)

    trade_rows[0]["qty"] = "9"
    with output_files["trades"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(trade_rows)
    with pytest.raises(RuntimeError, match="serialized simulation audit mismatch"):
        verify_streaming_audit_files(
            output_files,
            event_trace_count=summary["event_trace_count"],
            fill_count=summary["fill_count"],
            fill_sha256=summary["audit_retention"]["fill_audit_sha256"],
            markout_count=summary["audit_retention"]["markout_rows_emitted"],
            markout_sha256=summary["audit_retention"]["markout_audit_sha256"],
        )


def test_failed_simulation_leaves_only_partials_and_incomplete_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(monkeypatch, tmp_path)
    input_path = _write_fill_fixture(tmp_path / "failed.ndjson")

    def fail_run(self: SimulationEngine, *args: object, **kwargs: object) -> None:
        del self, args, kwargs
        raise RuntimeError("injected engine failure")

    monkeypatch.setattr(SimulationEngine, "run", fail_run)
    with pytest.raises(RuntimeError, match="injected engine failure"):
        run_bounded_simulation(cfg, input_path)

    run_dirs = list((tmp_path / "outputs").glob("run_failed_*"))
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "_INCOMPLETE.json").is_file()
    assert sorted(path.name for path in run_dir.glob("*.partial")) == [
        "event_trace.csv.partial",
        "markouts.csv.partial",
        "trades.csv.partial",
    ]
    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "summary.json").exists()
    assert not (run_dir / "event_trace.csv").exists()


def test_finalization_failure_keeps_completion_sentinel_and_withholds_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(monkeypatch, tmp_path)
    input_path = _write_fill_fixture(tmp_path / "finalize_failed.ndjson")

    def fail_summary_csv(path: Path, summary: object) -> None:
        del path, summary
        raise RuntimeError("injected summary failure")

    monkeypatch.setattr("lob_sim.sim.engine.atomic_write_summary_csv", fail_summary_csv)
    with pytest.raises(RuntimeError, match="injected summary failure"):
        run_bounded_simulation(cfg, input_path)

    run_dir = next((tmp_path / "outputs").glob("run_finalize_failed_*"))
    assert (run_dir / "_INCOMPLETE.json").is_file()
    assert (run_dir / "event_trace.csv").is_file()
    assert (run_dir / "trades.csv").is_file()
    assert (run_dir / "markouts.csv").is_file()
    assert (run_dir / "summary.json").is_file()
    assert not (run_dir / "manifest.json").exists()


def test_clock_regressions_are_streamed_in_logical_order_with_raw_time_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(monkeypatch, tmp_path)
    input_path = tmp_path / "clock_regression.ndjson"
    records = [
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.5,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(10, [("100.0", "0.001")], [("100.1", "0.001")]),
        ),
    ]
    input_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    output_files, summary = run_bounded_simulation(cfg, input_path)
    rows = _read_csv(output_files["event_trace"])
    market_rows = [row for row in rows if row["event_type"] == "market_record"]

    assert summary["integrity"]["clock_regressions_clamped"] == 1
    assert [float(row["ts_local"]) for row in rows] == sorted(float(row["ts_local"]) for row in rows)
    snapshot_details = json.loads(next(row["details"] for row in market_rows if row["source"] == "snapshot"))
    assert snapshot_details["clock_clamped"] is True
    assert snapshot_details["observed_ts_local"] == 1.5
