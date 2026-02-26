from __future__ import annotations

from decimal import Decimal
from typing import Dict, Iterable

from ..book.types import SymbolSpec


def parse_exchange_info_for_symbol(exchange_info: dict, symbol: str) -> SymbolSpec:
    symbols = exchange_info.get("symbols", [])
    entry = next((s for s in symbols if s.get("symbol") == symbol), None)
    if entry is None:
        raise KeyError(f"Symbol {symbol} not found in exchangeInfo")

    tick_size: str | None = None
    step_size: str | None = None
    for f in entry.get("filters", []):
        if f.get("filterType") == "PRICE_FILTER":
            tick_size = f.get("tickSize")
        elif f.get("filterType") == "LOT_SIZE":
            step_size = f.get("stepSize")
    if not tick_size or not step_size:
        raise ValueError(f"Missing tickSize or stepSize for {symbol}")
    return SymbolSpec(symbol=symbol, tick_size=Decimal(tick_size), step_size=Decimal(step_size))
