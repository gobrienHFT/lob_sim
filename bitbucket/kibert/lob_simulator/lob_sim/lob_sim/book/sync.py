from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .local_book import LocalOrderBook
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
    buffer: deque[DepthUpdateEvent] = field(default_factory=deque)
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
        """Invalidate the current book and start a new, explicit sync epoch."""

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
        raise BookSyncGapError(
            f"Buffered depth does not cover snapshot id {snapshot.last_update_id}: "
            f"U={first.first_update_id}, u={first.final_update_id}"
        )

    def on_snapshot(self, snapshot: SnapshotEvent) -> list[LevelChange]:
        """Install a snapshot only after its buffered bridge is validated.

        A snapshot that is too old leaves the buffer intact so a retry can align
        without pausing websocket ingestion or discarding evidence.
        """

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
                    self.gap_count += 1
                    self.snapshot_id = None
                    self.synced = False
                    self.last_update_id = None
                    self.ready = False
                    self.invalid_reason = "gap_in_snapshot_buffer"
                    self.book.clear()
                    raise BookSyncGapError(
                        f"Gap in buffered depth for {self.book.symbol}: "
                        f"expected pu={previous_id}, got pu={event.prev_update_id}"
                    )
                previous_id = event.final_update_id

        bids = {tick: qty for tick, qty in snapshot.bids}
        asks = {tick: qty for tick, qty in snapshot.asks}
        self.book.reset_from_snapshot(snapshot.last_update_id, bids, asks)
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
            changes.extend(self.book.apply_depth_update(event.bids, event.asks))
            self.last_update_id = event.final_update_id
            self.book.last_update_id = event.final_update_id

        if usable:
            self.synced = True
            self.invalid_reason = None
        return changes

    def on_depth_update(self, event: DepthUpdateEvent) -> list[LevelChange]:
        self._validate_symbol(event.symbol)
        if not self.ready:
            self._buffer_event(event)
            return []
        return self._apply(event)

    def _invalidate_gap(self, reason: str, event: DepthUpdateEvent, message: str) -> list[LevelChange]:
        self.gap_count += 1
        self.begin_resync(reason, initial_event=event)
        # Detection is never optional. `resync_on_gap` controls whether a live
        # collector automatically requests the next snapshot; callers must
        # always be told that the previous epoch is unusable.
        raise BookSyncGapError(message)

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
            self.synced = True
            self.invalid_reason = None
            self.last_update_id = event.final_update_id
            changes = self.book.apply_depth_update(event.bids, event.asks)
            self.book.last_update_id = event.final_update_id
            return changes

        if event.final_update_id <= (self.last_update_id or -1):
            return []
        if event.prev_update_id != self.last_update_id:
            return self._invalidate_gap(
                "sequence_gap",
                event,
                f"Gap detected for {self.book.symbol}: "
                f"expected pu={self.last_update_id}, got pu={event.prev_update_id}",
            )

        self.last_update_id = event.final_update_id
        changes = self.book.apply_depth_update(event.bids, event.asks)
        self.book.last_update_id = event.final_update_id
        return changes
