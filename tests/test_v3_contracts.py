from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.oracle import Checkpoint, read_checkpoint, state_hash, write_checkpoint
from lob_sim.record.envelope import EventEnvelope, LogicalTime, SCHEMA_V3, ValidityState, payload_checksum
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


def test_segment_writer_rotates_atomically_and_writes_hashed_manifest(tmp_path: Path) -> None:
    with SegmentedCaptureWriter(
        tmp_path,
        "capture-1",
        max_bytes=1,
        compression="none",
    ) as writer:
        writer.write(_envelope(1))
        writer.write(_envelope(2))

    segments = sorted(tmp_path.glob("capture-1_*.ndjson"))
    assert len(segments) == 2
    assert not list(tmp_path.glob("*.partial"))
    assert all(validate_segment(path).ok for path in segments)
    manifest = json.loads((tmp_path / "capture-1.manifest.json").read_text(encoding="utf-8"))
    assert manifest["segment_count"] == 2
    assert manifest["event_count"] == 2
    assert manifest["first_recv_seq"] == 1
    assert manifest["last_recv_seq"] == 2
    assert len(manifest["manifest_sha256"]) == 64
    replayed = list(iter_records(tmp_path / "capture-1.manifest.json"))
    assert [record.data["_capture"]["recvSeq"] for record in replayed] == [1, 2]
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


def test_validated_manifest_normalizes_to_bounded_arrow_ipc(tmp_path: Path) -> None:
    with SegmentedCaptureWriter(tmp_path, "capture-1", compression="none") as writer:
        writer.write(_envelope(1))
        writer.write(_envelope(2))

    arrow_path = tmp_path / "normalized.arrow"
    report = normalize_to_arrow(tmp_path / "capture-1.manifest.json", arrow_path, batch_size=1)
    rows = list(iter_arrow_rows(arrow_path))

    assert report["records"] == 2
    assert len(report["output_sha256"]) == 64
    assert [row["recv_seq"] for row in rows] == [1, 2]
    assert all(row["logical_time_source"] == "capture_receive_clock" for row in rows)
    assert arrow_metadata(arrow_path)["causal_order"] == "recv_monotonic_ns,recv_seq"
