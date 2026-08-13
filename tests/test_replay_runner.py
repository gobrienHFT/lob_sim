from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.reader import RecordedEvent
from lob_sim.replay.runner import _ReplayValidityTracker, replay
from lob_sim.config import load_config


def _v3_capture(
    sequence: int,
    route: str,
    *,
    stream_epoch: int = 1,
    sync_epoch: int = 1,
    monotonic_ns: int | None = None,
) -> dict[str, object]:
    return {
        "route": route,
        "streamEpoch": stream_epoch,
        "syncEpoch": sync_epoch,
        "recvSeq": sequence,
        "recvMonotonicNs": sequence if monotonic_ns is None else monotonic_ns,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recvSeq", "2"),
        ("recvMonotonicNs", 2.0),
        ("streamEpoch", True),
        ("syncEpoch", -1),
        ("route", ""),
    ],
)
def test_validity_tracker_rejects_coercible_schema_v3_metadata(field: str, value: object) -> None:
    tracker = _ReplayValidityTracker(trade_stream_required=True)
    tracker.observe(
        RecordedEvent(
            ts_local=0.0,
            symbol="*",
            type="captureMeta",
            data={"schemaVersion": 3, "clock": "receive_time"},
        )
    )
    capture = _v3_capture(2, "public")
    capture[field] = value
    tracker.observe(
        RecordedEvent(
            ts_local=0.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"_capture": capture},
        )
    )

    assert tracker.capture_valid is False
    assert f"invalid_capture_metadata:{field}" in tracker.invalid_reasons


def test_validity_tracker_rejects_coercible_schema_version() -> None:
    tracker = _ReplayValidityTracker(trade_stream_required=False)
    tracker.observe(
        RecordedEvent(
            ts_local=0.0,
            symbol="*",
            type="captureMeta",
            data={"schemaVersion": "3", "clock": "receive_time"},
        )
    )

    assert tracker.capture_valid is False
    assert tracker.schema_version == 1
    assert "invalid_capture_schema_version" in tracker.invalid_reasons


def test_replay_returns_structured_symbol_diagnostics(tmp_path: Path, capsys) -> None:
    path = tmp_path / "replay.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result.events_processed == 3
    assert result.depth_events == 1
    assert result.gap_count == 0
    assert result.events_per_sec > 0
    assert set(result.symbols) == {"BTCUSDT"}
    assert result.symbols["BTCUSDT"].snapshot_seen is True
    assert result.symbols["BTCUSDT"].synced is True
    assert result.symbols["BTCUSDT"].total_levels == 2
    assert result.symbols["BTCUSDT"].last_update_id == 105


def test_replay_counts_non_resync_gap_without_advancing_book(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    path = tmp_path / "gap.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.010")], [("100.1", "0.010")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.008"]], "a": [["100.1", "0.009"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 110, "pu": 999, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = replace(load_config(".env.example"), resync_on_gap=False)

    result = replay(path, cfg)

    assert result.gap_count == 1
    assert result.symbols["BTCUSDT"].gap_count == 1
    assert result.symbols["BTCUSDT"].synced is False
    assert result.symbols["BTCUSDT"].last_update_id is None
    # Gap invalidation clears the previous epoch; no stale liquidity remains
    # available to fills or markouts while awaiting a fresh snapshot.
    assert result.symbols["BTCUSDT"].total_levels == 0


def test_replay_counts_gap_discovered_while_bridging_snapshot_buffer(tmp_path: Path) -> None:
    path = tmp_path / "buffer_gap.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 90, "u": 100, "pu": 89, "b": [], "a": []},
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 102, "u": 110, "pu": 999, "b": [], "a": []},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(95, [("100.0", "0.010")], [("100.1", "0.010")]),
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path)

    assert result.gap_count == 1
    assert result.symbols["BTCUSDT"].gap_count == 1
    assert result.symbols["BTCUSDT"].synced is False
    assert result.symbols["BTCUSDT"].total_levels == 0


def test_replay_reports_claim_ready_schema_v3_validity(tmp_path: Path) -> None:
    path = tmp_path / "schema_v3.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="*",
            type="captureMeta",
            data={"schemaVersion": 3, "clock": "receive_time"},
        ),
        NDJSONRecord(
            ts_local=0.1,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={
                "tickSize": "0.1",
                "stepSize": "0.001",
                "_capture": _v3_capture(2, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
        NDJSONRecord(
            ts_local=0.2,
            symbol="BTCUSDT",
            type="captureEvent",
            data={"event": "connect", "route": "public", "_capture": _v3_capture(3, "public")},
        ),
        NDJSONRecord(
            ts_local=0.3,
            symbol="BTCUSDT",
            type="captureEvent",
            data={"event": "connect", "route": "market", "_capture": _v3_capture(4, "market")},
        ),
        NDJSONRecord(
            ts_local=0.4,
            symbol="BTCUSDT",
            type="snapshot",
            data={
                **snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
                "_capture": _v3_capture(5, "public"),
            },
        ),
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 95,
                "u": 105,
                "pu": 94,
                "b": [["100.0", "0.001"]],
                "a": [["100.1", "0.001"]],
                "_capture": _v3_capture(6, "public"),
            },
        ),
        NDJSONRecord(
            ts_local=0.6,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True, "_capture": _v3_capture(7, "market")},
        ),
        NDJSONRecord(
            ts_local=0.7,
            symbol="*",
            type="captureEvent",
            data={
                "event": "capture_trailer",
                "route": "control",
                "_capture": _v3_capture(8, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path, replace(load_config(".env.example"), mm_strategy_profile="baseline"))

    assert result.validity is not None
    assert result.validity.claim_ready is True
    assert result.validity.capture_valid is True
    assert result.validity.clock_valid is True
    assert result.validity.last_receive_seq == 8
    assert result.validity.boundary_count == 2
    assert result.validity.boundaries_omitted == 0
    assert {boundary.kind for boundary in result.validity.boundaries} == {"recovered"}
    assert {boundary.reason for boundary in result.validity.boundaries} == {"public_connected", "market_connected"}
    assert result.symbols["BTCUSDT"].validity is not None
    assert result.symbols["BTCUSDT"].validity.execution_inputs_valid is True


def test_replay_fails_closed_on_schema_v3_receive_clock_regression(tmp_path: Path) -> None:
    path = tmp_path / "clock_regression.ndjson"
    records = [
        NDJSONRecord(0.0, "*", "captureMeta", {"schemaVersion": 3, "clock": "receive_time"}),
        NDJSONRecord(
            0.1,
            "BTCUSDT",
            "exchangeInfo",
            {
                "tickSize": "0.1",
                "stepSize": "0.001",
                "_capture": _v3_capture(1, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
        NDJSONRecord(
            0.2,
            "BTCUSDT",
            "captureEvent",
            {"event": "connect", "route": "public", "_capture": _v3_capture(2, "public", monotonic_ns=20)},
        ),
        NDJSONRecord(
            0.3,
            "BTCUSDT",
            "snapshot",
            {
                **snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
                "_capture": _v3_capture(3, "public", monotonic_ns=10),
            },
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path)

    assert result.validity is not None
    assert result.validity.clock_valid is False
    assert result.validity.claim_ready is False
    assert result.validity.receive_clock_regressions == 1
    assert "receive_monotonic_regression" in result.validity.invalid_reasons
    assert result.validity.boundary_count == 2
    boundary = next(item for item in result.validity.boundaries if item.scope == "clock")
    assert boundary.kind == "invalidated"
    assert boundary.scope == "clock"
    assert boundary.recv_seq == 3
    assert boundary.recv_monotonic_ns == 10
    assert result.symbols["BTCUSDT"].validity is not None
    assert result.symbols["BTCUSDT"].validity.execution_inputs_valid is False


def test_replay_fails_closed_on_schema_v3_receive_sequence_gap(tmp_path: Path) -> None:
    path = tmp_path / "sequence_gap.ndjson"
    records = [
        NDJSONRecord(0.0, "*", "captureMeta", {"schemaVersion": 3, "clock": "receive_time"}),
        NDJSONRecord(
            0.1,
            "BTCUSDT",
            "exchangeInfo",
            {
                "tickSize": "0.1",
                "stepSize": "0.001",
                "_capture": _v3_capture(1, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
        NDJSONRecord(
            0.2,
            "BTCUSDT",
            "captureEvent",
            {"event": "connect", "route": "public", "_capture": _v3_capture(3, "public")},
        ),
        NDJSONRecord(
            0.3,
            "BTCUSDT",
            "captureEvent",
            {"event": "connect", "route": "market", "_capture": _v3_capture(4, "market")},
        ),
        NDJSONRecord(
            0.4,
            "BTCUSDT",
            "snapshot",
            {
                **snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
                "_capture": _v3_capture(5, "public"),
            },
        ),
        NDJSONRecord(
            0.5,
            "BTCUSDT",
            "depthUpdate",
            {
                "U": 95,
                "u": 105,
                "pu": 94,
                "b": [["100.0", "0.001"]],
                "a": [["100.1", "0.001"]],
                "_capture": _v3_capture(6, "public"),
            },
        ),
        NDJSONRecord(
            0.6,
            "BTCUSDT",
            "aggTrade",
            {"p": "100.0", "q": "0.001", "m": True, "_capture": _v3_capture(7, "market")},
        ),
        NDJSONRecord(
            0.7,
            "*",
            "captureEvent",
            {
                "event": "capture_trailer",
                "route": "control",
                "_capture": _v3_capture(8, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path, replace(load_config(".env.example"), mm_strategy_profile="baseline"))

    assert result.validity is not None
    assert result.validity.receive_sequence_gaps == 1
    assert result.validity.capture_valid is False
    assert result.validity.claim_ready is False
    assert "receive_sequence_gap" in result.validity.invalid_reasons
    assert result.symbols["BTCUSDT"].validity is not None
    assert result.symbols["BTCUSDT"].validity.execution_inputs_valid is False


def test_replay_reconnect_and_rejected_snapshot_clear_book_epoch(tmp_path: Path) -> None:
    path = tmp_path / "reconnect_rejected_snapshot.ndjson"
    records = [
        NDJSONRecord(0.0, "BTCUSDT", "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001"}),
        NDJSONRecord(0.1, "BTCUSDT", "snapshot", snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")])),
        NDJSONRecord(0.2, "BTCUSDT", "depthUpdate", {"U": 95, "u": 105, "pu": 94, "b": [], "a": []}),
        NDJSONRecord(
            0.3,
            "BTCUSDT",
            "captureEvent",
            {"event": "disconnect", "route": "public", "reason": "socket closed"},
        ),
        NDJSONRecord(
            0.4,
            "BTCUSDT",
            "snapshot",
            {
                **snapshot_payload(200, [("100.0", "0.001")], [("100.1", "0.001")]),
                "_capture": {"snapshotAccepted": False, "validationError": "too_old"},
            },
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path)

    symbol = result.symbols["BTCUSDT"]
    assert symbol.synced is False
    assert symbol.total_levels == 0
    assert symbol.validity is not None
    assert symbol.validity.snapshot_rejections == 1
    assert symbol.validity.execution_inputs_valid is False
    assert result.validity is not None
    assert result.validity.claim_ready is False
    assert any(boundary.scope == "stream" and boundary.kind == "invalidated" for boundary in result.validity.boundaries)


def test_replay_marks_same_epoch_snapshot_replacement_as_invalid_boundary(tmp_path: Path) -> None:
    path = tmp_path / "snapshot_replacement.ndjson"
    records = [
        NDJSONRecord(0.0, "*", "captureMeta", {"schemaVersion": 3, "clock": "receive_time"}),
        NDJSONRecord(
            0.1,
            "BTCUSDT",
            "exchangeInfo",
            {
                "tickSize": "0.1",
                "stepSize": "0.001",
                "_capture": _v3_capture(1, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
        NDJSONRecord(
            0.2,
            "BTCUSDT",
            "captureEvent",
            {"event": "connect", "route": "public", "_capture": _v3_capture(2, "public")},
        ),
        NDJSONRecord(
            0.3,
            "BTCUSDT",
            "captureEvent",
            {"event": "connect", "route": "market", "_capture": _v3_capture(3, "market")},
        ),
        NDJSONRecord(
            0.4,
            "BTCUSDT",
            "snapshot",
            {**snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]), "_capture": _v3_capture(4, "public")},
        ),
        NDJSONRecord(
            0.5,
            "BTCUSDT",
            "depthUpdate",
            {
                "U": 95,
                "u": 105,
                "pu": 94,
                "b": [["100.0", "0.001"]],
                "a": [["100.1", "0.001"]],
                "_capture": _v3_capture(5, "public"),
            },
        ),
        NDJSONRecord(
            0.6,
            "BTCUSDT",
            "snapshot",
            {**snapshot_payload(105, [("100.0", "0.001")], [("100.1", "0.001")]), "_capture": _v3_capture(6, "public")},
        ),
        NDJSONRecord(
            0.7,
            "BTCUSDT",
            "depthUpdate",
            {
                "U": 105,
                "u": 110,
                "pu": 104,
                "b": [["100.0", "0.002"]],
                "a": [["100.1", "0.002"]],
                "_capture": _v3_capture(7, "public"),
            },
        ),
        NDJSONRecord(
            0.8,
            "*",
            "captureEvent",
            {
                "event": "capture_trailer",
                "route": "control",
                "_capture": _v3_capture(8, "control", stream_epoch=0, sync_epoch=0),
            },
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path, replace(load_config(".env.example"), mm_strategy_profile="baseline"))

    assert result.symbols["BTCUSDT"].synced is True
    assert result.validity is not None
    assert result.validity.claim_ready is False
    assert any(
        boundary.reason == "snapshot_replaced_synced_book" and boundary.scope == "book"
        for boundary in result.validity.boundaries
    )
