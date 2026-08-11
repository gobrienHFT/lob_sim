"""Deterministic market-by-order exchange used only for synthetic experiments.

This module provides exact price-time priority because every synthetic order is
known to the exchange.  It is intentionally separate from the public Binance
L2 venue model, where participant identity and historical FIFO position are not
observable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..oracle import state_hash
from ..record.envelope import LogicalTime
from .sinks import EventSink, NullSink

Side = Literal["bid", "ask"]
TimeInForce = Literal["GTC", "IOC"]
SelfTradePrevention = Literal["cancel_aggressor", "cancel_resting"]


@dataclass
class SyntheticOrder:
    order_id: str
    participant_id: str
    side: Side
    price_tick: int | None
    original_lots: int
    remaining_lots: int
    time_in_force: TimeInForce
    post_only: bool
    arrival_sequence: int
    state: str = "accepted"


@dataclass(frozen=True)
class SyntheticFill:
    fill_id: str
    time: LogicalTime
    price_tick: int
    qty_lots: int
    maker_order_id: str
    taker_order_id: str
    maker_participant_id: str
    taker_participant_id: str


@dataclass(frozen=True)
class SyntheticTransition:
    transition_id: str
    time: LogicalTime
    event: str
    order_id: str
    participant_id: str
    state: str
    reason: str | None = None
    price_tick: int | None = None
    qty_lots: int | None = None


@dataclass(frozen=True)
class ExchangeResult:
    order_id: str
    accepted: bool
    terminal_state: str
    fills: tuple[SyntheticFill, ...]
    transitions: tuple[SyntheticTransition, ...]


class SyntheticExchange:
    """Exact synthetic MBO matcher with deterministic price-time priority."""

    def __init__(
        self,
        *,
        self_trade_prevention: SelfTradePrevention = "cancel_aggressor",
        event_sink: EventSink | None = None,
        retain_transition_log: bool = True,
    ) -> None:
        if self_trade_prevention not in ("cancel_aggressor", "cancel_resting"):
            raise ValueError(f"unsupported self-trade prevention mode: {self_trade_prevention}")
        self.self_trade_prevention = self_trade_prevention
        self._bids: dict[int, deque[str]] = {}
        self._asks: dict[int, deque[str]] = {}
        self._orders: dict[str, SyntheticOrder] = {}
        self._event_sink = event_sink or NullSink()
        self._retain_transition_log = retain_transition_log
        self.transition_log: list[SyntheticTransition] = []
        self._action_sequence = 0
        self._transition_sequence = 0
        self._fill_sequence = 0
        self._last_time = LogicalTime(0, 0)

    @property
    def orders(self) -> dict[str, SyntheticOrder]:
        return dict(self._orders)

    def best_bid_tick(self) -> int | None:
        return max(self._bids, default=None)

    def best_ask_tick(self) -> int | None:
        return min(self._asks, default=None)

    def _time(self, supplied: LogicalTime | None) -> LogicalTime:
        if supplied is None:
            self._action_sequence += 1
            supplied = LogicalTime(self._last_time.recv_monotonic_ns, self._action_sequence)
        if supplied < self._last_time:
            raise ValueError("synthetic exchange logical time must be nondecreasing")
        self._last_time = supplied
        self._action_sequence = max(self._action_sequence, supplied.recv_seq)
        return supplied

    def _emit(
        self,
        *,
        time: LogicalTime,
        event: str,
        order: SyntheticOrder,
        state: str,
        reason: str | None = None,
        price_tick: int | None = None,
        qty_lots: int | None = None,
    ) -> SyntheticTransition:
        self._transition_sequence += 1
        transition = SyntheticTransition(
            transition_id=f"transition-{self._transition_sequence}",
            time=time,
            event=event,
            order_id=order.order_id,
            participant_id=order.participant_id,
            state=state,
            reason=reason,
            price_tick=price_tick,
            qty_lots=qty_lots,
        )
        payload = asdict(transition)
        payload["time"] = asdict(transition.time)
        self._event_sink.write(payload)
        if self._retain_transition_log:
            self.transition_log.append(transition)
        return transition

    @staticmethod
    def _validate_new(
        order_id: str,
        participant_id: str,
        side: str,
        qty_lots: int,
        price_tick: int | None,
        time_in_force: str,
        post_only: bool,
    ) -> str | None:
        if not order_id:
            return "empty_order_id"
        if not participant_id:
            return "empty_participant_id"
        if side not in ("bid", "ask"):
            return "invalid_side"
        if isinstance(qty_lots, bool) or qty_lots <= 0:
            return "invalid_quantity"
        if price_tick is not None and (isinstance(price_tick, bool) or price_tick <= 0):
            return "invalid_price"
        if time_in_force not in ("GTC", "IOC"):
            return "invalid_time_in_force"
        if price_tick is None and time_in_force != "IOC":
            return "market_order_requires_ioc"
        if price_tick is None and post_only:
            return "market_order_cannot_be_post_only"
        return None

    def _crosses(self, side: Side, price_tick: int | None) -> bool:
        if side == "bid":
            best = self.best_ask_tick()
            return best is not None and (price_tick is None or best <= price_tick)
        best = self.best_bid_tick()
        return best is not None and (price_tick is None or best >= price_tick)

    def _book_for(self, side: Side) -> dict[int, deque[str]]:
        return self._bids if side == "bid" else self._asks

    def _opposite_book(self, side: Side) -> dict[int, deque[str]]:
        return self._asks if side == "bid" else self._bids

    def _best_opposite_price(self, side: Side) -> int | None:
        book = self._opposite_book(side)
        if not book:
            return None
        return min(book) if side == "bid" else max(book)

    def _price_is_matchable(self, side: Side, limit_tick: int | None, resting_tick: int) -> bool:
        if limit_tick is None:
            return True
        return resting_tick <= limit_tick if side == "bid" else resting_tick >= limit_tick

    def _remove_from_book(self, order: SyntheticOrder) -> None:
        if order.price_tick is None:
            return
        book = self._book_for(order.side)
        queue = book.get(order.price_tick)
        if queue is None:
            return
        try:
            queue.remove(order.order_id)
        except ValueError:
            return
        if not queue:
            del book[order.price_tick]

    def submit_new(
        self,
        *,
        order_id: str,
        participant_id: str,
        side: Side,
        qty_lots: int,
        price_tick: int | None,
        time_in_force: TimeInForce = "GTC",
        post_only: bool = False,
        time: LogicalTime | None = None,
    ) -> ExchangeResult:
        logical_time = self._time(time)
        if order_id in self._orders:
            placeholder = self._orders[order_id]
            transition = self._emit(
                time=logical_time,
                event="rejected",
                order=placeholder,
                state="rejected",
                reason="duplicate_order_id",
            )
            return ExchangeResult(order_id, False, "rejected", (), (transition,))

        reason = self._validate_new(
            order_id,
            participant_id,
            side,
            qty_lots,
            price_tick,
            time_in_force,
            post_only,
        )
        order = SyntheticOrder(
            order_id=order_id,
            participant_id=participant_id,
            side=side,
            price_tick=price_tick,
            original_lots=qty_lots,
            remaining_lots=qty_lots,
            time_in_force=time_in_force,
            post_only=post_only,
            arrival_sequence=self._action_sequence,
            state="rejected" if reason else "accepted",
        )
        if reason is not None:
            transition = self._emit(
                time=logical_time,
                event="rejected",
                order=order,
                state="rejected",
                reason=reason,
            )
            return ExchangeResult(order_id, False, "rejected", (), (transition,))

        if post_only and self._crosses(side, price_tick):
            order.state = "rejected"
            transition = self._emit(
                time=logical_time,
                event="rejected",
                order=order,
                state="rejected",
                reason="post_only_would_cross",
            )
            return ExchangeResult(order_id, False, "rejected", (), (transition,))

        self._orders[order_id] = order
        transitions = [
            self._emit(time=logical_time, event="accepted", order=order, state="accepted", qty_lots=qty_lots)
        ]
        fills: list[SyntheticFill] = []

        while order.remaining_lots > 0:
            resting_tick = self._best_opposite_price(side)
            if resting_tick is None or not self._price_is_matchable(side, price_tick, resting_tick):
                break
            opposite = self._opposite_book(side)
            queue = opposite[resting_tick]
            maker = self._orders[queue[0]]

            if maker.participant_id == participant_id:
                if self.self_trade_prevention == "cancel_resting":
                    self._remove_from_book(maker)
                    maker.state = "cancelled"
                    transitions.append(
                        self._emit(
                            time=logical_time,
                            event="self_trade_prevention",
                            order=maker,
                            state="cancelled",
                            reason="cancel_resting",
                        )
                    )
                    continue
                order.state = "cancelled"
                transitions.append(
                    self._emit(
                        time=logical_time,
                        event="self_trade_prevention",
                        order=order,
                        state="cancelled",
                        reason="cancel_aggressor",
                    )
                )
                break

            fill_lots = min(order.remaining_lots, maker.remaining_lots)
            order.remaining_lots -= fill_lots
            maker.remaining_lots -= fill_lots
            self._fill_sequence += 1
            fill = SyntheticFill(
                fill_id=f"fill-{self._fill_sequence}",
                time=logical_time,
                price_tick=resting_tick,
                qty_lots=fill_lots,
                maker_order_id=maker.order_id,
                taker_order_id=order.order_id,
                maker_participant_id=maker.participant_id,
                taker_participant_id=order.participant_id,
            )
            fills.append(fill)
            transitions.append(
                self._emit(
                    time=logical_time,
                    event="fill",
                    order=maker,
                    state="partially_filled" if maker.remaining_lots else "filled",
                    price_tick=resting_tick,
                    qty_lots=fill_lots,
                )
            )
            transitions.append(
                self._emit(
                    time=logical_time,
                    event="fill",
                    order=order,
                    state="partially_filled" if order.remaining_lots else "filled",
                    price_tick=resting_tick,
                    qty_lots=fill_lots,
                )
            )
            if maker.remaining_lots == 0:
                queue.popleft()
                maker.state = "filled"
                if not queue:
                    del opposite[resting_tick]

        if order.remaining_lots == 0:
            order.state = "filled"
        elif order.state == "cancelled":
            pass
        elif price_tick is not None and time_in_force == "GTC":
            self._book_for(side).setdefault(price_tick, deque()).append(order_id)
            order.state = "live"
            transitions.append(
                self._emit(
                    time=logical_time,
                    event="rested",
                    order=order,
                    state="live",
                    price_tick=price_tick,
                    qty_lots=order.remaining_lots,
                )
            )
        else:
            order.state = "expired"
            transitions.append(
                self._emit(
                    time=logical_time,
                    event="expired",
                    order=order,
                    state="expired",
                    reason="ioc_or_market_remainder",
                    qty_lots=order.remaining_lots,
                )
            )

        return ExchangeResult(order_id, True, order.state, tuple(fills), tuple(transitions))

    def cancel(self, order_id: str, *, time: LogicalTime | None = None) -> ExchangeResult:
        logical_time = self._time(time)
        order = self._orders.get(order_id)
        if order is None:
            placeholder = SyntheticOrder(order_id, "", "bid", None, 0, 0, "IOC", False, 0, "rejected")
            transition = self._emit(
                time=logical_time,
                event="cancel_rejected",
                order=placeholder,
                state="rejected",
                reason="unknown_order",
            )
            return ExchangeResult(order_id, False, "rejected", (), (transition,))
        if order.state != "live":
            transition = self._emit(
                time=logical_time,
                event="cancel_rejected",
                order=order,
                state=order.state,
                reason="order_not_live",
            )
            return ExchangeResult(order_id, False, order.state, (), (transition,))
        self._remove_from_book(order)
        order.state = "cancelled"
        transition = self._emit(time=logical_time, event="cancelled", order=order, state="cancelled")
        return ExchangeResult(order_id, True, "cancelled", (), (transition,))

    def replace(
        self,
        order_id: str,
        *,
        new_order_id: str,
        price_tick: int,
        qty_lots: int,
        post_only: bool = False,
        time: LogicalTime | None = None,
    ) -> ExchangeResult:
        logical_time = self._time(time)
        existing = self._orders.get(order_id)
        if existing is None or existing.state != "live":
            return self.cancel(order_id, time=logical_time)
        invalid = self._validate_new(
            new_order_id,
            existing.participant_id,
            existing.side,
            qty_lots,
            price_tick,
            "GTC",
            post_only,
        )
        if invalid is not None or new_order_id in self._orders:
            reason = invalid or "duplicate_order_id"
            transition = self._emit(
                time=logical_time,
                event="replace_rejected",
                order=existing,
                state="live",
                reason=reason,
            )
            return ExchangeResult(order_id, False, "live", (), (transition,))
        if post_only and self._crosses(existing.side, price_tick):
            transition = self._emit(
                time=logical_time,
                event="replace_rejected",
                order=existing,
                state="live",
                reason="post_only_would_cross",
            )
            return ExchangeResult(order_id, False, "live", (), (transition,))

        cancel_result = self.cancel(order_id, time=logical_time)
        new_result = self.submit_new(
            order_id=new_order_id,
            participant_id=existing.participant_id,
            side=existing.side,
            qty_lots=qty_lots,
            price_tick=price_tick,
            time_in_force="GTC",
            post_only=post_only,
            time=logical_time,
        )
        return ExchangeResult(
            new_order_id,
            new_result.accepted,
            new_result.terminal_state,
            new_result.fills,
            (*cancel_result.transitions, *new_result.transitions),
        )

    def snapshot(self) -> dict[str, Any]:
        def levels(book: dict[int, deque[str]], *, reverse: bool) -> list[dict[str, Any]]:
            return [
                {
                    "price_tick": price,
                    "orders": [
                        {
                            "order_id": order_id,
                            "participant_id": self._orders[order_id].participant_id,
                            "remaining_lots": self._orders[order_id].remaining_lots,
                        }
                        for order_id in book[price]
                    ],
                }
                for price in sorted(book, reverse=reverse)
            ]

        return {
            "mode": "exact_synthetic_mbo_price_time_priority",
            "historical_binance_fifo": False,
            "self_trade_prevention": self.self_trade_prevention,
            "bids": levels(self._bids, reverse=True),
            "asks": levels(self._asks, reverse=False),
            "orders": {order_id: asdict(order) for order_id, order in sorted(self._orders.items())},
        }

    def state_sha256(self) -> str:
        return state_hash(self.snapshot())

    def close(self) -> None:
        self._event_sink.close()
