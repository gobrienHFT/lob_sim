"""Checksummed but untrustworthy wire data must not pass research admission."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from lob_sim.record.envelope import EventEnvelope, canonical_json as wire_json, payload_checksum
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_audit import audit_capture
from lob_sim.research.capture_receipts import audit_receipts, MAX_CAPTURE_ROW_CHARS, strict_envelopes
from test_research_capture_intervals import capture, cfg


def change_manifest(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    data["manifest_sha256"] = identity({k: v for k, v in data.items() if k != "manifest_sha256"})
    path.write_text(json.dumps(data))


def change_segment(path, mutate):
    """Retain native CRC/content/file identities after an adversarial edit.

    This intentionally uses the native canonical envelope digest so tests can
    distinguish extra research admission from ordinary checksum validation.
    """
    manifest = json.loads(path.read_text())
    segment = path.parent / manifest["segments"][0]["path"]
    rows = [json.loads(line) for line in segment.read_text().splitlines()]
    events = [row for row in rows if row["record"] == "event"]
    mutate(events)
    content = sha256()
    for row in events:
        content.update(wire_json(EventEnvelope.from_dict(row["event"]).as_dict()))
    rows[-1]["content_sha256"] = content.hexdigest()
    segment.write_text("".join(json.dumps(row) + "\n" for row in rows))
    change_manifest(
        path,
        lambda m: m["segments"][0].update(
            file_sha256=file_sha256(segment), size_bytes=segment.stat().st_size, content_sha256=content.hexdigest()
        ),
    )


@pytest.mark.parametrize("field", ["first_recv_seq", "segment_count", "event_count"])
def test_manifest_integer_metadata_is_not_coerced(tmp_path, field):
    path = capture(tmp_path)
    change_manifest(path, lambda m: m.update({field: True}))
    with pytest.raises(ValueError):
        audit_receipts(path, cfg().symbols)


@pytest.mark.parametrize("field", ["stream_epoch", "sync_epoch", "raw_payload_checksum"])
def test_research_may_not_invent_missing_envelope_identity(tmp_path, field):
    path = capture(tmp_path)
    change_segment(path, lambda events: events[0]["event"].pop(field))
    with pytest.raises(ValueError, match="missing"):
        audit_receipts(path, cfg().symbols)


def test_null_raw_checksum_cannot_be_computed_into_admission(tmp_path):
    path = capture(tmp_path)
    change_segment(path, lambda events: events[0]["event"].update(raw_payload_checksum=None))
    with pytest.raises(ValueError, match="raw checksum"):
        audit_receipts(path, cfg().symbols)


def test_public_provenance_label_cannot_replace_full_contract_metadata(tmp_path):
    path = capture(tmp_path)

    def relabel(events):
        row = events[0]
        row["event"]["payload"].update(source_kind="public_binance_network")
        checksum = payload_checksum(row["event"]["payload"])
        row["event"]["raw_payload_checksum"] = row["payload_checksum"] = checksum

    change_segment(path, relabel)
    result = audit_receipts(path, cfg().symbols)
    assert not result["integrity"]["ok"]
    assert "public_instrument_metadata_missing" in result["integrity"]["reasons"]


@pytest.mark.parametrize("suffix", [".failure.json", ".reserve", "_000001.ndjson.partial"])
def test_final_manifest_does_not_erase_failed_finalization_markers(tmp_path, suffix):
    path = capture(tmp_path)
    (path.parent / ("mechanics" + suffix)).write_text("retained failure")
    with pytest.raises(ValueError, match="finalization"):
        audit_receipts(path, cfg().symbols)


@pytest.mark.parametrize(
    "writer,reason",
    [
        ({"complete": False, "overflow_count": 0}, "writer_completion_missing"),
        ({"complete": True, "overflow_count": 1}, "writer_overflow_or_missing_count"),
        ({"complete": True, "overflow_count": False}, "writer_overflow_or_missing_count"),
        ({"complete": True, "overflow_count": 0, "records_written": 1}, "writer_record_census"),
    ],
)
def test_writer_failure_evidence_is_never_relabeled_complete(tmp_path, writer, reason):
    path = capture(tmp_path)
    change_manifest(path, lambda m: m["capture_runtime"].update(writer=writer))
    result = audit_receipts(path, cfg().symbols)
    assert not result["integrity"]["ok"]
    assert reason in result["integrity"]["reasons"]


def test_duplicate_manifest_keys_fail_even_when_the_last_value_hashes(tmp_path):
    path = capture(tmp_path)
    original = path.read_text()
    path.write_text(original.replace("{", '{"event_count":1,', 1))
    with pytest.raises(ValueError, match="duplicate"):
        audit_receipts(path, cfg().symbols)


def test_bounded_wire_reader_refuses_large_lines_and_preserves_them(tmp_path):
    path = tmp_path / "large.ndjson"
    raw = b" " * (MAX_CAPTURE_ROW_CHARS + 1) + b"\n"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="limit"):
        list(strict_envelopes(path))
    assert path.read_bytes() == raw


def test_output_publication_race_cannot_overwrite_a_destination(tmp_path, monkeypatch):
    path = capture(tmp_path)
    out = tmp_path / "audit"
    import lob_sim.research.capture_audit as module

    original_link = module.os.link

    def racing_link(source, destination):
        if Path(destination).name == "intervals.jsonl":
            Path(destination).write_bytes(b"concurrent forensic destination")
        return original_link(source, destination)

    monkeypatch.setattr(module.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        audit_capture(path, out, cfg())
    assert (out / "intervals.jsonl").read_bytes() == b"concurrent forensic destination"
    assert (out / "intervals.jsonl.partial").exists()
    assert (out / "failure.json").exists()
    assert not (out / "report.json").exists()


def test_processing_code_change_prevents_publication(tmp_path, monkeypatch):
    path = capture(tmp_path)
    import lob_sim.research.capture_audit as module

    calls = 0
    original = module.checkpoint_code_identity

    def changed_code():
        nonlocal calls
        calls += 1
        result = original()
        if calls > 1:
            result["sha256"] = "0" * 64
        return result

    monkeypatch.setattr(module, "checkpoint_code_identity", changed_code)
    with pytest.raises(ValueError, match="processing source changed"):
        audit_capture(path, tmp_path / "audit", cfg())
    assert not (tmp_path / "audit" / "report.json").exists()
    assert (tmp_path / "audit" / "intervals.jsonl.partial").exists()
