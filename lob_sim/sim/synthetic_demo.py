"""Small reviewer-facing proof for the exact synthetic MBO venue.

This is deliberately separate from public-L2 replay.  The synthetic exchange
knows participant and order identity, so its price-time result is ground truth
inside this mode; it must never be presented as historical Binance FIFO.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..record.envelope import LogicalTime
from .synthetic_exchange import ExchangeResult, SyntheticExchange


def _result_summary(result: ExchangeResult) -> dict[str, Any]:
    return {
        "order_id": result.order_id,
        "accepted": result.accepted,
        "terminal_state": result.terminal_state,
        "fill_count": len(result.fills),
        "transition_count": len(result.transitions),
    }


def run_exact_synthetic_demo() -> dict[str, Any]:
    """Run a compact exact-FIFO scenario and return JSON-safe evidence."""

    exchange = SyntheticExchange(retain_transition_log=True)
    maker_a = exchange.submit_new(
        order_id="ask-a",
        participant_id="maker-a",
        side="ask",
        qty_lots=2,
        price_tick=101,
        time_in_force="GTC",
        post_only=True,
        time=LogicalTime(1_000, 1),
    )
    maker_b = exchange.submit_new(
        order_id="ask-b",
        participant_id="maker-b",
        side="ask",
        qty_lots=2,
        price_tick=101,
        time_in_force="GTC",
        post_only=True,
        time=LogicalTime(1_000, 2),
    )
    taker = exchange.submit_new(
        order_id="buy",
        participant_id="taker",
        side="bid",
        qty_lots=3,
        price_tick=101,
        time_in_force="IOC",
        time=LogicalTime(1_000, 3),
    )
    post_only_rejection = exchange.submit_new(
        order_id="crossing-post-only",
        participant_id="maker-c",
        side="bid",
        qty_lots=1,
        price_tick=101,
        time_in_force="GTC",
        post_only=True,
        time=LogicalTime(1_000, 4),
    )

    expected_fills = [("ask-a", 2), ("ask-b", 1)]
    observed_fills = [(fill.maker_order_id, fill.qty_lots) for fill in taker.fills]
    transition_rows = [asdict(transition) for transition in exchange.transition_log]
    return {
        "schema_version": "lob_sim.synthetic_exchange_demo.v1",
        "mode": "exact_synthetic_mbo_price_time_priority",
        "historical_binance_fifo": False,
        "self_trade_prevention": exchange.self_trade_prevention,
        "actions": [_result_summary(result) for result in (maker_a, maker_b, taker, post_only_rejection)],
        "fifo_ground_truth": {
            "expected_maker_order_sequence": [order_id for order_id, _ in expected_fills],
            "observed_maker_order_sequence": [order_id for order_id, _ in observed_fills],
            "expected_fill_lots": [qty for _, qty in expected_fills],
            "observed_fill_lots": [qty for _, qty in observed_fills],
            "matches": observed_fills == expected_fills,
        },
        "fills": [asdict(fill) for fill in taker.fills],
        "transitions": transition_rows,
        "post_only_rejection_reason": post_only_rejection.transitions[0].reason,
        "state_sha256": exchange.state_sha256(),
        "final_snapshot": exchange.snapshot(),
        "claim": "exact participant/order identity and price-time priority inside this synthetic venue only",
        "non_claim": "not historical Binance FIFO, private fill truth, or a public-L2 execution result",
    }
