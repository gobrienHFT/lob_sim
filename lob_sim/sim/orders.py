from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OrderSide = Literal["bid", "ask"]
OrderType = Literal["limit", "market", "cancel"]


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    price_tick: int | None
    qty_lots: int
    quote_slot: str = "base"
    queue_ahead_lots: int = 0
    created_ts: float = 0.0
    remaining_lots: int = 0
    active: bool = True
    order_type: OrderType = "limit"
    is_strategy: bool = True
    refresh_key: str = ""


@dataclass
class Fill:
    ts_local: float
    symbol: str
    side: OrderSide
    price_tick: int
    qty_lots: int
    maker: bool = True
    order_id: str | None = None
    queue_ahead_lots: int = 0
    created_ts: float | None = None
