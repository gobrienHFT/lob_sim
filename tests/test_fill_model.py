from __future__ import annotations

import os
from decimal import Decimal

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import AggTradeEvent, LevelChange, SymbolSpec
from lob_sim.config import (
    LEGACY_SYNTHETIC_QUEUE_AHEAD_MODE,
    SYNTHETIC_QUEUE_AHEAD_MODE,
    Config,
    FillAssumptionConfig,
    fill_assumption_config_for_profile,
    load_config,
)
from lob_sim.sim.fill_model import PassiveFillModel
from lob_sim.sim.metrics import SimulationMetrics
from lob_sim.sim.orders import Order


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


def test_fill_assumption_manifest_uses_explicit_synthetic_queue_name() -> None:
    config = FillAssumptionConfig(uncorroborated_depth_reduction_mode=LEGACY_SYNTHETIC_QUEUE_AHEAD_MODE)

    assert config.uncorroborated_depth_reduction_mode == SYNTHETIC_QUEUE_AHEAD_MODE
    assert config.as_dict()["uncorroborated_depth_reduction_mode"] == SYNTHETIC_QUEUE_AHEAD_MODE
    assert (
        fill_assumption_config_for_profile("aggressive").as_dict()["uncorroborated_depth_reduction_mode"]
        == SYNTHETIC_QUEUE_AHEAD_MODE
    )


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

    consumption_events = model.drain_public_consumption_events()
    assert [
        (
            event.source,
            event.side,
            event.price_tick,
            event.observed_lots,
            event.modeled_lots,
            event.overlap_netted_lots,
            event.queue_consumed_lots,
            event.unmatched_lots,
        )
        for event in consumption_events
    ] == [
        ("agg_trade", "bid", 10000, 2, 2, 0, 2, 0),
        ("depth_update", "bid", 10000, 1, 0, 1, 0, 0),
    ]
    assert model.drain_public_consumption_events() == []

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


def test_overlap_netting_uses_exact_logical_nanoseconds_not_float_seconds() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
    model.place_order(
        Order(
            order_id="strategy-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=10000,
            qty_lots=2,
            remaining_lots=2,
            created_ts=0.0,
        )
    )

    # Keep the public float timestamp identical while moving the exact
    # logical clock just beyond the 125 ms reconciliation window. A float-only
    # implementation would incorrectly net the depth reduction away.
    logical_first = 10_000_000_000_000_000
    logical_second = logical_first + 125_000_001
    model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=1.0),
        1.0,
        logical_time_ns=logical_first,
    )
    model.apply_depth_changes(
        "BTCUSDT",
        [LevelChange("bids", 10000, 1, 0)],
        1.0,
        logical_time_ns=logical_second,
    )

    depth_event = next(event for event in model.drain_public_consumption_events() if event.source == "depth_update")
    assert depth_event.modeled_lots == 1
    assert depth_event.overlap_netted_lots == 0
    assert depth_event.queue_consumed_lots == 1
    assert depth_event.unmatched_lots == 0


def test_passive_fill_carries_trigger_and_order_provenance() -> None:
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
    order = Order(
        order_id="strategy-bid",
        symbol="BTCUSDT",
        side="bid",
        price_tick=10000,
        qty_lots=2,
        remaining_lots=2,
        created_ts=1.0,
        arrival_evidence_ids=("book:arrival",),
        new_order_latency_ms=5.0,
    )
    model.place_order(order)
    order.mark_pending_cancel()
    order.cancel_latency_ms = 7.0
    validity = {
        "book_valid": True,
        "trade_stream_valid": True,
        "clock_valid": True,
        "capture_valid": True,
        "trade_stream_required": True,
        "execution_valid": True,
        "reason": None,
    }

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=2.0),
        2.0,
        evidence_ids=("trade:trigger",),
        validity=validity,
    )

    assert len(fills) == 1
    fill = fills[0]
    assert fill.evidence_ids == ("book:arrival", "trade:trigger")
    assert fill.validity == validity
    assert fill.order_state_at_fill == "pending_cancel"
    assert fill.latency_draws_ms == {"new_order": 5.0, "cancel": 7.0}
    assert fill.queue_trajectory == {
        "queue_ahead_before_trigger_lots": 1,
        "queue_ahead_at_fill_lots": 0,
        "queue_consumed_before_fill_lots": 1,
        "public_consumption_trigger_lots": 2,
        "fill_lots": 1,
        "remaining_order_lots_after_fill": 1,
    }


def test_public_consumption_summary_exposes_unmatched_queue_consumption():
    model = PassiveFillModel()
    model.seed_from_snapshot("BTCUSDT", bids=[], asks=[(10010, 1)])

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=3, buyer_is_maker=True, ts_local=1.0),
        1.0,
    )

    assert fills == []
    consumption_events = model.drain_public_consumption_events()
    assert len(consumption_events) == 1
    assert consumption_events[0].source == "agg_trade"
    assert consumption_events[0].observed_lots == 3
    assert consumption_events[0].modeled_lots == 3
    assert consumption_events[0].queue_consumed_lots == 0
    assert consumption_events[0].unmatched_lots == 3
    assert model.public_consumption_summary()["sources"]["agg_trade"] == {
        "observed_lots": 3,
        "modeled_lots": 3,
        "overlap_netted_lots": 0,
        "queue_consumed_lots": 0,
        "unmatched_lots": 3,
    }


def test_conservative_depth_only_reduction_records_unknown_without_fill():
    model = PassiveFillModel(fill_assumption_config_for_profile("conservative"))
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
    model.place_order(
        Order(
            order_id="strategy-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=10000,
            qty_lots=1,
            remaining_lots=1,
            created_ts=0.0,
        )
    )

    fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)

    assert fills == []
    assert model.get_order("BTCUSDT", "bid") is not None
    events = model.drain_public_consumption_events()
    assert len(events) == 1
    assert events[0].source == "depth_update"
    assert events[0].modeled_lots == 1
    assert events[0].queue_consumed_lots == 0
    assert events[0].unmatched_lots == 1
    assert events[0].fill_assumption_profile == "conservative"
    assert model.fill_assumption_diagnostics()["uncorroborated_depth_reduction_lots"] == 1


def test_conservative_agg_trade_only_print_consumes_queue():
    model = PassiveFillModel(fill_assumption_config_for_profile("conservative"))
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
    model.place_order(
        Order(
            order_id="strategy-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=10000,
            qty_lots=1,
            remaining_lots=1,
            created_ts=0.0,
        )
    )

    fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.0),
        1.0,
    )

    assert [(fill.order_id, fill.qty_lots, fill.source) for fill in fills] == [("strategy-bid", 1, "agg_trade")]
    assert model.public_consumption_summary()["sources"]["agg_trade"]["unmatched_lots"] == 0


def test_conservative_recent_trade_corroborates_depth_without_double_fill():
    model = PassiveFillModel(fill_assumption_config_for_profile("conservative"))
    model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
    model.place_order(
        Order(
            order_id="strategy-bid",
            symbol="BTCUSDT",
            side="bid",
            price_tick=10000,
            qty_lots=2,
            remaining_lots=2,
            created_ts=0.0,
        )
    )

    trade_fills = model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.0),
        1.0,
    )
    depth_fills = model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.05)

    assert [(fill.qty_lots, fill.source) for fill in trade_fills] == [(1, "agg_trade")]
    assert depth_fills == []
    assert model.get_order("BTCUSDT", "bid") is not None
    assert model.fill_assumption_diagnostics()["corroborated_depth_reduction_lots"] == 1


def test_aggressive_disables_overlap_netting_as_upper_bound():
    base = PassiveFillModel(fill_assumption_config_for_profile("base"))
    aggressive = PassiveFillModel(fill_assumption_config_for_profile("aggressive"))
    for model in (base, aggressive):
        model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
        model.place_order(
            Order(
                order_id=f"{model.fill_assumption.profile}-bid",
                symbol="BTCUSDT",
                side="bid",
                price_tick=10000,
                qty_lots=2,
                remaining_lots=2,
                created_ts=0.0,
            )
        )

    base.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)
    base_fills = base.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=1.05),
        1.05,
    )
    aggressive.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)
    aggressive_fills = aggressive.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=1, buyer_is_maker=True, ts_local=1.05),
        1.05,
    )

    assert base_fills == []
    assert [(fill.qty_lots, fill.source) for fill in aggressive_fills] == [(1, "agg_trade")]
    assert aggressive.public_consumption_summary()["overlap_window_seconds"] == 0.0


def test_base_profile_matches_default_fill_model_behavior():
    default_model = PassiveFillModel()
    base_model = PassiveFillModel(fill_assumption_config_for_profile("base"))
    for model in (default_model, base_model):
        model.seed_from_snapshot("BTCUSDT", bids=[(10000, 1)], asks=[(10010, 1)])
        model.place_order(
            Order(
                order_id=f"{model.fill_assumption.profile}-bid",
                symbol="BTCUSDT",
                side="bid",
                price_tick=10000,
                qty_lots=2,
                remaining_lots=2,
                created_ts=0.0,
            )
        )
        model.apply_depth_changes("BTCUSDT", [LevelChange("bids", 10000, 1, 0)], 1.0)

    default_fills = default_model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.05),
        1.05,
    )
    base_fills = base_model.apply_agg_trade(
        AggTradeEvent(symbol="BTCUSDT", price_tick=10000, qty_lots=2, buyer_is_maker=True, ts_local=1.05),
        1.05,
    )

    assert [(fill.qty_lots, fill.source) for fill in default_fills] == [(1, "agg_trade")]
    assert [(fill.qty_lots, fill.source) for fill in base_fills] == [(1, "agg_trade")]
    assert default_model.public_consumption_summary() == base_model.public_consumption_summary()


def test_overlap_credit_state_is_bounded_on_one_sided_new_prices() -> None:
    model = PassiveFillModel()

    for index in range(1_000):
        model.apply_agg_trade(
            AggTradeEvent(
                symbol="BTCUSDT",
                price_tick=10_000 + index,
                qty_lots=1,
                buyer_is_maker=True,
                ts_local=float(index),
            ),
            float(index),
        )

    assert model.overlap_credit_state() == {
        "active_credit_keys": 1,
        "active_credits": 1,
        "expiry_entries": 1,
        "memory_bounded_by_overlap_window": True,
        "overlap_window_seconds": 0.125,
    }
