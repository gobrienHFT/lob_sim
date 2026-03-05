from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from ..book.local_book import LocalOrderBook
from ..config import Config
from .orders import Order


@dataclass
class StrategyDecision:
    bid_tick: int | None
    ask_tick: int | None
    bid_refresh: bool = False
    ask_refresh: bool = False
    reason: str | None = None
    size_lots: int = 0


class MarketMakingStrategy:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._returns = deque(maxlen=cfg.mm_volatility_window)
        self._prev_mid = None

    def _update_volatility(self, book: LocalOrderBook) -> None:
        mid = book.mid_price()
        if mid is None:
            return
        if self._prev_mid is not None and self._prev_mid > 0:
            ret = abs(mid - self._prev_mid) / self._prev_mid
            self._returns.append(ret)
        self._prev_mid = mid

    def _volatility(self) -> Decimal:
        if not self._returns:
            return Decimal("0")
        return sum(self._returns) / Decimal(len(self._returns))

    def _bps_to_ticks(self, book: LocalOrderBook, bps: Decimal) -> Decimal:
        mid = book.mid_price()
        if mid is None:
            return Decimal("0")
        return (mid * bps / Decimal("10000")) / book.spec.tick_size

    def _tick_round(self, book: LocalOrderBook, value: Decimal) -> int:
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))

    def should_refresh(self, book: LocalOrderBook, side: str, order: Order | None) -> bool:
        if order is None:
            return False
        # queue deterioration is measured in lots from front of queue for the order's resting level
        return order.queue_ahead_lots > self.cfg.mm_queue_repost_lots

    def propose(self, book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        self._update_volatility(book)
        best = book.best_ticks()
        if best is None:
            return StrategyDecision(None, None)

        bid_tick, ask_tick = best
        spread_scale = Decimal("1") + (self._volatility() * self.cfg.mm_volatility_spread_factor)
        half_spread_bps = max(Decimal("0"), self.cfg.mm_half_spread_bps * spread_scale)
        half_spread_ticks = max(Decimal("1"), self._bps_to_ticks(book, half_spread_bps))

        mid = (book.spec.tick_to_price(bid_tick) + book.spec.tick_to_price(ask_tick)) / Decimal("2")
        skew_ticks = (inventory_qty * self.cfg.mm_skew_bps_per_unit / Decimal("10000")) * (mid / book.spec.tick_size)

        bid = self._tick_round(book, mid / book.spec.tick_size - half_spread_ticks - skew_ticks)
        ask = self._tick_round(book, mid / book.spec.tick_size + half_spread_ticks + skew_ticks)

        if bid >= ask:
            return StrategyDecision(None, None, reason="crossing_quotes")

        size = book.spec.qty_to_lot(max(Decimal("0.00000001"), self.cfg.mm_order_qty))
        return StrategyDecision(int(bid), int(ask), size_lots=size)
