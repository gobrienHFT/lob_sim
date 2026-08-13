from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from .local_book import BookInvariantError, LocalOrderBook
from .types import DepthUpdateEvent, LevelChange, SnapshotEvent


class BookSyncError(RuntimeError):
    """Base sync error."""


class BookSyncGapError(BookSyncError):
    """Raised when sequence continuity fails."""


class BookSyncBufferOverflowError(BookSyncError):
    """Raised rather than silently dropping pre-snapshot depth events."""


@dataclass
class BookSynchronizer:
    book: LocalOrderBook
    resync_on_gap: bool
    max_buffer_events: int = 20_000
    buffer: Deque[DepthUpdateEvent] = field(default_factory=deque)
    snapshot_id: int | None = None
    synced: bool = False
    last_update_id: int | None = None
    gap_count: int = 0
    ready: bool = False
    epoch: int = 0
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        if self.max_buffer_events <= 0:
            raise ValueError("max_buffer_events must be > 0")

    def _validate_symbol(self, symbol: str) -> None:
        if symbol != self.book.symbol:
            raise BookSyncError(f"Event symbol {symbol} does not match book symbol {self.book.symbol}")

    def _buffer_event(self, event: DepthUpdateEvent) -> None:
        self._validate_symbol(event.symbol)
        if len(self.buffer) >= self.max_buffer_events:
            self.invalid_reason = "buffer_overflow"
            raise BookSyncBufferOverflowError(
                f"Depth buffer overflow for {self.book.symbol} in sync epoch {self.epoch}: "
                f"limit={self.max_buffer_events}"
            )
        self.buffer.append(event)

    def begin_resync(self, reason: str, initial_event: DepthUpdateEvent | None = None) -> int:
        """Invalidate the current book and start an explicit sync epoch."""

        self.epoch += 1
        self.buffer.clear()
        self.snapshot_id = None
        self.synced = False
        self.last_update_id = None
        self.ready = False
        self.invalid_reason = reason
        self.book.clear()
        if initial_event is not None:
            self._buffer_event(initial_event)
        return self.epoch

    def reset(self) -> None:
        """Backward-compatible full reset; operational gap count starts over."""

        self.begin_resync("reset")
        self.gap_count = 0

    def _raise_snapshot_gap(self, snapshot: SnapshotEvent, first: DepthUpdateEvent) -> None:
        self.gap_count += 1
        self.snapshot_id = None
        self.synced = False
        self.last_update_id = None
        self.ready = False
        self.invalid_reason = "snapshot_does_not_bridge_buffer"
        self.book.clear()
        # Keep the buffered events for a retry.  A too-old REST snapshot is a
        # normal stream-first race, not permission to discard the evidence.
        raise BookSyncGapError(
            f"Buffered depth does not cover snapshot id {snapshot.last_update_id}: "
            f"U={first.first_update_id}, u={first.final_update_id}"
        )

    def _invalidate_buffered_gap(self, event: DepthUpdateEvent, previous_id: int) -> None:
        """Discard a pre-snapshot chain that contains a continuity gap."""

        self.gap_count += 1
        self.begin_resync("gap_in_snapshot_buffer")
        raise BookSyncGapError(
            f"Gap in buffered depth for {self.book.symbol}: "
            f"expected pu={previous_id}, got pu={event.prev_update_id}"
        )

    def _invalidate_invalid_snapshot(self, snapshot: SnapshotEvent, error: BookInvariantError) -> None:
        """Fail closed when a REST snapshot cannot seed a valid book."""

        self.gap_count += 1
        self.begin_resync("invalid_snapshot")
        raise BookSyncGapError(
            f"Invalid snapshot for {self.book.symbol} at u={snapshot.last_update_id}: {error}"
        ) from error

    def on_snapshot(self, snapshot: SnapshotEvent) -> list[LevelChange]:
        self._validate_symbol(snapshot.symbol)
        buffered = list(self.buffer)
        usable = [event for event in buffered if event.final_update_id >= snapshot.last_update_id]

        if usable:
            first = usable[0]
            if not (first.first_update_id <= snapshot.last_update_id <= first.final_update_id):
                self._raise_snapshot_gap(snapshot, first)

            previous_id = first.final_update_id
            for event in usable[1:]:
                if event.final_update_id <= previous_id:
                    continue
                if event.prev_update_id != previous_id:
                    self._invalidate_buffered_gap(event, previous_id)
                previous_id = event.final_update_id

        bids = {tick: qty for tick, qty in snapshot.bids}
        asks = {tick: qty for tick, qty in snapshot.asks}
        try:
            self.book.reset_from_snapshot(snapshot.last_update_id, bids, asks)
        except BookInvariantError as exc:
            self._invalidate_invalid_snapshot(snapshot, exc)
        self.snapshot_id = snapshot.last_update_id
        self.last_update_id = None
        self.synced = False
        self.ready = True
        self.invalid_reason = "awaiting_snapshot_bridge"
        self.buffer.clear()

        changes: list[LevelChange] = []
        for event in usable:
            if self.last_update_id is not None and event.final_update_id <= self.last_update_id:
                continue
            # Buffered events must use the same fail-closed mutation boundary
            # as live updates.  Otherwise an invalid bridge batch could leave
            # a snapshot-seeded book partially applied without invalidating
            # the sync epoch.
            changes.extend(self._apply_book_update(event))
            self.last_update_id = event.final_update_id
            self.book.last_update_id = event.final_update_id

        if usable:
            self.synced = True
            self.invalid_reason = None
        return changes

    def on_depth_update(self, event: DepthUpdateEvent) -> list[LevelChange]:
        if not self.ready:
            self._buffer_event(event)
            return []
        return self._apply(event)

    def _invalidate_gap(self, reason: str, event: DepthUpdateEvent, message: str) -> list[LevelChange]:
        self.gap_count += 1
        self.begin_resync(reason, initial_event=event)
        # Detection is never optional. RESYNC_ON_GAP only controls whether the
        # collector requests a new snapshot automatically; callers must still
        # invalidate the previous epoch and observe the error.
        raise BookSyncGapError(message)

    def _invalidate_invalid_update(self, event: DepthUpdateEvent, error: BookInvariantError) -> list[LevelChange]:
        """Fail closed when a depth batch cannot produce a valid book.

        A malformed or crossed batch is not safe to retain as a bridge event:
        replaying it after a snapshot would recreate the same invalid state.
        Discard it, clear the book, and require a fresh snapshot plus future
        contiguous updates before execution can resume.
        """

        self.gap_count += 1
        self.begin_resync("invalid_book_update")
        raise BookSyncGapError(
            f"Invalid depth update for {self.book.symbol} at u={event.final_update_id}: {error}"
        ) from error

    def _apply_book_update(self, event: DepthUpdateEvent) -> list[LevelChange]:
        try:
            return self.book.apply_depth_update(event.bids, event.asks)
        except BookInvariantError as exc:
            return self._invalidate_invalid_update(event, exc)

    def _apply(self, event: DepthUpdateEvent) -> list[LevelChange]:
        if self.snapshot_id is None:
            self._buffer_event(event)
            return []
        if not self.synced:
            if event.final_update_id < self.snapshot_id:
                return []
            if not (event.first_update_id <= self.snapshot_id <= event.final_update_id):
                return self._invalidate_gap(
                    "first_event_does_not_bridge_snapshot",
                    event,
                    f"First depth event does not cover snapshot id {self.snapshot_id}: "
                    f"U={event.first_update_id}, u={event.final_update_id}",
                )
            changes = self._apply_book_update(event)
            self.synced = True
            self.invalid_reason = None
            self.last_update_id = event.final_update_id
            self.book.last_update_id = event.final_update_id
            return changes

        if event.final_update_id <= (self.last_update_id or -1):
            return []
        if event.prev_update_id != self.last_update_id:
            return self._invalidate_gap(
                "sequence_gap",
                event,
                f"Gap detected for {self.book.symbol}: expected pu={self.last_update_id}, got pu={event.prev_update_id}",
            )

        changes = self._apply_book_update(event)
        self.last_update_id = event.final_update_id
        self.book.last_update_id = event.final_update_id
        return changes
