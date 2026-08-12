from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lob_sim.cli import _EnvelopeRecordWriter, _capture_trailer_record, _write_capture_failure_report
from lob_sim.oracle import Checkpoint, read_checkpoint, state_hash, write_checkpoint
from lob_sim.record.envelope import EventEnvelope, LogicalTime, SCHEMA_V3, ValidityState, payload_checksum
from lob_sim.record.format import NDJSONRecord
from lob_sim.record.segmented import SegmentedCaptureWriter, recover_valid_envelopes, validate_segment
from lob_sim.replay.reader import iter_records
from lob_sim.replay.arrow_store import arrow_metadata, iter_arrow_rows, normalize_to_arrow
from lob_sim.sim.latency import LatencyModel


def _envelope(sequence: int, *, wall_ns: int | None = None) -> EventEnvelope:
    return EventEnvelope(
        capture_id="capture-1",
        schema_version=SCHEMA_V3,
        venue="BINANCE_USDM",
        instrument="BTCUSDT",
        event_kind="depthUpdate",
        route="public",
        recv_seq=sequence,
        recv_wall_ns=wall_ns if wall_ns is not None else 1_000_000_000 + sequence,
        recv_monotonic_ns=5_000 + sequence,
        stream_epoch=1,
        sync_epoch=2,
        payload={"U": sequence, "u": sequence, "b": [], "a": []},
    )


def test_event_envelope_round_trip_and_validity_dimensions() -> None:
    envelope = _envelope(7)
    restored = EventEnvelope.from_dict(json.loads(envelope.to_json()))

    assert restored == envelope
    assert restored.logical_time == LogicalTime(5_007, 7)
    assert restored.raw_payload_checksum == payload_checksum(restored.payload)
    assert ValidityState(True, True, True, True).execution_valid is True
    assert ValidityState(True, False, True, True, reason="trade reconnect").execution_valid is False
    assert ValidityState(True, False, True, True, trade_stream_required=False).execution_valid is True


def test_segment_writer_rotates_atomically_and_writes_hashed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_whole_file_reads(path: Path) -> bytes:
        raise AssertionError(f"capture hashing must stream files, not read_bytes(): {path}")

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_reads)
    with SegmentedCaptureWriter(
        tmp_path,
        "capture-1",
        max_bytes=1,
        compression="none",
    ) as writer:
        writer.write(_envelope(1))
        writer.write(_envelope(2))
        writer.update_manifest_metadata(
            {
                "writer": {
                    "queue_capacity": 8,
                    "queue_high_water": 2,
                    "overflow_count": 0,
                    "complete": True,
                }
            }
        )

    segments = sorted(tmp_path.glob("capture-1_*.ndjson"))
    assert len(segments) == 2
    assert not list(tmp_path.glob("*.partial"))
    assert all(validate_segment(path).ok for path in segments)
    manifest = json.loads((tmp_path / "capture-1.manifest.json").read_text(encoding="utf-8"))
    assert manifest["segment_count"] == 2
    assert manifest["event_count"] == 2
    assert manifest["first_recv_seq"] == 1
    assert manifest["last_recv_seq"] == 2
    assert manifest["capture_runtime"]["writer"] == {
        "queue_capacity": 8,
        "queue_high_water": 2,
        "overflow_count": 0,
        "complete": True,
    }
    assert len(manifest["manifest_sha256"]) == 64

    replayed = list(iter_records(tmp_path / "capture-1.manifest.json"))
    assert [record.data["_capture"]["recvSeq"] for record in replayed] == [1, 2]
    assert [record.data["_capture"]["captureId"] for record in replayed] == ["capture-1", "capture-1"]
    assert all(record.data["_capture"]["payloadChecksum"].startswith("crc32c:") for record in replayed)
    assert [record.type for record in replayed] == ["depthUpdate", "depthUpdate"]


def test_partial_tail_is_visible_and_recovers_only_complete_records(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="capture interrupted"):
        with SegmentedCaptureWriter(tmp_path, "capture-1", compression="none") as writer:
            writer.write(_envelope(1))
            raise RuntimeError("capture interrupted")

    partial = next(tmp_path.glob("*.partial"))
    assert [event.recv_seq for event in recover_valid_envelopes(partial)] == [1]
    report = validate_segment(partial)
    assert report.ok is False
    assert "missing complete segment trailer" in report.issues
    assert not (tmp_path / "capture-1.manifest.json").exists()


def test_segment_rejects_nonincreasing_global_receive_sequence(tmp_path: Path) -> None:
    writer = SegmentedCaptureWriter(tmp_path, "capture-1", max_bytes=1, compression="none")
    writer.write(_envelope(2))
    with pytest.raises(Exception, match="receive sequence must increase"):
        writer.write(_envelope(2))
    writer.close()


def test_capture_event_envelope_preserves_route_epochs_and_receive_identity() -> None:
    envelopes: list[EventEnvelope] = []

    class _Writer:
        def write(self, envelope: EventEnvelope) -> None:
            envelopes.append(envelope)

    fallback_calls = 0

    def fallback_sequence() -> int:
        nonlocal fallback_calls
        fallback_calls += 1
        return 999

    adapter = _EnvelopeRecordWriter(_Writer(), "capture-1", fallback_sequence)  # type: ignore[arg-type]
    adapter.write(
        NDJSONRecord(
            ts_local=1.25,
            symbol="BTCUSDT",
            type="captureEvent",
            data={
                "event": "connect",
                "route": "public",
                "recvSeq": 41,
                "recvMonotonicNs": 123_456,
                "streamEpoch": 3,
                "syncEpoch": 7,
            },
        )
    )

    assert fallback_calls == 0
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.recv_seq == 41
    assert envelope.recv_monotonic_ns == 123_456
    assert envelope.route == "public"
    assert envelope.stream_epoch == 3
    assert envelope.sync_epoch == 7


def test_envelope_adapter_assigns_fallback_identity_before_sink_write_returns() -> None:
    envelopes: list[EventEnvelope] = []
    assigned: list[int] = []

    class _Writer:
        def write(self, envelope: EventEnvelope) -> None:
            envelopes.append(envelope)

    def next_sequence() -> int:
        sequence = len(assigned) + 1
        assigned.append(sequence)
        return sequence

    adapter = _EnvelopeRecordWriter(_Writer(), "capture-1", next_sequence)
    adapter.write(NDJSONRecord(ts_local=1.0, symbol="*", type="captureMeta", data={"schemaVersion": 3}))

    assert assigned == [1]
    assert envelopes[0].recv_seq == 1


def test_capture_trailer_has_one_shared_receive_identity() -> None:
    record = _capture_trailer_record(iter([81]).__next__, writer_queue_capacity=4096)

    assert record.type == "captureEvent"
    assert record.data["event"] == "capture_trailer"
    assert record.data["route"] == "control"
    assert record.data["recvSeq"] == 81
    assert record.data["_capture"]["recvSeq"] == 81
    assert record.data["writerQueueCapacity"] == 4096


def test_capture_failure_report_is_atomic_sanitized_and_hashed(tmp_path: Path) -> None:
    report_path = _write_capture_failure_report(
        tmp_path,
        "capture-1",
        OSError("sensitive local path"),
        {"failure_type": "CaptureWriterIOError", "complete": False},
    )

    assert report_path.name == "capture-1.failure.json"
    assert not list(tmp_path.glob("*.partial"))
    raw = report_path.read_text(encoding="utf-8")
    assert "sensitive local path" not in raw
    report = json.loads(raw)
    expected_hash = report.pop("report_sha256")
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert expected_hash == hashlib.sha256(encoded).hexdigest()
    assert report["complete"] is False
    assert report["failure_types"] == ["OSError"]


def test_partial_recovery_recomputes_payload_checksum(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="capture interrupted"):
        with SegmentedCaptureWriter(tmp_path, "capture-1", compression="none") as writer:
            writer.write(_envelope(1))
            raise RuntimeError("capture interrupted")

    partial = next(tmp_path.glob("*.partial"))
    rows = [json.loads(line) for line in partial.read_text(encoding="utf-8").splitlines()]
    event_row = next(row for row in rows if row.get("record") == "event")
    event_row["event"]["payload"]["U"] = 999
    partial.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")

    assert list(recover_valid_envelopes(partial)) == []
    report = validate_segment(partial)
    assert "line 2: payload checksum mismatch" in report.issues


def test_checkpoint_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    checkpoint = Checkpoint.create(
        event_index=12,
        logical_time=(123, 12),
        state={"bids": {100: 2}, "cash": "1.25"},
    )
    path = tmp_path / "checkpoint.json"
    write_checkpoint(path, checkpoint)

    assert read_checkpoint(path) == checkpoint
    assert state_hash({"b": 2, "a": 1}) == state_hash({"a": 1, "b": 2})

    value = json.loads(path.read_text(encoding="utf-8"))
    value["state"]["cash"] = "9.99"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="state hash mismatch"):
        read_checkpoint(path)


def test_latency_draws_are_seeded_scenarios_and_reject_nonfinite_values() -> None:
    first = LatencyModel(mode="empirical", samples_ms=(1.0, 5.0, 25.0), seed=17)
    second = LatencyModel(mode="empirical", samples_ms=(1.0, 5.0, 25.0), seed=17)
    assert [first.draw("new_order") for _ in range(20)] == [second.draw("new_order") for _ in range(20)]
    assert (
        LatencyModel(
            mode="stress_tail",
            samples_ms=(1.0, 5.0),
            stress_multiplier=3.0,
        ).draw("cancel")
        == 15.0
    )
    with pytest.raises(ValueError, match="finite"):
        LatencyModel(new_order_ms=float("nan"))


def test_validated_manifest_normalizes_to_bounded_arrow_ipc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SegmentedCaptureWriter(tmp_path, "capture-1", compression="none") as writer:
        writer.write(_envelope(1))
        writer.write(_envelope(2))

    arrow_path = tmp_path / "normalized.arrow"

    def reject_whole_file_reads(path: Path) -> bytes:
        raise AssertionError(f"Arrow normalization must hash files incrementally, not read_bytes(): {path}")

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_reads)
    report = normalize_to_arrow(tmp_path / "capture-1.manifest.json", arrow_path, batch_size=1)
    rows = list(iter_arrow_rows(arrow_path))

    assert report["records"] == 2
    assert len(report["output_sha256"]) == 64
    assert [row["recv_seq"] for row in rows] == [1, 2]
    assert all(row["logical_time_source"] == "capture_receive_clock" for row in rows)
    assert arrow_metadata(arrow_path)["causal_order"] == "recv_monotonic_ns,recv_seq"
