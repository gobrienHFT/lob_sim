from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

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
    diagnostics: dict[str, Any] = field(default_factory=dict)


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

    def _format_decimal(self, value: Decimal) -> str:
        return str(value)

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

    def _combined_imbalance(self, book: LocalOrderBook) -> Decimal:
        return (self._top_of_book_imbalance(book) + self._recent_trade_imbalance(book.symbol)) / Decimal("2")

    def _fee_floor_half_spread_bps(self) -> Decimal:
        round_trip_maker_fee = max(Decimal("0"), self.cfg.fees_maker_bps * Decimal("2"))
        return (round_trip_maker_fee / Decimal("2")) + self.cfg.mm_fee_floor_buffer_bps

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        if order is None:
            return False
        if self.cfg.mm_strategy_profile in {"layered_mm", "research_mm"} and order.refresh_key != target.refresh_key:
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

    def _base_diagnostics(
        self,
        book: LocalOrderBook,
        inventory_qty: Decimal,
        size_lots: int,
        bid_tick: int,
        ask_tick: int,
        mid_ticks: Decimal,
        skew_ticks: Decimal,
    ) -> dict[str, Any]:
        return {
            "profile": self.cfg.mm_strategy_profile,
            "best_bid_tick": bid_tick,
            "best_ask_tick": ask_tick,
            "mid_ticks": self._format_decimal(mid_ticks),
            "mid_price": self._format_decimal(mid_ticks * book.spec.tick_size),
            "inventory_qty": self._format_decimal(inventory_qty),
            "size_lots": size_lots,
            "volatility": self._format_decimal(self._volatility(book.symbol)),
            "skew_ticks": self._format_decimal(skew_ticks),
            "top_of_book_imbalance": self._format_decimal(self._top_of_book_imbalance(book)),
            "recent_trade_imbalance": self._format_decimal(self._recent_trade_imbalance(book.symbol)),
        }

    def _baseline_quotes(
        self,
        book: LocalOrderBook,
        inventory_qty: Decimal,
        size_lots: int,
    ) -> tuple[list[QuoteTarget], dict[str, Any]]:
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        volatility = self._volatility(book.symbol)
        spread_scale = Decimal("1") + (volatility * self.cfg.mm_volatility_spread_factor)
        half_spread_bps = max(Decimal("0"), self.cfg.mm_half_spread_bps * spread_scale)
        half_spread_ticks = max(Decimal("1"), self._bps_to_ticks(book, half_spread_bps))
        diagnostics = self._base_diagnostics(book, inventory_qty, size_lots, bid_tick, ask_tick, mid_ticks, skew_ticks)
        diagnostics.update(
            {
                "spread_scale": self._format_decimal(spread_scale),
                "half_spread_bps": self._format_decimal(half_spread_bps),
                "half_spread_ticks": self._format_decimal(half_spread_ticks),
            }
        )

        bid = self._tick_round(mid_ticks - half_spread_ticks - skew_ticks)
        ask = self._tick_round(mid_ticks + half_spread_ticks + skew_ticks)
        if bid >= ask:
            return [], diagnostics

        refresh_base = f"baseline:{bid_tick}:{ask_tick}"
        return (
            [
                QuoteTarget("bid", "base", int(bid), size_lots, f"{refresh_base}:bid"),
                QuoteTarget("ask", "base", int(ask), size_lots, f"{refresh_base}:ask"),
            ],
            diagnostics,
        )

    def _layered_quotes(
        self,
        book: LocalOrderBook,
        inventory_qty: Decimal,
        size_lots: int,
    ) -> tuple[list[QuoteTarget], dict[str, Any]]:
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        volatility = self._volatility(book.symbol)
        spread_scale = Decimal("1") + (volatility * self.cfg.mm_volatility_spread_factor)
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
        diagnostics = self._base_diagnostics(book, inventory_qty, size_lots, bid_tick, ask_tick, mid_ticks, skew_ticks)
        diagnostics.update(
            {
                "spread_scale": self._format_decimal(spread_scale),
                "inner_spread_ticks": self._format_decimal(inner_spread_ticks),
                "outer_spread_ticks": self._format_decimal(outer_spread_ticks),
                "gate_label": gate_label,
                "gate_ticks": gate_ticks,
            }
        )

        bid_inner = self._tick_round(mid_ticks - inner_spread_ticks - skew_ticks - Decimal(bid_gate))
        ask_inner = self._tick_round(mid_ticks + inner_spread_ticks + skew_ticks + Decimal(ask_gate))
        bid_outer = self._tick_round(mid_ticks - outer_spread_ticks - skew_ticks - Decimal(bid_gate))
        ask_outer = self._tick_round(mid_ticks + outer_spread_ticks + skew_ticks + Decimal(ask_gate))

        bid_outer = min(bid_outer, bid_inner - 1)
        ask_outer = max(ask_outer, ask_inner + 1)
        if bid_inner >= ask_inner or bid_outer >= ask_outer:
            return [], diagnostics

        refresh_base = f"{bid_tick}:{ask_tick}:{gate_label}"
        return (
            [
                QuoteTarget("bid", "inner", int(bid_inner), size_lots, f"inner:bid:{refresh_base}"),
                QuoteTarget("bid", "outer", int(bid_outer), size_lots, f"outer:bid:{refresh_base}"),
                QuoteTarget("ask", "inner", int(ask_inner), size_lots, f"inner:ask:{refresh_base}"),
                QuoteTarget("ask", "outer", int(ask_outer), size_lots, f"outer:ask:{refresh_base}"),
            ],
            diagnostics,
        )

    def _research_quotes(
        self,
        book: LocalOrderBook,
        inventory_qty: Decimal,
        size_lots: int,
    ) -> tuple[list[QuoteTarget], dict[str, Any]]:
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        volatility = self._volatility(book.symbol)
        spread_scale = Decimal("1") + (volatility * self.cfg.mm_volatility_spread_factor)
        combined_imbalance = self._combined_imbalance(book)
        toxicity_bps = abs(combined_imbalance) * self.cfg.mm_toxicity_spread_factor
        base_half_spread_bps = self.cfg.mm_half_spread_bps * spread_scale
        fee_floor_bps = self._fee_floor_half_spread_bps()
        half_spread_bps = max(Decimal("0"), base_half_spread_bps + toxicity_bps, fee_floor_bps)
        half_spread_ticks = max(Decimal("1"), self._bps_to_ticks(book, half_spread_bps))

        threshold = self.cfg.mm_microstructure_gate_threshold
        gate_ticks = self._tick_round(self._bps_to_ticks(book, self.cfg.mm_microstructure_gate_bps))
        if combined_imbalance >= threshold:
            gate_label = "bullish_toxic"
            bid_extra = Decimal("0")
            ask_extra = Decimal(gate_ticks)
        elif combined_imbalance <= -threshold:
            gate_label = "bearish_toxic"
            bid_extra = Decimal(gate_ticks)
            ask_extra = Decimal("0")
        else:
            gate_label = "neutral"
            bid_extra = Decimal("0")
            ask_extra = Decimal("0")

        reservation_ticks = mid_ticks - skew_ticks
        diagnostics = self._base_diagnostics(book, inventory_qty, size_lots, bid_tick, ask_tick, mid_ticks, skew_ticks)
        diagnostics.update(
            {
                "spread_scale": self._format_decimal(spread_scale),
                "combined_imbalance": self._format_decimal(combined_imbalance),
                "toxicity_bps": self._format_decimal(toxicity_bps),
                "base_half_spread_bps": self._format_decimal(base_half_spread_bps),
                "fee_floor_bps": self._format_decimal(fee_floor_bps),
                "half_spread_bps": self._format_decimal(half_spread_bps),
                "half_spread_ticks": self._format_decimal(half_spread_ticks),
                "reservation_ticks": self._format_decimal(reservation_ticks),
                "reservation_tick": self._tick_round(reservation_ticks),
                "gate_label": gate_label,
                "gate_ticks": gate_ticks,
                "bid_extra_ticks": self._format_decimal(bid_extra),
                "ask_extra_ticks": self._format_decimal(ask_extra),
            }
        )
        bid_near = self._tick_round(reservation_ticks - half_spread_ticks - bid_extra)
        ask_near = self._tick_round(reservation_ticks + half_spread_ticks + ask_extra)
        outer_spread_ticks = max(
            half_spread_ticks + Decimal("1"),
            self._bps_to_ticks(book, max(self.cfg.mm_layered_outer_spread_bps, half_spread_bps * Decimal("2"))),
        )
        diagnostics["outer_spread_ticks"] = self._format_decimal(outer_spread_ticks)
        bid_far = min(self._tick_round(reservation_ticks - outer_spread_ticks - bid_extra), bid_near - 1)
        ask_far = max(self._tick_round(reservation_ticks + outer_spread_ticks + ask_extra), ask_near + 1)
        if bid_near >= ask_near or bid_far >= ask_far:
            return [], diagnostics

        refresh_base = (
            f"research:{bid_tick}:{ask_tick}:{self._tick_round(reservation_ticks)}:{gate_label}:"
            f"{self._tick_round(half_spread_ticks)}"
        )
        return (
            [
                QuoteTarget("bid", "near", int(bid_near), size_lots, f"near:bid:{refresh_base}"),
                QuoteTarget("bid", "far", int(bid_far), size_lots, f"far:bid:{refresh_base}"),
                QuoteTarget("ask", "near", int(ask_near), size_lots, f"near:ask:{refresh_base}"),
                QuoteTarget("ask", "far", int(ask_far), size_lots, f"far:ask:{refresh_base}"),
            ],
            diagnostics,
        )

    def propose(self, book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        self._update_volatility(book)
        if book.best_ticks() is None:
            return StrategyDecision(reason="no_best_quotes")

        size_lots = max(1, self._size_lots(book))
        if self.cfg.mm_strategy_profile == "research_mm":
            quotes, diagnostics = self._research_quotes(book, inventory_qty, size_lots)
        elif self.cfg.mm_strategy_profile == "layered_mm":
            quotes, diagnostics = self._layered_quotes(book, inventory_qty, size_lots)
        else:
            quotes, diagnostics = self._baseline_quotes(book, inventory_qty, size_lots)

        if not quotes:
            return StrategyDecision(reason="crossing_quotes", diagnostics=diagnostics)
        return StrategyDecision(quotes=quotes, diagnostics=diagnostics)
