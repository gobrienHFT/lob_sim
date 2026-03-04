from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque, Dict, Iterable, List, Tuple

from .types import LevelChange, SymbolSpec


@dataclass
class LocalOrderBook:
    symbol: str
    spec: SymbolSpec
    top_n: int = 50
    bids: Dict[int, int] = field(default_factory=dict)
    asks: Dict[int, int] = field(default_factory=dict)
    last_update_id: int | None = None

    def reset_from_snapshot(self, last_update_id: int, bids: dict[int, int], asks: dict[int, int]) -> None:
        self.bids = {tick: qty for tick, qty in bids.items() if qty > 0}
        self.asks = {tick: qty for tick, qty in asks.items() if qty > 0}
        self.last_update_id = last_update_id

    def _apply_side(
        self,
        book: Dict[int, int],
        updates: Iterable[tuple[int, int]],
        side: str,
        changes: List[LevelChange],
    ) -> None:
        for tick, qty in updates:
            prev = book.get(tick, 0)
            if qty <= 0:
                if tick in book:
                    del book[tick]
                qty = 0
            else:
                book[tick] = qty
            if prev != qty:
                changes.append(LevelChange(side=side, price_tick=tick, previous_lots=prev, new_lots=qty))

    def apply_depth_update(self, bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> list[LevelChange]:
        changes: list[LevelChange] = []
        self._apply_side(self.bids, bids, "bids", changes)
        self._apply_side(self.asks, asks, "asks", changes)
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
