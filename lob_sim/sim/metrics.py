from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List
from math import sqrt

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

    def register_symbol(self, symbol: str) -> None:
        self.position.setdefault(symbol, PositionState())

    def on_quote_requested(self) -> None:
        self.quote_count += 1

    def inventory_lots(self, symbol: str) -> int:
        return self.position.get(symbol, PositionState()).lot_size

    def on_fill(self, fill: Fill, book: LocalOrderBook, mid: Decimal | None) -> None:
        pos = self.position.setdefault(fill.symbol, PositionState())
        qty = book.spec.lot_to_qty(fill.qty_lots)
        price = book.spec.tick_to_price(fill.price_tick)
        side_sign = 1 if fill.side == "bid" else -1
        signed_qty_lots = side_sign * fill.qty_lots
        signed_qty = book.spec.lot_to_qty(abs(signed_qty_lots))

        # Position update with average cost handling for same side closes.
        if pos.lot_size == 0:
            pos.lot_size = signed_qty_lots
            pos.avg_cost = price
        elif pos.lot_size * signed_qty_lots > 0:
            total_new_abs = Decimal(abs(pos.lot_size) + abs(signed_qty_lots))
            old_abs_qty = book.spec.lot_to_qty(abs(pos.lot_size))
            add_qty = book.spec.lot_to_qty(abs(signed_qty_lots))
            pos.avg_cost = (old_abs_qty * (pos.avg_cost or Decimal("0")) + add_qty * price) / (book.spec.lot_to_qty(total_new_abs))
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

        if mid is not None:
            signed_mid_capture = (mid - price) if fill.side == "bid" else (price - mid)
            self.spread_capture_sum += signed_mid_capture * qty
            self.spread_capture_qty += qty

        self.fills_log.append(
            {
                "ts_local": fill.ts_local,
                "symbol": fill.symbol,
                "side": fill.side,
                "price": str(price),
                "qty": str(qty),
                "maker": fill.maker,
                "order_id": fill.order_id,
            }
        )

    def update_unrealized(self, books: Dict[str, LocalOrderBook], mid_override: Dict[str, Decimal] | None = None) -> None:
        unreal = Decimal("0")
        total_inventory = Decimal("0")
        for symbol, pos in self.position.items():
            if pos.lot_size == 0 or pos.avg_cost is None:
                continue
            book = books.get(symbol)
            if book is None:
                continue
            mid = mid_override.get(symbol) if mid_override else None
            if mid is None:
                mid = book.mid_price()
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

        # Inventory stats are tracked on signed quantity for simplicity.
        self._inv_n += 1
        delta = total_inventory - self._inv_mean
        self._inv_mean += delta / Decimal(self._inv_n)
        self._inv_m2 += delta * (total_inventory - self._inv_mean)

    def get_summary(self, books: Dict[str, LocalOrderBook]) -> dict:
        avg_spread = Decimal("0")
        if self.spread_capture_qty > 0:
            avg_spread = self.spread_capture_sum / self.spread_capture_qty
        fill_rate = Decimal("0")
        if self.quote_count > 0:
            fill_rate = Decimal(self.fill_count) / Decimal(self.quote_count)

        inv_stdev = Decimal("0")
        if self._inv_n > 1:
            inv_stdev = Decimal(str(sqrt(float(self._inv_m2 / Decimal(self._inv_n - 1)))))

        # Ensure book references kept current for inventory calculation in output.
        self.update_unrealized(books, None)
        total_inventory = Decimal("0")
        for symbol, pos in self.position.items():
            if books.get(symbol) is None:
                continue
            total_inventory += books[symbol].spec.lot_to_qty(pos.lot_size)

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
            "fills": list(self.fills_log),
            "total_inventory": float(total_inventory),
            "quote_count": self.quote_count,
        }
