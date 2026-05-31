from __future__ import annotations

from decimal import Decimal

import pytest

from lob_sim.replay.normalization import (
    agg_trade_from_record,
    depth_update_from_record,
    instrument_spec_from_record,
    snapshot_from_record,
)
from lob_sim.replay.reader import RecordedEvent
from lob_sim.replay.runner import parse_symbol_spec_from_record, symbol_spec_from_record


def test_exchange_info_normalizes_to_instrument_spec_with_metadata() -> None:
    record = RecordedEvent(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="exchangeInfo",
        data={
            "tickSize": "0.10",
            "stepSize": "0.001",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "venue": "BINANCE_USDM",
        },
    )

    spec = instrument_spec_from_record(record)

    assert spec is not None
    assert spec.symbol == "BTCUSDT"
    assert spec.tick_to_price(715999) == Decimal("71599.90")
    assert spec.lot_to_qty(3) == Decimal("0.003")
    assert spec.quantity_unit == "BTC"
    assert spec.price_currency == "USDT"
    assert spec.venue == "BINANCE_USDM"
    assert symbol_spec_from_record(record) == spec
    assert parse_symbol_spec_from_record(record) == ("BTCUSDT", spec.tick_size, spec.step_size)


def test_replay_records_normalize_to_book_and_trade_events() -> None:
    spec = instrument_spec_from_record(
        RecordedEvent(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.10", "stepSize": "0.001"},
        )
    )
    assert spec is not None

    snapshot = snapshot_from_record(
        RecordedEvent(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="snapshot",
            data={"lastUpdateId": 100, "bids": [["100.0", "0.002"]], "asks": [["100.2", "0.003"]]},
        ),
        spec,
    )
    assert snapshot.last_update_id == 100
    assert snapshot.bids == [(1000, 2)]
    assert snapshot.asks == [(1002, 3)]

    depth = depth_update_from_record(
        RecordedEvent(
            ts_local=2.5,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 101, "u": 102, "pu": 100, "b": [["100.0", "0.001"]], "a": [["100.2", "0"]]},
        ),
        spec,
    )
    assert depth.first_update_id == 101
    assert depth.final_update_id == 102
    assert depth.prev_update_id == 100
    assert depth.bids == [(1000, 1)]
    assert depth.asks == [(1002, 0)]
    assert depth.ts_local == 2.5

    trade = agg_trade_from_record(
        RecordedEvent(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.1", "q": "0.004", "m": True},
        ),
        spec,
    )
    assert trade.price_tick == 1001
    assert trade.qty_lots == 4
    assert trade.buyer_is_maker is True
    assert trade.ts_local == 3.0


def test_normalizers_reject_wrong_record_type() -> None:
    record = RecordedEvent(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="aggTrade",
        data={"p": "100.0", "q": "0.001", "m": True},
    )
    spec = instrument_spec_from_record(
        RecordedEvent(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.10", "stepSize": "0.001"},
        )
    )
    assert spec is not None

    with pytest.raises(ValueError, match="Expected snapshot record"):
        snapshot_from_record(record, spec)

    with pytest.raises(ValueError, match="Expected depthUpdate record"):
        depth_update_from_record(record, spec)
