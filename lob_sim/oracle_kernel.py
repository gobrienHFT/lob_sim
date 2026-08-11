"""Independent Python specifications for deterministic kernel primitives.

This module deliberately has no Rust import.  Its small, explicit state
machines are used as the readable oracle for cross-language differential
tests; they are not wrappers around the PyO3 extension.
"""

from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass
from typing import Literal

from .record.envelope import LogicalTime


@dataclass(frozen=True)
class OracleDecision:
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class _ScheduledAction:
    action_id: int
    due: LogicalTime
    insertion_sequence: int


class DeterministicSchedulerOracle:
    """Integer-nanosecond action scheduler with explicit tie semantics."""

    def __init__(self) -> None:
        self._next_insertion_sequence = 0
        self._pending: dict[int, _ScheduledAction] = {}
        self._seen_action_ids: set[int] = set()
        self._heap: list[tuple[int, int, int, int]] = []

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def schedule(self, action_id: int, due: LogicalTime) -> OracleDecision:
        if action_id < 0:
            return OracleDecision(False, "invalid_action_id")
        if action_id in self._seen_action_ids:
            return OracleDecision(False, "duplicate_action_id")
        self._seen_action_ids.add(action_id)
        insertion_sequence = self._next_insertion_sequence
        self._next_insertion_sequence += 1
        action = _ScheduledAction(action_id, due, insertion_sequence)
        self._pending[action_id] = action
        heapq.heappush(
            self._heap,
            (due.recv_monotonic_ns, due.recv_seq, insertion_sequence, action_id),
        )
        return OracleDecision(True)

    def cancel(self, action_id: int) -> OracleDecision:
        if action_id not in self._pending:
            return OracleDecision(False, "unknown_action")
        del self._pending[action_id]
        return OracleDecision(True)

    def drain(self, cutoff: LogicalTime, *, inclusive: bool) -> tuple[int, ...]:
        drained: list[int] = []
        cutoff_key = (cutoff.recv_monotonic_ns, cutoff.recv_seq)
        while self._heap:
            monotonic_ns, recv_seq, _insertion_sequence, action_id = self._heap[0]
            due_key = (monotonic_ns, recv_seq)
            if due_key > cutoff_key or (due_key == cutoff_key and not inclusive):
                break
            heapq.heappop(self._heap)
            if action_id not in self._pending:
                continue
            del self._pending[action_id]
            drained.append(action_id)
        return tuple(drained)

    def canonical_bytes(self) -> bytes:
        pieces = [
            f"next:{self._next_insertion_sequence};",
            "seen:" + ",".join(str(action_id) for action_id in sorted(self._seen_action_ids)) + ";",
        ]
        for action in sorted(
            self._pending.values(),
            key=lambda item: (
                item.due.recv_monotonic_ns,
                item.due.recv_seq,
                item.insertion_sequence,
                item.action_id,
            ),
        ):
            pieces.append(
                f"action:{action.action_id}:{action.due.recv_monotonic_ns}:"
                f"{action.due.recv_seq}:{action.insertion_sequence};"
            )
        return "".join(pieces).encode("utf-8")

    def state_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


ReservationState = Literal["live", "pending_cancel", "filled", "cancelled", "epoch_invalidated"]


@dataclass
class _ReservedOrder:
    order_id: int
    is_bid: bool
    remaining_lots: int
    state: ReservationState = "live"


class RiskReservationOracle:
    """Worst-case per-symbol live-plus-pending lot reservation ledger."""

    def __init__(self, max_position_lots: int) -> None:
        if max_position_lots <= 0:
            raise ValueError("max_position_lots must be positive")
        self.max_position_lots = max_position_lots
        self.position_lots = 0
        self._orders: dict[int, _ReservedOrder] = {}
        self._seen_order_ids: set[int] = set()

    def _reservation_totals(self) -> tuple[int, int]:
        buy_lots = sum(
            order.remaining_lots
            for order in self._orders.values()
            if order.is_bid and order.state in {"live", "pending_cancel"}
        )
        sell_lots = sum(
            order.remaining_lots
            for order in self._orders.values()
            if not order.is_bid and order.state in {"live", "pending_cancel"}
        )
        return buy_lots, sell_lots

    @property
    def reserved_buy_lots(self) -> int:
        return self._reservation_totals()[0]

    @property
    def reserved_sell_lots(self) -> int:
        return self._reservation_totals()[1]

    def _within_limits(self, buy_lots: int, sell_lots: int) -> bool:
        return (
            self.position_lots + buy_lots <= self.max_position_lots
            and self.position_lots - sell_lots >= -self.max_position_lots
        )

    def _assert_invariants(self) -> None:
        buy_lots, sell_lots = self._reservation_totals()
        if not self._within_limits(buy_lots, sell_lots):
            raise AssertionError("reservation ledger exceeded its worst-case position limit")
        if any(order.remaining_lots < 0 for order in self._orders.values()):
            raise AssertionError("reservation ledger contains a negative remaining quantity")

    def reserve(self, order_id: int, *, is_bid: bool, qty_lots: int) -> OracleDecision:
        if order_id < 0:
            return OracleDecision(False, "invalid_order_id")
        if order_id in self._seen_order_ids:
            return OracleDecision(False, "duplicate_order_id")
        self._seen_order_ids.add(order_id)
        if qty_lots <= 0:
            return OracleDecision(False, "invalid_quantity")
        buy_lots, sell_lots = self._reservation_totals()
        if is_bid:
            buy_lots += qty_lots
            limit_reason = "long_limit"
        else:
            sell_lots += qty_lots
            limit_reason = "short_limit"
        if not self._within_limits(buy_lots, sell_lots):
            return OracleDecision(False, limit_reason)
        self._orders[order_id] = _ReservedOrder(order_id, is_bid, qty_lots)
        self._assert_invariants()
        return OracleDecision(True)

    def request_cancel(self, order_id: int) -> OracleDecision:
        order = self._orders.get(order_id)
        if order is None:
            return OracleDecision(False, "unknown_order")
        if order.state != "live":
            return OracleDecision(False, "order_not_live")
        order.state = "pending_cancel"
        self._assert_invariants()
        return OracleDecision(True)

    def cancel_ack(self, order_id: int) -> OracleDecision:
        order = self._orders.get(order_id)
        if order is None:
            return OracleDecision(False, "unknown_order")
        if order.state != "pending_cancel":
            return OracleDecision(False, "cancel_not_pending")
        order.state = "cancelled"
        self._assert_invariants()
        return OracleDecision(True)

    def fill(self, order_id: int, qty_lots: int) -> OracleDecision:
        order = self._orders.get(order_id)
        if order is None:
            return OracleDecision(False, "unknown_order")
        if order.state not in {"live", "pending_cancel"}:
            return OracleDecision(False, "order_not_fillable")
        if qty_lots <= 0:
            return OracleDecision(False, "invalid_fill_quantity")
        if qty_lots > order.remaining_lots:
            return OracleDecision(False, "fill_exceeds_remaining")
        order.remaining_lots -= qty_lots
        self.position_lots += qty_lots if order.is_bid else -qty_lots
        if order.remaining_lots == 0:
            order.state = "filled"
        self._assert_invariants()
        return OracleDecision(True)

    def invalidate_epoch(self) -> OracleDecision:
        for order in self._orders.values():
            if order.state in {"live", "pending_cancel"}:
                order.state = "epoch_invalidated"
        self._assert_invariants()
        return OracleDecision(True)

    def canonical_bytes(self) -> bytes:
        pieces = [
            f"max:{self.max_position_lots};",
            f"position:{self.position_lots};",
            "seen:" + ",".join(str(order_id) for order_id in sorted(self._seen_order_ids)) + ";",
        ]
        for order_id in sorted(self._orders):
            order = self._orders[order_id]
            side = "bid" if order.is_bid else "ask"
            pieces.append(f"order:{order_id}:{side}:{order.remaining_lots}:{order.state};")
        return "".join(pieces).encode("utf-8")

    def state_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
