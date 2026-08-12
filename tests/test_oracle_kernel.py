from __future__ import annotations

from lob_sim.oracle_kernel import (
    ACCOUNTING_CASH_SCALE,
    AccountingMarkoutOracle,
    DeterministicSchedulerOracle,
    PortfolioNotionalReservationOracle,
    RiskReservationOracle,
)
from lob_sim.record.envelope import LogicalTime


def test_scheduler_separates_strict_before_from_same_time_actions() -> None:
    scheduler = DeterministicSchedulerOracle()
    assert scheduler.schedule(1, LogicalTime(100, 5)).accepted
    assert scheduler.schedule(2, LogicalTime(100, 5)).accepted
    assert scheduler.schedule(3, LogicalTime(100, 6)).accepted

    assert scheduler.drain(LogicalTime(100, 5), inclusive=False) == ()
    assert scheduler.drain(LogicalTime(100, 5), inclusive=True) == (1, 2)
    assert scheduler.drain(LogicalTime(100, 6), inclusive=True) == (3,)
    assert scheduler.pending_count == 0


def test_scheduler_rejects_reused_identity_after_cancel() -> None:
    scheduler = DeterministicSchedulerOracle()
    assert scheduler.schedule(7, LogicalTime(10, 1)).accepted
    assert scheduler.cancel(7).accepted
    duplicate = scheduler.schedule(7, LogicalTime(20, 2))

    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate_action_id"
    assert scheduler.drain(LogicalTime(100, 100), inclusive=True) == ()


def test_pending_cancel_remains_reserved_and_fillable_until_ack() -> None:
    risk = RiskReservationOracle(max_position_lots=10)
    assert risk.reserve(1, is_bid=True, qty_lots=7).accepted
    assert risk.request_cancel(1).accepted

    rejected = risk.reserve(2, is_bid=True, qty_lots=4)
    assert rejected.accepted is False
    assert rejected.reason == "long_limit"
    assert risk.reserved_buy_lots == 7

    assert risk.fill(1, 3).accepted
    assert risk.position_lots == 3
    assert risk.reserved_buy_lots == 4
    assert risk.cancel_ack(1).accepted
    assert risk.reserved_buy_lots == 0
    assert risk.reserve(3, is_bid=True, qty_lots=7).accepted


def test_risk_reservations_enforce_both_sides_and_epoch_invalidation() -> None:
    risk = RiskReservationOracle(max_position_lots=5)
    assert risk.reserve(1, is_bid=True, qty_lots=5).accepted
    assert risk.reserve(2, is_bid=False, qty_lots=5).accepted
    assert risk.fill(1, 5).accepted

    rejected = risk.reserve(3, is_bid=True, qty_lots=1)
    assert rejected.accepted is False
    assert rejected.reason == "long_limit"
    assert risk.request_cancel(2).accepted
    assert risk.invalidate_epoch().accepted
    assert risk.reserved_buy_lots == 0
    assert risk.reserved_sell_lots == 0
    assert risk.position_lots == 5


def test_invalid_risk_transitions_are_atomic() -> None:
    risk = RiskReservationOracle(max_position_lots=5)
    assert risk.reserve(1, is_bid=False, qty_lots=3).accepted
    before = risk.state_sha256()

    overfill = risk.fill(1, 4)
    assert overfill.accepted is False
    assert overfill.reason == "fill_exceeds_remaining"
    assert risk.state_sha256() == before


def test_risk_reservation_totals_support_extreme_valid_integer_limits() -> None:
    limit = 2**63 - 1
    risk = RiskReservationOracle(max_position_lots=limit)
    assert risk.reserve(1, is_bid=False, qty_lots=limit).accepted
    assert risk.fill(1, limit).accepted
    assert risk.reserve(2, is_bid=True, qty_lots=limit).accepted
    assert risk.reserve(3, is_bid=True, qty_lots=limit).accepted
    assert risk.reserved_buy_lots == limit * 2


def test_portfolio_notional_reservation_is_gross_and_cross_symbol() -> None:
    ledger = PortfolioNotionalReservationOracle(max_notional_units=100)
    assert ledger.set_inventory(1, 30).accepted
    assert ledger.reserve(1, symbol_id=2, is_bid=True, notional_units=40).accepted
    assert ledger.total_reserved_units == 70

    assert ledger.request_cancel(1).accepted
    rejected = ledger.reserve(2, symbol_id=3, is_bid=False, notional_units=31)
    assert rejected == type(rejected)(False, "portfolio_notional_limit")
    assert ledger.fill(1, 20).accepted
    assert ledger.inventory_by_symbol == {1: 30, 2: 20}
    assert ledger.reserved_order_units == 20
    assert ledger.total_reserved_units == 70
    assert ledger.cancel_ack(1).accepted
    assert ledger.total_reserved_units == 50
    assert ledger.reserve(3, symbol_id=3, is_bid=False, notional_units=50).accepted
    assert ledger.total_reserved_units == 100


def test_portfolio_notional_mark_updates_and_invalid_transitions_are_atomic() -> None:
    ledger = PortfolioNotionalReservationOracle(max_notional_units=10)
    assert ledger.set_inventory(7, -4).accepted
    assert ledger.reserve(1, symbol_id=7, is_bid=True, notional_units=6).accepted
    before = ledger.state_sha256()

    overfill = ledger.fill(1, 7)
    assert overfill.accepted is False
    assert overfill.reason == "fill_exceeds_remaining"
    assert ledger.state_sha256() == before

    unavailable = ledger.set_inventory(8, 5)
    assert unavailable.accepted is False
    assert unavailable.reason == "portfolio_notional_limit"
    assert ledger.inventory_by_symbol == {7: -4}
    assert ledger.invalidate_epoch().accepted
    assert ledger.reserved_order_units == 0
    assert ledger.total_reserved_units == 4


def test_accounting_handles_reversals_fees_missing_marks_and_markouts() -> None:
    accounting = AccountingMarkoutOracle()
    assert accounting.fill(1, is_bid=True, price_tick=100, qty_lots=3, fee_cash_units=7).accepted
    assert accounting.fill(1, is_bid=False, price_tick=110, qty_lots=1, fee_cash_units=-2).accepted
    assert accounting.realized_pnl_cash_units == 10 * ACCOUNTING_CASH_SCALE
    assert accounting.total_fees_cash_units == 5
    assert accounting.unrealized_pnl_cash_units is None
    assert accounting.mark(1, 105).accepted
    assert accounting.unrealized_pnl_cash_units == 10 * ACCOUNTING_CASH_SCALE
    assert accounting.markout(
        is_bid=True,
        fill_price_tick=100,
        qty_lots=2,
        mark_price_tick=98,
    ).accepted
    assert accounting.markout_cash_units == -4 * ACCOUNTING_CASH_SCALE


def test_accounting_overfill_reversal_is_atomic_and_state_is_hashable() -> None:
    accounting = AccountingMarkoutOracle()
    before = accounting.state_sha256()
    rejected = accounting.fill(1, is_bid=True, price_tick=0, qty_lots=1)
    assert rejected == type(rejected)(False, "invalid_fill_price")
    assert accounting.state_sha256() == before
    assert accounting.fill(1, is_bid=False, price_tick=100, qty_lots=2).accepted
    assert accounting.fill(1, is_bid=True, price_tick=90, qty_lots=3).accepted
    assert accounting.position_lots == 1
    assert accounting.unrealized_pnl_cash_units is None
