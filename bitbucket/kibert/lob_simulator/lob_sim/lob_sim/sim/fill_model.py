from __future__ import annotations

from enum import StrEnum

from ..book.types import AggTradeEvent, LevelChange
from .orders import Fill, Order, OrderState


class FillSource(StrEnum):
    """Mutually exclusive observable used to advance the synthetic queue."""

    TRADE = "trade"
    DEPTH = "depth"


class DuplicateActiveOrderError(RuntimeError):
    """Raised when the one-live-order-per-side model would be violated."""


class PassiveFillModel:
    """Market-by-price passive fill approximation.

    Binance L2 does not expose FIFO order identities or cancellation position.  To
    avoid consuming the same execution twice, a model instance uses exactly one
    source: aggregate trades (conservative default) *or* displayed depth
    reductions (an explicitly optimistic sensitivity case).
    """

    def __init__(self, fill_source: str | FillSource = FillSource.TRADE) -> None:
        self.fill_source = FillSource(fill_source)
        self._orders_by_id: dict[str, Order] = {}
        self._active_by_key: dict[tuple[str, str], str] = {}
        self._terminal_counts = {
            OrderState.FILLED.value: 0,
            OrderState.CANCELLED.value: 0,
        }
        self._accepted_count = 0

    def get_order(self, symbol: str, side: str) -> Order | None:
        order_id = self._active_by_key.get((symbol, side))
        if order_id is None:
            return None
        order = self._orders_by_id.get(order_id)
        return order if order is not None and order.active else None

    @property
    def active_order_count(self) -> int:
        return sum(order.active for order in self._orders_by_id.values())

    @property
    def accepted_order_count(self) -> int:
        return self._accepted_count

    def order_state_counts(self) -> dict[str, int]:
        counts = {state.value: 0 for state in OrderState}
        counts.update(self._terminal_counts)
        for order in self._orders_by_id.values():
            counts[order.state.value] += 1
        return counts

    def place_order(self, order: Order) -> None:
        key = (order.symbol, order.side)
        existing = self.get_order(*key)
        if existing is not None:
            raise DuplicateActiveOrderError(
                f"Active order already exists for {order.symbol} {order.side}: {existing.order_id}"
            )
        if order.order_id in self._orders_by_id:
            raise DuplicateActiveOrderError(f"Duplicate order id: {order.order_id}")
        self._orders_by_id[order.order_id] = order
        self._active_by_key[key] = order.order_id
        self._accepted_count += 1

    def cancel_order(self, order_id: str, ts: float = 0.0) -> bool:
        order = self._orders_by_id.get(order_id)
        if order is None or not order.active:
            return False
        order.cancel(ts)
        key = (order.symbol, order.side)
        if self._active_by_key.get(key) == order_id:
            self._active_by_key.pop(key, None)
        self._terminal_counts[OrderState.CANCELLED.value] += 1
        self._orders_by_id.pop(order_id, None)
        return True

    def cancel_all_for_symbol_side(self, symbol: str, side: str, ts: float = 0.0) -> int:
        key = (symbol, side)
        order_id = self._active_by_key.get(key)
        if order_id is None:
            return 0
        return int(self.cancel_order(order_id, ts))

    def cancel_all_for_symbol(self, symbol: str, ts: float = 0.0) -> int:
        return sum(self.cancel_all_for_symbol_side(symbol, side, ts) for side in ("bid", "ask"))

    def _retire_if_closed(self, order: Order) -> None:
        if order.active:
            return
        key = (order.symbol, order.side)
        if self._active_by_key.get(key) == order.order_id:
            self._active_by_key.pop(key, None)
        self._terminal_counts[order.state.value] += 1
        self._orders_by_id.pop(order.order_id, None)

    def _apply_level_fill(
        self,
        order: Order,
        reduction_lots: int,
        ts_local: float,
        cause: str,
        trade_through: bool = False,
    ) -> Fill | None:
        if not order.active or reduction_lots <= 0 or order.remaining_lots <= 0:
            return None
        queue_before = order.queue_ahead_lots
        if trade_through:
            order.queue_ahead_lots = 0
            reduction_lots = order.remaining_lots
        elif order.queue_ahead_lots > 0:
            drained = min(order.queue_ahead_lots, reduction_lots)
            order.queue_ahead_lots -= drained
            reduction_lots -= drained
        if reduction_lots <= 0:
            return None
        fill_lots = min(order.remaining_lots, reduction_lots)
        order.apply_fill(fill_lots, ts_local)
        fill = Fill(
            ts_local=ts_local,
            symbol=order.symbol,
            side=order.side,
            price_tick=order.price_tick,
            qty_lots=fill_lots,
            maker=True,
            order_id=order.order_id,
            cause=cause,
            queue_ahead_before_lots=queue_before,
        )
        self._retire_if_closed(order)
        return fill

    def _book_side(self, side: str) -> str:
        return "bids" if side == "bid" else "asks"

    def apply_depth_changes(self, symbol: str, changes: list[LevelChange], ts_local: float) -> list[Fill]:
        if self.fill_source is not FillSource.DEPTH:
            return []
        fills: list[Fill] = []
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
                fill = self._apply_level_fill(order, dec, ts_local, cause="depth_decrease")
                if fill:
                    fills.append(fill)
        return fills

    def apply_agg_trade(self, trade: AggTradeEvent, ts_local: float) -> list[Fill]:
        if self.fill_source is not FillSource.TRADE:
            return []
        side = "bid" if trade.buyer_is_maker else "ask"
        order = self.get_order(trade.symbol, side)
        if order is None or not order.active:
            return []
        traded_through = (side == "bid" and trade.price_tick < order.price_tick) or (
            side == "ask" and trade.price_tick > order.price_tick
        )
        if order.price_tick != trade.price_tick and not traded_through:
            return []
        fill = self._apply_level_fill(
            order,
            trade.qty_lots,
            ts_local,
            cause="trade_through" if traded_through else "agg_trade",
            trade_through=traded_through,
        )
        return [fill] if fill else []
