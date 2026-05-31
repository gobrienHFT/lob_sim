from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..book.types import InstrumentSpec
from ..config import Config
from .orders import Fill


@dataclass(frozen=True)
class FeeAssessment:
    rate_bps: Decimal
    notional: Decimal
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class StaticFeeModel:
    maker_bps: Decimal
    taker_bps: Decimal

    @classmethod
    def from_config(cls, cfg: Config) -> StaticFeeModel:
        return cls(maker_bps=cfg.fees_maker_bps, taker_bps=cfg.fees_taker_bps)

    def rate_bps(self, fill: Fill) -> Decimal:
        return self.maker_bps if fill.maker else self.taker_bps

    def assess(self, fill: Fill, spec: InstrumentSpec) -> FeeAssessment:
        price = spec.tick_to_price(fill.price_tick)
        qty = spec.lot_to_qty(fill.qty_lots)
        notional = price * qty * spec.contract_multiplier
        rate = self.rate_bps(fill)
        amount = notional * (rate / Decimal("10000"))
        return FeeAssessment(
            rate_bps=rate,
            notional=notional,
            amount=amount,
            currency=spec.price_currency or "quote",
        )
