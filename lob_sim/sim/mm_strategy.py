from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from ..book.local_book import LocalOrderBook
from ..book.types import AggTradeEvent
from ..config import Config
from .orders import Order, OrderSide


@dataclass
class QuoteTarget:
    side: OrderSide
    quote_slot: str
    price_tick: int
    qty_lots: int
    refresh_key: str = ""


@dataclass
class StrategyDecision:
    quotes: list[QuoteTarget] = field(default_factory=list)
    reason: str | None = None


class MarketMakingStrategy:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._returns: dict[str, deque[Decimal]] = defaultdict(lambda: deque(maxlen=cfg.mm_volatility_window))
        self._prev_mid: dict[str, Decimal] = {}
        self._recent_trade_signals: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=cfg.mm_trade_imbalance_window)
        )

    def observe_trade(self, trade: AggTradeEvent) -> None:
        signed_lots = -trade.qty_lots if trade.buyer_is_maker else trade.qty_lots
        self._recent_trade_signals[trade.symbol].append(signed_lots)

    def _update_volatility(self, book: LocalOrderBook) -> None:
        mid = book.mid_price()
        if mid is None:
            return
        prev_mid = self._prev_mid.get(book.symbol)
        if prev_mid is not None and prev_mid > 0:
            ret = abs(mid - prev_mid) / prev_mid
            self._returns[book.symbol].append(ret)
        self._prev_mid[book.symbol] = mid

    def _volatility(self, symbol: str) -> Decimal:
        returns = self._returns.get(symbol)
        if not returns:
            return Decimal("0")
        return sum(returns) / Decimal(len(returns))

    def _bps_to_ticks(self, book: LocalOrderBook, bps: Decimal) -> Decimal:
        mid = book.mid_price()
        if mid is None:
            return Decimal("0")
        return (mid * bps / Decimal("10000")) / book.spec.tick_size

    def _tick_round(self, value: Decimal) -> int:
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))

    def _top_of_book_imbalance(self, book: LocalOrderBook) -> Decimal:
        best = book.best_ticks()
        if best is None:
            return Decimal("0")
        bid_tick, ask_tick = best
        bid_lots = book.get_level_size("bids", bid_tick)
        ask_lots = book.get_level_size("asks", ask_tick)
        total = bid_lots + ask_lots
        if total <= 0:
            return Decimal("0")
        return Decimal(bid_lots - ask_lots) / Decimal(total)

    def _recent_trade_imbalance(self, symbol: str) -> Decimal:
        trades = self._recent_trade_signals.get(symbol)
        if not trades:
            return Decimal("0")
        total = sum(abs(value) for value in trades)
        if total <= 0:
            return Decimal("0")
        signed = sum(trades)
        return Decimal(signed) / Decimal(total)

    def _microstructure_gate(self, book: LocalOrderBook) -> tuple[str, int]:
        threshold = self.cfg.mm_microstructure_gate_threshold
        book_imbalance = self._top_of_book_imbalance(book)
        trade_imbalance = self._recent_trade_imbalance(book.symbol)
        gate_ticks = self._tick_round(self._bps_to_ticks(book, self.cfg.mm_microstructure_gate_bps))

        if gate_ticks <= 0:
            return "neutral", 0
        if book_imbalance >= threshold and trade_imbalance >= threshold:
            return "bullish", gate_ticks
        if book_imbalance <= -threshold and trade_imbalance <= -threshold:
            return "bearish", gate_ticks
        return "neutral", 0

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        if order is None:
            return False
        if self.cfg.mm_strategy_profile == "layered_mm" and order.refresh_key != target.refresh_key:
            return True
        return order.queue_ahead_lots > self.cfg.mm_queue_repost_lots

    def _size_lots(self, book: LocalOrderBook) -> int:
        return book.spec.qty_to_lot(max(Decimal("0.00000001"), self.cfg.mm_order_qty))

    def _base_quote_inputs(self, book: LocalOrderBook, inventory_qty: Decimal) -> tuple[int, int, Decimal, Decimal]:
        best = book.best_ticks()
        if best is None:
            raise ValueError("book must have best bid/ask before quoting")
        bid_tick, ask_tick = best
        mid = (book.spec.tick_to_price(bid_tick) + book.spec.tick_to_price(ask_tick)) / Decimal("2")
        mid_ticks = mid / book.spec.tick_size
        skew_ticks = (inventory_qty * self.cfg.mm_skew_bps_per_unit / Decimal("10000")) * (mid / book.spec.tick_size)
        return bid_tick, ask_tick, mid_ticks, skew_ticks

    def _baseline_quotes(self, book: LocalOrderBook, inventory_qty: Decimal, size_lots: int) -> list[QuoteTarget]:
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        spread_scale = Decimal("1") + (self._volatility(book.symbol) * self.cfg.mm_volatility_spread_factor)
        half_spread_bps = max(Decimal("0"), self.cfg.mm_half_spread_bps * spread_scale)
        half_spread_ticks = max(Decimal("1"), self._bps_to_ticks(book, half_spread_bps))

        bid = self._tick_round(mid_ticks - half_spread_ticks - skew_ticks)
        ask = self._tick_round(mid_ticks + half_spread_ticks + skew_ticks)
        if bid >= ask:
            return []

        refresh_base = f"baseline:{bid_tick}:{ask_tick}"
        return [
            QuoteTarget("bid", "base", int(bid), size_lots, f"{refresh_base}:bid"),
            QuoteTarget("ask", "base", int(ask), size_lots, f"{refresh_base}:ask"),
        ]

    def _layered_quotes(self, book: LocalOrderBook, inventory_qty: Decimal, size_lots: int) -> list[QuoteTarget]:
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        spread_scale = Decimal("1") + (self._volatility(book.symbol) * self.cfg.mm_volatility_spread_factor)
        inner_spread_ticks = max(
            Decimal("1"),
            self._bps_to_ticks(book, self.cfg.mm_layered_inner_spread_bps * spread_scale),
        )
        outer_spread_ticks = max(
            inner_spread_ticks,
            self._bps_to_ticks(book, self.cfg.mm_layered_outer_spread_bps * spread_scale),
        )
        gate_label, gate_ticks = self._microstructure_gate(book)
        bid_gate = gate_ticks if gate_label == "bearish" else 0
        ask_gate = gate_ticks if gate_label == "bullish" else 0

        bid_inner = self._tick_round(mid_ticks - inner_spread_ticks - skew_ticks - Decimal(bid_gate))
        ask_inner = self._tick_round(mid_ticks + inner_spread_ticks + skew_ticks + Decimal(ask_gate))
        bid_outer = self._tick_round(mid_ticks - outer_spread_ticks - skew_ticks - Decimal(bid_gate))
        ask_outer = self._tick_round(mid_ticks + outer_spread_ticks + skew_ticks + Decimal(ask_gate))

        bid_outer = min(bid_outer, bid_inner - 1)
        ask_outer = max(ask_outer, ask_inner + 1)
        if bid_inner >= ask_inner or bid_outer >= ask_outer:
            return []

        refresh_base = f"{bid_tick}:{ask_tick}:{gate_label}"
        return [
            QuoteTarget("bid", "inner", int(bid_inner), size_lots, f"inner:bid:{refresh_base}"),
            QuoteTarget("bid", "outer", int(bid_outer), size_lots, f"outer:bid:{refresh_base}"),
            QuoteTarget("ask", "inner", int(ask_inner), size_lots, f"inner:ask:{refresh_base}"),
            QuoteTarget("ask", "outer", int(ask_outer), size_lots, f"outer:ask:{refresh_base}"),
        ]

    def propose(self, book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        self._update_volatility(book)
        if book.best_ticks() is None:
            return StrategyDecision(reason="no_best_quotes")

        size_lots = max(1, self._size_lots(book))
        if self.cfg.mm_strategy_profile == "layered_mm":
            quotes = self._layered_quotes(book, inventory_qty, size_lots)
        else:
            quotes = self._baseline_quotes(book, inventory_qty, size_lots)

        if not quotes:
            return StrategyDecision(reason="crossing_quotes")
        return StrategyDecision(quotes=quotes)
