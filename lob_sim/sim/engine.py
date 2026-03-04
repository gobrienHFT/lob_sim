from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
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
from .fill_model import PassiveFillModel
from .metrics import SimulationMetrics
from .mm_strategy import compute_quotes
from .orders import Order


@dataclass(order=True)
class _Action:
    ts: float
    order: int
    kind: str
    symbol: str
    data: Dict[str, Any]


class SimulationEngine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.metrics = SimulationMetrics(cfg)
        self.fill_model = PassiveFillModel()
        self._specs: Dict[str, SymbolSpec] = {}
        self._books: Dict[str, LocalOrderBook] = {}
        self._syncers: Dict[str, BookSynchronizer] = {}
        self._next_decision: Dict[str, float] = {}
        self._actions: list[_Action] = []
        self._id_counter = count()

    def _schedule(self, ts: float, kind: str, symbol: str, data: Dict[str, Any]) -> None:
        heappush(self._actions, _Action(ts=ts, order=next(self._id_counter), kind=kind, symbol=symbol, data=data))

    def _schedule_decisions_up_to(self, symbol: str, now: float) -> None:
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
        return self._books.get(symbol)

    def _get_sync(self, symbol: str) -> BookSynchronizer | None:
        book = self._get_or_create_book(symbol)
        if book is None:
            return None
        return self._syncers[symbol]

    def _handle_decision(self, symbol: str, ts: float) -> None:
        if not self.cfg.mm_enabled:
            return
        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return

        inventory = book.spec.lot_to_qty(self.metrics.inventory_lots(symbol))
        quote_tuple = compute_quotes(book, inventory, self.cfg)
        if quote_tuple is None:
            return
        bid_tick, ask_tick = quote_tuple
        qty_lots = max(1, book.spec.qty_to_lot(self.cfg.mm_order_qty))

        for side in ("bid", "ask"):
            if side == "bid" and inventory > self.cfg.mm_max_position:
                continue
            if side == "ask" and inventory < -self.cfg.mm_max_position:
                continue

            desired_price = bid_tick if side == "bid" else ask_tick
            existing = self.fill_model.get_order(symbol, side)
            if existing is not None:
                self._schedule(ts + self.cfg.sim_cancel_latency_ms / 1000.0, "cancel", symbol, {"order_id": existing.order_id})

            self._schedule(
                ts + self.cfg.sim_order_latency_ms / 1000.0,
                "place",
                symbol,
                {"side": side, "price_tick": desired_price, "qty_lots": qty_lots},
            )

    def _handle_place(self, symbol: str, payload: Dict[str, Any], now: float) -> None:
        side = payload["side"]
        price_tick = int(payload["price_tick"])
        qty_lots = int(payload["qty_lots"])
        book = self._books.get(symbol)
        if book is None or qty_lots <= 0:
            return

        inventory = book.spec.lot_to_qty(self.metrics.inventory_lots(symbol))
        if side == "bid" and inventory > self.cfg.mm_max_position:
            return
        if side == "ask" and inventory < -self.cfg.mm_max_position:
            return

        queue_side = "bids" if side == "bid" else "asks"
        queue_ahead = book.get_level_size(queue_side, price_tick)
        order = Order(
            order_id=f"{symbol}-{side}-{int(now*1e6)}-{next(self._id_counter)}",
            symbol=symbol,
            side=side,
            price_tick=price_tick,
            qty_lots=qty_lots,
            queue_ahead_lots=queue_ahead,
            created_ts=now,
            remaining_lots=qty_lots,
        )
        self.fill_model.place_order(order)
        self.metrics.on_quote_requested()

    def _handle_cancel(self, payload: Dict[str, Any]) -> None:
        order_id = payload.get("order_id")
        if order_id is None:
            return
        self.fill_model.cancel_order(str(order_id))

    def _drain_actions(self, now: float) -> None:
        while self._actions and self._actions[0].ts <= now:
            action = heappop(self._actions)
            if action.kind == "decision":
                self._handle_decision(action.symbol, action.ts)
            elif action.kind == "place":
                self._handle_place(action.symbol, action.data, action.ts)
            elif action.kind == "cancel":
                self._handle_cancel(action.data)

    def _on_fills(self, fills: list):
        for fill in fills:
            book = self._books.get(fill.symbol)
            if book is None:
                continue
            self.metrics.on_fill(fill, book, book.mid_price())

    def run(self, file_path: str | Path) -> SimulationMetrics:
        last_ts = 0.0
        for rec in iter_records(file_path):
            now = float(rec.ts_local)
            if now > last_ts:
                last_ts = now
            self._drain_actions(now)

            if rec.type == "exchangeInfo":
                spec = self._parse_exchange_info(rec)
                self._get_or_create_book(rec.symbol)
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
                if syncer:
                    syncer.on_snapshot(snapshot)
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
                        # Replay file should include a newer snapshot to continue.
                        # In this project scope, we report the first gap as unrecoverable.
                        continue
                    changes = []
                if changes:
                    fills = self.fill_model.apply_depth_changes(rec.symbol, changes, now)
                    self._on_fills(fills)

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
                self._on_fills(fills)

            self.metrics.update_unrealized(self._books)

        final_ts = last_ts + max(
            self.cfg.mm_requote_ms / 1000.0,
            max(self.cfg.sim_order_latency_ms, self.cfg.sim_cancel_latency_ms) / 1000.0,
        )
        self._drain_actions(final_ts)
        self.metrics.update_unrealized(self._books)
        return self.metrics

    def write_outputs(self, file_path: str, metrics: SimulationMetrics) -> tuple[Path, Path, dict]:
        summary = metrics.get_summary(self._books)
        output_dir = self.cfg.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(file_path).stem.replace(".ndjson", "")
        summary_path = output_dir / f"summary_{stem}.json"
        trades_path = output_dir / f"trades_{stem}.csv"

        with open(summary_path, "w", encoding="utf-8") as fh:
            import json

            json.dump(summary, fh, indent=2)

        with open(trades_path, "w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["ts_local", "symbol", "side", "price", "qty", "maker", "order_id"],
            )
            writer.writeheader()
            for row in summary.get("fills", []):
                writer.writerow(row)
        return summary_path, trades_path, summary
