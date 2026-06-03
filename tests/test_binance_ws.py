from __future__ import annotations

from decimal import Decimal

from lob_sim.binance.ws import parse_agg_trade
from lob_sim.book.types import SymbolSpec


def test_parse_agg_trade_accepts_raw_trade_payload() -> None:
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))

    event = parse_agg_trade(
        "BTCUSDT",
        spec,
        {
            "e": "trade",
            "E": 1780500088697,
            "T": 1780500088697,
            "s": "BTCUSDT",
            "p": "66240.10",
            "q": "0.061",
            "m": False,
            "t": 7721648663,
        },
    )

    assert event.symbol == "BTCUSDT"
    assert event.price_tick == 662401
    assert event.qty_lots == 61
    assert event.buyer_is_maker is False
    assert event.ts_local == 1780500088.697
