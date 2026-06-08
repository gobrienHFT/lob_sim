from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Literal, cast

from ..book.types import AggTradeEvent, LevelChange
from ..config import (
    DEFAULT_FILL_OVERLAP_WINDOW_SECONDS,
    FillAssumptionConfig,
    fill_assumption_config_for_profile,
)
from .orders import Fill, FillSource, Order, OrderSide

# One Binance 100 ms diff bucket plus a small local timestamp tolerance.
TRADE_DEPTH_OVERLAP_WINDOW_SECONDS = DEFAULT_FILL_OVERLAP_WINDOW_SECONDS
PUBLIC_CONSUMPTION_SOURCES: tuple[FillSource, ...] = ("depth_update", "agg_trade")


@dataclass
class _ConsumptionCredit:
    ts_local: float
    lots: int
    source: FillSource


@dataclass
class _TakerExecution:
    fills: list[Fill]
    self_trade_prevented: bool = False


@dataclass(frozen=True)
class PublicConsumptionEvent:
    ts_local: float
    symbol: str
    side: OrderSide
    price_tick: int
    source: FillSource
    observed_lots: int
    modeled_lots: int
    overlap_netted_lots: int
    queue_consumed_lots: int
    unmatched_lots: int
    fill_assumption_profile: str


class PassiveFillModel:
    def __init__(self, fill_assumption: FillAssumptionConfig | None = None) -> None:
        self.fill_assumption = fill_assumption or fill_assumption_config_for_profile("base")
        self._books: dict[str, dict[str, dict[int, Deque[Order]]]] = {}
        self._orders: dict[tuple[str, OrderSide, str], Order] = {}
        self._order_index: dict[str, tuple[str, OrderSide, str]] = {}
        self._synthetic_queue_ahead: dict[str, int] = {}
        self._public_consumption_credits: dict[tuple[str, OrderSide, int], Deque[_ConsumptionCredit]] = {}
        self._public_consumption_stats: dict[FillSource, dict[str, int]] = {
            source: {
                "observed_lots": 0,
                "modeled_lots": 0,
                "overlap_netted_lots": 0,
                "queue_consumed_lots": 0,
                "unmatched_lots": 0,
            }
            for source in PUBLIC_CONSUMPTION_SOURCES
        }
        self._fill_assumption_stats = {
            "corroborated_depth_reduction_lots": 0,
            "uncorroborated_depth_reduction_lots": 0,
        }
        self.last_self_trade_prevented = False
        self._public_consumption_events: list[PublicConsumptionEvent] = []
        self._seq = 0

    def _book(self, symbol: str) -> dict[str, dict[int, Deque[Order]]]:
        return self._books.setdefault(symbol, {"bids": {}, "asks": {}})

    def _bucket(self, side: str) -> Literal["bids", "asks"]:
        return "bids" if side == "bid" else "asks"

    def _reverse_side(self, side: str) -> OrderSide:
        return "ask" if side == "bid" else "bid"

    def _credit_key(self, symbol: str, side: str, price_tick: int) -> tuple[str, OrderSide, int]:
        return (symbol, self._ensure_order_type(side), price_tick)

    def _ensure_order_type(self, side: str) -> OrderSide:
        if side not in {"bid", "ask"}:
            raise ValueError(f"Invalid side: {side}")
        return cast(OrderSide, side)

    def get_order(self, symbol: str, side: str, quote_slot: str = "base") -> Order | None:
        return self._orders.get((symbol, self._ensure_order_type(side), quote_slot))

    def get_orders(self, symbol: str, side: str) -> list[Order]:
        normalized_side = self._ensure_order_type(side)
        orders = [
            order
            for (order_symbol, order_side, _slot), order in self._orders.items()
            if order_symbol == symbol and order_side == normalized_side
        ]
        reverse = normalized_side == "bid"
        return sorted(
            orders,
            key=lambda order: (
                order.price_tick if order.price_tick is not None else 0,
                order.created_ts,
                order.order_id,
            ),
            reverse=reverse,
        )

    def queue_ahead_lots(self, symbol: str, order: Order | None) -> int:
        if order is None:
            return 0
        if order.price_tick is None:
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
        synthetic_ahead = self._synthetic_queue_ahead.get(order.order_id)
        if synthetic_ahead is not None:
            return max(0, synthetic_ahead)
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
        if order.price_tick is None:
            order.active = False
            return
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
        self._synthetic_queue_ahead.pop(order.order_id, None)
        order.active = False

    def _clean_side_level_if_empty(self, symbol: str, side: str, price_tick: int) -> None:
        queue = self._book(symbol)[self._bucket(side)].get(price_tick)
        if queue is None:
            return
        while queue and (not queue[0].active or queue[0].remaining_lots <= 0):
            queue.popleft()
        if not queue:
            self._book(symbol)[self._bucket(side)].pop(price_tick, None)

    def _opposite_public_source(self, source: FillSource) -> FillSource | None:
        if source == "depth_update":
            return "agg_trade"
        if source == "agg_trade":
            return "depth_update"
        return None

    def _net_recent_public_consumption(
        self,
        symbol: str,
        side: str,
        price_tick: int,
        lots: int,
        ts_local: float,
        source: FillSource,
    ) -> int:
        opposite_source = self._opposite_public_source(source)
        if (
            opposite_source is None
            or lots <= 0
            or not self.fill_assumption.overlap_netting_enabled
            or self.fill_assumption.overlap_window_seconds <= 0
        ):
            return lots

        key = self._credit_key(symbol, side, price_tick)
        credits = self._public_consumption_credits.get(key)
        if not credits:
            return lots

        overlap_window_seconds = self.fill_assumption.overlap_window_seconds
        remaining = lots
        kept: Deque[_ConsumptionCredit] = deque()
        for credit in credits:
            age = ts_local - credit.ts_local
            if age > overlap_window_seconds:
                continue
            if age < 0:
                kept.append(credit)
                continue
            if credit.source == opposite_source and remaining > 0:
                used = min(remaining, credit.lots)
                remaining -= used
                credit.lots -= used
            if credit.lots > 0:
                kept.append(credit)

        if kept:
            self._public_consumption_credits[key] = kept
        else:
            self._public_consumption_credits.pop(key, None)
        return remaining

    def _record_public_consumption_credit(
        self,
        symbol: str,
        side: str,
        price_tick: int,
        lots: int,
        ts_local: float,
        source: FillSource,
    ) -> None:
        if (
            self._opposite_public_source(source) is None
            or lots <= 0
            or not self.fill_assumption.overlap_netting_enabled
            or self.fill_assumption.overlap_window_seconds <= 0
        ):
            return
        key = self._credit_key(symbol, side, price_tick)
        credits = self._public_consumption_credits.setdefault(key, deque())
        overlap_window_seconds = self.fill_assumption.overlap_window_seconds
        while credits and ts_local - credits[0].ts_local > overlap_window_seconds:
            credits.popleft()
        credits.append(_ConsumptionCredit(ts_local=ts_local, lots=lots, source=source))

    def _record_public_consumption_stats(
        self,
        source: FillSource,
        observed_lots: int,
        modeled_lots: int,
        queue_consumed_lots: int,
    ) -> None:
        stats = self._public_consumption_stats.get(source)
        if stats is None:
            return
        observed = max(0, observed_lots)
        modeled = max(0, modeled_lots)
        queue_consumed = min(modeled, max(0, queue_consumed_lots))
        stats["observed_lots"] += observed
        stats["modeled_lots"] += modeled
        stats["overlap_netted_lots"] += max(0, observed - modeled)
        stats["queue_consumed_lots"] += queue_consumed
        stats["unmatched_lots"] += max(0, modeled - queue_consumed)

    def _record_public_consumption_event(
        self,
        symbol: str,
        side: str,
        price_tick: int,
        ts_local: float,
        source: FillSource,
        observed_lots: int,
        modeled_lots: int,
        queue_consumed_lots: int,
    ) -> None:
        observed = max(0, observed_lots)
        modeled = max(0, modeled_lots)
        queue_consumed = min(modeled, max(0, queue_consumed_lots))
        self._public_consumption_events.append(
            PublicConsumptionEvent(
                ts_local=ts_local,
                symbol=symbol,
                side=self._ensure_order_type(side),
                price_tick=price_tick,
                source=source,
                observed_lots=observed,
                modeled_lots=modeled,
                overlap_netted_lots=max(0, observed - modeled),
                queue_consumed_lots=queue_consumed,
                unmatched_lots=max(0, modeled - queue_consumed),
                fill_assumption_profile=self.fill_assumption.profile,
            )
        )

    def drain_public_consumption_events(self) -> list[PublicConsumptionEvent]:
        events = self._public_consumption_events
        self._public_consumption_events = []
        return events

    def public_consumption_summary(self) -> dict[str, object]:
        sources = {source: dict(self._public_consumption_stats[source]) for source in PUBLIC_CONSUMPTION_SOURCES}
        return {
            "overlap_window_seconds": self.fill_assumption.overlap_window_seconds,
            "sources": sources,
            "total_observed_lots": sum(source["observed_lots"] for source in sources.values()),
            "total_modeled_lots": sum(source["modeled_lots"] for source in sources.values()),
            "total_overlap_netted_lots": sum(source["overlap_netted_lots"] for source in sources.values()),
            "total_queue_consumed_lots": sum(source["queue_consumed_lots"] for source in sources.values()),
            "total_unmatched_lots": sum(source["unmatched_lots"] for source in sources.values()),
        }

    def fill_assumption_diagnostics(self) -> dict[str, object]:
        return {
            **self.fill_assumption.as_dict(),
            **self._fill_assumption_stats,
        }

    def queue_position(self, order: Order) -> int:
        return self.queue_ahead_lots(order.symbol, order)

    def cancel_order(self, order_id: str) -> None:
        key = self._order_index.pop(order_id, None)
        self._synthetic_queue_ahead.pop(order_id, None)
        if key is None:
            return
        self._orders.pop(key, None)
        symbol, _side, _slot = key
        # best effort removal from book (if active map already diverged, still cleanup queue)
        book_side = self._book(symbol)[self._bucket(_side)]
        for queue in book_side.values():
            for q in list(queue):
                if q.order_id == order_id:
                    self._remove_order_from_book(q)
                    return

    def cancel_all_for_symbol_side(self, symbol: str, side: str) -> None:
        normalized_side = self._ensure_order_type(side)
        keys = [key for key in self._orders if key[0] == symbol and key[1] == normalized_side]
        for key in keys:
            order = self._orders.pop(key, None)
            if order is None:
                continue
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
        source: FillSource,
    ) -> tuple[list[Fill], int]:
        fills: list[Fill] = []
        remaining = max(0, lots)
        while remaining > 0 and queue:
            head = queue[0]
            if not head.active or head.remaining_lots <= 0:
                queue.popleft()
                continue
            if head.price_tick is None:
                queue.popleft()
                head.active = False
                continue

            synthetic_ahead = self._synthetic_queue_ahead.get(head.order_id, 0)
            if head.is_strategy and synthetic_ahead > 0:
                consumed_ahead = min(remaining, synthetic_ahead)
                synthetic_ahead -= consumed_ahead
                remaining -= consumed_ahead
                if synthetic_ahead > 0:
                    self._synthetic_queue_ahead[head.order_id] = synthetic_ahead
                else:
                    self._synthetic_queue_ahead.pop(head.order_id, None)
                head.queue_ahead_lots = synthetic_ahead
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
                        source=source,
                    )
                )

                if head.remaining_lots <= 0:
                    self._orders.pop((symbol, head.side, head.quote_slot), None)
                    self._order_index.pop(head.order_id, None)
                    self._synthetic_queue_ahead.pop(head.order_id, None)

            if head.remaining_lots <= 0:
                queue.popleft()
                head.active = False
            elif not head.is_strategy:
                # venue liquidity partially consumed and remains in book
                break

        if not queue:
            if "head" in locals() and head.price_tick is not None:
                self._book(symbol)[self._bucket(side)].pop(head.price_tick, None)
        return fills, remaining

    def _consume_level(
        self,
        symbol: str,
        side: str,
        price_tick: int,
        lots: int,
        ts_local: float,
        maker_fill: bool,
        source: FillSource,
    ) -> tuple[list[Fill], int]:
        bucket = self._bucket(side)
        queue = self._book(symbol)[bucket].get(price_tick)
        if queue is None:
            return [], 0

        fills, remaining = self._consume_front(symbol, side, queue, lots, ts_local, maker_fill, source)
        queue_consumed_lots = max(0, lots) - remaining
        if not queue:
            self._book(symbol)[bucket].pop(price_tick, None)
        return fills, queue_consumed_lots

    def _price_breaches_limit(self, taker_side: str, level_tick: int, price_cap: int | None) -> bool:
        if price_cap is None:
            return False
        if taker_side == "bid":
            return level_tick > price_cap
        return level_tick < price_cap

    def _execute_taker_order(
        self,
        order: Order,
        ts_local: float,
    ) -> _TakerExecution:
        if order.remaining_lots <= 0:
            return _TakerExecution(fills=[])

        opposite_bucket = self._bucket(self._reverse_side(order.side))
        levels = self._book(order.symbol)[opposite_bucket]
        level_ticks = sorted(levels.keys(), reverse=(order.side == "ask"))

        fills: list[Fill] = []
        remaining = order.remaining_lots
        for tick in level_ticks:
            if remaining <= 0:
                break
            if self._price_breaches_limit(order.side, tick, order.price_tick):
                break

            queue = levels.get(tick)
            while remaining > 0 and queue:
                head = queue[0]
                if not head.active or head.remaining_lots <= 0:
                    queue.popleft()
                    continue

                if order.is_strategy and head.is_strategy:
                    order.remaining_lots = remaining
                    order.active = False
                    self.last_self_trade_prevented = True
                    return _TakerExecution(fills=fills, self_trade_prevented=True)

                take = min(remaining, head.remaining_lots)
                head.remaining_lots -= take
                remaining -= take
                fills.append(
                    Fill(
                        ts_local=ts_local,
                        symbol=order.symbol,
                        side=order.side,
                        price_tick=tick,
                        qty_lots=take,
                        maker=False,
                        order_id=order.order_id,
                        queue_ahead_lots=0,
                        created_ts=order.created_ts,
                        source="taker_order",
                    )
                )

                if head.remaining_lots <= 0:
                    if head.is_strategy:
                        self._orders.pop((head.symbol, head.side, head.quote_slot), None)
                        self._order_index.pop(head.order_id, None)
                        self._synthetic_queue_ahead.pop(head.order_id, None)
                    head.active = False
                    queue.popleft()
                elif not head.is_strategy:
                    break

            if queue is None or not queue:
                levels.pop(tick, None)

        order.remaining_lots = remaining
        return _TakerExecution(fills=fills)

    def _marketable_fill(
        self,
        order: Order,
        ts_local: float,
    ) -> _TakerExecution:
        return self._execute_taker_order(order, ts_local=ts_local)

    def _can_market(self, order: Order) -> bool:
        if order.price_tick is None:
            return True
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
        synthetic_ahead = 0 if visible_queue_ahead > 0 else max(0, order.queue_ahead_lots)
        if synthetic_ahead > 0:
            self._synthetic_queue_ahead[order.order_id] = synthetic_ahead
        else:
            self._synthetic_queue_ahead.pop(order.order_id, None)
        order.queue_ahead_lots = synthetic_ahead
        order.active = True
        queue.append(order)
        self._orders[(order.symbol, order.side, order.quote_slot)] = order
        self._order_index[order.order_id] = (order.symbol, order.side, order.quote_slot)

    def seed_from_snapshot(self, symbol: str, bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> None:
        # Strategy orders are external to the venue stream. Remove active strategy quotes and rebuild.
        self.cancel_all_for_symbol_side(symbol, "bid")
        self.cancel_all_for_symbol_side(symbol, "ask")
        self._books[symbol] = {"bids": {}, "asks": {}}
        self._public_consumption_credits = {
            key: credits for key, credits in self._public_consumption_credits.items() if key[0] != symbol
        }

        for price, qty in bids:
            self._add_venue_order(symbol=symbol, side="bid", price_tick=price, lots=qty)

        for price, qty in asks:
            self._add_venue_order(symbol=symbol, side="ask", price_tick=price, lots=qty)

    def place_order(self, order: Order) -> list[Fill]:
        self.last_self_trade_prevented = False
        if order.qty_lots <= 0:
            return []
        order.remaining_lots = max(order.remaining_lots or order.qty_lots, 0)
        if order.order_type == "cancel":
            return []
        if order.order_type == "market":
            order.price_tick = None
            return self._execute_taker_order(order, ts_local=order.created_ts).fills

        existing = self.get_order(order.symbol, order.side, order.quote_slot)
        if existing is not None and existing.order_id != order.order_id:
            self.cancel_order(existing.order_id)
        if order.order_type == "limit":
            if self._can_market(order):
                execution = self._marketable_fill(order, ts_local=order.created_ts)
                fills = execution.fills
                if order.remaining_lots <= 0 or execution.self_trade_prevented:
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
                lots_to_consume = self._net_recent_public_consumption(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    lots=dec,
                    ts_local=ts_local,
                    source="depth_update",
                )
                if not self.fill_assumption.depth_reductions_consume_queue:
                    self._fill_assumption_stats["corroborated_depth_reduction_lots"] += max(
                        0,
                        dec - lots_to_consume,
                    )
                    self._fill_assumption_stats["uncorroborated_depth_reduction_lots"] += max(
                        0,
                        lots_to_consume,
                    )
                    self._record_public_consumption_stats("depth_update", dec, lots_to_consume, 0)
                    self._record_public_consumption_event(
                        symbol=symbol,
                        side=side,
                        price_tick=change.price_tick,
                        ts_local=ts_local,
                        source="depth_update",
                        observed_lots=dec,
                        modeled_lots=lots_to_consume,
                        queue_consumed_lots=0,
                    )
                    continue
                self._record_public_consumption_credit(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    lots=lots_to_consume,
                    ts_local=ts_local,
                    source="depth_update",
                )
                if lots_to_consume <= 0:
                    self._record_public_consumption_stats("depth_update", dec, lots_to_consume, 0)
                    self._record_public_consumption_event(
                        symbol=symbol,
                        side=side,
                        price_tick=change.price_tick,
                        ts_local=ts_local,
                        source="depth_update",
                        observed_lots=dec,
                        modeled_lots=lots_to_consume,
                        queue_consumed_lots=0,
                    )
                    continue
                level_fills, queue_consumed_lots = self._consume_level(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    lots=lots_to_consume,
                    ts_local=ts_local,
                    maker_fill=True,
                    source="depth_update",
                )
                self._record_public_consumption_stats(
                    "depth_update",
                    dec,
                    lots_to_consume,
                    queue_consumed_lots,
                )
                self._record_public_consumption_event(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    ts_local=ts_local,
                    source="depth_update",
                    observed_lots=dec,
                    modeled_lots=lots_to_consume,
                    queue_consumed_lots=queue_consumed_lots,
                )
                fills.extend(level_fills)
            elif change.new_lots > change.previous_lots:
                self._add_venue_order(
                    symbol=symbol,
                    side=side,
                    price_tick=change.price_tick,
                    lots=change.new_lots - change.previous_lots,
                )

        return fills

    def apply_agg_trade(self, trade: AggTradeEvent, ts_local: float) -> list[Fill]:
        side = "bid" if trade.buyer_is_maker else "ask"
        if not self.fill_assumption.agg_trades_consume_queue:
            self._record_public_consumption_stats("agg_trade", trade.qty_lots, 0, 0)
            self._record_public_consumption_event(
                symbol=trade.symbol,
                side=side,
                price_tick=trade.price_tick,
                ts_local=ts_local,
                source="agg_trade",
                observed_lots=trade.qty_lots,
                modeled_lots=0,
                queue_consumed_lots=0,
            )
            return []
        lots_to_consume = self._net_recent_public_consumption(
            symbol=trade.symbol,
            side=side,
            price_tick=trade.price_tick,
            lots=trade.qty_lots,
            ts_local=ts_local,
            source="agg_trade",
        )
        self._record_public_consumption_credit(
            symbol=trade.symbol,
            side=side,
            price_tick=trade.price_tick,
            lots=lots_to_consume,
            ts_local=ts_local,
            source="agg_trade",
        )
        if lots_to_consume <= 0:
            self._record_public_consumption_stats("agg_trade", trade.qty_lots, lots_to_consume, 0)
            self._record_public_consumption_event(
                symbol=trade.symbol,
                side=side,
                price_tick=trade.price_tick,
                ts_local=ts_local,
                source="agg_trade",
                observed_lots=trade.qty_lots,
                modeled_lots=lots_to_consume,
                queue_consumed_lots=0,
            )
            return []
        fills, queue_consumed_lots = self._consume_level(
            symbol=trade.symbol,
            side=side,
            price_tick=trade.price_tick,
            lots=lots_to_consume,
            ts_local=ts_local,
            maker_fill=True,
            source="agg_trade",
        )
        self._record_public_consumption_stats(
            "agg_trade",
            trade.qty_lots,
            lots_to_consume,
            queue_consumed_lots,
        )
        self._record_public_consumption_event(
            symbol=trade.symbol,
            side=side,
            price_tick=trade.price_tick,
            ts_local=ts_local,
            source="agg_trade",
            observed_lots=trade.qty_lots,
            modeled_lots=lots_to_consume,
            queue_consumed_lots=queue_consumed_lots,
        )
        return fills
