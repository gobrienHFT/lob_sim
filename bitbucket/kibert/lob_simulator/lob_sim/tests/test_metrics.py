from __future__ import annotations

from decimal import Decimal

import pytest
from config_fixtures import make_config

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import SymbolSpec
from lob_sim.sim.metrics import SimulationMetrics
from lob_sim.sim.orders import Fill


def _book(
    symbol: str = "BTCUSDT",
    *,
    tick_size: str = "1",
    step_size: str = "1",
    bid_tick: int = 100,
    ask_tick: int = 102,
) -> LocalOrderBook:
    spec = SymbolSpec(symbol, Decimal(tick_size), Decimal(step_size))
    book = LocalOrderBook(symbol, spec)
    book.reset_from_snapshot(1, {bid_tick: 10_000}, {ask_tick: 10_000})
    return book


def _fill(ts: float, side: str, price_tick: int, qty_lots: int, order_id: str) -> Fill:
    return Fill(ts, "BTCUSDT", side, price_tick, qty_lots, maker=True, order_id=order_id)


def test_partial_close_and_reversal_preserve_exact_position_accounting():
    metrics = SimulationMetrics(make_config())
    book = _book()

    metrics.on_fill(_fill(1.0, "bid", 100, 5, "buy"), book, Decimal("101"))
    metrics.on_fill(_fill(2.0, "ask", 110, 2, "partial-close"), book, Decimal("101"))

    assert metrics.inventory_lots("BTCUSDT") == 3
    assert metrics.position["BTCUSDT"].avg_cost == Decimal("100")
    assert metrics.realized_pnl == Decimal("20")

    metrics.on_fill(_fill(3.0, "ask", 90, 5, "reverse"), book, Decimal("101"))

    assert metrics.inventory_lots("BTCUSDT") == -2
    assert metrics.position["BTCUSDT"].avg_cost == Decimal("90")
    assert metrics.realized_pnl == Decimal("-10")


def test_short_partial_close_and_reversal_are_symmetric():
    metrics = SimulationMetrics(make_config())
    book = _book()

    metrics.on_fill(_fill(1.0, "ask", 100, 4, "sell"), book, Decimal("101"))
    metrics.on_fill(_fill(2.0, "bid", 90, 1, "partial-close"), book, Decimal("101"))

    assert metrics.inventory_lots("BTCUSDT") == -3
    assert metrics.position["BTCUSDT"].avg_cost == Decimal("100")
    assert metrics.realized_pnl == Decimal("10")

    metrics.on_fill(_fill(3.0, "bid", 110, 5, "reverse"), book, Decimal("101"))

    assert metrics.inventory_lots("BTCUSDT") == 2
    assert metrics.position["BTCUSDT"].avg_cost == Decimal("110")
    assert metrics.realized_pnl == Decimal("-20")


def test_summary_keeps_inventory_and_fill_quantities_symbol_safe():
    metrics = SimulationMetrics(make_config())
    btc = _book(tick_size="1", step_size="0.001", bid_tick=9999, ask_tick=10001)
    eth = _book(
        "ETHUSDT",
        tick_size="0.1",
        step_size="0.01",
        bid_tick=9999,
        ask_tick=10001,
    )

    for _ in range(4):
        metrics.on_quote_requested()
    metrics.on_fill(Fill(1.0, "BTCUSDT", "bid", 9999, 1, True, "btc-order"), btc, Decimal("10000"))
    metrics.on_fill(Fill(1.1, "BTCUSDT", "bid", 9999, 1, True, "btc-order"), btc, Decimal("10000"))
    metrics.on_fill(Fill(1.2, "ETHUSDT", "bid", 9999, 2, True, "eth-order"), eth, Decimal("1000"))
    metrics.update_unrealized({"BTCUSDT": btc, "ETHUSDT": eth})

    summary = metrics.get_summary({"BTCUSDT": btc, "ETHUSDT": eth})

    assert summary["total_inventory"] is None
    assert summary["avg_inventory"] is None
    assert summary["inventory_by_symbol"]["BTCUSDT"]["base_qty"] == pytest.approx(0.002)
    assert summary["inventory_by_symbol"]["ETHUSDT"]["base_qty"] == pytest.approx(0.02)
    assert summary["inventory_quote_notional_by_symbol"]["BTCUSDT"] == pytest.approx(20.0)
    assert summary["inventory_quote_notional_by_symbol"]["ETHUSDT"] == pytest.approx(20.0)

    fills = summary["fill_metrics"]
    assert fills["fill_event_count"] == 3
    assert fills["identified_filled_order_count"] == 2
    assert fills["fill_event_rate_per_quote_order"] == pytest.approx(0.75)
    assert fills["identified_order_fill_rate_lower_bound"] == pytest.approx(0.5)
    assert fills["filled_base_qty_by_symbol"] == pytest.approx({"BTCUSDT": 0.002, "ETHUSDT": 0.02})
    assert fills["quantity_fill_rate"] is None


def test_single_symbol_legacy_inventory_keys_remain_compatible():
    metrics = SimulationMetrics(make_config())
    book = _book(step_size="0.001", bid_tick=9999, ask_tick=10001)
    metrics.on_fill(Fill(1.0, "BTCUSDT", "bid", 9999, 2, True, "order"), book, Decimal("10000"))
    metrics.update_unrealized({"BTCUSDT": book})

    first = metrics.get_summary({"BTCUSDT": book})
    second = metrics.get_summary({"BTCUSDT": book})

    assert first["total_inventory"] == pytest.approx(0.002)
    assert first["avg_inventory"] == pytest.approx(0.002)
    assert first["inventory_by_symbol"]["BTCUSDT"]["inventory_observations"] == 1
    assert second["inventory_by_symbol"]["BTCUSDT"]["inventory_observations"] == 1


def test_markouts_use_first_causal_mid_and_report_unresolved_coverage():
    metrics = SimulationMetrics(make_config(sim_markout_horizons_ms=(100, 1000)))
    book = _book()
    metrics.on_fill(_fill(1.0, "bid", 100, 2, "buy"), book, Decimal("101"))

    assert metrics.observe_mid("BTCUSDT", 1.099, Decimal("150")) == 0
    assert metrics.observe_mid("BTCUSDT", 1.1, Decimal("99")) == 1
    assert metrics.observe_mid("BTCUSDT", 1.5, Decimal("120")) == 0

    summary = metrics.get_summary({"BTCUSDT": book})
    short = summary["markouts"]["100ms"]
    long = summary["markouts"]["1000ms"]

    assert short["resolved_count"] == 1
    assert short["unresolved_count"] == 0
    assert short["markout_pnl"] == pytest.approx(-2.0)
    assert short["notional_weighted_bps"] == pytest.approx(-100.0)
    assert long["resolved_count"] == 0
    assert long["unresolved_count"] == 1
    assert long["notional_weighted_bps"] is None
    assert summary["fills"][0]["markouts"]["100ms"]["observation_ts"] == pytest.approx(1.1)


def test_sell_markout_sign_is_positive_when_future_mid_falls():
    metrics = SimulationMetrics(make_config(sim_markout_horizons_ms=(100,)))
    book = _book()
    metrics.on_fill(_fill(1.0, "ask", 102, 2, "sell"), book, Decimal("101"))
    metrics.observe_mid("BTCUSDT", 1.1, Decimal("100"))

    markout = metrics.get_summary({"BTCUSDT": book})["markouts"]["100ms"]
    assert markout["markout_pnl"] == pytest.approx(4.0)
    expected_bps = float(Decimal("2") / Decimal("102") * 10_000)
    assert markout["notional_weighted_bps"] == pytest.approx(expected_bps)


def test_mid_observations_must_be_symbol_causal():
    metrics = SimulationMetrics(make_config(sim_markout_horizons_ms=(100,)))
    metrics.observe_mid("BTCUSDT", 2.0, Decimal("100"))

    with pytest.raises(ValueError, match="nondecreasing"):
        metrics.observe_mid("BTCUSDT", 1.9, Decimal("100"))


def test_book_gap_invalidates_markouts_instead_of_bridging_resync():
    metrics = SimulationMetrics(make_config(sim_markout_horizons_ms=(100,)))
    book = _book()
    metrics.on_fill(_fill(1.0, "bid", 100, 2, "buy"), book, Decimal("101"))

    assert metrics.on_book_invalidated("BTCUSDT", 1.05, "sequence_gap") == 1
    assert metrics.observe_mid("BTCUSDT", 1.1, Decimal("99")) == 0

    summary = metrics.get_summary({"BTCUSDT": book})
    markout = summary["markouts"]["100ms"]
    assert markout["resolved_count"] == 0
    assert markout["unresolved_count"] == 1
    assert markout["pending_count"] == 0
    assert markout["invalidated_count"] == 1
    assert summary["fills"][0]["markouts"]["100ms"]["status"] == "invalidated"


def test_open_position_without_mark_makes_aggregate_valuation_unavailable():
    metrics = SimulationMetrics(make_config(fees_maker_bps=Decimal("0")))
    book = _book()
    metrics.on_fill(_fill(1.0, "bid", 100, 1, "buy"), book, Decimal("101"))
    book.clear()

    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["valuation"] == {
        "complete": False,
        "missing_mark_symbols": ["BTCUSDT"],
        "policy": "aggregate unrealized and total PnL are null when an open position lacks a mark",
    }
    assert summary["unrealized_pnl"] is None
    assert summary["total_pnl"] is None
    assert summary["inventory_by_symbol"]["BTCUSDT"]["valuation_status"] == "missing_mark"


def test_gross_and_net_pnl_make_rebate_contribution_explicit():
    metrics = SimulationMetrics(make_config(fees_maker_bps=Decimal("-1")))
    book = _book()
    metrics.on_fill(_fill(1.0, "bid", 100, 1, "buy"), book, Decimal("101"))

    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["gross_realized_pnl_before_fees"] == pytest.approx(0.0)
    assert summary["realized_pnl"] == pytest.approx(0.01)
    assert summary["fee_pnl_contribution"] == pytest.approx(0.01)
    assert summary["gross_total_pnl_before_fees"] == pytest.approx(1.0)
    assert summary["net_total_pnl"] == pytest.approx(1.01)
