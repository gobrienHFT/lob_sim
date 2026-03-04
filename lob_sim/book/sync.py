from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from .local_book import LocalOrderBook
from .types import DepthUpdateEvent, LevelChange, SnapshotEvent


class BookSyncError(RuntimeError):
    """Base sync error."""


class BookSyncGapError(BookSyncError):
    """Raised when sequence continuity fails."""


@dataclass
class BookSynchronizer:
    book: LocalOrderBook
    resync_on_gap: bool
    buffer: Deque[DepthUpdateEvent] = field(default_factory=deque)
    snapshot_id: int | None = None
    synced: bool = False
    last_update_id: int | None = None
    gap_count: int = 0
    ready: bool = False

    def reset(self) -> None:
        self.buffer.clear()
        self.snapshot_id = None
        self.synced = False
        self.last_update_id = None
        self.gap_count = 0
        self.ready = False
        self.book.bids.clear()
        self.book.asks.clear()

    def on_snapshot(self, snapshot: SnapshotEvent) -> None:
        bids = {tick: qty for tick, qty in snapshot.bids}
        asks = {tick: qty for tick, qty in snapshot.asks}
        self.book.reset_from_snapshot(snapshot.last_update_id, bids, asks)
        self.snapshot_id = snapshot.last_update_id
        self.last_update_id = None
        self.synced = False
        self.ready = True
        buffered = list(self.buffer)
        self.buffer.clear()
        for event in buffered:
            self.on_depth_update(event)

    def on_depth_update(self, event: DepthUpdateEvent) -> list[LevelChange]:
        if not self.ready:
            self.buffer.append(event)
            return []
        return self._apply(event)

    def _apply(self, event: DepthUpdateEvent) -> list[LevelChange]:
        if self.snapshot_id is None:
            self.buffer.append(event)
            return []
        if not self.synced:
            if event.final_update_id < self.snapshot_id:
                return []
            if not (event.first_update_id <= self.snapshot_id <= event.final_update_id):
                self.gap_count += 1
                if self.resync_on_gap:
                    raise BookSyncGapError(
                        f"First depth event does not cover snapshot id {self.snapshot_id}: "
                        f"U={event.first_update_id}, u={event.final_update_id}"
                    )
                return []
            self.synced = True
            self.last_update_id = event.final_update_id
            changes = self.book.apply_depth_update(event.bids, event.asks)
            return changes

        if event.final_update_id <= (self.last_update_id or -1):
            return []
        if self.resync_on_gap and event.prev_update_id != self.last_update_id:
            self.gap_count += 1
            raise BookSyncGapError(
                f"Gap detected for {self.book.symbol}: expected pu={self.last_update_id}, got pu={event.prev_update_id}"
            )

        self.last_update_id = event.final_update_id
        return self.book.apply_depth_update(event.bids, event.asks)
