from __future__ import annotations

from decimal import Decimal

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSynchronizer
from lob_sim.book.types import DepthUpdateEvent, SnapshotEvent, SymbolSpec


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
