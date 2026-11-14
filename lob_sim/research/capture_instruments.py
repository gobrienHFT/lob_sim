"""Initial public USD-M contract metadata admission, not private venue rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from lob_sim.binance.symbols import parse_exchange_info_for_symbol


def validate_instrument_entry(symbol: str, entry: Any) -> dict[str, Any]:
    if symbol not in {"BTCUSDT", "ETHUSDT"} or not isinstance(entry, dict) or entry.get("symbol") != symbol:
        raise ValueError("required instrument metadata identity is missing")
    if (
        entry.get("status") != "TRADING"
        or entry.get("contractType") != "PERPETUAL"
        or entry.get("baseAsset") != symbol.removesuffix("USDT")
        or entry.get("quoteAsset") != "USDT"
        or entry.get("marginAsset") != "USDT"
        or not isinstance(entry.get("filters"), list)
    ):
        raise ValueError("required linear USDT perpetual contract is unavailable")
    filters = [f.get("filterType") for f in entry["filters"] if isinstance(f, dict)]
    if (
        len(filters) != len(entry["filters"])
        or any(not isinstance(f, str) or not f for f in filters)
        or len(set(filters)) != len(filters)
        or not {"PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL"}.issubset(filters)
    ):
        raise ValueError("instrument filters are incomplete or ambiguous")
    try:
        spec = parse_exchange_info_for_symbol({"symbols": [entry]}, symbol)
    except InvalidOperation as exc:
        raise ValueError("invalid instrument grid metadata") from exc
    if any(not value.is_finite() or value <= 0 for value in (spec.tick_size, spec.step_size)):
        raise ValueError("instrument grid is nonfinite or nonpositive")
    # Complete captured raw filters are retained. This does not pretend that
    # every newly introduced exchange filter has an implemented execution rule.
    notional = next(f for f in entry["filters"] if f["filterType"] == "MIN_NOTIONAL").get("notional")
    if not isinstance(notional, str) or len(notional) > 128:
        raise ValueError("minimum notional metadata is missing")
    try:
        value = Decimal(notional)
    except InvalidOperation as exc:
        raise ValueError("invalid minimum notional metadata") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("minimum notional metadata is invalid")
    return entry
