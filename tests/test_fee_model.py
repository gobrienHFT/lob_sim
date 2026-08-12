from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import SymbolSpec
from lob_sim.config import ConfigError, load_config
from lob_sim.sim.fees import StaticFeeModel
from lob_sim.sim.metrics import MarkoutCapacityError, PositionState, SimulationMetrics
from lob_sim.sim.orders import Fill
from lob_sim.sim.sinks import AggregateMetricsSink


def _build_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str):
    defaults = {
        "RECORD_DIR": str(tmp_path),
        "RECORD_GZIP": "0",
        "LOG_LEVEL": "ERROR",
        "FEES_MAKER_BPS": "-1.0",
        "FEES_TAKER_BPS": "4.0",
    }
    defaults.update({key: str(value) for key, value in overrides.items()})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return load_config(".env.example")


def test_static_fee_model_supports_rebates_taker_fees_and_multiplier() -> None:
    spec = SymbolSpec(
        symbol="TEST",
        tick_size=Decimal("0.5"),
        step_size=Decimal("0.1"),
        price_currency="USD",
        contract_multiplier=Decimal("10"),
    )
    model = StaticFeeModel(maker_bps=Decimal("-1.0"), taker_bps=Decimal("4.0"))

    maker = model.assess(
        Fill(ts_local=1.0, symbol="TEST", side="bid", price_tick=1000, qty_lots=2, maker=True),
        spec,
    )
    taker = model.assess(
        Fill(ts_local=1.0, symbol="TEST", side="bid", price_tick=1000, qty_lots=2, maker=False),
        spec,
    )

    assert maker.notional == Decimal("1000.0")
    assert maker.rate_bps == Decimal("-1.0")
    assert maker.amount == Decimal("-0.10000")
    assert maker.currency == "USD"
    assert taker.rate_bps == Decimal("4.0")
    assert taker.amount == Decimal("0.4000")


def test_metrics_records_per_fill_fee_audit_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(monkeypatch, tmp_path)
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(
        symbol="BTCUSDT",
        tick_size=Decimal("1"),
        step_size=Decimal("1"),
        price_currency="USDT",
    )
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 1}, asks={102: 1})

    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=2,
            maker=True,
            order_id="maker-fill",
            created_ts=0.0,
        ),
        book,
        book.mid_price(),
    )
    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["total_fees"] == pytest.approx(-0.02)
    assert summary["realized_pnl"] == pytest.approx(0.02)
    assert summary["fills"][0]["fee_bps"] == "-1.0"
    assert Decimal(summary["fills"][0]["fee"]) == Decimal("-0.02")
    assert summary["fills"][0]["fee_currency"] == "USDT"
    assert summary["fills"][0]["fill_source"] == "depth_update"
    assert summary["fills"][0]["notional"] == "200"
    assert summary["fills"][0]["contract_multiplier"] == "1"


def test_open_inventory_without_a_book_is_unvalued_not_zero_pnl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(
        symbol="BTCUSDT",
        tick_size=Decimal("1"),
        step_size=Decimal("0.001"),
        price_currency="USDT",
    )
    metrics.position["BTCUSDT"] = PositionState(lot_size=2, avg_cost=Decimal("100"))

    summary = metrics.get_summary({}, specs={"BTCUSDT": spec})

    assert summary["valuation_complete"] is False
    assert summary["missing_mark_symbols"] == ["BTCUSDT"]
    assert summary["unrealized_pnl"] is None
    assert summary["total_pnl"] is None
    assert summary["inventory_by_symbol"] == {"BTCUSDT": pytest.approx(0.002)}
    assert summary["total_inventory"] == pytest.approx(0.002)


def test_open_inventory_without_instrument_metadata_is_explicitly_unpriced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(monkeypatch, tmp_path, FEES_MAKER_BPS="0", FEES_TAKER_BPS="0")
    metrics = SimulationMetrics(cfg)
    metrics.position["UNKNOWN"] = PositionState(lot_size=1, avg_cost=Decimal("100"))

    summary = metrics.get_summary({})

    assert summary["valuation_complete"] is False
    assert summary["missing_mark_symbols"] == ["UNKNOWN"]
    assert summary["inventory_missing_spec_symbols"] == ["UNKNOWN"]
    assert summary["unrealized_pnl"] is None
    assert summary["total_pnl"] is None


def test_metrics_apply_contract_multiplier_to_pnl_spread_and_markout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="1.0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(
        symbol="FUT",
        tick_size=Decimal("1"),
        step_size=Decimal("1"),
        price_currency="USD",
        contract_multiplier=Decimal("10"),
    )
    book = LocalOrderBook(symbol="FUT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="FUT",
            side="bid",
            price_tick=100,
            qty_lots=2,
            maker=True,
            order_id="open-long",
            created_ts=0.0,
        ),
        book,
        book.mid_price(),
    )

    book.reset_from_snapshot(2, bids={102: 2}, asks={104: 2})
    metrics.update_unrealized({"FUT": book}, now_ts=1.1)
    summary = metrics.get_summary({"FUT": book})

    assert summary["unrealized_pnl"] == pytest.approx(60.0)
    assert summary["avg_spread_captured"] == pytest.approx(10.0)
    assert summary["avg_markout_1s"] == pytest.approx(30.0)
    assert summary["markout_events"][0]["markout"] == "30"
    assert summary["markout_events"][0]["contract_multiplier"] == "10"
    assert summary["markout_events"][0]["fill_source"] == "depth_update"
    assert summary["markout_by_fill_source"] == {
        "depth_update": {
            "samples": 1,
            "adverse_samples": 0,
            "qty": 2.0,
            "avg_markout_1s": 30.0,
            "adverse_fill_rate_1s": 0.0,
        },
        "agg_trade": {
            "samples": 0,
            "adverse_samples": 0,
            "qty": 0.0,
            "avg_markout_1s": 0.0,
            "adverse_fill_rate_1s": 0.0,
        },
        "taker_order": {
            "samples": 0,
            "adverse_samples": 0,
            "qty": 0.0,
            "avg_markout_1s": 0.0,
            "adverse_fill_rate_1s": 0.0,
        },
    }
    assert summary["regime_performance"]["normal_balanced"]["avg_spread_capture"] == pytest.approx(10.0)
    assert summary["fills"][0]["notional"] == "2000"
    assert summary["fills"][0]["contract_multiplier"] == "10"


def test_metrics_split_markout_quality_by_fill_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="1.0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=1,
            maker=True,
            source="agg_trade",
        ),
        book,
        book.mid_price(),
    )
    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="BTCUSDT",
            side="ask",
            price_tick=102,
            qty_lots=2,
            maker=False,
            source="taker_order",
        ),
        book,
        book.mid_price(),
    )

    book.reset_from_snapshot(2, bids={98: 2}, asks={100: 2})
    metrics.update_unrealized({"BTCUSDT": book}, now_ts=1.1)
    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["markout_by_fill_source"]["agg_trade"] == {
        "samples": 1,
        "adverse_samples": 1,
        "qty": 1.0,
        "avg_markout_1s": -1.0,
        "adverse_fill_rate_1s": 1.0,
    }
    assert summary["markout_by_fill_source"]["taker_order"] == {
        "samples": 1,
        "adverse_samples": 0,
        "qty": 2.0,
        "avg_markout_1s": 3.0,
        "adverse_fill_rate_1s": 0.0,
    }
    assert summary["markout_events"][0]["fill_source"] == "agg_trade"
    assert summary["markout_events"][1]["fill_source"] == "taker_order"


def test_metrics_emit_null_markout_when_epoch_is_invalidated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="1.0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(
            ts_local=1.0,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=1,
            maker=True,
            source="agg_trade",
            order_id="epoch-fill",
        ),
        book,
        book.mid_price(),
    )

    assert metrics.invalidate_markouts("BTCUSDT", "depth_gap", ts_local=1.5) == 1
    event = metrics.drain_new_markout_events()[0]

    assert event["status"] == "invalidated"
    assert event["invalid_reason"] == "depth_gap"
    assert event["fill_price"] == "100"
    assert event["fill_mid"] == "101"
    assert event["mid_after"] is None
    assert event["markout"] is None
    assert event["adverse"] is None
    assert event["horizon"] == 1.0
    assert event["markout_ts_local"] == 1.5
    summary = metrics.get_summary({"BTCUSDT": book})
    assert summary["markout_resolved_count"] == 0
    assert summary["markout_invalidated_count"] == 1
    assert summary["markout_unresolved_count"] == 1


def test_metrics_apply_contract_multiplier_to_realized_pnl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(
        symbol="FUT",
        tick_size=Decimal("1"),
        step_size=Decimal("1"),
        price_currency="USD",
        contract_multiplier=Decimal("10"),
    )
    book = LocalOrderBook(symbol="FUT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(ts_local=0.0, symbol="FUT", side="bid", price_tick=100, qty_lots=2, maker=True),
        book,
        book.mid_price(),
    )
    metrics.on_fill(
        Fill(ts_local=1.0, symbol="FUT", side="ask", price_tick=104, qty_lots=2, maker=True),
        book,
        book.mid_price(),
    )
    summary = metrics.get_summary({"FUT": book})

    assert summary["realized_pnl"] == pytest.approx(80.0)
    assert summary["unrealized_pnl"] == pytest.approx(0.0)
    assert summary["total_pnl"] == pytest.approx(80.0)


def test_markouts_resolve_after_round_trip_flattens_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="1.0",
    )
    metrics = SimulationMetrics(cfg)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(ts_local=0.0, symbol="BTCUSDT", side="bid", price_tick=100, qty_lots=1),
        book,
        book.mid_price(),
    )
    metrics.on_fill(
        Fill(ts_local=0.1, symbol="BTCUSDT", side="ask", price_tick=102, qty_lots=1),
        book,
        book.mid_price(),
    )
    assert metrics.inventory_lots("BTCUSDT") == 0

    book.reset_from_snapshot(2, bids={102: 2}, asks={104: 2})
    metrics.update_unrealized({"BTCUSDT": book}, now_ts=1.2)
    summary = metrics.get_summary({"BTCUSDT": book})

    assert summary["markout_resolved_count"] == 2
    assert summary["markout_samples_remaining"] == 0
    assert [event["markout"] for event in summary["markout_events"]] == ["3", "-1"]


def test_aggregate_only_metrics_discard_detail_rows_but_keep_audit_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="0",
    )
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})
    full = SimulationMetrics(cfg)
    fill_sink = AggregateMetricsSink()
    bounded = SimulationMetrics(cfg, fill_sink=fill_sink, retain_audit_rows=False)

    for index in range(1000):
        side = "bid" if index % 2 == 0 else "ask"
        price_tick = 100 if side == "bid" else 102
        fill = Fill(
            ts_local=float(index),
            symbol="BTCUSDT",
            side=side,
            price_tick=price_tick,
            qty_lots=1,
            order_id=f"order-{index}",
        )
        full.on_fill(fill, book, book.mid_price())
        bounded.on_fill(fill, book, book.mid_price())

    bounded_summary = bounded.get_summary({"BTCUSDT": book})

    assert bounded.fills_log == []
    assert bounded_summary["fills"] is None
    assert bounded_summary["markout_events"] is None
    assert bounded_summary["fill_count"] == 1000
    assert bounded_summary["audit_retention"] == {
        "schema_version": "lob_sim.audit_retention.v1",
        "mode": "aggregate_only",
        "memory_bounded_by_tape_duration": True,
        "built_in_sinks_memory_bounded": True,
        "detail_rows_complete_in_summary": False,
        "fill_rows_emitted": 1000,
        "fill_rows_retained": 0,
        "fill_audit_sha256": bounded.fill_audit_sha256,
        "fill_sink": "AggregateMetricsSink",
        "markout_rows_emitted": 0,
        "markout_rows_retained": 0,
        "markout_audit_sha256": bounded.markout_audit_sha256,
        "markout_sink": "NullSink",
        "markout_trace_buffering": False,
        "pending_markouts": 0,
        "max_pending_markouts": 100000,
    }
    assert fill_sink.count == 1000
    assert bounded.fill_audit_sha256 == full.fill_audit_sha256


def test_pending_markout_capacity_fails_before_fill_accounting_mutates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(
        monkeypatch,
        tmp_path,
        FEES_MAKER_BPS="0",
        FEES_TAKER_BPS="0",
        SIM_ADVERSE_MARKOUT_SECONDS="10",
        SIM_MAX_PENDING_MARKOUTS="1",
    )
    metrics = SimulationMetrics(cfg, retain_audit_rows=False)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})
    metrics.on_fill(
        Fill(ts_local=0.0, symbol="BTCUSDT", side="bid", price_tick=100, qty_lots=1, order_id="one"),
        book,
        book.mid_price(),
    )
    before = (metrics.fill_count, metrics.inventory_lots("BTCUSDT"), metrics.realized_pnl)

    with pytest.raises(MarkoutCapacityError, match="SIM_MAX_PENDING_MARKOUTS=1"):
        metrics.on_fill(
            Fill(ts_local=0.1, symbol="BTCUSDT", side="ask", price_tick=102, qty_lots=1, order_id="two"),
            book,
            book.mid_price(),
        )

    assert (metrics.fill_count, metrics.inventory_lots("BTCUSDT"), metrics.realized_pnl) == before


def test_pending_markout_capacity_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_MAX_PENDING_MARKOUTS", "0")

    with pytest.raises(ConfigError, match="SIM_MAX_PENDING_MARKOUTS must be > 0"):
        load_config(".env.example")


def test_quote_fill_probability_needs_no_completed_order_id_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(monkeypatch, tmp_path, SIM_ADVERSE_MARKOUT_SECONDS="0")
    metrics = SimulationMetrics(cfg, retain_audit_rows=False)
    metrics.on_order_arrival(resting_after_arrival=True, immediate_fills=0, remaining_lots_after_arrival=2)
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("1"), step_size=Decimal("1"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(1, bids={100: 2}, asks={102: 2})

    metrics.on_fill(
        Fill(
            ts_local=0.0,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=1,
            order_id="partial",
            is_first_fill_for_order=True,
        ),
        book,
        book.mid_price(),
    )
    metrics.on_fill(
        Fill(
            ts_local=0.1,
            symbol="BTCUSDT",
            side="bid",
            price_tick=100,
            qty_lots=1,
            order_id="partial",
            is_first_fill_for_order=False,
        ),
        book,
        book.mid_price(),
    )
    summary = metrics.get_summary({"BTCUSDT": book})

    assert metrics.filled_order_count == 1
    assert summary["quote_fill_probability"] == 1.0
    assert summary["fills_per_arrived_order"] == 2.0
