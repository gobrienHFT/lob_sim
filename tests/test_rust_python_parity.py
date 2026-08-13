from __future__ import annotations

import hashlib
import random

from lob_sim.sim.synthetic_exchange import SyntheticExchange
from scripts.check_rust_python_parity import (
    _engine_contract_operations,
    _generated_portfolio_operations,
    _generated_accounting_operations,
    _generated_public_queue_operations,
    _generated_risk_operations,
    _generated_scheduler_operations,
    _generated_synthetic_operations,
    _python_risk_trace,
    _python_portfolio_trace,
    _python_accounting_trace,
    _python_engine_contract_trace,
    _python_public_queue_trace,
    _python_scheduler_trace,
    _python_synthetic_trace,
    _synthetic_state_sha256,
)


def test_synthetic_parity_state_encoding_is_explicit() -> None:
    exchange = SyntheticExchange()
    exchange.submit_new(
        order_id="1",
        participant_id="10",
        side="bid",
        qty_lots=2,
        price_tick=100,
        post_only=True,
    )

    encoded = "bid:100:1;order:1:10:bid:100:2:2:1:GTC:true:live;"
    assert _synthetic_state_sha256(exchange) == hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_generated_synthetic_operations_are_seeded_and_mixed() -> None:
    first = _generated_synthetic_operations(random.Random(17), 500)
    second = _generated_synthetic_operations(random.Random(17), 500)

    assert first == second
    assert len(first) == 500
    assert {operation[0] for operation in first} == {0, 1, 2}
    assert any(operation[4] is None for operation in first if operation[0] == 0)
    assert any(operation[5] <= 0 for operation in first if operation[0] == 0)


def test_public_queue_trace_is_seeded_and_covers_lifecycle_boundaries() -> None:
    first = _generated_public_queue_operations(random.Random(53), 500)
    second = _generated_public_queue_operations(random.Random(53), 500)

    assert first == second
    assert len(first) == 500
    assert {operation[0] for operation in first} == {0, 1, 2, 3}
    trace = _python_public_queue_trace(first, checkpoint_interval=17)
    assert len(trace) == 500
    assert any(row[2] for row in trace)
    assert any(row[1] is not None for row in trace)
    assert trace[-1][5] is not None


def test_python_synthetic_trace_reports_lifecycle_fills_and_checkpoints() -> None:
    operations = [
        (0, 1, 10, False, 101, 2, True, False),
        (0, 2, 20, False, 101, 2, True, False),
        (0, 3, 30, True, 101, 3, False, True),
        (2, 2, 4, True, 102, 1, True, False),
        (2, 4, 5, True, 0, 1, False, False),
        (1, 4, 0, True, None, 0, False, False),
        (0, 1, 40, True, 100, 1, False, False),
        (1, 999, 0, True, None, 0, False, False),
    ]

    trace = _python_synthetic_trace(operations, checkpoint_interval=2)

    assert trace[2][0:3] == (True, "filled", None)
    assert trace[2][3] == [(1, 3, 101, 2), (2, 3, 101, 1)]
    assert trace[3][0:3] == (True, "live", None)
    assert trace[4][0:3] == (False, "live", "invalid_price")
    assert trace[5][0:3] == (True, "cancelled", None)
    assert trace[6][0:3] == (False, "rejected", "duplicate_order_id")
    assert trace[7][0:3] == (False, "rejected", "unknown_order")
    assert [index for index, row in enumerate(trace, start=1) if row[4] is not None] == [2, 4, 6, 8]


def test_generated_scheduler_and_risk_operations_are_seeded_and_mixed() -> None:
    first_scheduler = _generated_scheduler_operations(random.Random(17), 500)
    second_scheduler = _generated_scheduler_operations(random.Random(17), 500)
    first_risk = _generated_risk_operations(random.Random(23), 500)
    second_risk = _generated_risk_operations(random.Random(23), 500)

    assert first_scheduler == second_scheduler
    assert first_risk == second_risk
    assert {operation[0] for operation in first_scheduler} == {0, 1, 2}
    assert {operation[0] for operation in first_risk} == {0, 1, 2, 3, 4}


def test_generated_portfolio_operations_are_seeded_and_mixed() -> None:
    first = _generated_portfolio_operations(random.Random(31), 500)
    second = _generated_portfolio_operations(random.Random(31), 500)

    assert first == second
    assert len(first) == 500
    assert {operation[0] for operation in first} == {0, 1, 2, 3, 4, 5}


def test_python_portfolio_trace_is_gross_and_checkpointed() -> None:
    operations = [
        (5, 0, 1, False, 30),
        (0, 1, 2, True, 40),
        (1, 1, 2, False, 0),
        (3, 1, 2, False, 20),
        (2, 1, 2, False, 0),
        (0, 2, 3, False, 50),
        (4, 0, 0, False, 0),
    ]

    trace = _python_portfolio_trace(operations, max_notional_units=100, checkpoint_interval=3)

    assert trace[1][2:5] == (30, 40, 70)
    assert trace[3][2:5] == (50, 20, 70)
    assert trace[5][0] is True
    assert trace[6][2:5] == (50, 0, 50)
    assert [index for index, row in enumerate(trace, start=1) if row[5] is not None] == [3, 6, 7]


def test_generated_accounting_operations_are_seeded_and_mixed() -> None:
    first = _generated_accounting_operations(random.Random(47), 500)
    second = _generated_accounting_operations(random.Random(47), 500)

    assert first == second
    assert len(first) == 500
    assert {operation[0] for operation in first} == {0, 1, 2, 3}


def test_python_accounting_trace_reports_nullable_marks_and_signed_markouts() -> None:
    operations = [
        (0, 1, True, 100, 3, 7),
        (0, 1, False, 110, 1, -2),
        (3, 1, True, 100, 2, 98),
        (1, 1, False, 105, 0, 0),
    ]

    trace = _python_accounting_trace(operations, checkpoint_interval=2)

    assert trace[1][2:6] == (2, 2, 10_000_000, 5)
    assert trace[1][6] is None
    assert trace[2][8:10] == (-4_000_000, 2)
    assert trace[3][6] == 10_000_000
    assert trace[3][7] is True
    assert [index for index, row in enumerate(trace, start=1) if row[10] is not None] == [2, 4]


def test_composed_engine_contract_trace_spans_components_and_rejections() -> None:
    operations = _engine_contract_operations()
    first = _python_engine_contract_trace(operations)
    second = _python_engine_contract_trace(operations)

    assert first == second
    assert len(first) == len(operations)
    assert sum(1 for row in first if row[0]) == 14
    assert sum(1 for row in first if not row[0]) == 4
    assert sum(len(row[2]) for row in first) == 1
    assert len(first[-1][3]) == 64
    assert first[12][1] == "duplicate_order_id"
    assert first[13][1] == "batch would cross the book"
    assert first[14][1] == "invalid_consumption"


def test_python_scheduler_trace_proves_exact_time_boundary_and_checkpoints() -> None:
    operations = [
        (0, 1, 100, 5, False),
        (0, 2, 100, 5, False),
        (1, 0, 100, 5, False),
        (1, 0, 100, 5, True),
    ]

    trace = _python_scheduler_trace(operations, checkpoint_interval=2)

    assert trace[2][2] == []
    assert trace[3][2] == [1, 2]
    assert trace[3][3] == 0
    assert [index for index, row in enumerate(trace, start=1) if row[4] is not None] == [2, 4]


def test_python_risk_trace_keeps_pending_cancel_reserved_and_fillable() -> None:
    operations = [
        (0, 1, True, 7),
        (1, 1, False, 0),
        (0, 2, True, 4),
        (3, 1, False, 3),
        (2, 1, False, 0),
        (0, 3, True, 7),
    ]

    trace = _python_risk_trace(operations, max_position_lots=10, checkpoint_interval=3)

    assert trace[1][3] == 7
    assert trace[2][0:2] == (False, "long_limit")
    assert trace[3][2:4] == (3, 4)
    assert trace[4][3] == 0
    assert trace[5][0] is True
    assert [index for index, row in enumerate(trace, start=1) if row[5] is not None] == [3, 6]
