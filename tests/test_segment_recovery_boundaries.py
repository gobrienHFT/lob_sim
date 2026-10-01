from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.schema import RecordValidationError
from lob_sim.record.segmented import SegmentedCaptureWriter, recover_valid_envelopes, validate_segment
from lob_sim.replay.reader import iter_records


def _segment(tmp_path: Path) -> tuple[Path, list[str]]:
    with SegmentedCaptureWriter(tmp_path, "recovery", compression="none") as writer:
        for seq in (1, 2):
            writer.write(
                EventEnvelope(
                    capture_id="recovery",
                    schema_version=SCHEMA_V3,
                    venue="BINANCE_USDM",
                    instrument="BTCUSDT",
                    event_kind="depthUpdate",
                    route="public",
                    recv_seq=seq,
                    recv_wall_ns=seq,
                    recv_monotonic_ns=seq,
                    payload={"U": seq, "u": seq, "b": [], "a": []},
                )
            )
    segment = tmp_path / "recovery_000000.ndjson"
    return segment, segment.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("value", [None, [], True, 7, "event"])
def test_nonobject_segment_rows_are_reported_and_stop_prefix_recovery(tmp_path: Path, value: object) -> None:
    segment, lines = _segment(tmp_path)
    lines.insert(2, json.dumps(value))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = validate_segment(segment)
    assert not report.ok
    assert any("record must be a JSON object" in issue for issue in report.issues)
    assert [event.recv_seq for event in recover_valid_envelopes(segment)] == [1]
    with pytest.raises(RecordValidationError, match="record must be a JSON object"):
        list(iter_records(segment))


@pytest.mark.parametrize("record", [{"record": "unknown"}, {"unexpected": "event"}])
def test_recovery_never_skips_an_unknown_record_to_resume_later_events(tmp_path: Path, record: dict) -> None:
    segment, lines = _segment(tmp_path)
    lines.insert(2, json.dumps(record))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert not validate_segment(segment).ok
    assert [event.recv_seq for event in recover_valid_envelopes(segment)] == [1]


@pytest.mark.parametrize("schema", [None, "lob_sim.record.v99", 3])
def test_recovery_rejects_unknown_header_schema(tmp_path: Path, schema: object) -> None:
    segment, lines = _segment(tmp_path)
    header = json.loads(lines[0])
    header["schema_version"] = schema
    lines[0] = json.dumps(header)
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert not validate_segment(segment).ok
    assert list(recover_valid_envelopes(segment)) == []


def test_invalid_utf8_retains_only_the_preceding_checksummed_prefix(tmp_path: Path) -> None:
    segment, lines = _segment(tmp_path)
    prefix = ("\n".join(lines[:2]) + "\n").encode("utf-8")
    suffix = ("\n".join(lines[2:]) + "\n").encode("utf-8")
    segment.write_bytes(prefix + b'{"record":"\xff"}\n' + suffix)

    report = validate_segment(segment)
    assert not report.ok
    assert any("read failure" in issue for issue in report.issues)
    assert [event.recv_seq for event in recover_valid_envelopes(segment)] == [1]
    with pytest.raises(RecordValidationError, match="read failure"):
        list(iter_records(segment))
    assert [record.data["_capture"]["recvSeq"] for record in iter_records(segment, validate=False)] == [1]


def test_corrupt_zstd_is_a_validation_issue_not_an_unhandled_decoder_error(tmp_path: Path) -> None:
    pytest.importorskip("zstandard")
    segment = tmp_path / "corrupt.ndjson.zst"
    segment.write_bytes(b"not a zstandard stream")

    report = validate_segment(segment)
    assert not report.ok
    assert any("read failure" in issue for issue in report.issues)
    assert list(recover_valid_envelopes(segment)) == []


@pytest.mark.parametrize("value", [None, [], True, 7, "manifest"])
def test_nonobject_manifest_has_a_structured_validation_error(tmp_path: Path, value: object) -> None:
    manifest = tmp_path / "bad.manifest.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RecordValidationError, match="manifest must be a JSON object") as exc:
        list(iter_records(manifest))
    assert exc.value.path == str(manifest)


def test_truncated_manifest_has_a_structured_validation_error(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.manifest.json"
    manifest.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(RecordValidationError, match="invalid capture manifest JSON"):
        list(iter_records(manifest))
