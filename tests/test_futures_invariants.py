from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import AggTradeEvent, DepthUpdateEvent, LevelChange, SnapshotEvent, SymbolSpec
from lob_sim.config import Config, load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
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
    assert resting.remaining_lots == 1
    assert model.depth_levels("BTCUSDT", "ask") == [(10011, 1)]


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
            return StrategyDecision(
                quotes=[QuoteTarget("bid", "base", price_tick=1000, qty_lots=1, refresh_key="old")]
            )
        return StrategyDecision(
            quotes=[QuoteTarget("bid", "base", price_tick=998, qty_lots=1, refresh_key="new")]
        )


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
            ts_local=2.05,
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
    assert summary["quote_count"] == 2
    assert summary["cancel_count"] == 1


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
