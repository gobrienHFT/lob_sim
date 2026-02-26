from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str  # "bid" or "ask"
    price_tick: int
    qty_lots: int
    queue_ahead_lots: int
    created_ts: float
    remaining_lots: int
    active: bool = True


@dataclass
class Fill:
    ts_local: float
    symbol: str
    side: str
    price_tick: int
    qty_lots: int
    maker: bool = True
    order_id: str | None = None
