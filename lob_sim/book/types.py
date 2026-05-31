from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

Side = Literal["bid", "ask"]


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    price_currency: str = ""
    quantity_unit: str = ""
    contract_multiplier: Decimal = Decimal("1")
    venue: str = ""

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("symbol must be non-empty")
        object.__setattr__(self, "tick_size", self._positive_decimal(self.tick_size, "tick_size"))
        object.__setattr__(self, "step_size", self._positive_decimal(self.step_size, "step_size"))
        object.__setattr__(
            self,
            "contract_multiplier",
            self._positive_decimal(self.contract_multiplier, "contract_multiplier"),
        )

    @staticmethod
    def _positive_decimal(value: Decimal | str | float | int, field_name: str) -> Decimal:
        try:
            decimal_value = Decimal(str(value))
        except Exception as exc:
            raise ValueError(f"{field_name} must be a positive finite decimal") from exc
        if not decimal_value.is_finite() or decimal_value <= 0:
            raise ValueError(f"{field_name} must be a positive finite decimal")
        return decimal_value

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


SymbolSpec = InstrumentSpec


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
