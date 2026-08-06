from __future__ import annotations

from decimal import Decimal

import pytest

from lob_sim.book.local_book import BookInvariantError, LocalOrderBook
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
    assert book.last_update_id == 120
    assert sync.last_update_id == 120


def test_snapshot_retry_preserves_buffer_until_a_snapshot_bridges_it():
    spec = _spec()
    book = LocalOrderBook(symbol="BTCUSDT", spec=spec)
    sync = BookSynchronizer(book=book, resync_on_gap=True)
    sync.begin_resync("bootstrap")

    buffered = DepthUpdateEvent(
        symbol="BTCUSDT",
        first_update_id=150,
        final_update_id=160,
        prev_update_id=149,
        bids=[(10000, 8)],
        asks=[],
        ts_local=1.0,
    )
    sync.on_depth_update(buffered)

    from lob_sim.book.sync import BookSyncGapError

    try:
        sync.on_snapshot(
            SnapshotEvent(
                symbol="BTCUSDT",
                last_update_id=100,
                bids=[(10000, 10)],
                asks=[(10100, 10)],
            )
        )
    except BookSyncGapError:
        pass
    else:
        raise AssertionError("A snapshot older than the buffered bridge must be retried")

    assert list(sync.buffer) == [buffered]
    assert sync.synced is False

    sync.on_snapshot(
        SnapshotEvent(
            symbol="BTCUSDT",
            last_update_id=155,
            bids=[(10000, 10)],
            asks=[(10100, 10)],
        )
    )

    assert sync.synced is True
    assert sync.buffer == sync.buffer.__class__()
    assert book.bids[10000] == 8
    assert book.last_update_id == 160


def test_pre_snapshot_buffer_has_a_hard_visible_limit():
    from lob_sim.book.sync import BookSyncBufferOverflowError

    spec = _spec()
    sync = BookSynchronizer(
        book=LocalOrderBook(symbol="BTCUSDT", spec=spec),
        resync_on_gap=True,
        max_buffer_events=1,
    )
    first = DepthUpdateEvent("BTCUSDT", 1, 1, 0, [], [], 1.0)
    second = DepthUpdateEvent("BTCUSDT", 2, 2, 1, [], [], 2.0)

    sync.on_depth_update(first)
    try:
        sync.on_depth_update(second)
    except BookSyncBufferOverflowError:
        pass
    else:
        raise AssertionError("Buffer overflow must be visible to task supervision")

    assert sync.invalid_reason == "buffer_overflow"


def test_crossed_snapshot_is_rejected_without_mutating_existing_book():
    book = LocalOrderBook(symbol="BTCUSDT", spec=_spec())
    book.reset_from_snapshot(10, {10000: 5}, {10100: 5})

    with pytest.raises(BookInvariantError, match="crossed or locked"):
        book.reset_from_snapshot(11, {10200: 5}, {10100: 5})

    assert book.best_ticks() == (10000, 10100)
    assert book.last_update_id == 10


def test_crossing_depth_batch_is_rejected_atomically():
    book = LocalOrderBook(symbol="BTCUSDT", spec=_spec())
    book.reset_from_snapshot(10, {10000: 5}, {10100: 5})

    with pytest.raises(BookInvariantError, match="crossed or locked"):
        book.apply_depth_update([(10200, 5)], [])

    assert book.best_ticks() == (10000, 10100)
