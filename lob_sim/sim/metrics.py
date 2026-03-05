from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from typing import Any, Dict, List

from ..book.local_book import LocalOrderBook
from ..config import Config
from .orders import Fill


@dataclass
class PositionState:
    lot_size: int = 0
    avg_cost: Decimal | None = None


class SimulationMetrics:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.position: Dict[str, PositionState] = {}
        self.specs: Dict[str, object] = {}

        self.realized_pnl = Decimal("0")
        self.unrealized_pnl = Decimal("0")
        self.total_fees = Decimal("0")

        self.fill_count = 0
        self.fill_qty: Decimal = Decimal("0")
        self.quote_count = 0
        self.spread_capture_sum = Decimal("0")
        self.spread_capture_qty = Decimal("0")
        self.max_drawdown = Decimal("0")
        self.equity_peak = Decimal("0")

        self._inv_mean = Decimal("0")
        self._inv_m2 = Decimal("0")
        self._inv_n = 0

        self.fills_log: List[dict] = []

        self._pending_markouts: list[dict[str, Any]] = []
        self._markout_events: list[dict[str, Any]] = []
        self.markout_sum = Decimal("0")
        self.markout_count = 0
        self.adverse_markout_count = 0
        self._markout_by_side: dict[str, int] = defaultdict(int)
        self._markout_adverse_by_side: dict[str, int] = defaultdict(int)

        self._regime_fill_counts: dict[str, int] = defaultdict(int)
        self._regime_fill_qty: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        self._regime_spread_capture: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        self._regime_markout_counts: dict[str, int] = defaultdict(int)
        self._regime_adverse_counts: dict[str, int] = defaultdict(int)

        self.fill_from_top_count = 0
        self.queue_fill_count = 0
        self.fill_wait_count = 0
        self.fill_wait_ms_total = Decimal("0")
        self.max_queue_ahead_lots = 0

        self.consecutive_loss_count = 0
        self.max_consecutive_loss_count = 0
        self.kill_switch_triggered = False
        self.kill_switch_reason: str | None = None

    def register_symbol(self, symbol: str) -> None:
        self.position.setdefault(symbol, PositionState())

    def on_quote_requested(self) -> None:
        self.quote_count += 1

    def inventory_lots(self, symbol: str) -> int:
        return self.position.get(symbol, PositionState()).lot_size

    def _regime(self, book: LocalOrderBook) -> str:
        best = book.best_ticks()
        if best is None:
            return "stale_book"

        bid_tick, ask_tick = best
        spread_ticks = max(0, ask_tick - bid_tick)
        if spread_ticks <= 1:
            spread_bucket = "tight"
        elif spread_ticks <= 3:
            spread_bucket = "normal"
        else:
            spread_bucket = "wide"

        bid_top = book.get_level_size("bids", bid_tick)
        ask_top = book.get_level_size("asks", ask_tick)
        depth = bid_top + ask_top
        if depth <= 0:
            imbalance_bucket = "flat"
        else:
            imbalance = Decimal(bid_top - ask_top) / Decimal(depth)
            if imbalance >= Decimal("0.35"):
                imbalance_bucket = "buy"
            elif imbalance <= Decimal("-0.35"):
                imbalance_bucket = "sell"
            elif imbalance >= Decimal("0.15"):
                imbalance_bucket = "mild_buy"
            elif imbalance <= Decimal("-0.15"):
                imbalance_bucket = "mild_sell"
            else:
                imbalance_bucket = "balanced"

        return f"{spread_bucket}_{imbalance_bucket}"

    def _record_fill_regime(self, regime: str, qty: Decimal, spread_capture: Decimal) -> None:
        self._regime_fill_counts[regime] += 1
        self._regime_fill_qty[regime] += qty
        self._regime_spread_capture[regime] += spread_capture

    def _drain_markout_windows(self, now_ts: float, mids: Dict[str, Decimal]) -> None:
        if not self._pending_markouts:
            return

        keep: list[dict[str, Any]] = []
        for entry in self._pending_markouts:
            if entry["deadline_ts"] > now_ts:
                keep.append(entry)
                continue

            symbol = str(entry["symbol"])
            mid = mids.get(symbol)
            if mid is None:
                keep.append(entry)
                continue

            side = str(entry["side"])
            price = Decimal(str(entry["price"]))
            qty = Decimal(str(entry["qty"]))
            regime = str(entry["regime"])
            side_sign = Decimal("1") if side == "bid" else Decimal("-1")
            markout = (mid - price) * side_sign

            adverse = markout < 0
            self.markout_sum += markout * qty
            self.markout_count += 1
            self._regime_markout_counts[regime] += 1
            self._markout_by_side[side] += 1
            if adverse:
                self._markout_adverse_by_side[side] += 1

            if adverse:
                self.adverse_markout_count += 1
                self._regime_adverse_counts[regime] += 1

            self._markout_events.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "regime": regime,
                    "fill_price": str(price),
                    "qty": str(qty),
                    "fill_mid": str(entry["mid_at_fill"]) if entry.get("mid_at_fill") is not None else None,
                    "mid_after": str(mid),
                    "markout": str(markout),
                    "adverse": adverse,
                    "horizon": self.cfg.sim_adverse_markout_seconds,
                    "ts_local": entry.get("ts_local"),
                }
            )

        self._pending_markouts = keep

    def _evaluate_risk(self, equity: Decimal) -> None:
        if not self.cfg.sim_kill_switch_enabled or self.kill_switch_triggered:
            return

        if self.cfg.sim_kill_max_drawdown > 0 and self.max_drawdown >= self.cfg.sim_kill_max_drawdown:
            self.kill_switch_triggered = True
            self.kill_switch_reason = (
                f"max_drawdown_exceeded: {float(self.max_drawdown):.6f} >= "
                f"{float(self.cfg.sim_kill_max_drawdown):.6f}"
            )
            return

        if self.cfg.sim_kill_max_consecutive_losses > 0 and self.max_consecutive_loss_count >= self.cfg.sim_kill_max_consecutive_losses:
            self.kill_switch_triggered = True
            self.kill_switch_reason = (
                f"consecutive_losses_exceeded: {self.max_consecutive_loss_count} >= "
                f"{self.cfg.sim_kill_max_consecutive_losses}"
            )

    def on_fill(self, fill: Fill, book: LocalOrderBook, mid: Decimal | None) -> None:
        pos = self.position.setdefault(fill.symbol, PositionState())
        qty = book.spec.lot_to_qty(fill.qty_lots)
        price = book.spec.tick_to_price(fill.price_tick)
        side_sign = Decimal("1") if fill.side == "bid" else Decimal("-1")
        signed_qty_lots = side_sign * fill.qty_lots

        realized_delta = Decimal("0")
        if pos.lot_size == 0:
            pos.lot_size = signed_qty_lots
            pos.avg_cost = price
        elif pos.lot_size * signed_qty_lots > 0:
            total_new_abs = Decimal(abs(pos.lot_size) + abs(signed_qty_lots))
            old_abs_qty = book.spec.lot_to_qty(abs(pos.lot_size))
            add_qty = book.spec.lot_to_qty(abs(signed_qty_lots))
            pos.avg_cost = (old_abs_qty * (pos.avg_cost or Decimal("0")) + add_qty * price) / book.spec.lot_to_qty(total_new_abs)
            pos.lot_size += signed_qty_lots
        else:
            close_qty_lots = min(abs(pos.lot_size), abs(signed_qty_lots))
            close_qty = book.spec.lot_to_qty(close_qty_lots)
            if pos.lot_size > 0:
                realized_delta = close_qty * (price - (pos.avg_cost or Decimal("0")))
            else:
                realized_delta = close_qty * ((pos.avg_cost or Decimal("0")) - price)
            self.realized_pnl += realized_delta

            remaining = abs(signed_qty_lots) - close_qty_lots
            if remaining == 0:
                if abs(pos.lot_size) == close_qty_lots:
                    pos.lot_size = 0
                    pos.avg_cost = None
            else:
                pos.lot_size = side_sign * remaining
                pos.avg_cost = price

        fee_bps = self.cfg.fees_maker_bps if fill.maker else self.cfg.fees_taker_bps
        fee = qty * price * (fee_bps / Decimal("10000"))
        self.total_fees += fee
        self.realized_pnl -= fee

        self.fill_count += 1
        self.fill_qty += qty

        spread_capture = Decimal("0")
        if mid is not None:
            spread_capture = (mid - price) if fill.side == "bid" else (price - mid)
            self.spread_capture_sum += spread_capture * qty
            self.spread_capture_qty += qty

        wait_ms = Decimal("0")
        if fill.created_ts is not None:
            wait_ms = Decimal(str(max(0.0, fill.ts_local - fill.created_ts))) * Decimal("1000")
            self.fill_wait_ms_total += wait_ms
            self.fill_wait_count += 1

        if fill.queue_ahead_lots > 0:
            self.queue_fill_count += 1
            if fill.queue_ahead_lots > self.max_queue_ahead_lots:
                self.max_queue_ahead_lots = fill.queue_ahead_lots
        else:
            self.fill_from_top_count += 1

        regime = self._regime(book)
        self._record_fill_regime(regime, qty, spread_capture)

        self.fills_log.append(
            {
                "ts_local": fill.ts_local,
                "symbol": fill.symbol,
                "side": fill.side,
                "price": str(price),
                "qty": str(qty),
                "maker": fill.maker,
                "order_id": fill.order_id,
                "mid_at_fill": str(mid) if mid is not None else None,
                "regime": regime,
                "queue_ahead_lots": fill.queue_ahead_lots,
                "time_in_book_ms": float(wait_ms),
                "markout_horizon": self.cfg.sim_adverse_markout_seconds,
                "book_bid_tick": book.best_ticks()[0] if book.best_ticks() else None,
                "book_ask_tick": book.best_ticks()[1] if book.best_ticks() else None,
            }
        )

        if self.cfg.sim_adverse_markout_seconds > 0:
            self._pending_markouts.append(
                {
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "price": str(price),
                    "qty": str(qty),
                    "regime": regime,
                    "ts_local": fill.ts_local,
                    "deadline_ts": fill.ts_local + self.cfg.sim_adverse_markout_seconds,
                    "mid_at_fill": str(mid) if mid is not None else None,
                }
            )

        if realized_delta < 0:
            self.consecutive_loss_count += 1
        else:
            self.consecutive_loss_count = 0

        if self.consecutive_loss_count > self.max_consecutive_loss_count:
            self.max_consecutive_loss_count = self.consecutive_loss_count

    def update_unrealized(self, books: Dict[str, LocalOrderBook], now_ts: float | None = None, mid_override: Dict[str, Decimal] | None = None) -> None:
        unreal = Decimal("0")
        total_inventory = Decimal("0")
        mids = dict(mid_override or {})

        for symbol, pos in self.position.items():
            if pos.lot_size == 0 or pos.avg_cost is None:
                continue
            book = books.get(symbol)
            if book is None:
                continue

            mid = mids.get(symbol)
            if mid is None:
                mid = book.mid_price()
                if mid is not None:
                    mids[symbol] = mid
            if mid is None:
                continue

            qty = book.spec.lot_to_qty(abs(pos.lot_size))
            sign = Decimal(1) if pos.lot_size > 0 else Decimal("-1")
            unreal += sign * qty * (mid - pos.avg_cost)
            total_inventory += sign * qty

        self.unrealized_pnl = unreal
        equity = self.realized_pnl + self.unrealized_pnl

        if equity > self.equity_peak:
            self.equity_peak = equity
        else:
            self.max_drawdown = max(self.max_drawdown, self.equity_peak - equity)

        if now_ts is not None:
            self._drain_markout_windows(now_ts, mids)

        self._evaluate_risk(equity)

        # Inventory stats are tracked on signed quantity for simplicity.
        self._inv_n += 1
        delta = total_inventory - self._inv_mean
        self._inv_mean += delta / Decimal(self._inv_n)
        self._inv_m2 += delta * (total_inventory - self._inv_mean)

    def get_summary(self, books: Dict[str, LocalOrderBook]) -> dict:
        self.update_unrealized(books)

        avg_spread = Decimal("0")
        if self.spread_capture_qty > 0:
            avg_spread = self.spread_capture_sum / self.spread_capture_qty

        fill_rate = Decimal("0")
        if self.quote_count > 0:
            fill_rate = Decimal(self.fill_count) / Decimal(self.quote_count)

        avg_fill_wait_ms = Decimal("0")
        if self.fill_wait_count > 0:
            avg_fill_wait_ms = self.fill_wait_ms_total / Decimal(self.fill_wait_count)

        avg_markout = Decimal("0")
        if self.markout_count > 0:
            avg_markout = self.markout_sum / Decimal(self.markout_count)

        adverse_markout_rate = Decimal("0")
        if self.markout_count > 0:
            adverse_markout_rate = Decimal(self.adverse_markout_count) / Decimal(self.markout_count)

        adverse_markout_rate_by_side: dict[str, float] = {}
        for side in ("bid", "ask"):
            sample = self._markout_by_side.get(side, 0)
            adverse = self._markout_adverse_by_side.get(side, 0)
            adverse_markout_rate_by_side[side] = float(Decimal(adverse) / Decimal(sample)) if sample else 0.0

        inv_stdev = Decimal("0")
        if self._inv_n > 1:
            inv_stdev = Decimal(str(sqrt(float(self._inv_m2 / Decimal(self._inv_n - 1)))) )

        total_inventory = Decimal("0")
        inventory_by_symbol: dict[str, float] = {}
        for symbol, pos in self.position.items():
            book = books.get(symbol)
            if book is None:
                continue
            qty = book.spec.lot_to_qty(pos.lot_size)
            total_inventory += qty
            inventory_by_symbol[symbol] = float(qty)

        fill_from_top_rate = Decimal("0")
        if self.fill_count > 0:
            fill_from_top_rate = Decimal(self.fill_from_top_count) / Decimal(self.fill_count)

        regime_performance: dict[str, dict[str, float]] = {}
        for regime in self._regime_fill_counts:
            fills = self._regime_fill_counts[regime]
            qty = self._regime_fill_qty[regime]
            capture_sum = self._regime_spread_capture[regime]
            markouts = self._regime_markout_counts.get(regime, 0)
            adverse = self._regime_adverse_counts.get(regime, 0)

            regime_performance[regime] = {
                "fills": fills,
                "qty": float(qty),
                "avg_spread_capture": float(capture_sum / qty) if qty > 0 else 0.0,
                "markout_samples": markouts,
                "adverse_markout_rate": float(Decimal(adverse) / Decimal(markouts)) if markouts else 0.0,
            }

        return {
            "total_pnl": float(self.realized_pnl + self.unrealized_pnl),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(self.unrealized_pnl),
            "max_drawdown": float(self.max_drawdown),
            "fill_count": self.fill_count,
            "fill_rate": float(fill_rate),
            "avg_spread_captured": float(avg_spread),
            "avg_inventory": float(self._inv_mean),
            "inventory_stdev": float(inv_stdev),
            "total_fees": float(self.total_fees),
            "total_inventory": float(total_inventory),
            "quote_count": self.quote_count,
            "avg_fill_wait_ms": float(avg_fill_wait_ms),
            "fill_from_top_rate": float(fill_from_top_rate),
            "adverse_fill_rate_1s": float(adverse_markout_rate),
            "adverse_fill_rate_1s_by_side": adverse_markout_rate_by_side,
            "queue_fill_count": self.queue_fill_count,
            "max_queue_ahead_lots": self.max_queue_ahead_lots,
            "max_consecutive_loss_count": self.max_consecutive_loss_count,
            "markout_samples_remaining": len(self._pending_markouts),
            "avg_markout_1s": float(avg_markout),
            "markout_events": list(self._markout_events),
            "inventory_by_symbol": inventory_by_symbol,
            "kill_switch_triggered": self.kill_switch_triggered,
            "kill_switch_reason": self.kill_switch_reason,
            "regime_performance": regime_performance,
            "fills": list(self.fills_log),
        }
