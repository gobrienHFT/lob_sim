from __future__ import annotations

from decimal import Decimal

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import AggTradeEvent, LevelChange, SymbolSpec
from lob_sim.sim.fill_model import PassiveFillModel
from lob_sim.sim.metrics import SimulationMetrics
from lob_sim.config import Config, load_config
from lob_sim.sim.orders import Order
import os


def _build_config() -> Config:
    values = {
        "BINANCE_FAPI_BASE": "https://fapi.binance.com",
        "BINANCE_FWS_BASE": "wss://fstream.binance.com",
        "DEPTH_STREAM_SUFFIX": "@depth@100ms",
        "TRADE_STREAM_SUFFIX": "@aggTrade",
        "SYMBOLS": "BTCUSDT",
        "SNAPSHOT_LIMIT": "1000",
        "BOOK_TOP_N": "50",
        "COLLECT_SECONDS": "10",
        "RECORD_DIR": "./data",
        "RECORD_FORMAT": "ndjson",
        "RECORD_GZIP": "0",
        "RECORD_FLUSH_EVERY": "2000",
        "HTTP_TIMEOUT": "10",
        "HTTP_RETRIES": "2",
        "RATE_LIMIT_REQ_PER_SEC": "8",
        "WS_PING_INTERVAL": "180",
        "WS_PING_TIMEOUT": "600",
        "WS_RECONNECT_MAX_SEC": "30",
        "RESYNC_ON_GAP": "1",
        "SIM_SEED": "1",
        "SIM_ORDER_LATENCY_MS": "25",
        "SIM_CANCEL_LATENCY_MS": "25",
        "MM_ENABLED": "1",
        "MM_REQUOTE_MS": "250",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "0.01",
        "MM_HALF_SPREAD_BPS": "2.0",
        "MM_SKEW_BPS_PER_UNIT": "10.0",
        "FEES_MAKER_BPS": "-0.2",
        "FEES_TAKER_BPS": "4.0",
        "LOG_LEVEL": "INFO",
    }
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return load_config(".env")


def test_fill_model_queue_ahead_consumption_and_fill():
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    book.reset_from_snapshot(
        1,
        bids={10000: 10},
        asks={10100: 10},
    )
    model = PassiveFillModel()
    order = Order(
        order_id="o1",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=2,
        queue_ahead_lots=10,
        created_ts=0.0,
        remaining_lots=2,
    )
    model.place_order(order)

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 10, 5)], 1.0)
    assert fills == []
    assert model.get_order("BTCUSDT", "bid") is not None

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 5, 0)], 2.0)
    assert fills == []

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 0, 3)], 3.0)
    assert fills == []

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 3, 0)], 4.0)
    assert len(fills) == 1
    assert fills[0].qty_lots == 2
    assert model.get_order("BTCUSDT", "bid") is None

    cfg = _build_config()
    m = SimulationMetrics(cfg)
    for fill in fills:
        m.on_fill(fill, book, mid=book.mid_price())

    assert m.fill_count == 1
    assert m.inventory_lots("BTCUSDT") == 2


def test_fill_model_public_consumption_summary_tracks_overlap_netting():
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

    depth_fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.05)
    assert depth_fills == []

    assert model.public_consumption_summary() == {
        "overlap_window_seconds": 0.125,
        "sources": {
            "depth_update": {
                "observed_lots": 1,
                "modeled_lots": 0,
                "overlap_netted_lots": 1,
                "queue_consumed_lots": 0,
                "unmatched_lots": 0,
            },
            "agg_trade": {
                "observed_lots": 2,
                "modeled_lots": 2,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 2,
                "unmatched_lots": 0,
            },
        },
        "total_observed_lots": 3,
        "total_modeled_lots": 2,
        "total_overlap_netted_lots": 1,
        "total_queue_consumed_lots": 2,
        "total_unmatched_lots": 0,
    }


def test_public_consumption_summary_exposes_unmatched_queue_consumption():
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[], asks=[(10010, 1)])

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=3, buyer_is_maker=True, ts_local=1.0),
        1.0,
    )

    assert fills == []
    assert model.public_consumption_summary()["sources"]["agg_trade"] == {
        "observed_lots": 3,
        "modeled_lots": 3,
        "overlap_netted_lots": 0,
        "queue_consumed_lots": 0,
        "unmatched_lots": 3,
    }
