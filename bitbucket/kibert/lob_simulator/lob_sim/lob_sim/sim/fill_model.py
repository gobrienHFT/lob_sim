from __future__ import annotations

from typing import Dict, Iterable, List

from ..book.types import AggTradeEvent, LevelChange
from .orders import Fill, Order


class PassiveFillModel:
    def __init__(self) -> None:
        self._orders: dict[tuple[str, str], Order] = {}
        self._order_index: dict[str, tuple[str, str]] = {}

    def get_order(self, symbol: str, side: str) -> Order | None:
        return self._orders.get((symbol, side))

    def place_order(self, order: Order) -> None:
        self._orders[(order.symbol, order.side)] = order
        self._order_index[order.order_id] = (order.symbol, order.side)

    def cancel_order(self, order_id: str) -> None:
        key = self._order_index.pop(order_id, None)
        if key is None:
            return
        self._orders.pop(key, None)

    def cancel_all_for_symbol_side(self, symbol: str, side: str) -> None:
        key = (symbol, side)
        order = self._orders.pop(key, None)
        if order is not None:
            self._order_index.pop(order.order_id, None)

    def _apply_level_fill(
        self,
        order: Order,
        reduction_lots: int,
        ts_local: float,
    ) -> Fill | None:
        if not order.active or reduction_lots <= 0 or order.remaining_lots <= 0:
            return None
        if order.queue_ahead_lots > 0:
            drained = min(order.queue_ahead_lots, reduction_lots)
            order.queue_ahead_lots -= drained
            reduction_lots -= drained
        if reduction_lots <= 0:
            return None
        fill_lots = min(order.remaining_lots, reduction_lots)
        order.remaining_lots -= fill_lots
        if order.remaining_lots <= 0:
            order.active = False
        return Fill(
            ts_local=ts_local,
            symbol=order.symbol,
            side=order.side,
            price_tick=order.price_tick,
            qty_lots=fill_lots,
            maker=True,
            order_id=order.order_id,
        )

    def _book_side(self, side: str) -> str:
        return "bids" if side == "bid" else "asks"

    def apply_depth_changes(self, symbol: str, changes: list[LevelChange], ts_local: float) -> list[Fill]:
        fills: list[Fill] = []
        to_remove: list[tuple[str, str]] = []
        for side in ("bid", "ask"):
            order = self.get_order(symbol, side)
            if order is None or not order.active:
                continue
            for ch in changes:
                if ch.side != self._book_side(side):
                    continue
                if ch.price_tick != order.price_tick:
                    continue
                dec = max(0, ch.previous_lots - ch.new_lots)
                if dec <= 0:
                    continue
                fill = self._apply_level_fill(order, dec, ts_local)
                if fill:
                    fills.append(fill)
            if order.active is False or order.remaining_lots <= 0:
                to_remove.append((symbol, side))

        for key in to_remove:
            ord_obj = self._orders.pop(key, None)
            if ord_obj is not None:
                self._order_index.pop(ord_obj.order_id, None)
        return fills

    def apply_agg_trade(self, trade: AggTradeEvent, ts_local: float) -> list[Fill]:
        side = "bid" if trade.buyer_is_maker else "ask"
        order = self.get_order(trade.symbol, side)
        if order is None or not order.active:
            return []
        if order.price_tick != trade.price_tick:
            return []
        fill = self._apply_level_fill(order, trade.qty_lots, ts_local)
        if order.remaining_lots <= 0:
            self.cancel_order(order.order_id)
            return [fill] if fill else []
        if fill is None:
            return []
        self._orders[(trade.symbol, side)] = order
        return [fill]
