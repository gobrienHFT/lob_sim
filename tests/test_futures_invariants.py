from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import AggTradeEvent, DepthUpdateEvent, LevelChange, SnapshotEvent, SymbolSpec
from lob_sim.config import Config, load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.reader import RecordedEvent
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.fill_model import PassiveFillModel
from lob_sim.sim.metrics import SimulationMetrics
from lob_sim.sim.mm_strategy import QuoteTarget, StrategyDecision
from lob_sim.sim.orders import Fill, Order


def _build_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> Config:
    defaults = {
        "RECORD_DIR": str(tmp_path),
        "RECORD_GZIP": "0",
        "LOG_LEVEL": "ERROR",
        "RESYNC_ON_GAP": "1",
        "SIM_ORDER_LATENCY_MS": "0",
        "SIM_CANCEL_LATENCY_MS": "0",
        "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
        "MM_REQUOTE_MS": "1000",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "0.01",
        "MM_HALF_SPREAD_BPS": "0.05",
        "MM_SKEW_BPS_PER_UNIT": "0",
        "MM_QUEUE_REPOST_LOTS": "0",
        "FEES_MAKER_BPS": "0",
        "FEES_TAKER_BPS": "0",
    }
    defaults.update({key: str(value) for key, value in overrides.items()})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return load_config(".env.example")


def _spec() -> SymbolSpec:
    return SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))


def _write_replay_file(path: Path) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
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
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.010"]], "a": [["100.1", "0.010"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
    ]
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.to_json())
            handle.write("\n")
    return path


def test_first_depth_event_must_cover_snapshot_id() -> None:
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)
    sync.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=100,
            bids=[(10000, 10)],
            asks=[(10010, 10)],
        )
    )

    with pytest.raises(BookSyncGapError):
        sync.on_depth_update(
            DepthUpdateEvent(
                symbol="BTCUSDT",
                first_update_id=101,
                final_update_id=110,
                prev_update_id=100,
                bids=[],
                asks=[],
                ts_local=1.0,
            )
        )


def test_capture_epoch_transition_invalidates_book_orders_and_pending_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = SimulationEngine(_build_config(monkeypatch, tmp_path))
    engine._specs["BTCUSDT"] = _spec()
    syncer = engine._get_sync("BTCUSDT")
    assert syncer is not None
    syncer.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=100,
            bids=[(1000, 10)],
            asks=[(1001, 10)],
        )
    )
    syncer.on_depth_update(
        DepthUpdateEvent(
            symbol="BTCUSDT",
            first_update_id=100,
            final_update_id=101,
            prev_update_id=99,
            bids=[],
            asks=[],
            ts_local=1.0,
        )
    )
    order = Order(
        order_id="BTCUSDT-bid-live",
        symbol="BTCUSDT",
        side="bid",
        price_tick=999,
        qty_lots=1,
        quote_slot="base",
        created_ts=1.0,
        remaining_lots=1,
    )
    engine.fill_model.place_order(order)
    engine._schedule(3.0, "order_arrival", "BTCUSDT", {"side": "bid", "qty_lots": 1})

    engine._observe_capture_epoch(
        RecordedEvent(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="captureEvent",
            data={"_capture": {"route": "public", "streamEpoch": 1, "syncEpoch": 1, "recvSeq": 1}},
        ),
        1.0,
    )
    engine._observe_capture_epoch(
        RecordedEvent(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="captureEvent",
            data={"_capture": {"route": "public", "streamEpoch": 2, "syncEpoch": 2, "recvSeq": 2}},
        ),
        2.0,
    )

    assert syncer.synced is False
    assert syncer.ready is False
    assert syncer.book.total_levels() == 0
    assert order.state == "epoch_invalidated"
    assert not engine._actions
    assert engine.metrics.book_invalidation_count == 1


def _capture_event(
    *,
    ts_local: float,
    route: str,
    event: str,
    recv_seq: int,
    stream_epoch: int,
    sync_epoch: int = 1,
    reason: str | None = None,
) -> RecordedEvent:
    capture: dict[str, object] = {
        "route": route,
        "streamEpoch": stream_epoch,
        "syncEpoch": sync_epoch,
        "recvSeq": recv_seq,
    }
    data: dict[str, object] = {"event": event, "route": route, "_capture": capture}
    if reason is not None:
        data["reason"] = reason
        capture["reason"] = reason
    return RecordedEvent(ts_local=ts_local, symbol="BTCUSDT", type="captureEvent", data=data)


def test_trade_disconnect_invalidates_trade_dependent_state_once_then_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = SimulationEngine(_build_config(monkeypatch, tmp_path, SIM_FILL_MODEL="trade"))
    engine._capture_schema_version = 3
    engine._specs["BTCUSDT"] = _spec()
    syncer = engine._get_sync("BTCUSDT")
    assert syncer is not None
    syncer.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=100,
            bids=[(1000, 10)],
            asks=[(1001, 10)],
        )
    )
    syncer.on_depth_update(
        DepthUpdateEvent(
            symbol="BTCUSDT",
            first_update_id=100,
            final_update_id=101,
            prev_update_id=99,
            bids=[],
            asks=[],
            ts_local=1.0,
        )
    )
    engine._observe_capture_epoch(
        _capture_event(
            ts_local=1.0,
            route="market",
            event="connect",
            recv_seq=1,
            stream_epoch=1,
        ),
        1.0,
    )
    order = Order(
        order_id="BTCUSDT-bid-live",
        symbol="BTCUSDT",
        side="bid",
        price_tick=1000,
        qty_lots=1,
        remaining_lots=1,
    )
    engine.fill_model.place_order(order)
    engine._schedule(3.0, "order_arrival", "BTCUSDT", {"side": "bid", "qty_lots": 1})
    engine.strategy.observe_trade(
        AggTradeEvent(
            symbol="BTCUSDT",
            price_tick=1000,
            qty_lots=2,
            buyer_is_maker=False,
            ts_local=1.5,
        )
    )
    assert engine.strategy._recent_trade_imbalance("BTCUSDT") == Decimal("1")

    disconnect = _capture_event(
        ts_local=2.0,
        route="market",
        event="disconnect",
        recv_seq=2,
        stream_epoch=1,
        reason="OSError",
    )
    engine._observe_capture_epoch(disconnect, 2.0)
    engine._observe_capture_epoch(
        _capture_event(
            ts_local=2.1,
            route="market",
            event="disconnect",
            recv_seq=3,
            stream_epoch=1,
            reason="OSError",
        ),
        2.1,
    )

    assert syncer.synced is True
    assert order.state == "epoch_invalidated"
    assert not engine._actions
    assert engine.strategy._recent_trade_imbalance("BTCUSDT") == Decimal("0")
    assert engine._trade_stream_is_valid("BTCUSDT") is False
    assert engine.metrics.trade_stream_invalidation_count == 1
    assert engine.metrics.book_invalidation_count == 0

    engine._observe_capture_epoch(
        _capture_event(
            ts_local=3.0,
            route="market",
            event="connect",
            recv_seq=4,
            stream_epoch=2,
        ),
        3.0,
    )

    assert engine._trade_stream_is_valid("BTCUSDT") is True
    assert engine.metrics.trade_stream_invalidation_count == 1
    assert engine.metrics.trade_stream_recovery_count == 1
    assert syncer.synced is True


def test_implicit_trade_epoch_jump_fails_closed_before_accepting_new_epoch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = SimulationEngine(_build_config(monkeypatch, tmp_path, SIM_FILL_MODEL="trade"))
    engine._capture_schema_version = 3
    engine._specs["BTCUSDT"] = _spec()
    engine._observe_capture_epoch(
        _capture_event(
            ts_local=1.0,
            route="market",
            event="connect",
            recv_seq=1,
            stream_epoch=1,
        ),
        1.0,
    )
    order = Order(
        order_id="BTCUSDT-bid-old-epoch",
        symbol="BTCUSDT",
        side="bid",
        price_tick=1000,
        qty_lots=1,
        remaining_lots=1,
    )
    engine.fill_model.place_order(order)

    engine._observe_capture_epoch(
        RecordedEvent(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={
                "p": "100.0",
                "q": "0.001",
                "m": True,
                "_capture": {
                    "route": "market",
                    "streamEpoch": 2,
                    "syncEpoch": 1,
                    "recvSeq": 2,
                },
            },
        ),
        2.0,
    )

    assert order.state == "epoch_invalidated"
    assert engine._trade_stream_is_valid("BTCUSDT") is True
    assert engine.metrics.trade_stream_invalidation_count == 1
    assert engine.metrics.trade_stream_recovery_count == 1


def test_book_only_baseline_keeps_depth_execution_state_on_trade_outage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = SimulationEngine(
        _build_config(
            monkeypatch,
            tmp_path,
            SIM_FILL_MODEL="depth",
            MM_STRATEGY_PROFILE="baseline",
        )
    )
    engine._capture_schema_version = 3
    engine._specs["BTCUSDT"] = _spec()
    engine._observe_capture_epoch(
        _capture_event(
            ts_local=1.0,
            route="market",
            event="connect",
            recv_seq=1,
            stream_epoch=1,
        ),
        1.0,
    )
    order = Order(
        order_id="BTCUSDT-bid-depth-only",
        symbol="BTCUSDT",
        side="bid",
        price_tick=1000,
        qty_lots=1,
        remaining_lots=1,
    )
    engine.fill_model.place_order(order)
    engine._schedule(3.0, "order_cancel", "BTCUSDT", {"order_id": order.order_id})

    engine._observe_capture_epoch(
        _capture_event(
            ts_local=2.0,
            route="market",
            event="disconnect",
            recv_seq=2,
            stream_epoch=1,
            reason="ConnectionClosed",
        ),
        2.0,
    )

    assert engine._trade_stream_required() is False
    assert engine._trade_stream_is_valid("BTCUSDT") is False
    assert order.state == "live"
    assert len(engine._actions) == 1
    assert engine.metrics.trade_stream_invalidation_count == 1


def test_regressing_stream_epoch_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = SimulationEngine(_build_config(monkeypatch, tmp_path))
    engine._capture_schema_version = 3
    engine._observe_capture_epoch(
        _capture_event(
            ts_local=1.0,
            route="market",
            event="connect",
            recv_seq=1,
            stream_epoch=2,
        ),
        1.0,
    )

    with pytest.raises(ValueError, match="regressing stream epoch"):
        engine._observe_capture_epoch(
            _capture_event(
                ts_local=2.0,
                route="market",
                event="connect",
                recv_seq=2,
                stream_epoch=1,
            ),
            2.0,
        )


def test_schema_v3_trade_outage_has_no_cross_epoch_fill_and_new_epoch_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def captured(
        data: dict[str, object],
        *,
        route: str,
        recv_seq: int,
        stream_epoch: int,
        sync_epoch: int = 1,
    ) -> dict[str, object]:
        return {
            **data,
            "_capture": {
                "route": route,
                "recvSeq": recv_seq,
                "recvMonotonicNs": recv_seq * 1_000_000,
                "streamEpoch": stream_epoch,
                "syncEpoch": sync_epoch,
            },
        }

    records = [
        NDJSONRecord(
            ts_local=0.1,
            symbol="*",
            type="captureMeta",
            data={"schemaVersion": 3, "clock": "receive_time"},
        ),
        NDJSONRecord(
            ts_local=0.2,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data=captured(
                {"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
                route="control",
                recv_seq=1,
                stream_epoch=0,
                sync_epoch=0,
            ),
        ),
        NDJSONRecord(
            ts_local=0.3,
            symbol="BTCUSDT",
            type="captureEvent",
            data=captured(
                {"event": "connect", "route": "public"},
                route="public",
                recv_seq=2,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.31,
            symbol="BTCUSDT",
            type="captureEvent",
            data=captured(
                {"event": "connect", "route": "market"},
                route="market",
                recv_seq=3,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.4,
            symbol="BTCUSDT",
            type="snapshot",
            data=captured(
                snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
                route="public",
                recv_seq=4,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="depthUpdate",
            data=captured(
                {"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
                route="public",
                recv_seq=5,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.6,
            symbol="BTCUSDT",
            type="captureEvent",
            data=captured(
                {"event": "disconnect", "route": "market", "reason": "ConnectionClosed"},
                route="market",
                recv_seq=6,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.7,
            symbol="BTCUSDT",
            type="aggTrade",
            data=captured(
                {"p": "100.0", "q": "0.002", "m": True},
                route="market",
                recv_seq=7,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=0.8,
            symbol="BTCUSDT",
            type="captureEvent",
            data=captured(
                {"event": "connect", "route": "market"},
                route="market",
                recv_seq=8,
                stream_epoch=2,
            ),
        ),
        NDJSONRecord(
            ts_local=0.9,
            symbol="BTCUSDT",
            type="depthUpdate",
            data=captured(
                {"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
                route="public",
                recv_seq=9,
                stream_epoch=1,
            ),
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data=captured(
                {"p": "100.0", "q": "0.002", "m": True},
                route="market",
                recv_seq=10,
                stream_epoch=2,
            ),
        ),
    ]
    replay_path = tmp_path / "trade_epoch_outage.ndjson"
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        SIM_FILL_MODEL="trade",
        MM_REQUOTE_MS="1000",
        MM_HALF_SPREAD_BPS="0",
        MM_MAX_POSITION="1",
    )
    engine = SimulationEngine(cfg)
    engine.strategy = _QueueObserveHoldStrategy()

    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)
    summary.update(engine._summary_annotations())

    assert summary["fill_count"] == 1
    assert summary["fills"][0]["fill_source"] == "agg_trade"
    assert summary["trade_stream_invalidation_count"] == 1
    assert summary["trade_stream_recovery_count"] == 1
    assert summary["book_invalidation_count"] == 0
    assert summary["integrity"]["book_state"]["BTCUSDT"]["synced_at_end"] is True
    assert summary["integrity"]["stream_state"]["BTCUSDT"]["trade_stream_valid"] is True
    assert summary["integrity"]["stream_state"]["BTCUSDT"]["market_stream_epoch"] == 2

    invalidations = [row for row in engine.event_trace if row["event_type"] == "trade_epoch_invalidated"]
    ignored = [row for row in engine.event_trace if row["event_type"] == "trade_ignored_invalid_epoch"]
    recoveries = [row for row in engine.event_trace if row["event_type"] == "trade_stream_recovered"]
    arrivals = [row for row in engine.event_trace if row["event_type"] == "order_arrival"]
    fills = [row for row in engine.event_trace if row["event_type"] == "fill"]

    assert len(invalidations) == 1
    assert invalidations[0]["details"]["invalidated_active_order_count"] == 1
    assert len(ignored) == 1
    assert len(recoveries) == 1
    assert len(arrivals) >= 2
    assert len(fills) == 1
    assert fills[0]["order_id"] == arrivals[1]["order_id"]
    assert fills[0]["order_id"] != arrivals[0]["order_id"]


def test_capture_meta_participates_in_global_receive_sequence_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    duplicate_capture = {
        "route": "control",
        "recvSeq": 1,
        "recvMonotonicNs": 1,
        "streamEpoch": 0,
        "syncEpoch": 0,
    }
    records = [
        NDJSONRecord(
            ts_local=0.1,
            symbol="*",
            type="captureMeta",
            data={
                "schemaVersion": 3,
                "clock": "receive_time",
                "_capture": duplicate_capture,
            },
        ),
        NDJSONRecord(
            ts_local=0.2,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={
                "symbol": "BTCUSDT",
                "tickSize": "0.1",
                "stepSize": "0.001",
                "_capture": duplicate_capture,
            },
        ),
    ]
    replay_path = tmp_path / "duplicate_capture_meta_sequence.ndjson"
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-increasing receive sequence"):
        SimulationEngine(_build_config(monkeypatch, tmp_path)).run(replay_path)


def test_fifo_queue_priority_keeps_later_venue_volume_behind_resting_strategy_order() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 2)], asks=[(10010, 2)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=1,
        remaining_lots=1,
        created_ts=0.0,
    )
    model.place_order(order)

    assert model.queue_ahead_lots("BTCUSDT", order) == 2

    model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 2, 4)], 1.0)
    assert model.queue_ahead_lots("BTCUSDT", order) == 2

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 4, 3)], 2.0)
    assert fills == []
    assert model.queue_ahead_lots("BTCUSDT", order) == 1

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 3, 2)], 3.0)
    assert fills == []
    assert model.queue_ahead_lots("BTCUSDT", order) == 0

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 2, 1)], 4.0)
    assert len(fills) == 1
    assert fills[0].order_id == "strategy-bid"
    assert fills[0].qty_lots == 1
    assert fills[0].source == "depth_update"


def test_observed_queue_ahead_does_not_mutate_fifo_consumption_state() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 2)], asks=[(10010, 2)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=1,
        remaining_lots=1,
        created_ts=0.0,
    )
    model.place_order(order)

    order.queue_ahead_lots = model.queue_ahead_lots("BTCUSDT", order)
    assert order.queue_ahead_lots == 2

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 2, 0)], 1.0)
    assert fills == []

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=1.2),
        1.2,
    )
    assert [(fill.order_id, fill.qty_lots, fill.source) for fill in fills] == [("strategy-bid", 1, "agg_trade")]
    assert model.get_order("BTCUSDT", "bid") is None


def test_partial_fills_accumulate_after_queue_ahead_is_consumed() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=3,
        remaining_lots=3,
        created_ts=0.0,
    )
    model.place_order(order)

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)
    assert fills == []
    assert model.get_order("BTCUSDT", "bid") is not None

    first = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=2.0),
        2.0,
    )
    assert [fill.qty_lots for fill in first] == [1]
    assert [fill.source for fill in first] == ["agg_trade"]
    resting = model.get_order("BTCUSDT", "bid")
    assert resting is not None
    assert resting.remaining_lots == 2

    second = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=3.0),
        3.0,
    )
    assert [fill.qty_lots for fill in second] == [2]
    assert model.get_order("BTCUSDT", "bid") is None


def test_recent_agg_trade_then_depth_update_does_not_double_count_same_consumption() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=2,
        remaining_lots=2,
        created_ts=0.0,
    )
    model.place_order(order)

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.0),
        1.0,
    )
    assert [(fill.qty_lots, fill.source) for fill in fills] == [(1, "agg_trade")]
    resting = model.get_order("BTCUSDT", "bid")
    assert resting is not None
    assert resting.remaining_lots == 1

    depth_fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.05)
    assert depth_fills == []
    resting = model.get_order("BTCUSDT", "bid")
    assert resting is not None
    assert resting.remaining_lots == 1


def test_recent_depth_update_then_agg_trade_nets_overlapping_consumption() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=2,
        remaining_lots=2,
        created_ts=0.0,
    )
    model.place_order(order)

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)
    assert fills == []

    trade_fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.05),
        1.05,
    )
    assert [(fill.qty_lots, fill.source) for fill in trade_fills] == [(1, "agg_trade")]
    resting = model.get_order("BTCUSDT", "bid")
    assert resting is not None
    assert resting.remaining_lots == 1


def test_cancelled_resting_order_cannot_fill_after_queue_ahead_clears() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 2)], asks=[(10010, 2)])

    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=1,
        remaining_lots=1,
        created_ts=0.0,
    )
    model.place_order(order)

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 2, 0)], 1.0)
    assert fills == []
    assert model.queue_ahead_lots("BTCUSDT", order) == 0

    model.cancel_order(order.order_id)

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=2.0),
        2.0,
    )
    assert fills == []
    assert model.get_order("BTCUSDT", "bid") is None


def test_marketable_limit_generates_taker_fill_and_posts_remainder() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1), (10011, 1)])

    fills = model.place_order(
        Order(
            order_id="crossing-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=10010,
            qty_lots=2,
            remaining_lots=2,
            created_ts=1.0,
        )
    )

    assert [(fill.price_tick, fill.qty_lots, fill.maker) for fill in fills] == [(10010, 1, False)]
    assert [fill.source for fill in fills] == ["taker_order"]
    resting = model.get_order("BTCUSDT", "bid")
    assert resting is not None
    assert resting.order_id == "crossing-bid"
    assert resting.active is True
    assert resting.remaining_lots == 1
    assert model.depth_levels("BTCUSDT", "ask") == [(10011, 1)]


def test_marketable_limit_stops_before_self_trading_resting_strategy_liquidity() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])

    own_ask = Order(
        order_id="own-resting-ask",
        symbol="BTCUSDT",
        side="ask",
        price_tick=10010,
        qty_lots=1,
        quote_slot="maker-ask",
        remaining_lots=1,
        created_ts=0.0,
    )
    assert model.place_order(own_ask) == []

    crossing_bid = Order(
        order_id="crossing-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10010,
        qty_lots=2,
        quote_slot="crossing-bid",
        remaining_lots=2,
        created_ts=1.0,
    )
    fills = model.place_order(crossing_bid)

    assert [(fill.order_id, fill.price_tick, fill.qty_lots, fill.maker, fill.source) for fill in fills] == [
        ("crossing-bid", 10010, 1, False, "taker_order")
    ]
    assert model.last_self_trade_prevented is True
    assert crossing_bid.remaining_lots == 1
    assert crossing_bid.active is False
    assert model.get_order("BTCUSDT", "ask", quote_slot="maker-ask") is own_ask
    assert own_ask.remaining_lots == 1
    assert model.get_order("BTCUSDT", "bid", quote_slot="crossing-bid") is None
    assert model.depth_levels("BTCUSDT", "ask") == [(10010, 1)]


def test_marketable_limit_crossing_only_own_quote_is_expired_not_posted() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[])

    own_ask = Order(
        order_id="own-resting-ask",
        symbol="BTCUSDT",
        side="ask",
        price_tick=10010,
        qty_lots=1,
        quote_slot="maker-ask",
        remaining_lots=1,
        created_ts=0.0,
    )
    model.place_order(own_ask)

    crossing_bid = Order(
        order_id="crossing-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10010,
        qty_lots=1,
        quote_slot="crossing-bid",
        remaining_lots=1,
        created_ts=1.0,
    )
    fills = model.place_order(crossing_bid)

    assert fills == []
    assert model.last_self_trade_prevented is True
    assert crossing_bid.remaining_lots == 1
    assert crossing_bid.active is False
    assert model.get_order("BTCUSDT", "ask", quote_slot="maker-ask") is own_ask
    assert model.get_order("BTCUSDT", "bid", quote_slot="crossing-bid") is None
    assert model.depth_levels("BTCUSDT", "bid") == [(10000, 1)]
    assert model.depth_levels("BTCUSDT", "ask") == [(10010, 1)]


def test_market_order_sweeps_visible_depth_levels_as_taker() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1), (10011, 2)])

    fills = model.place_order(
        Order(
            order_id="market-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=None,
            qty_lots=3,
            remaining_lots=3,
            created_ts=1.0,
            order_type="market",
        )
    )

    assert [(fill.price_tick, fill.qty_lots, fill.maker) for fill in fills] == [
        (10010, 1, False),
        (10011, 2, False),
    ]
    assert [fill.source for fill in fills] == ["taker_order", "taker_order"]
    assert model.get_order("BTCUSDT", "bid") is None
    assert model.depth_levels("BTCUSDT", "ask") == []


class _ScriptedReplaceStrategy:
    def __init__(self) -> None:
        self.decisions = 0

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        return order is not None and order.refresh_key != target.refresh_key

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        self.decisions += 1
        if self.decisions == 1:
            return StrategyDecision(quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="old")])
        return StrategyDecision(quotes=[QuoteTarget("bid", "base", price_tick=998, qty_lots=1, refresh_key="new")])


class _StaticBidStrategy:
    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        _ = order
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        return StrategyDecision(quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="static")])


class _CancelAfterFirstQuoteStrategy:
    def __init__(self) -> None:
        self.decisions = 0

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        _ = order
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        self.decisions += 1
        if self.decisions == 1:
            return StrategyDecision(
                quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="one-shot")]
            )
        return StrategyDecision(quotes=[], reason="pull_quotes")


class _QueueRefreshSpyStrategy:
    def __init__(self) -> None:
        self.observed_queue_ahead: list[int] = []

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        if order is None:
            return False
        self.observed_queue_ahead.append(order.queue_ahead_lots)
        return order.queue_ahead_lots > 0

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        return StrategyDecision(quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="static")])


class _QueueObserveHoldStrategy:
    def __init__(self) -> None:
        self.observed_queue_ahead: list[int] = []

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        if order is not None:
            self.observed_queue_ahead.append(order.queue_ahead_lots)
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        return StrategyDecision(quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="static")])


class _SelfTradePreventionStrategy:
    def __init__(self) -> None:
        self.decisions = 0

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        _ = order
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        self.decisions += 1
        if self.decisions == 1:
            return StrategyDecision(
                quotes=[QuoteTarget("ask", "maker_ask", price_tick=1001, qty_lots=1, refresh_key="resting-ask")]
            )
        return StrategyDecision(
            quotes=[
                QuoteTarget("ask", "maker_ask", price_tick=1001, qty_lots=1, refresh_key="resting-ask"),
                QuoteTarget("bid", "crossing_bid", price_tick=1001, qty_lots=1, refresh_key="crossing-bid"),
            ]
        )


def test_strategy_decisions_are_not_backfilled_before_first_depth_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "no_pre_sync_decision_backfill.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(monkeypatch, tmp_path, MM_REQUOTE_MS="100", MM_MAX_POSITION="1")

    engine = SimulationEngine(cfg)
    engine.strategy = _StaticBidStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    decision_ts = [row["ts_local"] for row in engine.event_trace if row["event_type"] == "decision"]
    assert decision_ts == [2.0]
    assert summary["quote_count"] == 1


def test_first_strategy_decision_does_not_predate_snapshot_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "snapshot_time_watermark.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=1.5,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(monkeypatch, tmp_path, MM_REQUOTE_MS="100", MM_MAX_POSITION="1")

    engine = SimulationEngine(cfg)
    engine.strategy = _StaticBidStrategy()
    engine.run(replay_path)

    decision_ts = [row["ts_local"] for row in engine.event_trace if row["event_type"] == "decision"]
    assert decision_ts[0] == 2.0


def test_queue_ahead_state_is_visible_to_strategy_refresh_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "queue_refresh_trace.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="200",
        SIM_CANCEL_LATENCY_MS="0",
        MM_MAX_POSITION="1",
    )

    strategy = _QueueRefreshSpyStrategy()
    engine = SimulationEngine(cfg)
    engine.strategy = strategy
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert strategy.observed_queue_ahead == [2]
    assert summary["quote_count"] == 2
    assert summary["cancel_count"] == 1

    arrival_rows = [row for row in engine.event_trace if row["event_type"] == "order_arrival"]
    assert arrival_rows[0]["details"]["queue_ahead_lots_after_arrival"] == 2

    cancel_rows = [row for row in engine.event_trace if row["event_type"] == "cancel_requested"]
    assert len(cancel_rows) == 1
    cancel_details = cancel_rows[0]["details"]
    assert cancel_details["reason"] == "replace_quote"
    assert cancel_details["queue_ahead_lots"] == 2
    assert cancel_details["refresh_requested"] is True
    assert cancel_details["price_changed"] is False


def test_strategy_queue_observation_does_not_create_extra_fill_queue_ahead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "queue_observation_is_read_only.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.002"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=3.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 107, "u": 107, "pu": 106, "b": [["100.0", "0.000"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=3.3,
            symbol="BTCUSDT",
            type="aggTrade",
            # The default execution scenario is trade-only: displayed
            # decreases are not also counted as executions.  Three lots
            # consume the two-lot queue ahead and fill the one-lot order.
            data={"p": "100.0", "q": "0.003", "m": True},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="1000",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="0",
        MM_ORDER_QTY="0.001",
        MM_MAX_POSITION="1",
        MM_HALF_SPREAD_BPS="0",
        FEES_MAKER_BPS="0",
    )

    strategy = _QueueObserveHoldStrategy()
    engine = SimulationEngine(cfg)
    engine.strategy = strategy
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert strategy.observed_queue_ahead == [2]
    assert summary["fill_count"] == 1
    assert summary["fills"][0]["fill_source"] == "agg_trade"
    assert summary["fills"][0]["queue_ahead_lots"] == 0
    assert summary["resting_arrival_queue_samples"] == 1
    assert summary["arrival_with_queue_ahead_count"] == 1
    assert summary["avg_arrival_queue_ahead_lots"] == pytest.approx(2.0)
    assert summary["max_arrival_queue_ahead_lots"] == 2
    assert summary["fill_source_counts"] == {"depth_update": 0, "agg_trade": 1, "taker_order": 0}
    assert summary["public_consumption_summary"] == {
        "overlap_window_seconds": 0.125,
        "sources": {
            "depth_update": {
                "observed_lots": 2,
                "modeled_lots": 2,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 0,
                "unmatched_lots": 2,
            },
            "agg_trade": {
                "observed_lots": 3,
                "modeled_lots": 3,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 3,
                "unmatched_lots": 0,
            },
        },
        "total_observed_lots": 5,
        "total_modeled_lots": 5,
        "total_overlap_netted_lots": 0,
        "total_queue_consumed_lots": 3,
        "total_unmatched_lots": 2,
    }


def test_engine_summary_counts_self_trade_prevention_and_trace_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "self_trade_prevention_trace.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.5", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.5", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.001"]], "a": [["100.5", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="1000",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="0",
        MM_MAX_POSITION="1",
        MM_HALF_SPREAD_BPS="0",
    )

    engine = SimulationEngine(cfg)
    engine.strategy = _SelfTradePreventionStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["fill_count"] == 0
    assert summary["self_trade_prevention_count"] == 1
    assert summary["fill_source_counts"] == {"depth_update": 0, "agg_trade": 0, "taker_order": 0}
    assert summary["order_lifecycle_counts"] == {
        "arrival_scheduled": 2,
        "arrived": 2,
        "rested_after_arrival": 1,
        "immediate_fill_arrivals": 0,
        "expired_unfilled_arrivals": 1,
        "cancel_requested": 0,
        "cancel_acknowledged": 0,
        "self_trade_prevented": 1,
    }
    assert engine.fill_model.get_order("BTCUSDT", "ask", "maker_ask") is not None
    assert engine.fill_model.get_order("BTCUSDT", "bid", "crossing_bid") is None

    arrival_rows = [row for row in engine.event_trace if row["event_type"] == "order_arrival"]
    self_trade_rows = [row for row in arrival_rows if row["details"].get("self_trade_prevented") is True]
    assert len(self_trade_rows) == 1
    assert self_trade_rows[0]["side"] == "bid"
    assert self_trade_rows[0]["quote_slot"] == "crossing_bid"
    assert self_trade_rows[0]["details"]["remaining_lots_after_arrival"] == 1
    assert self_trade_rows[0]["details"]["resting_after_arrival"] is False


def test_replace_order_waits_for_cancel_latency_before_new_arrival(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "cancel_replace_race.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("99.9", "0.001")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["99.9", "0.001"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.16,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["99.9", "0.001"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.20,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="150",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="1000",
        MM_ORDER_QTY="0.001",
        MM_MAX_POSITION="1",
        MM_HALF_SPREAD_BPS="0",
        FEES_MAKER_BPS="0",
    )
    engine = SimulationEngine(cfg)
    engine.strategy = _ScriptedReplaceStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["fill_count"] == 1
    assert summary["fills"][0]["order_id"].startswith("BTCUSDT-bid-")
    assert summary["fills"][0]["price"] == "100.0"
    assert summary["fills"][0]["maker"] is True
    assert summary["fills"][0]["fill_source"] == "agg_trade"
    assert summary["fill_source_counts"] == {"depth_update": 0, "agg_trade": 1, "taker_order": 0}
    assert summary["quote_count"] == 2
    assert summary["cancel_count"] == 1
    assert summary["order_lifecycle_counts"] == {
        "arrival_scheduled": 2,
        "arrived": 2,
        "rested_after_arrival": 2,
        "immediate_fill_arrivals": 0,
        "expired_unfilled_arrivals": 0,
        "cancel_requested": 1,
        "cancel_acknowledged": 1,
        "self_trade_prevented": 0,
    }


def _write_cancel_latency_boundary_replay(path: Path, trade_ts: float) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.001"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=trade_ts,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def _cancel_latency_boundary_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Config:
    return _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="100",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="100",
        MM_ORDER_QTY="0.001",
        MM_MAX_POSITION="1",
        MM_HALF_SPREAD_BPS="0",
        FEES_MAKER_BPS="0",
    )


def test_public_trade_before_cancel_ack_can_still_fill_old_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = _write_cancel_latency_boundary_replay(tmp_path / "trade_before_cancel_ack.ndjson", 2.199)
    cfg = _cancel_latency_boundary_config(monkeypatch, tmp_path)

    engine = SimulationEngine(cfg)
    engine.strategy = _CancelAfterFirstQuoteStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["fill_count"] == 1
    assert summary["fills"][0]["fill_source"] == "agg_trade"
    assert summary["fills"][0]["queue_ahead_lots"] == 0
    assert summary["cancel_count"] == 1
    assert summary["order_lifecycle_counts"]["cancel_acknowledged"] == 1

    fill_ts = [row["ts_local"] for row in engine.event_trace if row["event_type"] == "fill"]
    cancel_ack_ts = [row["ts_local"] for row in engine.event_trace if row["event_type"] == "cancel_ack"]
    assert fill_ts == [2.199]
    assert cancel_ack_ts == [2.2]

    pull_decision = [
        row for row in engine.event_trace if row["event_type"] == "decision" and row["details"]["quote_count"] == 0
    ]
    assert pull_decision[0]["ts_local"] == 2.1
    assert {row["details"]["reason"] for row in pull_decision} == {"pull_quotes"}


def test_cancel_ack_at_same_timestamp_precedes_public_trade_fill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = _write_cancel_latency_boundary_replay(tmp_path / "trade_at_cancel_ack.ndjson", 2.2)
    cfg = _cancel_latency_boundary_config(monkeypatch, tmp_path)

    engine = SimulationEngine(cfg)
    engine.strategy = _CancelAfterFirstQuoteStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["fill_count"] == 0
    assert summary["fill_source_counts"] == {"depth_update": 0, "agg_trade": 0, "taker_order": 0}
    assert summary["cancel_count"] == 1
    assert summary["order_lifecycle_counts"]["cancel_acknowledged"] == 1

    same_ts_rows = [
        row
        for row in engine.event_trace
        if row["ts_local"] == 2.2 and row["event_type"] in {"cancel_ack", "market_record", "fill"}
    ]
    assert [row["event_type"] for row in same_ts_rows] == ["cancel_ack", "market_record"]
    assert same_ts_rows[1]["source"] == "aggTrade"

    pull_decision = [
        row for row in engine.event_trace if row["event_type"] == "decision" and row["details"]["quote_count"] == 0
    ]
    assert pull_decision[0]["ts_local"] == 2.1
    assert {row["details"]["reason"] for row in pull_decision} == {"pull_quotes"}


def test_simulation_engine_is_deterministic_for_same_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay_path = _write_replay_file(tmp_path / "deterministic.ndjson")
    cfg = _build_config(monkeypatch, tmp_path)

    first_engine = SimulationEngine(cfg)
    first_metrics = first_engine.run(replay_path)
    first_summary = first_metrics.get_summary(first_engine._books)

    second_engine = SimulationEngine(cfg)
    second_metrics = second_engine.run(replay_path)
    second_summary = second_metrics.get_summary(second_engine._books)

    assert first_summary == second_summary


def test_simulation_summary_surfaces_event_counts_and_book_gaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "gap_diagnostics.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
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
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.010"]], "a": [["100.1", "0.011"]]},
        ),
        NDJSONRecord(
            ts_local=2.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 999, "b": [["100.0", "0.009"]], "a": [["100.1", "0.011"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(monkeypatch, tmp_path, MM_ENABLED="0", RESYNC_ON_GAP="1")

    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["event_counts"] == {
        "records_processed": 4,
        "exchange_info": 1,
        "snapshot": 1,
        "depth_update": 2,
        "agg_trade": 0,
        "depth_changes_applied": 1,
        "book_gap_count": 1,
    }
    assert summary["book_gap_count_by_symbol"] == {"BTCUSDT": 1}


def test_simulation_records_non_resync_gap_without_applying_bad_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "non_resync_gap.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
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
            ts_local=2.1,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 999, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(monkeypatch, tmp_path, MM_ENABLED="0", RESYNC_ON_GAP="0")

    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["event_counts"]["book_gap_count"] == 1
    assert summary["event_counts"]["depth_changes_applied"] == 2
    assert summary["book_gap_count_by_symbol"] == {"BTCUSDT": 1}
    assert engine._books["BTCUSDT"].bids == {}
    assert engine._books["BTCUSDT"].asks == {}
    assert engine._syncers["BTCUSDT"].synced is False


def test_simulation_event_trace_exports_order_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "trace_lifecycle.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.010")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.010"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="1000",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="0",
    )

    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    output_files, summary = engine.write_outputs(str(replay_path), metrics)

    with output_files["event_trace"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    event_types = [row["event_type"] for row in rows]
    assert summary["event_trace_count"] == len(rows)
    assert summary["order_lifecycle_counts"] == {
        "arrival_scheduled": 3,
        "arrived": 3,
        "rested_after_arrival": 3,
        "immediate_fill_arrivals": 0,
        "expired_unfilled_arrivals": 0,
        "cancel_requested": 0,
        "cancel_acknowledged": 0,
        "self_trade_prevented": 0,
    }
    assert "fill_rate" not in summary
    assert summary["quote_fill_probability"] == pytest.approx(1 / 3)
    assert summary["fills_per_quote_request"] == pytest.approx(1 / 3)
    assert summary["fills_per_arrived_order"] == pytest.approx(1 / 3)
    assert summary["public_consumption_summary"]["sources"]["agg_trade"]["observed_lots"] == 2
    assert summary["public_consumption_summary"]["sources"]["agg_trade"]["modeled_lots"] == 2
    assert summary["public_consumption_summary"]["sources"]["agg_trade"]["queue_consumed_lots"] == 2
    assert "market_record" in event_types
    assert "decision" in event_types
    assert "order_arrival_scheduled" in event_types
    assert "order_arrival" in event_types
    assert "queue_consumption" in event_types
    assert "fill" in event_types
    assert "markout" in event_types
    assert [float(row["ts_local"]) for row in rows] == sorted(float(row["ts_local"]) for row in rows)

    queue_row = next(row for row in rows if row["event_type"] == "queue_consumption" and row["source"] == "agg_trade")
    queue_details = json.loads(queue_row["details"])
    assert queue_row["side"] == "bid"
    assert queue_row["price_tick"] == "1000"
    assert queue_row["qty_lots"] == "2"
    assert queue_details == {
        "fill_assumption_profile": "base",
        "modeled_lots": 2,
        "observed_lots": 2,
        "overlap_netted_lots": 0,
        "overlap_window_seconds": 0.125,
        "queue_consumed_lots": 2,
        "unmatched_lots": 0,
    }

    fill_row = next(row for row in rows if row["event_type"] == "fill")
    assert fill_row["symbol"] == "BTCUSDT"
    assert fill_row["side"] == "bid"
    assert fill_row["price_tick"] == "1000"
    assert fill_row["qty_lots"] == "1"
    assert fill_row["fill_source"] == "agg_trade"
    fill_details = json.loads(fill_row["details"])
    assert fill_details["maker"] is True
    assert fill_details["price"] == "100.0"
    assert fill_details["qty"] == "0.001"
    assert fill_details["notional"] == "0.1000"
    assert fill_details["contract_multiplier"] == "1"
    assert fill_details["fee_bps"] == "0"
    assert fill_details["fee"] == "0.0000"
    assert fill_details["mid_at_fill"] == "100.05"
    assert fill_details["spread_capture"] == "0.05"
    assert fill_details["spread_capture_value"] == "0.00005"
    assert fill_details["time_in_book_ms"] == pytest.approx(1000.0)
    assert fill_details["markout_horizon"] == 1.0
    assert fill_details["regime"] == "tight_sell"

    markout_row = next(row for row in rows if row["event_type"] == "markout")
    markout_details = json.loads(markout_row["details"])
    assert markout_row["source"] == "metrics"
    assert markout_row["side"] == "bid"
    assert markout_row["price_tick"] == "1000"
    assert markout_row["qty_lots"] == "1"
    assert markout_row["order_id"] == fill_row["order_id"]
    assert markout_row["fill_source"] == "agg_trade"
    assert markout_details["fill_ts_local"] == 3.0
    assert markout_details["deadline_ts"] == 4.0
    assert markout_details["horizon"] == 1.0
    assert markout_details["fill_price"] == "100.0"
    assert markout_details["qty"] == "0.001"
    assert markout_details["mid_after"] == "100.05"
    assert markout_details["markout"] == "0.05"
    assert markout_details["adverse"] is False
    assert summary["markout_by_fill_source"]["agg_trade"]["samples"] == 1

    decision_row = next(row for row in rows if row["event_type"] == "decision")
    decision_details = json.loads(decision_row["details"])
    diagnostics = decision_details["diagnostics"]
    assert diagnostics["profile"] == "baseline"
    assert diagnostics["best_bid_tick"] == 1000
    assert diagnostics["best_ask_tick"] == 1001
    assert "half_spread_bps" in diagnostics
    assert "half_spread_ticks" in diagnostics

    order_row = next(row for row in rows if row["event_type"] == "order_arrival")
    assert order_row["order_id"].startswith("BTCUSDT-bid-")
    assert order_row["quote_slot"] == "base"


def test_kill_switch_traces_risk_halt_and_clears_resting_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = tmp_path / "kill_switch_trace.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.2", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.1,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 106,
                "u": 106,
                "pu": 105,
                "b": [["100.0", "0"], ["90.0", "0.001"]],
                "a": [["100.2", "0"], ["90.2", "0.001"]],
            },
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 107, "u": 107, "pu": 106, "b": [["90.0", "0.001"]], "a": [["90.2", "0.001"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        MM_REQUOTE_MS="1000",
        SIM_ORDER_LATENCY_MS="0",
        SIM_CANCEL_LATENCY_MS="0",
        SIM_KILL_SWITCH_ENABLED="1",
        SIM_KILL_MAX_DRAWDOWN="0.001",
        MM_MAX_POSITION="1",
        MM_HALF_SPREAD_BPS="0",
    )

    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["kill_switch_triggered"] is True
    assert str(summary["kill_switch_reason"]).startswith("max_drawdown_exceeded")
    assert summary["fill_count"] == 1
    assert len([row for row in engine.event_trace if row["event_type"] == "decision"]) == 1
    assert engine.fill_model.get_orders("BTCUSDT", "bid") == []
    assert engine.fill_model.get_orders("BTCUSDT", "ask") == []

    risk_rows = [row for row in engine.event_trace if row["event_type"] == "risk_halt"]
    assert len(risk_rows) == 1
    assert risk_rows[0]["ts_local"] == pytest.approx(2.2)
    assert risk_rows[0]["source"] == "risk"
    details = risk_rows[0]["details"]
    assert details["reason"] == summary["kill_switch_reason"]
    assert details["phase"] == "market_record"
    assert details["canceled_order_count"] == 1
    assert details["canceled_orders_by_symbol"] == {"BTCUSDT": {"bid": 0, "ask": 1, "total": 1}}
    assert Decimal(details["max_drawdown"]) >= Decimal("0.001")


def test_markout_inventory_and_pnl_sanity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = _build_config(monkeypatch, tmp_path)
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    metrics.register_symbol("BTCUSDT")

    book.reset_from_snapshot(1, bids={100: 1}, asks={102: 1})
    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=1,
            maker=True,
            order_id="fill-1",
            queue_ahead_lots=0,
            created_ts=0.0,
        ),
        book,
        book.mid_price(),
    )

    assert metrics.inventory_lots("BTCUSDT") == 1

    book.reset_from_snapshot(2, bids={101: 1}, asks={103: 1})
    metrics.update_unrealized({"BTCUSDT": book}, now_ts=1.1)
    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["realized_pnl"] == pytest.approx(0.0)
    assert summary["unrealized_pnl"] == pytest.approx(2.0)
    assert summary["avg_markout_1s"] == pytest.approx(2.0)
    assert summary["adverse_fill_rate_1s"] == pytest.approx(0.0)
    assert summary["total_inventory"] == pytest.approx(1.0)
    assert summary["fill_source_counts"] == {"depth_update": 1, "agg_trade": 0, "taker_order": 0}
