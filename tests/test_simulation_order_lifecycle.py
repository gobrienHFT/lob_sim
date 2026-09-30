from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.types import AggTradeEvent, DepthUpdateEvent, SnapshotEvent, SymbolSpec
from lob_sim.config import Config, load_config
from lob_sim.sim.checkpoint import encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.mm_strategy import QuoteTarget, StrategyDecision


def _engine(tmp_path: Path) -> SimulationEngine:
    cfg: Config = replace(
        load_config(".env.example"),
        record_dir=tmp_path,
        sim_order_latency_ms=50,
        sim_cancel_latency_ms=500,
        sim_kill_switch_enabled=False,
        mm_strategy_profile="baseline",
        mm_requote_ms=10,
        mm_order_qty=Decimal("1"),
        mm_max_position=Decimal("10"),
        mm_half_spread_bps=Decimal("0"),
        mm_skew_bps_per_unit=Decimal("0"),
        mm_queue_repost_lots=100,
    )
    engine = SimulationEngine(cfg)
    engine._specs["BTCUSDT"] = SymbolSpec("BTCUSDT", Decimal("1"), Decimal("1"))
    syncer = engine._get_sync("BTCUSDT")
    assert syncer is not None
    syncer.on_snapshot(SnapshotEvent("BTCUSDT", 100, [(100, 10)], [(102, 10)]))
    syncer.on_depth_update(DepthUpdateEvent("BTCUSDT", 95, 101, 94, [], [], 1.0))
    engine.fill_model.seed_from_snapshot("BTCUSDT", [(100, 10)], [(102, 10)])
    return engine


def test_requotes_during_new_order_transit_keep_one_outbound_intent_per_slot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    for decision_ts in (1.0, 1.01, 1.02, 1.03, 1.04):
        engine._handle_decision("BTCUSDT", decision_ts)
    arrivals = [action for action in engine._actions if action.kind == "order_arrival"]
    assert len(arrivals) == 2
    assert {action.payload["side"] for action in arrivals} == {"bid", "ask"}
    assert engine.metrics.order_arrival_scheduled_count == 2
    engine._drain_events(1.05)
    first_bid = engine.fill_model.get_order("BTCUSDT", "bid")
    assert first_bid is not None
    engine._handle_decision("BTCUSDT", 1.06)
    engine._drain_events(1.10)
    assert engine.fill_model.get_order("BTCUSDT", "bid") is first_bid
    assert first_bid.state == "live"
    assert engine.metrics.cancel_count == engine.metrics.cancel_ack_count == 0


@pytest.mark.parametrize("cancel_before_duplicate", [False, True])
def test_delayed_duplicate_arrival_preserves_live_quote_until_its_cancel_ack(
    tmp_path: Path, cancel_before_duplicate: bool
) -> None:
    engine = _engine(tmp_path)
    engine._handle_decision("BTCUSDT", 1.0)
    engine._drain_events(1.05)
    order = engine.fill_model.get_order("BTCUSDT", "bid")
    assert order is not None
    if cancel_before_duplicate:
        engine._request_cancel(1.1, "BTCUSDT", order)
    engine._handle_arrival("BTCUSDT", {"side": "bid", "quote_slot": "base", "price_tick": 100, "qty_lots": 1}, 1.2)
    assert engine.fill_model.get_order("BTCUSDT", "bid") is order
    assert order.state == ("pending_cancel" if cancel_before_duplicate else "live")
    assert engine.metrics.order_rejected_by_reason["quote_slot_occupied"] == 1
    if not cancel_before_duplicate:
        engine._request_cancel(1.2, "BTCUSDT", order)
    ack_ts = engine._pending_cancel_ack_ts[order.order_id]
    fills = engine.fill_model.apply_agg_trade(AggTradeEvent("BTCUSDT", 100, 11, True, 1.3), 1.3)
    assert [fill.order_id for fill in fills] == [order.order_id]
    assert fills[0].order_state_at_fill == "pending_cancel"
    assert order.state == "filled"
    engine._drain_events(ack_ts)
    assert order.state == "filled"
    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    assert engine.metrics.cancel_count == engine.metrics.cancel_ack_count == 1


def test_checkpoint_retains_outbound_slot_coalescing(tmp_path: Path) -> None:
    original = _engine(tmp_path)
    original._handle_decision("BTCUSDT", 1.0)
    resumed = SimulationEngine(original.cfg)
    resumed._restore_checkpoint_mutable_state(encode(original._checkpoint_mutable_state()))
    for engine in (original, resumed):
        engine._handle_decision("BTCUSDT", 1.01)
        assert sum(action.kind == "order_arrival" for action in engine._actions) == 2
        engine._drain_events(1.05)
    assert resumed.state_sha256() == original.state_sha256()
    assert resumed.event_trace == original.event_trace


def test_replacement_waits_for_cancel_ack_then_new_order_transit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    engine._handle_decision("BTCUSDT", 1.0)
    engine._drain_events(1.05)
    old_bid = engine.fill_model.get_order("BTCUSDT", "bid")
    assert old_bid is not None
    plan = StrategyDecision(quotes=[QuoteTarget("bid", "base", 99, 1, "moved")])
    monkeypatch.setattr(engine.strategy, "propose", lambda *args, **kwargs: plan)
    engine._handle_decision("BTCUSDT", 1.1)
    engine._handle_decision("BTCUSDT", 1.11)
    assert old_bid.state == "pending_cancel"
    assert sum(action.kind == "order_arrival" for action in engine._actions) == 1
    engine._drain_events(1.59)
    assert engine.fill_model.get_order("BTCUSDT", "bid") is old_bid
    engine._drain_events(1.60)
    assert old_bid.state == "cancelled"
    assert engine.fill_model.get_order("BTCUSDT", "bid") is None
    engine._drain_events(1.65 + 1e-12)
    replacement = engine.fill_model.get_order("BTCUSDT", "bid")
    assert replacement is not None and replacement.price_tick == 99
    assert replacement.order_id != old_bid.order_id
