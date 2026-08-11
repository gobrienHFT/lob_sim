from __future__ import annotations

import hashlib
import random

from lob_sim.sim.synthetic_exchange import SyntheticExchange
from scripts.check_rust_python_parity import (
    _generated_synthetic_operations,
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
    assert {operation[0] for operation in first} == {0, 1}
    assert any(operation[4] is None for operation in first if operation[0] == 0)
    assert any(operation[5] <= 0 for operation in first if operation[0] == 0)


def test_python_synthetic_trace_reports_lifecycle_fills_and_checkpoints() -> None:
    operations = [
        (0, 1, 10, False, 101, 2, True, False),
        (0, 2, 20, False, 101, 2, True, False),
        (0, 3, 30, True, 101, 3, False, True),
        (1, 2, 0, True, None, 0, False, False),
        (0, 1, 40, True, 100, 1, False, False),
        (1, 999, 0, True, None, 0, False, False),
    ]

    trace = _python_synthetic_trace(operations, checkpoint_interval=2)

    assert trace[2][0:3] == (True, "filled", None)
    assert trace[2][3] == [(1, 3, 101, 2), (2, 3, 101, 1)]
    assert trace[3][0:3] == (True, "cancelled", None)
    assert trace[4][0:3] == (False, "rejected", "duplicate_order_id")
    assert trace[5][0:3] == (False, "rejected", "unknown_order")
    assert [index for index, row in enumerate(trace, start=1) if row[4] is not None] == [2, 4, 6]
