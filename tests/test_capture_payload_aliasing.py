"""Storage-boundary aliasing regressions; ordinary matching is not changed."""

import pytest

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import (
    SegmentIntegrityError,
    SegmentedCaptureWriter,
    validate_segment,
    recover_valid_envelopes,
)


def envelope(payload):
    return EventEnvelope("alias", SCHEMA_V3, "BINANCE_USDM", "BTCUSDT", "depthUpdate", "public", 1, 1, 1, payload)


def test_payload_changed_after_receipt_cannot_be_published_as_completed_capture(tmp_path):
    payload = {"nested": {"quantity": "1"}}
    event = envelope(payload)
    payload["nested"]["quantity"] = "2"
    with pytest.raises(SegmentIntegrityError, match="payload.*checksum"):
        with SegmentedCaptureWriter(tmp_path, "alias", compression="none") as writer:
            writer.write(event)
    assert not writer.manifest_path.exists()
    with pytest.raises(SegmentIntegrityError, match="failed"):
        writer.close()
    assert list(tmp_path.glob("*.partial"))


def test_content_digest_uses_the_same_serialized_snapshot_as_the_stored_row(tmp_path, monkeypatch):
    payload = {"nested": {"quantity": "1"}}
    event = envelope(payload)
    original = EventEnvelope.to_json

    def mutate_after_serialization(self):
        encoded = original(self)
        payload["nested"]["quantity"] = "2"
        return encoded

    monkeypatch.setattr(EventEnvelope, "to_json", mutate_after_serialization)
    with SegmentedCaptureWriter(tmp_path, "alias", compression="none") as writer:
        writer.write(event)
    segment = next(tmp_path.glob("*.ndjson"))
    assert validate_segment(segment).ok
    assert next(recover_valid_envelopes(segment)).payload == {"nested": {"quantity": "1"}}
    assert payload == {"nested": {"quantity": "2"}}
