from __future__ import annotations

from lob_sim.record.envelope import LogicalTime
from lob_sim.sim.synthetic_exchange import SyntheticExchange


def _seed_asks(exchange: SyntheticExchange) -> None:
    exchange.submit_new(
        order_id="ask-a",
        participant_id="maker-a",
        side="ask",
        qty_lots=2,
        price_tick=101,
        post_only=True,
    )
    exchange.submit_new(
        order_id="ask-b",
        participant_id="maker-b",
        side="ask",
        qty_lots=2,
        price_tick=101,
        post_only=True,
    )


def test_exact_price_time_priority_and_market_impact() -> None:
    exchange = SyntheticExchange()
    _seed_asks(exchange)

    result = exchange.submit_new(
        order_id="buy",
        participant_id="taker",
        side="bid",
        qty_lots=3,
        price_tick=101,
        time_in_force="IOC",
    )

    assert [(fill.maker_order_id, fill.qty_lots) for fill in result.fills] == [("ask-a", 2), ("ask-b", 1)]
    assert result.terminal_state == "filled"
    assert exchange.best_ask_tick() == 101
    assert exchange.snapshot()["asks"][0]["orders"] == [
        {"order_id": "ask-b", "participant_id": "maker-b", "remaining_lots": 1}
    ]


def test_post_only_rejects_without_mutating_book() -> None:
    exchange = SyntheticExchange()
    _seed_asks(exchange)
    before = exchange.state_sha256()

    result = exchange.submit_new(
        order_id="crossing-post-only",
        participant_id="maker-c",
        side="bid",
        qty_lots=1,
        price_tick=101,
        post_only=True,
    )

    assert result.accepted is False
    assert result.transitions[0].reason == "post_only_would_cross"
    assert exchange.state_sha256() == before


def test_ioc_partial_fill_expires_remainder() -> None:
    exchange = SyntheticExchange()
    exchange.submit_new(
        order_id="ask",
        participant_id="maker",
        side="ask",
        qty_lots=1,
        price_tick=101,
        post_only=True,
    )

    result = exchange.submit_new(
        order_id="ioc",
        participant_id="taker",
        side="bid",
        qty_lots=3,
        price_tick=101,
        time_in_force="IOC",
    )

    assert [fill.qty_lots for fill in result.fills] == [1]
    assert result.terminal_state == "expired"
    assert exchange.orders["ioc"].remaining_lots == 2
    assert exchange.best_ask_tick() is None


def test_cancel_aggressor_self_trade_prevention() -> None:
    exchange = SyntheticExchange(self_trade_prevention="cancel_aggressor")
    exchange.submit_new(
        order_id="own-ask",
        participant_id="same",
        side="ask",
        qty_lots=1,
        price_tick=101,
        post_only=True,
    )

    result = exchange.submit_new(
        order_id="own-buy",
        participant_id="same",
        side="bid",
        qty_lots=1,
        price_tick=101,
        time_in_force="IOC",
    )

    assert result.fills == ()
    assert result.terminal_state == "cancelled"
    assert exchange.orders["own-ask"].state == "live"
    assert any(event.event == "self_trade_prevention" for event in result.transitions)


def test_replace_loses_time_priority_and_invalid_replace_preserves_old_order() -> None:
    exchange = SyntheticExchange()
    exchange.submit_new(
        order_id="bid-a",
        participant_id="a",
        side="bid",
        qty_lots=1,
        price_tick=100,
        post_only=True,
    )
    exchange.submit_new(
        order_id="bid-b",
        participant_id="b",
        side="bid",
        qty_lots=1,
        price_tick=100,
        post_only=True,
    )

    rejected = exchange.replace("bid-a", new_order_id="bid-a2", price_tick=0, qty_lots=1)
    assert rejected.accepted is False
    assert exchange.orders["bid-a"].state == "live"

    replaced = exchange.replace("bid-a", new_order_id="bid-a2", price_tick=100, qty_lots=1)
    assert replaced.accepted is True
    assert [row["order_id"] for row in exchange.snapshot()["bids"][0]["orders"]] == ["bid-b", "bid-a2"]


def test_state_hash_is_deterministic_and_time_regressions_fail_closed() -> None:
    first = SyntheticExchange()
    second = SyntheticExchange()
    for exchange in (first, second):
        exchange.submit_new(
            order_id="bid",
            participant_id="maker",
            side="bid",
            qty_lots=1,
            price_tick=100,
            post_only=True,
            time=LogicalTime(10, 1),
        )

    assert first.state_sha256() == second.state_sha256()

    try:
        first.cancel("bid", time=LogicalTime(9, 2))
    except ValueError as exc:
        assert "nondecreasing" in str(exc)
    else:
        raise AssertionError("logical time regression was accepted")
