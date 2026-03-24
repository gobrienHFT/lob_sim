from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import AggTradeEvent, SymbolSpec
from lob_sim.config import Config, ConfigError, load_config
from lob_sim.sim.mm_strategy import MarketMakingStrategy


def _build_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> Config:
    defaults = {
        "BINANCE_FAPI_BASE": "https://fapi.binance.com",
        "BINANCE_FWS_BASE": "wss://fstream.binance.com",
        "DEPTH_STREAM_SUFFIX": "@depth@100ms",
        "TRADE_STREAM_SUFFIX": "@aggTrade",
        "SYMBOLS": "BTCUSDT",
        "SNAPSHOT_LIMIT": "1000",
        "BOOK_TOP_N": "20",
        "COLLECT_SECONDS": "10",
        "RECORD_DIR": str(tmp_path),
        "RECORD_FORMAT": "ndjson",
        "RECORD_GZIP": "0",
        "RECORD_FLUSH_EVERY": "300",
        "HTTP_TIMEOUT": "10",
        "HTTP_RETRIES": "2",
        "RATE_LIMIT_REQ_PER_SEC": "8",
        "WS_PING_INTERVAL": "180",
        "WS_PING_TIMEOUT": "600",
        "WS_RECONNECT_MAX_SEC": "30",
        "RESYNC_ON_GAP": "1",
        "SIM_SEED": "1",
        "SIM_ORDER_LATENCY_MS": "0",
        "SIM_CANCEL_LATENCY_MS": "0",
        "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
        "MM_ENABLED": "1",
        "MM_STRATEGY_PROFILE": "baseline",
        "MM_REQUOTE_MS": "1000",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "0.05",
        "MM_HALF_SPREAD_BPS": "10.0",
        "MM_LAYERED_INNER_SPREAD_BPS": "10.0",
        "MM_LAYERED_OUTER_SPREAD_BPS": "30.0",
        "MM_SKEW_BPS_PER_UNIT": "0",
        "MM_VOLATILITY_WINDOW": "10",
        "MM_VOLATILITY_SPREAD_FACTOR": "0",
        "MM_QUEUE_REPOST_LOTS": "0",
        "MM_TRADE_IMBALANCE_WINDOW": "4",
        "MM_MICROSTRUCTURE_GATE_THRESHOLD": "0.20",
        "MM_MICROSTRUCTURE_GATE_BPS": "10.0",
        "FEES_MAKER_BPS": "0",
        "FEES_TAKER_BPS": "0",
        "LOG_LEVEL": "ERROR",
    }
    defaults.update({key: str(value) for key, value in overrides.items()})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return load_config(".env.example")


def _book() -> LocalOrderBook:
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec, top_n=20)
    book.reset_from_snapshot(
        100,
        bids={1000: 5},
        asks={1002: 3},
    )
    return book


def test_strategy_profile_config_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = _build_config(monkeypatch, tmp_path, MM_STRATEGY_PROFILE="layered_mm")
    assert cfg.mm_strategy_profile == "layered_mm"

    with pytest.raises(ConfigError):
        _build_config(monkeypatch, tmp_path, MM_STRATEGY_PROFILE="unknown_profile")


def test_layered_profile_quotes_two_levels_per_side(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    baseline_cfg = _build_config(monkeypatch, tmp_path, MM_STRATEGY_PROFILE="baseline")
    layered_cfg = _build_config(monkeypatch, tmp_path, MM_STRATEGY_PROFILE="layered_mm")
    book = _book()

    baseline_quotes = MarketMakingStrategy(baseline_cfg).propose(book, inventory_qty=Decimal("0")).quotes
    layered_quotes = MarketMakingStrategy(layered_cfg).propose(book, inventory_qty=Decimal("0")).quotes

    assert len(baseline_quotes) == 2
    assert {quote.quote_slot for quote in baseline_quotes} == {"base"}

    assert len(layered_quotes) == 4
    bid_quotes = {quote.quote_slot: quote.price_tick for quote in layered_quotes if quote.side == "bid"}
    ask_quotes = {quote.quote_slot: quote.price_tick for quote in layered_quotes if quote.side == "ask"}
    assert set(bid_quotes) == {"inner", "outer"}
    assert set(ask_quotes) == {"inner", "outer"}
    assert bid_quotes["outer"] < bid_quotes["inner"]
    assert ask_quotes["outer"] > ask_quotes["inner"]


def test_layered_profile_microstructure_gate_widens_vulnerable_side(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _build_config(monkeypatch, tmp_path, MM_STRATEGY_PROFILE="layered_mm")

    neutral_book = _book()
    neutral_strategy = MarketMakingStrategy(cfg)
    neutral_quotes = neutral_strategy.propose(neutral_book, inventory_qty=Decimal("0")).quotes
    neutral_ask_inner = next(
        quote.price_tick for quote in neutral_quotes if quote.side == "ask" and quote.quote_slot == "inner"
    )

    gated_book = _book()
    gated_book.reset_from_snapshot(
        101,
        bids={1000: 8},
        asks={1002: 2},
    )
    gated_strategy = MarketMakingStrategy(cfg)
    gated_strategy.observe_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=1002, qty_lots=1, buyer_is_maker=False, ts_local=1.0)
    )
    gated_strategy.observe_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=1002, qty_lots=1, buyer_is_maker=False, ts_local=1.1)
    )
    gated_quotes = gated_strategy.propose(gated_book, inventory_qty=Decimal("0")).quotes
    gated_ask_inner = next(
        quote.price_tick for quote in gated_quotes if quote.side == "ask" and quote.quote_slot == "inner"
    )

    assert gated_ask_inner > neutral_ask_inner
