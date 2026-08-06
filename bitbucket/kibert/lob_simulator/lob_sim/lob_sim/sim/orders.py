from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..book.types import Side


class OrderState(StrEnum):
    """Venue-side lifecycle state for a simulated order."""

    LIVE = "live"
    PENDING_CANCEL = "pending_cancel"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    order_id: str
    symbol: str
    side: Side
    price_tick: int
    qty_lots: int
    queue_ahead_lots: int
    created_ts: float
    remaining_lots: int
    state: OrderState = OrderState.LIVE
    cancel_requested_ts: float | None = None
    closed_ts: float | None = None

    def __post_init__(self) -> None:
        if self.side not in {"bid", "ask"}:
            raise ValueError(f"Invalid order side: {self.side}")
        if not self.symbol or not self.order_id:
            raise ValueError("order_id and symbol cannot be empty")
        if self.price_tick <= 0:
            raise ValueError("price_tick must be positive")
        if isinstance(self.qty_lots, bool) or not isinstance(self.qty_lots, int) or self.qty_lots <= 0:
            raise ValueError("qty_lots must be a positive integer")
        if not 0 <= self.remaining_lots <= self.qty_lots:
            raise ValueError("remaining_lots must be between zero and qty_lots")
        if self.queue_ahead_lots < 0:
            raise ValueError("queue_ahead_lots cannot be negative")
        if not math.isfinite(self.created_ts) or self.created_ts < 0:
            raise ValueError("created_ts must be finite and nonnegative")

    @property
    def active(self) -> bool:
        return self.state in {OrderState.LIVE, OrderState.PENDING_CANCEL}

    @property
    def filled_lots(self) -> int:
        return self.qty_lots - self.remaining_lots

    def request_cancel(self, ts: float) -> bool:
        if self.state is not OrderState.LIVE:
            return False
        self.state = OrderState.PENDING_CANCEL
        self.cancel_requested_ts = ts
        return True

    def cancel(self, ts: float) -> None:
        if self.active:
            self.state = OrderState.CANCELLED
            self.closed_ts = ts

    def apply_fill(self, lots: int, ts: float) -> None:
        if lots <= 0 or lots > self.remaining_lots:
            raise ValueError("fill lots must be positive and no larger than remaining_lots")
        self.remaining_lots -= lots
        if self.remaining_lots == 0:
            self.state = OrderState.FILLED
            self.closed_ts = ts


@dataclass
class Fill:
    ts_local: float
    symbol: str
    side: Side
    price_tick: int
    qty_lots: int
    maker: bool = True
    order_id: str | None = None
    cause: str = "unknown"
    queue_ahead_before_lots: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.ts_local) or self.ts_local < 0:
            raise ValueError("ts_local must be finite and nonnegative")
        if not self.symbol:
            raise ValueError("symbol cannot be empty")
        if self.side not in {"bid", "ask"}:
            raise ValueError(f"Invalid fill side: {self.side}")
        if self.price_tick <= 0 or self.qty_lots <= 0:
            raise ValueError("fill price_tick and qty_lots must be positive")
        if self.queue_ahead_before_lots < 0:
            raise ValueError("queue_ahead_before_lots cannot be negative")
