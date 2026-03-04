from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

Side = Literal["bid", "ask"]


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    tick_size: Decimal
    step_size: Decimal

    def price_to_tick(self, price: Decimal | str | float | int) -> int:
        value = Decimal(str(price))
        return int((value / self.tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def tick_to_price(self, tick: int) -> Decimal:
        return Decimal(self.tick_size) * Decimal(tick)

    def qty_to_lot(self, qty: Decimal | str | float | int) -> int:
        value = Decimal(str(qty))
        return int((value / self.step_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def lot_to_qty(self, lots: int) -> Decimal:
        return Decimal(lots) * self.step_size


@dataclass(frozen=True)
class ExchangeInfoEvent:
    symbol: str
    tick_size: Decimal
    step_size: Decimal


@dataclass(frozen=True)
class SnapshotEvent:
    symbol: str
    last_update_id: int
    bids: list[tuple[int, int]]
    asks: list[tuple[int, int]]


@dataclass(frozen=True)
class DepthUpdateEvent:
    symbol: str
    first_update_id: int
    final_update_id: int
    prev_update_id: int
    bids: list[tuple[int, int]]
    asks: list[tuple[int, int]]
    ts_local: float


@dataclass(frozen=True)
class AggTradeEvent:
    symbol: str
    price_tick: int
    qty_lots: int
    buyer_is_maker: bool
    ts_local: float


@dataclass(frozen=True)
class LevelChange:
    side: str
    price_tick: int
    previous_lots: int
    new_lots: int
