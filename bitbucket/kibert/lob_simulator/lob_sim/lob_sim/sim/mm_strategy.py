from __future__ import annotations

from decimal import Decimal

from ..book.local_book import LocalOrderBook
from ..config import Config


def compute_quotes(
    book: LocalOrderBook,
    inventory_qty: Decimal,
    config: Config,
) -> tuple[int, int] | None:
    best = book.best_ticks()
    if best is None:
        return None

    bid_tick, ask_tick = best
    mid = (book.spec.tick_to_price(bid_tick) + book.spec.tick_to_price(ask_tick)) / Decimal("2")
    half_spread = mid * (config.mm_half_spread_bps / Decimal("10000"))
    skew = inventory_qty * (config.mm_skew_bps_per_unit / Decimal("10000")) * mid
    bid_price = mid - half_spread - skew
    ask_price = mid + half_spread - skew
    bid_q = book.spec.price_to_tick(bid_price)
    ask_q = book.spec.price_to_tick(ask_price)
    if bid_q >= ask_q:
        return None
    return bid_q, ask_q
