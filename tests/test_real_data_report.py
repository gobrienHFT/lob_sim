from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.replay.inspection import file_sha256
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from scripts.run_real_data_report import RAW_DATA_POLICY, REAL_DATA_REPORT_SCHEMA_VERSION, REPO_ROOT, run_report


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
    assert "python scripts/run_real_data_report.py" in markdown
    assert not any(path.suffix in {".csv", ".ndjson", ".gz"} for path in publish_dir.rglob("*"))


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
