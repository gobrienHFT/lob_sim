from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import SymbolSpec
from lob_sim.config import load_config
from lob_sim.sim.fees import StaticFeeModel
from lob_sim.sim.metrics import SimulationMetrics
from lob_sim.sim.orders import Fill


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
    assert summary["regime_performance"]["normal_balanced"]["avg_spread_capture"] == pytest.approx(10.0)
    assert summary["fills"][0]["notional"] == "2000"
    assert summary["fills"][0]["contract_multiplier"] == "10"


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
