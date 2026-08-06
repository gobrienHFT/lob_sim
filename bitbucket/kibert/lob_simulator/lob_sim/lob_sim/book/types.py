from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

Side = Literal["bid", "ask"]


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    tick_size: Decimal
    step_size: Decimal

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol cannot be empty")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.step_size <= 0:
            raise ValueError("step_size must be positive")

    def price_to_tick(self, price: Decimal | str | float | int) -> int:
        value = Decimal(str(price))
        return int((value / self.tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def price_to_tick_exact(self, price: Decimal | str | float | int) -> int:
        value = Decimal(str(price))
        ticks = value / self.tick_size
        integral = ticks.to_integral_value()
        if ticks != integral:
            raise ValueError(f"Price {value} is not aligned to tick size {self.tick_size}")
        return int(integral)

    def price_to_tick_floor(self, price: Decimal | str | float | int) -> int:
        value = Decimal(str(price))
        return int((value / self.tick_size).to_integral_value(rounding=ROUND_FLOOR))

    def price_to_tick_ceil(self, price: Decimal | str | float | int) -> int:
        value = Decimal(str(price))
        return int((value / self.tick_size).to_integral_value(rounding=ROUND_CEILING))

    def tick_to_price(self, tick: int) -> Decimal:
        return Decimal(self.tick_size) * Decimal(tick)

    def qty_to_lot(self, qty: Decimal | str | float | int) -> int:
        value = Decimal(str(qty))
        return int((value / self.step_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def qty_to_lot_exact(self, qty: Decimal | str | float | int) -> int:
        value = Decimal(str(qty))
        lots = value / self.step_size
        integral = lots.to_integral_value()
        if lots != integral:
            raise ValueError(f"Quantity {value} is not aligned to step size {self.step_size}")
        return int(integral)

    def qty_to_lot_floor(self, qty: Decimal | str | float | int) -> int:
        value = Decimal(str(qty))
        return int((value / self.step_size).to_integral_value(rounding=ROUND_FLOOR))

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
    event_ts: float | None = None
    transaction_ts: float | None = None
    receive_seq: int | None = None


@dataclass(frozen=True)
class AggTradeEvent:
    symbol: str
    price_tick: int
    qty_lots: int
    buyer_is_maker: bool
    ts_local: float
    aggregate_trade_id: int | None = None
    event_ts: float | None = None
    transaction_ts: float | None = None
    receive_seq: int | None = None


@dataclass(frozen=True)
class LevelChange:
    side: str
    price_tick: int
    previous_lots: int
    new_lots: int
