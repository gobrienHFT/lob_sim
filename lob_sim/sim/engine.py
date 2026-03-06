from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from heapq import heappush, heappop
from itertools import count
from pathlib import Path
from typing import Any, Dict

import csv

from ..book.local_book import LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import AggTradeEvent, DepthUpdateEvent, LevelChange, SnapshotEvent, SymbolSpec
from ..config import Config
from ..replay.reader import RecordedEvent, iter_records
from ..util import write_summary_csv
from .fill_model import PassiveFillModel
from .metrics import SimulationMetrics
from .mm_strategy import MarketMakingStrategy
from .orders import Order


@dataclass(order=True)
class _EngineEvent:
    ts: float
    order: int
    kind: str
    symbol: str
    payload: Dict[str, Any]


class SimulationEngine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.metrics = SimulationMetrics(cfg)
        self.fill_model = PassiveFillModel()
        self.strategy = MarketMakingStrategy(cfg)
        self._specs: Dict[str, SymbolSpec] = {}
        self._books: Dict[str, LocalOrderBook] = {}
        self._syncers: Dict[str, BookSynchronizer] = {}
        self._next_decision: Dict[str, float] = {}
        self._actions: list[_EngineEvent] = []
        self._id_counter = count()
        self._trading_halted = False

    def _schedule(self, ts: float, kind: str, symbol: str, payload: Dict[str, Any]) -> None:
        heappush(self._actions, _EngineEvent(ts=ts, order=next(self._id_counter), kind=kind, symbol=symbol, payload=payload))

    def _verbose(self, enabled: bool, message: str) -> None:
        if enabled:
            print(message, flush=True)

    def _emit_trade_event(self, ts: float, symbol: str, fills: list) -> None:
        if not fills:
            return
        self._schedule(ts, "trade_execution", symbol, {"fills": fills})

    def _schedule_decisions_up_to(self, symbol: str, now: float) -> None:
        if self._trading_halted:
            return
        interval = self.cfg.mm_requote_ms / 1000.0
        next_due = self._next_decision.get(symbol)
        if next_due is None:
            next_due = now
        while next_due <= now:
            self._schedule(next_due, "decision", symbol, {})
            next_due += interval
        self._next_decision[symbol] = next_due

    def _parse_exchange_info(self, rec: RecordedEvent) -> SymbolSpec:
        data = rec.data
        tick_size = str(data["tickSize"])
        step_size = str(data["stepSize"])
        spec = SymbolSpec(symbol=rec.symbol, tick_size=Decimal(tick_size), step_size=Decimal(step_size))
        self._specs[rec.symbol] = spec
        return spec

    def _get_or_create_book(self, symbol: str) -> LocalOrderBook | None:
        spec = self._specs.get(symbol)
        if spec is None:
            return None
        if symbol not in self._books:
            self._books[symbol] = LocalOrderBook(symbol=symbol, spec=spec, top_n=self.cfg.book_top_n)
            self._syncers[symbol] = BookSynchronizer(self._books[symbol], resync_on_gap=self.cfg.resync_on_gap)
            self.metrics.register_symbol(symbol)
            # seed both strategy and matching engine books with initial empty levels
        return self._books.get(symbol)

    def _get_sync(self, symbol: str) -> BookSynchronizer | None:
        book = self._get_or_create_book(symbol)
        if book is None:
            return None
        return self._syncers[symbol]

    def _disable_trading(self) -> None:
        self._trading_halted = True
        for symbol in list(self._books):
            self.fill_model.cancel_all_for_symbol_side(symbol, "bid")
            self.fill_model.cancel_all_for_symbol_side(symbol, "ask")

    def _handle_decision(self, symbol: str, ts: float) -> None:
        if self._trading_halted or not self.cfg.mm_enabled:
            return

        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return

        inventory = book.spec.lot_to_qty(self.metrics.inventory_lots(symbol))
        plan = self.strategy.propose(book, inventory_qty=inventory)
        if plan.bid_tick is None and plan.ask_tick is None:
            return

        quote_size = max(1, plan.size_lots)
        queue_positions = {
            "bid": self.fill_model.queue_ahead_lots(symbol, self.fill_model.get_order(symbol, "bid")),
            "ask": self.fill_model.queue_ahead_lots(symbol, self.fill_model.get_order(symbol, "ask")),
        }

        for side in ("bid", "ask"):
            existing = self.fill_model.get_order(symbol, side)
            if side == "bid" and inventory > self.cfg.mm_max_position:
                if existing is not None:
                    self._schedule(
                        ts + self.cfg.sim_cancel_latency_ms / 1000.0,
                        "order_cancel",
                        symbol,
                        {"order_id": existing.order_id},
                    )
                continue
            if side == "ask" and inventory < -self.cfg.mm_max_position:
                if existing is not None:
                    self._schedule(
                        ts + self.cfg.sim_cancel_latency_ms / 1000.0,
                        "order_cancel",
                        symbol,
                        {"order_id": existing.order_id},
                    )
                continue

            desired = plan.bid_tick if side == "bid" else plan.ask_tick
            if desired is None:
                continue

            refresh = self.strategy.should_refresh(book, side, existing)
            if queue_positions[side] >= self.cfg.mm_queue_repost_lots:
                refresh = True
            if existing is not None and (existing.price_tick != desired or refresh):
                self._schedule(
                    ts + self.cfg.sim_cancel_latency_ms / 1000.0,
                    "order_cancel",
                    symbol,
                    {"order_id": existing.order_id},
                )
                existing = None

            if existing is not None and existing.price_tick == desired:
                continue

            self._schedule(
                ts + self.cfg.sim_order_latency_ms / 1000.0,
                "order_arrival",
                symbol,
                    {"side": side, "price_tick": desired, "qty_lots": quote_size},
                )

    def _handle_arrival(self, symbol: str, payload: Dict[str, Any], now: float) -> None:
        if self._trading_halted:
            return

        side = payload["side"]
        price_tick = int(payload["price_tick"])
        qty_lots = int(payload["qty_lots"])
        book = self._books.get(symbol)
        if book is None or qty_lots <= 0:
            return

        order = Order(
            order_id=f"{symbol}-{side}-{int(now * 1_000_000)}-{next(self._id_counter)}",
            symbol=symbol,
            side=side,
            price_tick=price_tick,
            qty_lots=qty_lots,
            queue_ahead_lots=0,
            created_ts=now,
            remaining_lots=qty_lots,
        )
        fills = self.fill_model.place_order(order)
        if fills:
            self._emit_trade_event(now, symbol, fills)
        self.metrics.on_quote_requested()

    def _handle_cancel(self, payload: Dict[str, Any]) -> None:
        order_id = payload.get("order_id")
        if order_id is None:
            return
        self.fill_model.cancel_order(str(order_id))

    def _handle_trades(self, fills: list) -> None:
        for fill in fills:
            book = self._books.get(fill.symbol)
            if book is None:
                continue
            self.metrics.on_fill(fill, book, book.mid_price())

    def _drain_events(self, now: float) -> None:
        while self._actions and self._actions[0].ts <= now:
            event = heappop(self._actions)
            if event.kind == "decision":
                self._handle_decision(event.symbol, event.ts)
            elif event.kind == "order_arrival":
                self._handle_arrival(event.symbol, event.payload, event.ts)
            elif event.kind == "order_cancel":
                self._handle_cancel(event.payload)
            elif event.kind == "trade_execution":
                self._handle_trades(event.payload.get("fills", []))

    def run(
        self,
        file_path: str | Path,
        verbose: bool = False,
        progress_every: int = 5000,
    ) -> SimulationMetrics:
        last_ts = 0.0
        records_processed = 0
        self._verbose(verbose, f"[simulate] starting simulation for {file_path}")
        for rec in iter_records(file_path):
            records_processed += 1
            now = float(rec.ts_local)
            if now > last_ts:
                last_ts = now

            self._drain_events(now)
            if rec.type == "exchangeInfo":
                spec = self._parse_exchange_info(rec)
                self._get_or_create_book(rec.symbol)
                self._verbose(
                    verbose,
                    f"[simulate] loaded symbol={rec.symbol} tick_size={spec.tick_size} step_size={spec.step_size}",
                )
                continue

            if rec.symbol not in self._specs:
                continue

            self._schedule_decisions_up_to(rec.symbol, now)

            if rec.type == "snapshot":
                spec = self._specs[rec.symbol]
                snapshot = SnapshotEvent(
                    symbol=rec.symbol,
                    last_update_id=int(rec.data["lastUpdateId"]),
                    bids=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("bids", [])],
                    asks=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("asks", [])],
                )
                syncer = self._get_sync(rec.symbol)
                if syncer is None:
                    continue
                syncer.on_snapshot(snapshot)
                self.fill_model.seed_from_snapshot(
                    rec.symbol,
                    snapshot.bids,
                    snapshot.asks,
                )
                self._verbose(
                    verbose,
                    f"[simulate] snapshot synced for {rec.symbol} bids={len(snapshot.bids)} asks={len(snapshot.asks)}",
                )
                continue

            if rec.type == "depthUpdate":
                spec = self._specs[rec.symbol]
                syncer = self._get_sync(rec.symbol)
                if syncer is None:
                    continue

                event = DepthUpdateEvent(
                    symbol=rec.symbol,
                    first_update_id=int(rec.data["U"]),
                    final_update_id=int(rec.data["u"]),
                    prev_update_id=int(rec.data.get("pu", rec.data["U"])),
                    bids=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("b", [])],
                    asks=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("a", [])],
                    ts_local=now,
                )
                try:
                    changes: list[LevelChange] = syncer.on_depth_update(event)
                except BookSyncGapError:
                    if self.cfg.resync_on_gap:
                        continue
                    changes = []

                if changes:
                    fills = self.fill_model.apply_depth_changes(rec.symbol, changes, now)
                    if fills:
                        self._emit_trade_event(now, rec.symbol, fills)

            if rec.type == "aggTrade":
                spec = self._specs[rec.symbol]
                trade = AggTradeEvent(
                    symbol=rec.symbol,
                    price_tick=spec.price_to_tick(rec.data["p"]),
                    qty_lots=spec.qty_to_lot(rec.data["q"]),
                    buyer_is_maker=bool(rec.data["m"]),
                    ts_local=now,
                )
                fills = self.fill_model.apply_agg_trade(trade, now)
                if fills:
                    self._emit_trade_event(now, rec.symbol, fills)

            self._drain_events(now)
            if self._books:
                self.metrics.update_unrealized(self._books, now_ts=now)
            if self.metrics.kill_switch_triggered and not self._trading_halted:
                self._disable_trading()
                self._verbose(
                    verbose,
                    f"[simulate] kill switch triggered: {self.metrics.kill_switch_reason}",
                )

            if verbose and progress_every > 0 and records_processed % progress_every == 0:
                total_pnl = float(self.metrics.realized_pnl + self.metrics.unrealized_pnl)
                self._verbose(
                    verbose,
                    f"[simulate] records={records_processed} fills={self.metrics.fill_count} "
                    f"quotes={self.metrics.quote_count} pnl={total_pnl:.6f} pending_events={len(self._actions)} "
                    f"last={rec.symbol}:{rec.type}",
                )

        final_ts = last_ts + max(
            self.cfg.mm_requote_ms / 1000.0,
            max(self.cfg.sim_order_latency_ms, self.cfg.sim_cancel_latency_ms) / 1000.0,
            self.cfg.sim_adverse_markout_seconds,
            1.0,
        )
        self._drain_events(final_ts)
        self.metrics.update_unrealized(self._books, now_ts=final_ts)
        if self.metrics.kill_switch_triggered and not self._trading_halted:
            self._disable_trading()
            self._verbose(
                verbose,
                f"[simulate] kill switch triggered at shutdown: {self.metrics.kill_switch_reason}",
            )
        self._verbose(
            verbose,
            f"[simulate] completed records={records_processed} fills={self.metrics.fill_count} "
            f"quotes={self.metrics.quote_count}",
        )
        return self.metrics

    def write_outputs(self, file_path: str, metrics: SimulationMetrics) -> tuple[dict[str, Path], dict]:
        summary = metrics.get_summary(self._books)
        output_dir = self.cfg.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(file_path).stem.replace(".ndjson", "")
        summary_path = output_dir / f"summary_{stem}.json"
        summary_csv_path = output_dir / f"summary_{stem}.csv"
        trades_path = output_dir / f"trades_{stem}.csv"
        output_files = {
            "summary": summary_path,
            "summary_csv": summary_csv_path,
            "trades": trades_path,
        }
        summary["output_files"] = {name: str(path) for name, path in output_files.items()}

        with open(summary_path, "w", encoding="utf-8") as fh:
            import json

            json.dump(summary, fh, indent=2)
        write_summary_csv(summary_csv_path, summary, exclude_keys={"fills", "markout_events"})

        with open(trades_path, "w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "ts_local",
                    "symbol",
                    "side",
                    "price",
                    "qty",
                    "maker",
                    "order_id",
                    "mid_at_fill",
                    "regime",
                    "queue_ahead_lots",
                    "time_in_book_ms",
                    "markout_horizon",
                    "book_bid_tick",
                    "book_ask_tick",
                ],
            )
            writer.writeheader()
            for row in summary.get("fills", []):
                writer.writerow(row)
        return output_files, summary
