from __future__ import annotations

import re
from pathlib import Path

import pytest

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.schema import RecordValidationError
from lob_sim.record.segmented import SegmentedCaptureWriter, validate_segment
from lob_sim.replay.arrow_store import normalize_to_arrow
from lob_sim.replay.reader import iter_records


def _capture(tmp_path: Path, event_kind: str, payload: dict) -> tuple[Path, Path]:
    with SegmentedCaptureWriter(tmp_path, "payload-validation", compression="none") as writer:
        writer.write(
            EventEnvelope(
                capture_id="payload-validation",
                schema_version=SCHEMA_V3,
                venue="BINANCE_USDM",
                instrument="BTCUSDT",
                event_kind=event_kind,
                route="market" if event_kind == "aggTrade" else "public",
                recv_seq=1,
                recv_wall_ns=1_000_000_000,
                recv_monotonic_ns=100,
                stream_epoch=1,
                sync_epoch=1,
                payload=payload,
            )
        )
    return tmp_path / "payload-validation_000000.ndjson", writer.manifest_path


@pytest.mark.parametrize(
    ("event_kind", "payload", "message"),
    [
        ("aggTrade", {"p": "100", "q": "1", "m": "false"}, "aggTrade.m must be boolean"),
        ("aggTrade", {"p": "NaN", "q": "1", "m": False}, "aggTrade.p must be finite"),
        ("aggTrade", {"p": "100", "m": False}, "aggTrade payload is missing required field(s): q"),
        ("depthUpdate", {"U": True, "u": 1, "b": [], "a": []}, "depthUpdate.U must be an integer"),
        ("depthUpdate", {"U": 2, "u": 1, "b": [], "a": []}, "depthUpdate.U must be <= depthUpdate.u"),
        ("depthUpdate", {"U": 1, "u": 1, "b": []}, "depthUpdate payload is missing required field(s): a"),
    ],
)
def test_manifest_and_direct_segment_reject_the_same_market_payloads(
    tmp_path: Path,
    event_kind: str,
    payload: dict,
    message: str,
) -> None:
    segment, manifest = _capture(tmp_path, event_kind, payload)

    # These inputs have valid transport checksums.  A successful integrity
    # check cannot substitute for validation of the recorded market fields.
    assert validate_segment(segment).ok
    for source in (segment, manifest):
        with pytest.raises(RecordValidationError, match=re.escape(message)):
            list(iter_records(source))


@pytest.mark.parametrize(
    ("event_kind", "payload"),
    [
        ("aggTrade", {"p": "100", "q": "1", "m": False}),
        ("depthUpdate", {"U": 1, "u": 1, "pu": 0, "b": [["100", "1"]], "a": []}),
    ],
)
def test_manifest_and_direct_segment_agree_on_valid_market_payloads(
    tmp_path: Path,
    event_kind: str,
    payload: dict,
) -> None:
    segment, manifest = _capture(tmp_path, event_kind, payload)

    assert list(iter_records(manifest)) == list(iter_records(segment))


def test_schema_validation_can_only_be_skipped_by_explicit_request(tmp_path: Path) -> None:
    segment, manifest = _capture(tmp_path, "aggTrade", {"p": "100", "q": "1", "m": "false"})

    for source in (segment, manifest):
        rows = list(iter_records(source, validate=False))
        assert len(rows) == 1
        assert rows[0].data["m"] == "false"


def test_arrow_normalization_does_not_finalize_invalid_manifest_payloads(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    _segment, manifest = _capture(tmp_path, "aggTrade", {"p": "100", "q": "1", "m": "false"})
    output = tmp_path / "normalized.arrow"

    with pytest.raises(RecordValidationError, match="aggTrade.m must be boolean"):
        normalize_to_arrow(manifest, output, batch_size=1)

    assert not output.exists()
