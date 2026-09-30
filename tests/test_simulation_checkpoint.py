from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.types import AggTradeEvent, DepthUpdateEvent, SnapshotEvent, SymbolSpec
from lob_sim.config import Config, FillAssumptionConfig, load_config
from lob_sim.record.format import NDJSONRecord
from lob_sim.sim.checkpoint import encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.orders import Order


def _config(tmp_path: Path) -> Config:
    return replace(
        load_config(".env.example"),
        record_dir=tmp_path,
        sim_order_latency_ms=0,
        sim_cancel_latency_ms=500,
        sim_kill_switch_enabled=False,
        mm_strategy_profile="baseline",
        mm_requote_ms=100_000,
        mm_order_qty=Decimal("5"),
        mm_max_position=Decimal("6"),
        mm_half_spread_bps=Decimal("0"),
        mm_skew_bps_per_unit=Decimal("0"),
        mm_queue_repost_lots=0,
        fees_maker_bps=Decimal("0"),
        fees_taker_bps=Decimal("0"),
        fill_assumption=FillAssumptionConfig(),
        sim_fill_model="trade",
    )


def _prime(engine: SimulationEngine) -> None:
    engine._specs["BTCUSDT"] = SymbolSpec("BTCUSDT", Decimal("1"), Decimal("1"))
    syncer = engine._get_sync("BTCUSDT")
    assert syncer is not None
    syncer.on_snapshot(SnapshotEvent("BTCUSDT", 100, [(100, 10)], [(102, 10)]))
    syncer.on_depth_update(DepthUpdateEvent("BTCUSDT", 95, 101, 94, [], [], 1.0))
    engine.fill_model.seed_from_snapshot("BTCUSDT", [(100, 10)], [(102, 10)])


def _restore(engine: SimulationEngine) -> SimulationEngine:
    resumed = SimulationEngine(engine.cfg)
    resumed._restore_checkpoint_mutable_state(encode(engine._checkpoint_mutable_state()))
    assert resumed.state_sha256() == engine.state_sha256()
    return resumed


@pytest.mark.parametrize("pending_cancel", [False, True])
def test_checkpoint_preserves_partial_fill_risk_and_cancel_state(tmp_path: Path, pending_cancel: bool) -> None:
    original = SimulationEngine(_config(tmp_path))
    _prime(original)
    original.fill_model.place_order(Order("quote", "BTCUSDT", "bid", 100, 5, created_ts=1.0))
    if pending_cancel:
        order = original.fill_model.get_order_by_id("quote")
        assert order is not None
        original._request_cancel(1.1, "BTCUSDT", order)
    resumed = _restore(original)

    for engine in (original, resumed):
        order = engine.fill_model.get_order_by_id("quote")
        assert order is engine.fill_model._books["BTCUSDT"]["bids"][100][-1]
        fills = engine.fill_model.apply_agg_trade(AggTradeEvent("BTCUSDT", 100, 12, True, 1.2), 1.2)
        assert [fill.qty_lots for fill in fills] == [2]
        assert order is not None and order.remaining_lots == 3
        assert order.state == ("pending_cancel" if pending_cancel else "live")
        engine._handle_trades(fills)
        # Two filled lots plus three live lots reserve five of six lots.
        engine._handle_arrival("BTCUSDT", {"side": "bid", "quote_slot": "other", "price_tick": 99, "qty_lots": 1}, 1.3)
        assert engine.fill_model.get_order("BTCUSDT", "bid", "other") is not None
        engine._handle_cancel({"order_id": "quote"}, 1.6, "BTCUSDT")
        assert order.state == "cancelled"
        assert engine.fill_model.get_order_by_id("quote") is None
        assert all(item.order_id != "quote" for item in engine.fill_model._books["BTCUSDT"]["bids"].get(100, ()))
        engine.metrics.update_unrealized(engine._books, now_ts=7.0, specs=engine._specs)

    assert resumed.state_sha256() == original.state_sha256()
    assert resumed.event_trace == original.event_trace
    assert resumed.metrics.fill_audit_sha256 == original.metrics.fill_audit_sha256
    assert resumed.metrics.markout_audit_sha256 == original.metrics.markout_audit_sha256


def test_checkpoint_preserves_partially_netted_credit_expiration(tmp_path: Path) -> None:
    original = SimulationEngine(_config(tmp_path))
    original.fill_model._record_public_consumption_credit("BTCUSDT", "bid", 100, 10, 1.0, "agg_trade")
    resumed = _restore(original)
    for engine in (original, resumed):
        model = engine.fill_model
        assert model._net_recent_public_consumption("BTCUSDT", "bid", 100, 3, 1.05, "depth_update") == 0
        assert model._public_consumption_expiry_heap[0][3].lots == 7
        model._expire_public_consumption_credits(2_000_000_000)
        assert model.overlap_credit_state()["active_credits"] == 0
        assert model.overlap_credit_state()["expiry_entries"] == 0
    assert resumed.state_sha256() == original.state_sha256()


def test_checkpoint_rejects_disagreement_between_order_lookup_and_matching_queue(tmp_path: Path) -> None:
    original = SimulationEngine(_config(tmp_path))
    original.fill_model.place_order(Order("quote", "BTCUSDT", "bid", 100, 5))
    state = original._checkpoint_mutable_state()
    state["fill_model"] = dict(state["fill_model"])
    state["fill_model"]["_orders"] = {}
    with pytest.raises(ValueError, match="order indexes"):
        SimulationEngine(original.cfg)._restore_checkpoint_mutable_state(encode(state))


def test_checkpoint_resume_with_live_quotes_matches_uninterrupted_partial_fills(tmp_path: Path) -> None:
    fixture = tmp_path / "partial-fills.ndjson"
    records = [
        NDJSONRecord(0.5, "BTCUSDT", "exchangeInfo", {"tickSize": "1", "stepSize": "1"}),
        NDJSONRecord(
            1.0, "BTCUSDT", "snapshot", {"lastUpdateId": 100, "bids": [["100", "10"]], "asks": [["102", "10"]]}
        ),
        NDJSONRecord(2.0, "BTCUSDT", "depthUpdate", {"U": 95, "u": 101, "pu": 94, "b": [], "a": []}),
        NDJSONRecord(3.0, "BTCUSDT", "aggTrade", {"p": "100", "q": "12", "m": True}),
        NDJSONRecord(4.0, "BTCUSDT", "aggTrade", {"p": "100", "q": "1", "m": True}),
        NDJSONRecord(5.0, "BTCUSDT", "aggTrade", {"p": "102", "q": "11", "m": False}),
    ]
    fixture.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = _config(tmp_path)
    uninterrupted = SimulationEngine(cfg)
    uninterrupted.run(fixture)
    paused = SimulationEngine(cfg)
    checkpoint = tmp_path / "live-quotes.checkpoint.json"
    paused.run(fixture, checkpoint_path=checkpoint, stop_after_records=3)
    assert paused.fill_model.get_orders("BTCUSDT", "bid")
    resumed = SimulationEngine(cfg)
    resumed.run(fixture, resume_from=checkpoint)

    assert [row["qty"] for row in uninterrupted.metrics.fills_log] == ["2", "1", "1"]
    assert resumed.state_sha256() == uninterrupted.state_sha256()
    assert resumed.event_trace == uninterrupted.event_trace
    assert resumed.metrics.fill_audit_sha256 == uninterrupted.metrics.fill_audit_sha256
    assert resumed.metrics.markout_audit_sha256 == uninterrupted.metrics.markout_audit_sha256
    assert resumed.metrics.get_summary(resumed._books) == uninterrupted.metrics.get_summary(uninterrupted._books)
