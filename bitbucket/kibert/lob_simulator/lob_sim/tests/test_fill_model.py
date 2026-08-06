from __future__ import annotations

from decimal import Decimal

import pytest

from lob_sim.book.types import AggTradeEvent, LevelChange, SymbolSpec
from lob_sim.sim.fill_model import DuplicateActiveOrderError, PassiveFillModel
from lob_sim.sim.orders import Order, OrderState


def _order(
    order_id: str = "o1",
    *,
    side: str = "bid",
    price_tick: int = 10000,
    qty_lots: int = 2,
    queue_ahead_lots: int = 5,
) -> Order:
    return Order(
        order_id=order_id,
        symbol="BTCUSDT",
        side=side,
        price_tick=price_tick,
        qty_lots=qty_lots,
        queue_ahead_lots=queue_ahead_lots,
        created_ts=0.0,
        remaining_lots=qty_lots,
    )


def _trade(*, price_tick: int, qty_lots: int, buyer_is_maker: bool = True) -> AggTradeEvent:
    return AggTradeEvent(
        symbol="BTCUSDT",
        price_tick=price_tick,
        qty_lots=qty_lots,
        buyer_is_maker=buyer_is_maker,
        ts_local=1.0,
    )


def test_trade_model_does_not_double_consume_matching_depth_change() -> None:
    model = PassiveFillModel("trade")
    order = _order(queue_ahead_lots=5)
    model.place_order(order)

    assert model.apply_agg_trade(_trade(price_tick=10000, qty_lots=4), 1.0) == []
    assert order.queue_ahead_lots == 1

    # The corresponding displayed decrease is the same observable flow, not
    # independent evidence of another execution.
    assert model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 5, 1)], 1.001) == []
    assert order.queue_ahead_lots == 1

    fills = model.apply_agg_trade(_trade(price_tick=10000, qty_lots=2), 2.0)
    assert len(fills) == 1
    assert fills[0].qty_lots == 1
    assert fills[0].cause == "agg_trade"
    assert order.remaining_lots == 1


def test_depth_model_is_explicit_optimistic_sensitivity_and_ignores_trades() -> None:
    model = PassiveFillModel("depth")
    order = _order(queue_ahead_lots=2)
    model.place_order(order)

    assert model.apply_agg_trade(_trade(price_tick=10000, qty_lots=10), 1.0) == []
    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 5, 0)], 1.1)
    assert len(fills) == 1
    assert fills[0].qty_lots == 2
    assert fills[0].cause == "depth_decrease"
    assert order.state is OrderState.FILLED
    assert model.get_order("BTCUSDT", "bid") is None
    assert model.active_order_count == 0
    assert model.order_state_counts()["filled"] == 1
    assert model._orders_by_id == {}


def test_trade_through_fills_remainder_without_using_print_quantity_as_queue_volume() -> None:
    model = PassiveFillModel("trade")
    order = _order(qty_lots=3, queue_ahead_lots=100)
    model.place_order(order)

    fills = model.apply_agg_trade(_trade(price_tick=9999, qty_lots=1), 1.0)
    assert [fill.qty_lots for fill in fills] == [3]
    assert fills[0].cause == "trade_through"
    assert order.state is OrderState.FILLED


def test_stale_cancel_cannot_delete_replacement() -> None:
    model = PassiveFillModel()
    old = _order("old")
    model.place_order(old)
    assert model.cancel_order("old", 1.0)

    replacement = _order("new")
    model.place_order(replacement)
    assert model.cancel_order("old", 2.0) is False
    assert model.get_order("BTCUSDT", "bid") is replacement


def test_duplicate_live_order_is_rejected_instead_of_overwritten() -> None:
    model = PassiveFillModel()
    model.place_order(_order("old"))
    with pytest.raises(DuplicateActiveOrderError):
        model.place_order(_order("new"))


def test_symbol_spec_exact_and_side_aware_quantization() -> None:
    spec = SymbolSpec("BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))
    assert spec.price_to_tick_floor("100.19") == 1001
    assert spec.price_to_tick_ceil("100.11") == 1002
    assert spec.qty_to_lot_floor("0.0019") == 1
    with pytest.raises(ValueError):
        spec.price_to_tick_exact("100.15")
    with pytest.raises(ValueError):
        spec.qty_to_lot_exact("0.0015")
