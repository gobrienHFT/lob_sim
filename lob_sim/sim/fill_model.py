from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List

from ..book.types import AggTradeEvent, LevelChange
from .orders import Fill, Order, OrderSide


class PassiveFillModel:
    def __init__(self) -> None:
        self._books: dict[str, dict[str, dict[int, Deque[Order]]]] = {}
        self._orders: dict[tuple[str, OrderSide], Order] = {}
        self._order_index: dict[str, tuple[str, OrderSide]] = {}
        self._seq = 0

    def _book(self, symbol: str) -> dict[str, dict[int, Deque[Order]]]:
        return self._books.setdefault(symbol, {"bids": {}, "asks": {}})

    def _bucket(self, side: str) -> str:
        return "bids" if side == "bid" else "asks"

    def _reverse_side(self, side: str) -> str:
        return "ask" if side == "bid" else "bid"

    def _ensure_order_type(self, side: str) -> OrderSide:
        if side not in {"bid", "ask"}:
            raise ValueError(f"Invalid side: {side}")
        return side

    def get_order(self, symbol: str, side: str) -> Order | None:
        return self._orders.get((symbol, self._ensure_order_type(side)))

    def queue_ahead_lots(self, symbol: str, order: Order | None) -> int:
        if order is None:
            return 0
        bucket = self._book(order.symbol)[self._bucket(order.side)]
        queue = bucket.get(order.price_tick)
        if queue is None:
            return max(0, order.queue_ahead_lots)
        ahead = 0
        for q in queue:
            if q.order_id == order.order_id:
                break
            ahead += q.remaining_lots
        if ahead > 0:
            return max(0, ahead)
        return max(0, order.queue_ahead_lots)

    def best_bid_tick(self, symbol: str) -> int | None:
        bid = self._book(symbol)["bids"]
        return max(bid.keys()) if bid else None

    def best_ask_tick(self, symbol: str) -> int | None:
        ask = self._book(symbol)["asks"]
        return min(ask.keys()) if ask else None

    def depth_levels(self, symbol: str, side: str, levels: int = 20) -> list[tuple[int, int]]:
        bucket = self._bucket(self._ensure_order_type(side))
        entries = self._book(symbol)[bucket]
        if side == "bid":
            sorted_levels = sorted(entries.items(), reverse=True)
        else:
            sorted_levels = sorted(entries.items())
        return [(price, sum(order.remaining_lots for order in queue)) for price, queue in sorted_levels[:levels]]

    def _add_venue_order(self, symbol: str, side: str, price_tick: int, lots: int) -> None:
        if lots <= 0:
            return
        venue_order = Order(
            order_id=f"VENUE-{symbol}-{side}-{self._seq}",
            symbol=symbol,
            side=self._ensure_order_type(side),
            price_tick=price_tick,
            qty_lots=lots,
            remaining_lots=lots,
            created_ts=0.0,
            queue_ahead_lots=0,
            is_strategy=False,
        )
        self._seq += 1
        self._book(symbol)[self._bucket(side)].setdefault(price_tick, deque()).append(venue_order)

    def _remove_order_from_book(self, order: Order) -> None:
        bucket = self._book(order.symbol)[self._bucket(order.side)]
        queue = bucket.get(order.price_tick)
        if queue is None:
            return
        try:
            queue.remove(order)
        except ValueError:
            return
        if not queue:
            bucket.pop(order.price_tick, None)
        order.active = False

    def _clean_side_level_if_empty(self, symbol: str, side: str, price_tick: int) -> None:
        queue = self._book(symbol)[self._bucket(side)].get(price_tick)
        if queue is None:
            return
        while queue and (not queue[0].active or queue[0].remaining_lots <= 0):
            queue.popleft()
        if not queue:
            self._book(symbol)[self._bucket(side)].pop(price_tick, None)

    def queue_position(self, order: Order) -> int:
        return self.queue_ahead_lots(order.symbol, order)

    def cancel_order(self, order_id: str) -> None:
        key = self._order_index.pop(order_id, None)
        if key is None:
            return
        self._orders.pop(key, None)
        symbol, _side = key
        current_order = self._orders.get(key)
        if current_order is None:
            # best effort removal from book (if active map already diverged, still cleanup queue)
            book_side = self._book(symbol)[self._bucket(_side)]
            for queue in book_side.values():
                for q in list(queue):
                    if q.order_id == order_id:
                        self._remove_order_from_book(q)
                        return

    def cancel_all_for_symbol_side(self, symbol: str, side: str) -> None:
        key = (symbol, self._ensure_order_type(side))
        order = self._orders.pop(key, None)
        if order is None:
            return
        self._order_index.pop(order.order_id, None)
        self._remove_order_from_book(order)

    def _consume_front(
        self,
        symbol: str,
        side: str,
        queue: Deque[Order],
        lots: int,
        ts_local: float,
        maker_fill: bool,
    ) -> tuple[list[Fill], int]:
        fills: list[Fill] = []
        remaining = max(0, lots)
        while remaining > 0 and queue:
            head = queue[0]
            if not head.active or head.remaining_lots <= 0:
                queue.popleft()
                continue

            if head.is_strategy and head.queue_ahead_lots > 0:
                consumed_ahead = min(remaining, head.queue_ahead_lots)
                head.queue_ahead_lots -= consumed_ahead
                remaining -= consumed_ahead
                if remaining <= 0:
                    break

            queue_ahead = self.queue_ahead_lots(symbol, head)
            take = min(remaining, head.remaining_lots)
            head.remaining_lots -= take
            remaining -= take

            if head.is_strategy:
                fills.append(
                    Fill(
                        ts_local=ts_local,
                        symbol=symbol,
                        side=head.side,
                        price_tick=head.price_tick,
                        qty_lots=take,
                        maker=maker_fill,
                        order_id=head.order_id,
                        queue_ahead_lots=queue_ahead,
                        created_ts=head.created_ts,
                    )
                )

                if head.remaining_lots <= 0:
                    self._orders.pop((symbol, head.side), None)
                    self._order_index.pop(head.order_id, None)

            if head.remaining_lots <= 0:
                queue.popleft()
                head.active = False
            elif not head.is_strategy:
                # venue liquidity partially consumed and remains in book
                break

        if not queue:
            self._book(symbol)[self._bucket(side)].pop(head.price_tick, None) if "head" in locals() else None
        return fills, remaining

    def _consume_level(
        self,
        symbol: str,
        side: str,
        price_tick: int,
        lots: int,
        ts_local: float,
        maker_fill: bool,
    ) -> list[Fill]:
        bucket = self._bucket(side)
        queue = self._book(symbol)[bucket].get(price_tick)
        if queue is None:
            return []

        fills, remaining = self._consume_front(symbol, side, queue, lots, ts_local, maker_fill)
        if not queue:
            self._book(symbol)[bucket].pop(price_tick, None)
        if remaining <= 0:
            return fills

        # any unfinished consumption implies all volume at this level has been consumed.
        return fills

    def _consume_book(
        self,
        symbol: str,
        taker_side: str,
        qty: int,
        price_cap: int | None,
        ts_local: float,
        maker_fill: bool,
    ) -> list[Fill]:
        if qty <= 0:
            return []

        opposite_bucket = self._bucket(self._reverse_side(taker_side))
        levels = self._book(symbol)[opposite_bucket]
        level_ticks = sorted(levels.keys(), reverse=(taker_side == "ask"))

        fills: list[Fill] = []
        remaining = qty

        for tick in level_ticks:
            if remaining <= 0:
                break
            if price_cap is not None:
                if taker_side == "bid" and tick > price_cap:
                    continue
                if taker_side == "ask" and tick < price_cap:
                    continue
            fills_level, remaining = self._consume_front(
                symbol=symbol,
                side=self._reverse_side(taker_side),
                queue=levels[tick],
                lots=remaining,
                ts_local=ts_local,
                maker_fill=maker_fill,
            )
            fills.extend(fills_level)
            current_queue = levels.get(tick)
            if current_queue is None or not current_queue:
                levels.pop(tick, None)
            if remaining > 0:
                # Venue liquidity at this level was only partially consumed, so
                # price-time matching should stop for this side. This protects
                # from crossing into worse prices too aggressively.
                current_queue = levels.get(tick)
                if current_queue and not current_queue[0].is_strategy:
                    break

        return fills

    def _marketable_fill(
        self,
        order: Order,
        ts_local: float,
    ) -> list[Fill]:
        fills = self._consume_book(
            symbol=order.symbol,
            taker_side=order.side,
            qty=order.remaining_lots,
            price_cap=order.price_tick,
            ts_local=ts_local,
            maker_fill=False,
        )
        consumed = sum(fill.qty_lots for fill in fills)
        order.remaining_lots = max(0, order.remaining_lots - consumed)
        return fills

    def _can_market(self, order: Order) -> bool:
        if order.price_tick is None:
            return True
        bucket = self._book(order.symbol)
        if order.side == "bid":
            best_ask = self.best_ask_tick(order.symbol)
            return best_ask is not None and order.price_tick >= best_ask
        best_bid = self.best_bid_tick(order.symbol)
        return best_bid is not None and order.price_tick <= best_bid

    def _post_resting(self, order: Order) -> None:
        if order.price_tick is None or order.remaining_lots <= 0:
            return
        bucket = self._book(order.symbol)[self._bucket(order.side)]
        queue = bucket.setdefault(order.price_tick, deque())
        visible_queue_ahead = sum(q.remaining_lots for q in queue)
        order.queue_ahead_lots = 0 if visible_queue_ahead > 0 else max(0, order.queue_ahead_lots)
        order.active = True
        queue.append(order)
        self._orders[(order.symbol, order.side)] = order
        self._order_index[order.order_id] = (order.symbol, order.side)

    def seed_from_snapshot(self, symbol: str, bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> None:
        # Strategy orders are external to the venue stream. Remove active strategy quotes and rebuild.
        self.cancel_all_for_symbol_side(symbol, "bid")
        self.cancel_all_for_symbol_side(symbol, "ask")
        self._books[symbol] = {"bids": {}, "asks": {}}

        for price, qty in bids:
            self._add_venue_order(symbol=symbol, side="bid", price_tick=price, lots=qty)

        for price, qty in asks:
            self._add_venue_order(symbol=symbol, side="ask", price_tick=price, lots=qty)

    def place_order(self, order: Order) -> list[Fill]:
        if order.qty_lots <= 0:
            return []
        order.remaining_lots = max(order.remaining_lots or order.qty_lots, 0)
        if order.order_type == "cancel":
            return []
        if order.order_type == "market":
            return self._consume_book(
                symbol=order.symbol,
                taker_side=order.side,
                qty=order.remaining_lots,
                price_cap=None,
                ts_local=order.created_ts,
                maker_fill=False,
            )

        existing = self.get_order(order.symbol, order.side)
        if existing is not None and existing.order_id != order.order_id:
            self.cancel_order(existing.order_id)
        if order.order_type == "limit":
            if self._can_market(order):
                fills = self._marketable_fill(order, ts_local=order.created_ts)
                if order.remaining_lots <= 0:
                    return fills
                # Any remainder after a marketable sweep can now rest at the same limit price.
                self._post_resting(order)
                return fills

            self._post_resting(order)
            return []

        return []

    def apply_depth_changes(self, symbol: str, changes: list[LevelChange], ts_local: float) -> list[Fill]:
        fills: list[Fill] = []
        for change in changes:
            side = "bid" if change.side == "bids" else "ask"
            if change.previous_lots > change.new_lots:
                dec = change.previous_lots - change.new_lots
                fills.extend(
                    self._consume_level(
                        symbol=symbol,
                        side=side,
                        price_tick=change.price_tick,
                        lots=dec,
                        ts_local=ts_local,
                        maker_fill=True,
                    )
                )
            elif change.new_lots > change.previous_lots:
                self._add_venue_order(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    lots=change.new_lots - change.previous_lots,
                )

        return fills

    def apply_agg_trade(self, trade, ts_local: float) -> list[Fill]:
        side = "bid" if trade.buyer_is_maker else "ask"
        return self._consume_level(
            symbol=trade.symbol,
            side=side,
            price_tick=trade.price_tick,
            lots=trade.qty_lots,
            ts_local=ts_local,
            maker_fill=True,
        )
