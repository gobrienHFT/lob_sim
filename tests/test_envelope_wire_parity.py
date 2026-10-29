"""Stdlib serialization/bitwise-CRC oracles and legacy import controls."""

from dataclasses import asdict
import json
import random

import pytest

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import validate_segment, recover_valid_envelopes
from test_core_crc32c import bitwise_reference
from test_segment_recovery_boundaries import _segment


def tree(rng, depth):
    if not depth or rng.randrange(3) == 0:
        return rng.choice((None, False, True, -17, 2**60, -0.0, 0.125, 'αé\n\\"'))
    if rng.randrange(2):
        return [tree(rng, depth - 1) for _ in range(rng.randrange(4))]
    return {"é" + str(i): tree(rng, depth - 1) for i in range(rng.randrange(4))}


def test_one_thousand_generated_envelopes_match_stdlib_wire_and_bitwise_crc():
    rng = random.Random(917162)
    for index in range(1000):
        payload = {"nested": tree(rng, 4), "index": index}
        event = EventEnvelope(
            "wire",
            SCHEMA_V3,
            "BINANCE_USDM",
            "BTCUSDT",
            "captureEvent",
            "control",
            index + 1,
            1_700_000_000_000_000_000 + index,
            2**60 + index,
            payload,
            exchange_event_ns=None if index % 2 else index,
            exchange_transaction_ns=None if index % 3 else index,
            stream_epoch=index % 7,
            sync_epoch=index % 11,
        )
        expected = asdict(event)
        # Preserve the pre-optimization shallow payload contract exactly.
        expected["payload"] = dict(payload)
        encoded = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert event.to_json() == encoded
        assert event.as_dict()["payload"] is not payload
        assert event.as_dict()["payload"]["nested"] is payload["nested"]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert event.raw_payload_checksum == f"crc32c:{bitwise_reference(raw):08x}"


@pytest.mark.parametrize("inner", [False, True])
def test_both_independent_checksum_bindings_still_reject_a_changed_record(tmp_path, inner):
    segment, lines = _segment(tmp_path)
    row = json.loads(lines[2])
    if inner:
        row["event"]["raw_payload_checksum"] = "crc32c:00000000"
    else:
        row["payload_checksum"] = "crc32c:00000000"
    lines[2] = json.dumps(row)
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = validate_segment(segment)
    assert not report.ok and any("payload checksum mismatch" in issue for issue in report.issues)
    assert [event.recv_seq for event in recover_valid_envelopes(segment)] == [1]


@pytest.mark.parametrize("missing", [True, False])
def test_optional_legacy_envelope_defaults_keep_the_original_content_identity(tmp_path, missing):
    segment, lines = _segment(tmp_path)
    row = json.loads(lines[2])
    if missing:
        del row["event"]["raw_payload_checksum"]
    else:
        row["event"]["raw_payload_checksum"] = None
    for name in ("exchange_event_ns", "exchange_transaction_ns", "stream_epoch", "sync_epoch"):
        del row["event"][name]
    lines[2] = json.dumps(row)
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert validate_segment(segment).ok
    assert [event.recv_seq for event in recover_valid_envelopes(segment)] == [1, 2]
