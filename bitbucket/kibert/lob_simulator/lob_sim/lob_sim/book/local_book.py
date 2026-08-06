from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from .types import LevelChange, SymbolSpec


class BookInvariantError(RuntimeError):
    """Raised before invalid market-by-price state can become observable."""


@dataclass
class LocalOrderBook:
    symbol: str
    spec: SymbolSpec
    top_n: int = 50
    bids: dict[int, int] = field(default_factory=dict)
    asks: dict[int, int] = field(default_factory=dict)
    last_update_id: int | None = None

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        self._validate_state(self.bids, self.asks)

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = None

    def reset_from_snapshot(self, last_update_id: int, bids: dict[int, int], asks: dict[int, int]) -> None:
        if last_update_id < 0:
            raise BookInvariantError("last_update_id cannot be negative")
        for side, levels in (("bid", bids), ("ask", asks)):
            if any(tick <= 0 or qty < 0 for tick, qty in levels.items()):
                raise BookInvariantError(
                    f"{side} snapshot levels require positive ticks and nonnegative quantities"
                )
        next_bids = {tick: qty for tick, qty in bids.items() if qty > 0}
        next_asks = {tick: qty for tick, qty in asks.items() if qty > 0}
        self._validate_state(next_bids, next_asks)
        self.bids = next_bids
        self.asks = next_asks
        self.last_update_id = last_update_id

    @staticmethod
    def _apply_side(
        book: dict[int, int],
        updates: Iterable[tuple[int, int]],
        side: str,
        changes: list[LevelChange],
    ) -> None:
        for tick, qty in updates:
            if tick <= 0:
                raise BookInvariantError(f"{side} price tick must be positive: {tick}")
            if qty < 0:
                raise BookInvariantError(f"{side} quantity cannot be negative: {qty}")
            prev = book.get(tick, 0)
            if qty == 0:
                book.pop(tick, None)
            else:
                book[tick] = qty
            if prev != qty:
                changes.append(LevelChange(side=side, price_tick=tick, previous_lots=prev, new_lots=qty))

    @staticmethod
    def _validate_state(bids: dict[int, int], asks: dict[int, int]) -> None:
        for side, levels in (("bid", bids), ("ask", asks)):
            if any(tick <= 0 or qty <= 0 for tick, qty in levels.items()):
                raise BookInvariantError(f"{side} levels require positive ticks and quantities")
        if bids and asks:
            best_bid = max(bids)
            best_ask = min(asks)
            if best_bid >= best_ask:
                raise BookInvariantError(
                    f"crossed or locked book: best_bid_tick={best_bid}, best_ask_tick={best_ask}"
                )

    def apply_depth_update(
        self,
        bids: list[tuple[int, int]],
        asks: list[tuple[int, int]],
    ) -> list[LevelChange]:
        next_bids = self.bids.copy()
        next_asks = self.asks.copy()
        changes: list[LevelChange] = []
        self._apply_side(next_bids, bids, "bids", changes)
        self._apply_side(next_asks, asks, "asks", changes)
        self._validate_state(next_bids, next_asks)
        self.bids = next_bids
        self.asks = next_asks
        return changes

    def get_level_size(self, side: str, tick: int) -> int:
        if side == "bids":
            return self.bids.get(tick, 0)
        if side == "asks":
            return self.asks.get(tick, 0)
        raise ValueError(f"Invalid side: {side}")

    def best_bid(self) -> int | None:
        if not self.bids:
            return None
        return max(self.bids.keys())

    def best_ask(self) -> int | None:
        if not self.asks:
            return None
        return min(self.asks.keys())

    def best_ticks(self) -> tuple[int, int] | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return bid, ask

    def mid_price(self) -> Decimal | None:
        bt = self.best_ticks()
        if bt is None:
            return None
        b, a = bt
        return (self.spec.tick_to_price(b) + self.spec.tick_to_price(a)) / Decimal("2")

    def top_levels(self, side: str) -> list[tuple[int, int]]:
        if side == "bids":
            return sorted(self.bids.items(), reverse=True)[: self.top_n]
        if side == "asks":
            return sorted(self.asks.items())[: self.top_n]
        raise ValueError(f"Invalid side: {side}")

    def total_levels(self) -> int:
        return len(self.bids) + len(self.asks)
