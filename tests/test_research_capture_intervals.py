"""Small synthetic mechanics with hand-counted valid/invalid durations.

The rows deliberately declare synthetic provenance. They must never certify
an empirical day, even when the synchronization mechanics pass.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from lob_sim.regime.validation import strict_json
from lob_sim.research.capture_audit import audit_capture

SECOND = 1_000_000_000
WALL = 1_735_689_600 * SECOND
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def capture(tmp_path: Path, *, sequence_gap=False, clock_step=False, trailer=True):
    writer = SegmentedCaptureWriter(tmp_path / "raw", "mechanics", compression="none")
    sequence = 0

    def emit(t, kind, payload, symbol="*", route="control", stream_epoch=0):
        nonlocal sequence
        sequence += 1
        if sequence_gap and sequence == 12:
            sequence += 1
        writer.write(
            EventEnvelope(
                "mechanics",
                SCHEMA_V3,
                "BINANCE_USDM",
                symbol,
                kind,
                route,
                sequence,
                WALL + t * SECOND + (100_000_000 if clock_step and t >= 9 else 0),
                t * SECOND,
                payload,
                stream_epoch=stream_epoch,
            )
        )

    emit(0, "captureMeta", {"schemaVersion": 3, "clock": "receive_time", "source_kind": "synthetic_test"})
    for symbol in SYMBOLS:
        emit(0, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001", "venue": "BINANCE_USDM"}, symbol)
        for route in ("public", "market"):
            emit(0, "captureEvent", {"event": "connect", "route": route}, symbol, route)
    for symbol in SYMBOLS:
        emit(1, "snapshot", {"lastUpdateId": 100, "bids": [["99.9", "1"]], "asks": [["100.1", "1"]]}, symbol, "public")
    for t, last, previous in ((2, 101, 99), (3, 102, 101), (7, 103, 102)):
        if t == 7:
            emit(t, "captureEvent", {"event": "connect", "route": "market"}, "ETHUSDT", "market", 1)
        for symbol in SYMBOLS:
            emit(
                t,
                "depthUpdate",
                {"U": 100 if t == 2 else last, "u": last, "pu": previous, "b": [], "a": []},
                symbol,
                "public",
            )
            if t != 3:
                emit(
                    t,
                    "aggTrade",
                    {"p": "100", "q": "0.001", "m": False},
                    symbol,
                    "market",
                    int(t == 7 and symbol == "ETHUSDT"),
                )
        if t == 3:
            emit(6, "captureEvent", {"event": "disconnect", "route": "market"}, "ETHUSDT", "market")
    emit(9, "depthUpdate", {"U": 104, "u": 104, "pu": 100, "b": [], "a": []}, "BTCUSDT", "public")
    emit(10, "snapshot", {"lastUpdateId": 200, "bids": [["99.9", "1"]], "asks": [["100.1", "1"]]}, "BTCUSDT", "public")
    emit(10, "depthUpdate", {"U": 200, "u": 201, "pu": 199, "b": [], "a": []}, "BTCUSDT", "public")
    if trailer:
        emit(11, "captureEvent", {"event": "capture_trailer", "route": "control"})
    writer.update_manifest_metadata({"writer": {"complete": True, "overflow_count": 0}})
    writer.close()
    return writer.manifest_path


def cfg():
    return replace(load_config(".env.example", inherit_environment=False), symbols=SYMBOLS, mm_enabled=False)


def test_native_book_epochs_and_independent_trade_failure_have_exact_durations(tmp_path):
    path = capture(tmp_path)
    report = audit_capture(path, tmp_path / "audit", cfg())
    assert report["integrity"]["ok"]
    assert report["duration_ns"] == 11 * SECOND
    assert report["joint_valid_ns"] == 7 * SECOND
    assert report["symbols"]["BTCUSDT"]["depth_valid_ns"] == 8 * SECOND
    assert report["symbols"]["ETHUSDT"]["depth_valid_ns"] == 9 * SECOND
    assert report["symbols"]["ETHUSDT"]["trade_valid_ns"] == 8 * SECOND
    assert report["empirical_source"] is False
    rows = [strict_json(line) for line in (tmp_path / "audit" / "intervals.jsonl").read_text().splitlines()]
    assert sum(row["end_ns"] - row["start_ns"] for row in rows) == 11 * SECOND
    assert any(
        "trade" in " ".join(row["symbols"]["ETHUSDT"]["reasons"])
        for row in rows
        if row["start_ns"] == WALL + 6 * SECOND
    )
    assert any(not row["symbols"]["BTCUSDT"]["depth_valid"] for row in rows if row["start_ns"] == WALL + 9 * SECOND)
    assert report["intervals"]["rows"] == len(rows)


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"sequence_gap": True}, "receive_sequence_gap"),
        ({"clock_step": True}, "wall_clock_deviation"),
        ({"trailer": False}, "capture_trailer_missing"),
    ],
)
def test_integrity_failures_remain_visible_and_cannot_be_eligible(tmp_path, options, reason):
    path = capture(tmp_path, **options)
    report = audit_capture(path, tmp_path / "audit", cfg())
    assert report["integrity"]["ok"] is False
    assert reason in report["integrity"]["reasons"]
    assert report["research_usable"] is False


def test_audit_cannot_overwrite_an_existing_or_partial_evidence_directory(tmp_path):
    path = capture(tmp_path)
    out = tmp_path / "audit"
    audit_capture(path, out, cfg())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):
        audit_capture(path, out, cfg())
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_corruption_never_publishes_a_successful_audit_or_skips_to_later_events(tmp_path):
    path = capture(tmp_path)
    segment = next(path.parent.glob("*.ndjson"))
    with segment.open("ab") as handle:
        handle.write(b"corrupt tail\n")
    with pytest.raises(ValueError):
        audit_capture(path, tmp_path / "audit", cfg())
    assert not (tmp_path / "audit" / "report.json").exists()
    assert path.exists() and segment.read_bytes().endswith(b"corrupt tail\n")
