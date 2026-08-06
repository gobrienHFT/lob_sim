from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from ..book.local_book import LocalOrderBook
from ..book.types import SymbolSpec
from ..config import Config
from .orders import Fill

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass
class PositionState:
    lot_size: int = 0
    avg_cost: Decimal | None = None


@dataclass
class _RunningStat:
    count: int = 0
    mean: Decimal = ZERO
    m2: Decimal = ZERO

    def observe(self, value: Decimal) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / Decimal(self.count)
        self.m2 += delta * (value - self.mean)

    def stdev(self) -> Decimal:
        if self.count <= 1:
            return ZERO
        return Decimal(str(math.sqrt(float(self.m2 / Decimal(self.count - 1)))))


@dataclass(frozen=True)
class _PendingMarkout:
    symbol: str
    horizon_ms: int
    target_ts: float
    fill_price: Decimal
    fill_qty: Decimal
    side_sign: Decimal
    fill_log_index: int


@dataclass(frozen=True)
class _ResolvedMarkout:
    symbol: str
    horizon_ms: int
    pnl: Decimal
    bps: Decimal
    fill_notional: Decimal
    observation_lag_ms: float


class SimulationMetrics:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.position: dict[str, PositionState] = {}
        self.specs: dict[str, SymbolSpec] = {}

        self.realized_pnl = ZERO
        self.unrealized_pnl = ZERO
        self._missing_mark_symbols: set[str] = set()
        self.total_fees = ZERO

        self.fill_count = 0
        self.fill_qty = ZERO
        self.quote_count = 0
        self.spread_capture_sum = ZERO
        self.spread_capture_qty = ZERO
        self.max_drawdown = ZERO
        self.equity_peak = ZERO

        self._inventory_stats: dict[str, _RunningStat] = {}
        self._realized_pnl_by_symbol: dict[str, Decimal] = {}
        self._fees_by_symbol: dict[str, Decimal] = {}
        self._filled_qty_by_symbol: dict[str, Decimal] = {}
        self._filled_notional_by_symbol: dict[str, Decimal] = {}
        self._spread_capture_pnl_by_symbol: dict[str, Decimal] = {}
        self._spread_capture_qty_by_symbol: dict[str, Decimal] = {}
        self._spread_capture_notional_by_symbol: dict[str, Decimal] = {}
        self._filled_order_ids: set[str] = set()
        self._anonymous_fill_event_count = 0

        self._pending_markouts: dict[str, list[_PendingMarkout]] = {}
        self._resolved_markouts: dict[int, list[_ResolvedMarkout]] = {
            horizon_ms: [] for horizon_ms in cfg.sim_markout_horizons_ms
        }
        self._invalidated_markouts: dict[int, list[_PendingMarkout]] = {
            horizon_ms: [] for horizon_ms in cfg.sim_markout_horizons_ms
        }
        self._last_mid_ts: dict[str, float] = {}

        self.fills_log: list[dict] = []

    def register_symbol(self, symbol: str) -> None:
        self.position.setdefault(symbol, PositionState())
        self._inventory_stats.setdefault(symbol, _RunningStat())

    def on_quote_requested(self) -> None:
        """Record one accepted quote order.

        Existing callers do not expose requested quantity, so quantity fill rate
        remains explicitly unavailable in the summary.
        """

        self.quote_count += 1

    def inventory_lots(self, symbol: str) -> int:
        return self.position.get(symbol, PositionState()).lot_size

    def _apply_position_fill(
        self,
        pos: PositionState,
        signed_qty_lots: int,
        price: Decimal,
        spec: SymbolSpec,
    ) -> Decimal:
        old_lots = pos.lot_size
        if old_lots == 0:
            pos.lot_size = signed_qty_lots
            pos.avg_cost = price
            return ZERO

        if old_lots * signed_qty_lots > 0:
            old_qty = spec.lot_to_qty(abs(old_lots))
            added_qty = spec.lot_to_qty(abs(signed_qty_lots))
            total_qty = old_qty + added_qty
            pos.avg_cost = (old_qty * (pos.avg_cost or ZERO) + added_qty * price) / total_qty
            pos.lot_size = old_lots + signed_qty_lots
            return ZERO

        close_lots = min(abs(old_lots), abs(signed_qty_lots))
        close_qty = spec.lot_to_qty(close_lots)
        old_side_sign = Decimal("1") if old_lots > 0 else Decimal("-1")
        realized_delta = old_side_sign * close_qty * (price - (pos.avg_cost or ZERO))

        new_lots = old_lots + signed_qty_lots
        pos.lot_size = new_lots
        if new_lots == 0:
            pos.avg_cost = None
        elif old_lots * new_lots > 0:
            # A partial close leaves the entry price of the remaining position unchanged.
            pass
        else:
            # Any residual after crossing through flat is newly opened at this fill price.
            pos.avg_cost = price
        return realized_delta

    def on_fill(self, fill: Fill, book: LocalOrderBook, mid: Decimal | None) -> None:
        if fill.side not in {"bid", "ask"}:
            raise ValueError(f"Invalid fill side: {fill.side}")
        if fill.qty_lots <= 0:
            raise ValueError("Fill quantity must be positive")
        if not math.isfinite(fill.ts_local):
            raise ValueError("Fill timestamp must be finite")

        last_mid_ts = self._last_mid_ts.get(fill.symbol)
        if last_mid_ts is not None and fill.ts_local < last_mid_ts:
            raise ValueError("on_fill must precede later observe_mid calls for the same symbol")

        self.specs[fill.symbol] = book.spec
        pos = self.position.setdefault(fill.symbol, PositionState())
        self._inventory_stats.setdefault(fill.symbol, _RunningStat())

        qty = book.spec.lot_to_qty(fill.qty_lots)
        price = book.spec.tick_to_price(fill.price_tick)
        if price <= 0:
            raise ValueError("Fill price must be positive")
        side_sign = 1 if fill.side == "bid" else -1
        signed_qty_lots = side_sign * fill.qty_lots

        realized_delta = self._apply_position_fill(pos, signed_qty_lots, price, book.spec)
        self.realized_pnl += realized_delta
        self._realized_pnl_by_symbol[fill.symbol] = (
            self._realized_pnl_by_symbol.get(fill.symbol, ZERO) + realized_delta
        )

        fee_bps = self.cfg.fees_maker_bps if fill.maker else self.cfg.fees_taker_bps
        fill_notional = qty * price
        fee = fill_notional * (fee_bps / TEN_THOUSAND)
        self.total_fees += fee
        self.realized_pnl -= fee
        self._fees_by_symbol[fill.symbol] = self._fees_by_symbol.get(fill.symbol, ZERO) + fee
        self._realized_pnl_by_symbol[fill.symbol] -= fee

        self.fill_count += 1
        self.fill_qty += qty
        self._filled_qty_by_symbol[fill.symbol] = self._filled_qty_by_symbol.get(fill.symbol, ZERO) + qty
        self._filled_notional_by_symbol[fill.symbol] = (
            self._filled_notional_by_symbol.get(fill.symbol, ZERO) + fill_notional
        )
        if fill.order_id is None:
            self._anonymous_fill_event_count += 1
        else:
            self._filled_order_ids.add(fill.order_id)

        if mid is not None:
            mid = Decimal(mid)
            if not mid.is_finite() or mid <= 0:
                raise ValueError("Fill-time mid must be finite and positive")
            signed_mid_capture = (mid - price) if fill.side == "bid" else (price - mid)
            capture_pnl = signed_mid_capture * qty
            self.spread_capture_sum += capture_pnl
            self.spread_capture_qty += qty
            self._spread_capture_pnl_by_symbol[fill.symbol] = (
                self._spread_capture_pnl_by_symbol.get(fill.symbol, ZERO) + capture_pnl
            )
            self._spread_capture_qty_by_symbol[fill.symbol] = (
                self._spread_capture_qty_by_symbol.get(fill.symbol, ZERO) + qty
            )
            self._spread_capture_notional_by_symbol[fill.symbol] = (
                self._spread_capture_notional_by_symbol.get(fill.symbol, ZERO) + fill_notional
            )

        fill_log_index = len(self.fills_log)
        self.fills_log.append(
            {
                "ts_local": fill.ts_local,
                "symbol": fill.symbol,
                "side": fill.side,
                "price": str(price),
                "qty": str(qty),
                "maker": fill.maker,
                "order_id": fill.order_id,
                "cause": fill.cause,
                "queue_ahead_before_lots": fill.queue_ahead_before_lots,
                "mid_at_fill": str(mid) if mid is not None else None,
                "markouts": {f"{horizon_ms}ms": None for horizon_ms in self.cfg.sim_markout_horizons_ms},
            }
        )

        pending = self._pending_markouts.setdefault(fill.symbol, [])
        for horizon_ms in self.cfg.sim_markout_horizons_ms:
            pending.append(
                _PendingMarkout(
                    symbol=fill.symbol,
                    horizon_ms=horizon_ms,
                    target_ts=fill.ts_local + horizon_ms / 1000.0,
                    fill_price=price,
                    fill_qty=qty,
                    side_sign=Decimal(side_sign),
                    fill_log_index=fill_log_index,
                )
            )

    def observe_mid(self, symbol: str, ts: float, mid: Decimal) -> int:
        """Resolve due markouts using the first causal mid at/after each target.

        Call this after processing all fills at ``ts``. Timestamps must be
        nondecreasing independently for each symbol. The return value is the
        number of fill/horizon observations resolved by this midpoint.
        """

        if not math.isfinite(ts):
            raise ValueError("Midpoint timestamp must be finite")
        mid = Decimal(mid)
        if not mid.is_finite() or mid <= 0:
            raise ValueError("Observed midpoint must be finite and positive")
        last_ts = self._last_mid_ts.get(symbol)
        if last_ts is not None and ts < last_ts:
            raise ValueError(f"Midpoint timestamps must be nondecreasing for {symbol}")
        self._last_mid_ts[symbol] = ts

        unresolved: list[_PendingMarkout] = []
        resolved_count = 0
        for item in self._pending_markouts.get(symbol, []):
            if ts < item.target_ts:
                unresolved.append(item)
                continue

            signed_price_change = item.side_sign * (mid - item.fill_price)
            pnl = signed_price_change * item.fill_qty
            bps = (signed_price_change / item.fill_price) * TEN_THOUSAND
            result = _ResolvedMarkout(
                symbol=symbol,
                horizon_ms=item.horizon_ms,
                pnl=pnl,
                bps=bps,
                fill_notional=item.fill_price * item.fill_qty,
                observation_lag_ms=max(0.0, (ts - item.target_ts) * 1000.0),
            )
            self._resolved_markouts[item.horizon_ms].append(result)
            self.fills_log[item.fill_log_index]["markouts"][f"{item.horizon_ms}ms"] = {
                "observation_ts": ts,
                "observation_lag_ms": result.observation_lag_ms,
                "mid": str(mid),
                "pnl": float(pnl),
                "bps": float(bps),
            }
            resolved_count += 1

        self._pending_markouts[symbol] = unresolved
        return resolved_count

    def on_book_invalidated(self, symbol: str, ts: float, reason: str) -> int:
        """Prevent markouts from bridging a book gap or resynchronization."""

        invalidated = self._pending_markouts.pop(symbol, [])
        for item in invalidated:
            self._invalidated_markouts[item.horizon_ms].append(item)
            self.fills_log[item.fill_log_index]["markouts"][f"{item.horizon_ms}ms"] = {
                "status": "invalidated",
                "invalidation_ts": ts,
                "reason": reason,
            }
        return len(invalidated)

    def _inventory_snapshot(
        self,
        books: dict[str, LocalOrderBook],
        mid_override: dict[str, Decimal] | None = None,
    ) -> tuple[Decimal, dict[str, dict], set[str]]:
        unrealized_total = ZERO
        inventory: dict[str, dict] = {}
        missing_marks: set[str] = set()
        for symbol, pos in self.position.items():
            book = books.get(symbol)
            spec = book.spec if book is not None else self.specs.get(symbol)
            if spec is None:
                continue

            signed_base_qty = spec.lot_to_qty(pos.lot_size)
            mid = mid_override.get(symbol) if mid_override else None
            if mid is None and book is not None:
                mid = book.mid_price()
            if mid is not None:
                mid = Decimal(mid)

            symbol_unrealized: Decimal | None = ZERO
            if pos.lot_size != 0 and pos.avg_cost is not None and mid is not None:
                symbol_unrealized = signed_base_qty * (mid - pos.avg_cost)
            elif pos.lot_size != 0:
                symbol_unrealized = None
                missing_marks.add(symbol)
            if symbol_unrealized is not None:
                unrealized_total += symbol_unrealized

            stats = self._inventory_stats.setdefault(symbol, _RunningStat())
            inventory[symbol] = {
                "lots": pos.lot_size,
                "base_qty": float(signed_base_qty),
                "avg_cost": float(pos.avg_cost) if pos.avg_cost is not None else None,
                "mark_price": float(mid) if mid is not None else None,
                "signed_quote_notional": float(signed_base_qty * mid) if mid is not None else None,
                "absolute_quote_notional": float(abs(signed_base_qty * mid)) if mid is not None else None,
                "realized_pnl": float(self._realized_pnl_by_symbol.get(symbol, ZERO)),
                "gross_realized_pnl_before_fees": float(
                    self._realized_pnl_by_symbol.get(symbol, ZERO) + self._fees_by_symbol.get(symbol, ZERO)
                ),
                "unrealized_pnl": float(symbol_unrealized) if symbol_unrealized is not None else None,
                "valuation_status": "marked" if symbol_unrealized is not None else "missing_mark",
                "fees": float(self._fees_by_symbol.get(symbol, ZERO)),
                "avg_base_inventory": float(stats.mean),
                "base_inventory_stdev": float(stats.stdev()),
                "inventory_observations": stats.count,
            }
        return unrealized_total, inventory, missing_marks

    def update_unrealized(
        self,
        books: dict[str, LocalOrderBook],
        mid_override: dict[str, Decimal] | None = None,
    ) -> None:
        unrealized, inventory, missing_marks = self._inventory_snapshot(books, mid_override)
        self.unrealized_pnl = unrealized
        self._missing_mark_symbols = missing_marks
        if not missing_marks:
            equity = self.realized_pnl + self.unrealized_pnl
            if equity > self.equity_peak:
                self.equity_peak = equity
            else:
                self.max_drawdown = max(self.max_drawdown, self.equity_peak - equity)

        for symbol in inventory:
            spec = self.specs.get(symbol)
            if spec is None and symbol in books:
                spec = books[symbol].spec
            if spec is not None:
                signed_base_qty = spec.lot_to_qty(self.position[symbol].lot_size)
                self._inventory_stats.setdefault(symbol, _RunningStat()).observe(signed_base_qty)

    def _markout_summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        pending_by_horizon: dict[int, list[_PendingMarkout]] = {
            horizon_ms: [] for horizon_ms in self.cfg.sim_markout_horizons_ms
        }
        for pending in self._pending_markouts.values():
            for item in pending:
                pending_by_horizon[item.horizon_ms].append(item)

        for horizon_ms in self.cfg.sim_markout_horizons_ms:
            resolved = self._resolved_markouts[horizon_ms]
            invalidated = self._invalidated_markouts[horizon_ms]
            total_pnl = sum((item.pnl for item in resolved), ZERO)
            total_notional = sum((item.fill_notional for item in resolved), ZERO)
            observation_lags = [item.observation_lag_ms for item in resolved]
            weighted_bps = (total_pnl / total_notional) * TEN_THOUSAND if total_notional > 0 else None
            mean_bps = (
                sum((item.bps for item in resolved), ZERO) / Decimal(len(resolved)) if resolved else None
            )

            by_symbol: dict[str, dict] = {}
            symbols = sorted(
                {item.symbol for item in resolved}
                | {item.symbol for item in pending_by_horizon[horizon_ms]}
                | {item.symbol for item in invalidated}
            )
            for symbol in symbols:
                symbol_resolved = [item for item in resolved if item.symbol == symbol]
                symbol_pending = [item for item in pending_by_horizon[horizon_ms] if item.symbol == symbol]
                symbol_invalidated = [item for item in invalidated if item.symbol == symbol]
                symbol_pnl = sum((item.pnl for item in symbol_resolved), ZERO)
                symbol_notional = sum((item.fill_notional for item in symbol_resolved), ZERO)
                symbol_bps = (symbol_pnl / symbol_notional) * TEN_THOUSAND if symbol_notional > 0 else None
                by_symbol[symbol] = {
                    "resolved_count": len(symbol_resolved),
                    "unresolved_count": len(symbol_pending) + len(symbol_invalidated),
                    "pending_count": len(symbol_pending),
                    "invalidated_count": len(symbol_invalidated),
                    "markout_pnl": float(symbol_pnl),
                    "notional_weighted_bps": float(symbol_bps) if symbol_bps is not None else None,
                }

            result[f"{horizon_ms}ms"] = {
                "horizon_ms": horizon_ms,
                "resolved_count": len(resolved),
                "unresolved_count": len(pending_by_horizon[horizon_ms]) + len(invalidated),
                "pending_count": len(pending_by_horizon[horizon_ms]),
                "invalidated_count": len(invalidated),
                "markout_pnl": float(total_pnl),
                "notional_weighted_bps": float(weighted_bps) if weighted_bps is not None else None,
                "mean_fill_bps": float(mean_bps) if mean_bps is not None else None,
                "observation_lag_ms": {
                    "mean": sum(observation_lags) / len(observation_lags) if observation_lags else None,
                    "max": max(observation_lags) if observation_lags else None,
                },
                "by_symbol": by_symbol,
            }
        return result

    def _fill_metrics(self) -> dict:
        fill_event_rate = Decimal(self.fill_count) / Decimal(self.quote_count) if self.quote_count else ZERO
        identified_order_rate = (
            Decimal(len(self._filled_order_ids)) / Decimal(self.quote_count) if self.quote_count else ZERO
        )
        return {
            "quote_order_count": self.quote_count,
            "fill_event_count": self.fill_count,
            "identified_filled_order_count": len(self._filled_order_ids),
            "anonymous_fill_event_count": self._anonymous_fill_event_count,
            "fill_event_rate_per_quote_order": float(fill_event_rate),
            "identified_order_fill_rate_lower_bound": float(identified_order_rate),
            "filled_base_qty_by_symbol": {
                symbol: float(qty) for symbol, qty in sorted(self._filled_qty_by_symbol.items())
            },
            "filled_quote_notional_by_symbol": {
                symbol: float(value) for symbol, value in sorted(self._filled_notional_by_symbol.items())
            },
            "quantity_fill_rate": None,
            "quantity_fill_rate_reason": "requested quote quantity is not supplied to on_quote_requested",
        }

    def _spread_capture_summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for symbol in sorted(self._spread_capture_pnl_by_symbol):
            pnl = self._spread_capture_pnl_by_symbol[symbol]
            qty = self._spread_capture_qty_by_symbol[symbol]
            notional = self._spread_capture_notional_by_symbol[symbol]
            result[symbol] = {
                "capture_pnl": float(pnl),
                "filled_base_qty": float(qty),
                "notional_weighted_bps": float((pnl / notional) * TEN_THOUSAND) if notional > 0 else None,
            }
        return result

    def get_summary(self, books: dict[str, LocalOrderBook]) -> dict:
        # Refresh valuation without adding a synthetic inventory observation.
        unrealized, inventory, missing_marks = self._inventory_snapshot(books, None)
        self.unrealized_pnl = unrealized
        self._missing_mark_symbols = missing_marks
        if not missing_marks:
            equity = self.realized_pnl + self.unrealized_pnl
            if equity > self.equity_peak:
                self.equity_peak = equity
            else:
                self.max_drawdown = max(self.max_drawdown, self.equity_peak - equity)

        symbol_count = len(inventory)
        legacy_total_inventory = None
        legacy_avg_inventory = None
        legacy_inventory_stdev = None
        legacy_avg_spread = None
        if symbol_count == 1:
            symbol = next(iter(inventory))
            legacy_total_inventory = inventory[symbol]["base_qty"]
            stats = self._inventory_stats.get(symbol, _RunningStat())
            legacy_avg_inventory = float(stats.mean)
            legacy_inventory_stdev = float(stats.stdev())
            qty = self._spread_capture_qty_by_symbol.get(symbol, ZERO)
            legacy_avg_spread = float(self._spread_capture_pnl_by_symbol[symbol] / qty) if qty > 0 else 0.0

        fill_metrics = self._fill_metrics()
        gross_realized = self.realized_pnl + self.total_fees
        net_total = self.realized_pnl + self.unrealized_pnl
        gross_total = gross_realized + self.unrealized_pnl
        valuation_complete = not missing_marks
        return {
            # `total_pnl` is retained as the fee-adjusted legacy name, but is
            # unavailable when any open position lacks a valid midpoint.
            "total_pnl": float(net_total) if valuation_complete else None,
            "net_total_pnl": float(net_total) if valuation_complete else None,
            "gross_total_pnl_before_fees": float(gross_total) if valuation_complete else None,
            "realized_pnl": float(self.realized_pnl),
            "gross_realized_pnl_before_fees": float(gross_realized),
            "unrealized_pnl": float(self.unrealized_pnl) if valuation_complete else None,
            "known_marked_unrealized_pnl": float(self.unrealized_pnl),
            "max_drawdown": float(self.max_drawdown),
            "fill_count": self.fill_count,
            "avg_spread_captured": legacy_avg_spread,
            "avg_inventory": legacy_avg_inventory,
            "inventory_stdev": legacy_inventory_stdev,
            "total_fees": float(self.total_fees),
            "fee_pnl_contribution": float(-self.total_fees),
            "fills": list(self.fills_log),
            # Legacy scalar remains meaningful only for a single-symbol run.
            "total_inventory": legacy_total_inventory,
            "quote_count": self.quote_count,
            "inventory_by_symbol": inventory,
            "inventory_quote_notional_by_symbol": {
                symbol: values["signed_quote_notional"] for symbol, values in inventory.items()
            },
            "fill_metrics": fill_metrics,
            "spread_capture_by_symbol": self._spread_capture_summary(),
            "markouts": self._markout_summary(),
            "valuation": {
                "complete": valuation_complete,
                "missing_mark_symbols": sorted(missing_marks),
                "policy": "aggregate unrealized and total PnL are null when an open position lacks a mark",
            },
            "simulation_assumptions": {
                "fill_model": self.cfg.sim_fill_model,
                "markout_horizons_ms": list(self.cfg.sim_markout_horizons_ms),
            },
        }
