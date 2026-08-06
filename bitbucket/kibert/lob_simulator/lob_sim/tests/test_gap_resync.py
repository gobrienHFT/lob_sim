from __future__ import annotations

from decimal import Decimal

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.sync import BookSyncGapError, BookSynchronizer
from lob_sim.book.types import DepthUpdateEvent, SnapshotEvent, SymbolSpec


def _spec() -> SymbolSpec:
    return SymbolSpec(symbol="ETHUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))


def test_book_sync_raises_on_gap():
    spec = _spec()
    book = LocalOrderBook(symbol="ETHUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)
    sync.on_snapshot(
        SnapshotEvent(
            symbol="ETHUSDT",
            last_update_id=200,
            bids=[(20000, 10)],
            asks=[(20100, 10)],
        )
    )
    first = DepthUpdateEvent(
        symbol="ETHUSDT",
        first_update_id=150,
        final_update_id=220,
        prev_update_id=149,
        bids=[],
        asks=[],
        ts_local=1.0,
    )
    sync.on_depth_update(first)

    with pytest.raises(BookSyncGapError):
        sync.on_depth_update(
            DepthUpdateEvent(
                symbol="ETHUSDT",
                first_update_id=221,
                final_update_id=230,
                prev_update_id=219,  # should be 220
                bids=[],
                asks=[],
                ts_local=2.0,
            )
        )

    assert sync.synced is False
    assert sync.ready is False
    assert sync.invalid_reason == "sequence_gap"
    assert sync.epoch == 1
    assert sync.last_update_id is None
    assert book.last_update_id is None
    assert book.bids == {}
    assert book.asks == {}
    assert len(sync.buffer) == 1
