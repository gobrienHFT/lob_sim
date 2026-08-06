from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSynchronizer
from lob_sim.book.types import SnapshotEvent, SymbolSpec
from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord
from lob_sim.record.writer import NDJSONWriter
from lob_sim.replay.reader import RecordedEvent
from lob_sim.sim.engine import CaptureIntegrityError, ReplayClockError, SimulationEngine
from lob_sim.sim.orders import Order


def _config(tmp_path, **overrides):
    cfg = replace(
        load_config(".env"),
        record_dir=tmp_path,
        symbols=("BTCUSDT",),
        mm_requote_ms=250.0,
        sim_order_latency_ms=25.0,
        sim_cancel_latency_ms=25.0,
    )
    return replace(cfg, **overrides) if overrides else cfg


def _write_causality_fixture(path) -> None:
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="*",
            type="captureMeta",
            data={"schemaVersion": 2, "clock": "receive_time"},
        ),
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "1", "stepSize": "1"},
        ),
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="snapshot",
            data={"lastUpdateId": 100, "bids": [["100", "10"]], "asks": [["103", "10"]]},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 100, "u": 101, "pu": 99, "b": [["101", "10"]], "a": []},
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 102, "u": 102, "pu": 101, "b": [["102", "10"]], "a": []},
        ),
    ]
    with NDJSONWriter(path, flush_every=1) as writer:
        for record in records:
            writer.write(record)


def test_catch_up_decisions_cannot_observe_future_book(tmp_path) -> None:
    class ProbeEngine(SimulationEngine):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.observations: list[tuple[float, int]] = []

        def _handle_decision(self, symbol: str, ts: float) -> None:
            syncer = self._syncers.get(symbol)
            book = self._books.get(symbol)
            if syncer is not None and book is not None and syncer.synced:
                self.observations.append((ts, book.best_bid()))

    fixture = tmp_path / "causal.ndjson"
    _write_causality_fixture(fixture)
    engine = ProbeEngine(_config(tmp_path))
    engine.run(fixture)

    before_second_update = [bid for ts, bid in engine.observations if ts < 2.0]
    at_second_update = [bid for ts, bid in engine.observations if ts == 2.0]
    assert before_second_update
    assert set(before_second_update) == {101}
    assert at_second_update == [102]


def _synced_engine(tmp_path) -> SimulationEngine:
    engine = SimulationEngine(_config(tmp_path, mm_enabled=False, mm_max_position=Decimal("10")))
    spec = SymbolSpec("BTCUSDT", Decimal("1"), Decimal("1"))
    book = LocalOrderBook("BTCUSDT", spec)
    syncer = BookSynchronizer(book, resync_on_gap=True)
    syncer.on_snapshot(SnapshotEvent("BTCUSDT", 100, [(100, 10)], [(102, 10)]))
    # Establish the required snapshot bridge.
    from lob_sim.book.types import DepthUpdateEvent

    syncer.on_depth_update(DepthUpdateEvent("BTCUSDT", 100, 101, 99, [], [], 0.0))
    engine._specs["BTCUSDT"] = spec
    engine._books["BTCUSDT"] = book
    engine._syncers["BTCUSDT"] = syncer
    engine.metrics.register_symbol("BTCUSDT")
    return engine


def test_post_only_order_is_rejected_if_it_crosses_on_arrival(tmp_path) -> None:
    engine = _synced_engine(tmp_path)
    revision = engine._next_revision("BTCUSDT", "bid")
    engine._handle_place(
        "BTCUSDT",
        {"side": "bid", "price_tick": 102, "qty_lots": 1, "revision": revision},
        1.0,
    )

    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    assert engine.summary()["integrity"]["post_only_rejects"] == 1


def test_gap_immediately_invalidates_live_orders(tmp_path) -> None:
    engine = _synced_engine(tmp_path)
    engine.fill_model.place_order(Order("o1", "BTCUSDT", "bid", 100, 1, 10, 0.0, 1))
    gap = RecordedEvent(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="depthUpdate",
        data={"U": 200, "u": 201, "pu": 199, "b": [], "a": []},
    )

    engine._process_market_record(gap, 1.0)

    assert not engine._syncers["BTCUSDT"].synced
    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    assert engine.summary()["integrity"]["book_invalidations"] == 1


def test_legacy_millisecond_clock_is_normalized_and_regressions_are_clamped(tmp_path) -> None:
    engine = SimulationEngine(_config(tmp_path, mm_enabled=False))
    first = RecordedEvent(1.0, "BTCUSDT", "aggTrade", {"E": 1_700_000_000_100})
    second = RecordedEvent(2.0, "BTCUSDT", "aggTrade", {"E": 1_700_000_000_099})

    assert engine._event_time(first) == 1_700_000_000.1
    assert engine._event_time(second) == 1_700_000_000.1
    assert engine._clock_regressions == 1


def test_non_finite_clock_is_rejected(tmp_path) -> None:
    engine = SimulationEngine(_config(tmp_path, mm_enabled=False))
    record = RecordedEvent(float("nan"), "BTCUSDT", "aggTrade", {})

    with pytest.raises(ReplayClockError, match="Non-finite"):
        engine._event_time(record)


def test_rejected_snapshot_attempt_is_visible_and_later_retry_recovers(tmp_path) -> None:
    fixture = tmp_path / "snapshot-retry.ndjson"
    records = [
        NDJSONRecord(0.0, "*", "captureMeta", {"schemaVersion": 2, "clock": "receive_time"}),
        NDJSONRecord(0.0, "BTCUSDT", "exchangeInfo", {"tickSize": "1", "stepSize": "1"}),
        NDJSONRecord(
            1.0,
            "BTCUSDT",
            "depthUpdate",
            {
                "U": 150,
                "u": 160,
                "pu": 149,
                "b": [["100", "8"]],
                "a": [],
                "_capture": {"recvSeq": 1, "syncEpoch": 1},
            },
        ),
        NDJSONRecord(
            2.0,
            "BTCUSDT",
            "snapshot",
            {
                "lastUpdateId": 100,
                "bids": [["100", "10"]],
                "asks": [["102", "10"]],
                "_capture": {"recvSeq": 2, "syncEpoch": 1},
            },
        ),
        NDJSONRecord(
            3.0,
            "BTCUSDT",
            "snapshot",
            {
                "lastUpdateId": 155,
                "bids": [["99", "10"]],
                "asks": [["102", "10"]],
                "_capture": {"recvSeq": 3, "syncEpoch": 1},
            },
        ),
    ]
    with NDJSONWriter(fixture, flush_every=1) as writer:
        for record in records:
            writer.write(record)

    engine = SimulationEngine(_config(tmp_path, mm_enabled=False))
    engine.run(fixture)
    integrity = engine.summary()["integrity"]

    assert integrity["snapshot_attempts_rejected"] == 1
    assert integrity["book_state"]["BTCUSDT"]["synced_at_end"] is True
    assert engine._books["BTCUSDT"].last_update_id == 160


def test_capture_sync_epoch_transition_invalidates_live_order(tmp_path) -> None:
    engine = _synced_engine(tmp_path)
    engine._capture_sync_epochs["BTCUSDT"] = 1
    engine.fill_model.place_order(Order("old", "BTCUSDT", "bid", 100, 1, 10, 0.0, 1))
    reconnect_snapshot = RecordedEvent(
        1.0,
        "BTCUSDT",
        "snapshot",
        {
            "lastUpdateId": 200,
            "bids": [["99", "10"]],
            "asks": [["102", "10"]],
            "_capture": {"recvSeq": 1, "syncEpoch": 2, "reason": "reconnect"},
        },
    )

    engine._apply_capture_epoch(reconnect_snapshot, 1.0)
    engine._process_market_record(reconnect_snapshot, 1.0)

    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    assert engine.summary()["integrity"]["capture_sync_epoch_transitions"] == 1


def test_gap_detection_invalidates_orders_even_when_auto_resync_is_disabled(tmp_path) -> None:
    engine = _synced_engine(tmp_path)
    engine._syncers["BTCUSDT"].resync_on_gap = False
    engine.fill_model.place_order(Order("old", "BTCUSDT", "bid", 100, 1, 10, 0.0, 1))
    gap = RecordedEvent(
        1.0,
        "BTCUSDT",
        "depthUpdate",
        {"U": 200, "u": 201, "pu": 199, "b": [], "a": []},
    )

    engine._process_market_record(gap, 1.0)

    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    assert engine.summary()["integrity"]["book_invalidations"] == 1


def test_capture_receive_sequence_must_increase(tmp_path) -> None:
    engine = _synced_engine(tmp_path)
    first = RecordedEvent(1.0, "BTCUSDT", "aggTrade", {"_capture": {"recvSeq": 2}})
    duplicate = RecordedEvent(2.0, "BTCUSDT", "aggTrade", {"_capture": {"recvSeq": 2}})
    engine._validate_receive_sequence(first)

    with pytest.raises(CaptureIntegrityError, match="increase"):
        engine._validate_receive_sequence(duplicate)


def test_simulation_outputs_are_content_addressed_and_self_describing(tmp_path) -> None:
    fixture = tmp_path / "causal.ndjson"
    _write_causality_fixture(fixture)
    engine = SimulationEngine(_config(tmp_path, mm_enabled=False))
    metrics = engine.run(fixture)

    summary_path, trades_path, summary = engine.write_outputs(fixture, metrics)

    assert summary_path.exists()
    assert trades_path.exists()
    assert summary["run_id"] in summary_path.name
    assert len(summary["provenance"]["fixture"]["sha256"]) == 64
    assert len(summary["provenance"]["configuration"]["fingerprint_sha256"]) == 64
    assert len(summary["provenance"]["code"]["fingerprint_sha256"]) == 64
    assert summary["economic_assumptions"]["maker_fee_bps"]
