from __future__ import annotations

from decimal import Decimal

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSynchronizer, BookSyncGapError
from lob_sim.book.types import DepthUpdateEvent, InstrumentSpec, SnapshotEvent, SymbolSpec
from lob_sim.binance.symbols import parse_exchange_info_for_symbol


def _spec() -> SymbolSpec:
    return SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))


def test_book_sync_applies_snapshot_then_continuous_diffs():
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)

    snapshot = SnapshotEvent(
        symbol="BTCUSDT",
        last_update_id=100,
        bids=[(10000, 10)],
        asks=[(10100, 10)],
    )
    sync.on_snapshot(snapshot)

    first = DepthUpdateEvent(
        symbol="BTCUSDT",
        first_update_id=90,
        final_update_id=110,
        prev_update_id=80,
        bids=[(10000, 8)],
        asks=[(10100, 9)],
        ts_local=1.0,
    )
    second = DepthUpdateEvent(
        symbol="BTCUSDT",
        first_update_id=111,
        final_update_id=120,
        prev_update_id=110,
        bids=[],
        asks=[(10100, 7)],
        ts_local=2.0,
    )
    sync.on_depth_update(first)
    sync.on_depth_update(second)

    assert book.best_ticks() == (10000, 10100)
    assert book.bids[10000] == 8
    assert book.asks[10100] == 7


def test_book_sync_records_gap_without_advancing_when_resync_disabled():
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=False)

    sync.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=100,
            bids=[(10000, 10)],
            asks=[(10100, 10)],
        )
    )
    sync.on_depth_update(
        DepthUpdateEvent(
            symbol="BTCUSDT",
            first_update_id=90,
            final_update_id=110,
            prev_update_id=80,
            bids=[(10000, 8)],
            asks=[(10100, 9)],
            ts_local=1.0,
        )
    )

    with pytest.raises(BookSyncGapError):
        sync.on_depth_update(
            DepthUpdateEvent(
                symbol="BTCUSDT",
                first_update_id=111,
                final_update_id=120,
                prev_update_id=109,
                bids=[(10000, 1)],
                asks=[(10100, 1)],
                ts_local=2.0,
            )
        )
    assert sync.gap_count == 1
    assert sync.synced is False
    assert sync.last_update_id is None
    assert book.bids == {}
    assert book.asks == {}


def test_invalid_depth_batch_invalidates_epoch_without_advancing_sequence() -> None:
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)
    sync.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=100,
            bids=[(10000, 10)],
            asks=[(10100, 10)],
        )
    )
    sync.on_depth_update(
        DepthUpdateEvent(
            symbol="BTCUSDT",
            first_update_id=90,
            final_update_id=110,
            prev_update_id=80,
            bids=[],
            asks=[],
            ts_local=1.0,
        )
    )
    previous_epoch = sync.epoch

    with pytest.raises(BookSyncGapError, match="Invalid depth update"):
        sync.on_depth_update(
            DepthUpdateEvent(
                symbol="BTCUSDT",
                first_update_id=111,
                final_update_id=120,
                prev_update_id=110,
                # Adding a bid at the current best ask would lock/cross the book.
                bids=[(10100, 1)],
                asks=[],
                ts_local=2.0,
            )
        )

    assert sync.epoch == previous_epoch + 1
    assert sync.synced is False
    assert sync.ready is False
    assert sync.last_update_id is None
    assert sync.invalid_reason == "invalid_book_update"
    assert book.bids == {}
    assert book.asks == {}
    assert not sync.buffer


def test_invalid_buffered_bridge_invalidates_epoch_without_partial_snapshot_state() -> None:
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)

    sync.on_depth_update(
        DepthUpdateEvent(
            symbol="BTCUSDT",
            first_update_id=90,
            final_update_id=110,
            prev_update_id=80,
            # This update would cross the snapshot ask once applied.
            bids=[(10100, 1)],
            asks=[],
            ts_local=1.0,
        )
    )
    previous_epoch = sync.epoch

    with pytest.raises(BookSyncGapError, match="Invalid depth update"):
        sync.on_snapshot(
            SnapshotEvent(
                symbol="BTCUSDT",
                last_update_id=100,
                bids=[(10000, 10)],
                asks=[(10100, 10)],
            )
        )

    assert sync.epoch == previous_epoch + 1
    assert sync.synced is False
    assert sync.ready is False
    assert sync.last_update_id is None
    assert sync.invalid_reason == "invalid_book_update"
    assert book.bids == {}
    assert book.asks == {}
    assert not sync.buffer


def test_symbol_spec_is_compatibility_alias_for_instrument_spec():
    spec = SymbolSpec(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        price_currency="USDT",
        quantity_unit="BTC",
        venue="BINANCE_USDM",
    )

    assert isinstance(spec, InstrumentSpec)
    assert spec.tick_to_price(1000) == Decimal("100.0")
    assert spec.lot_to_qty(2) == Decimal("0.002")
    assert spec.price_currency == "USDT"


def test_binance_symbol_parser_preserves_asset_metadata():
    spec = parse_exchange_info_for_symbol(
        {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        },
        "BTCUSDT",
    )

    assert spec.price_currency == "USDT"
    assert spec.quantity_unit == "BTC"
    assert spec.venue == "BINANCE_USDM"
