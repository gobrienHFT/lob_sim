from __future__ import annotations

from dataclasses import dataclass, replace
from heapq import heappush, heappop
from itertools import count
from pathlib import Path
from typing import Any, Dict

import csv
import json

from ..book.local_book import LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import DepthUpdateEvent, LevelChange, SymbolSpec
from ..config import Config
from ..replay.normalization import (
    agg_trade_from_record,
    depth_update_from_record,
    instrument_spec_from_record,
    snapshot_from_record,
)
from ..replay.reader import RecordedEvent, iter_records
from ..util import write_summary_csv
from .fill_model import PassiveFillModel
from .metrics import SimulationMetrics
from .mm_strategy import MarketMakingStrategy, QuoteTarget
from .orders import Order
from .run_manifest import build_run_manifest

EVENT_TRACE_FIELDS = [
    "ts_local",
    "seq",
    "symbol",
    "event_type",
    "source",
    "side",
    "quote_slot",
    "price_tick",
    "qty_lots",
    "order_id",
    "fill_source",
    "details",
]


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
        self._trace_counter = count()
        self.event_trace: list[dict[str, Any]] = []
        self._trading_halted = False
        self._pending_cancel_ack_ts: dict[str, float] = {}
        self._pending_replacement_slots: set[tuple[str, str, str]] = set()
        self._symbol_time_watermark: Dict[str, float] = {}

    def _schedule(self, ts: float, kind: str, symbol: str, payload: Dict[str, Any]) -> None:
        heappush(self._actions, _EngineEvent(ts=ts, order=next(self._id_counter), kind=kind, symbol=symbol, payload=payload))

    def _trace(
        self,
        ts: float,
        symbol: str,
        event_type: str,
        source: str,
        *,
        side: str | None = None,
        quote_slot: str | None = None,
        price_tick: int | None = None,
        qty_lots: int | None = None,
        order_id: str | None = None,
        fill_source: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.event_trace.append(
            {
                "ts_local": ts,
                "seq": next(self._trace_counter),
                "symbol": symbol,
                "event_type": event_type,
                "source": source,
                "side": side,
                "quote_slot": quote_slot,
                "price_tick": price_tick,
                "qty_lots": qty_lots,
                "order_id": order_id,
                "fill_source": fill_source,
                "details": details or {},
            }
        )

    def _trace_market_record(self, rec: RecordedEvent) -> None:
        details: dict[str, Any] = {"record_type": rec.type}
        if rec.type == "exchangeInfo":
            details.update(
                {
                    "tick_size": rec.data.get("tickSize"),
                    "step_size": rec.data.get("stepSize"),
                    "base_asset": rec.data.get("baseAsset"),
                    "quote_asset": rec.data.get("quoteAsset"),
                }
            )
        elif rec.type == "snapshot":
            details.update(
                {
                    "last_update_id": rec.data.get("lastUpdateId"),
                    "bid_levels": len(rec.data.get("bids", [])),
                    "ask_levels": len(rec.data.get("asks", [])),
                }
            )
        elif rec.type == "depthUpdate":
            details.update(
                {
                    "first_update_id": rec.data.get("U"),
                    "final_update_id": rec.data.get("u"),
                    "prev_update_id": rec.data.get("pu"),
                    "bid_updates": len(rec.data.get("b", [])),
                    "ask_updates": len(rec.data.get("a", [])),
                }
            )
        elif rec.type == "aggTrade":
            details.update(
                {
                    "price": rec.data.get("p"),
                    "qty": rec.data.get("q"),
                    "buyer_is_maker": rec.data.get("m"),
                }
            )
        self._trace(float(rec.ts_local), rec.symbol, "market_record", rec.type, details=details)

    def _trace_book_gap(self, ts: float, event: DepthUpdateEvent) -> None:
        self._trace(
            ts,
            event.symbol,
            "book_gap",
            "book_sync",
            details={
                "first_update_id": event.first_update_id,
                "final_update_id": event.final_update_id,
                "prev_update_id": event.prev_update_id,
                "resync_on_gap": self.cfg.resync_on_gap,
            },
        )

    def _verbose(self, enabled: bool, message: str) -> None:
        if enabled:
            print(message, flush=True)

    def _emit_trade_event(self, ts: float, symbol: str, fills: list) -> None:
        if not fills:
            return
        self._schedule(ts, "trade_execution", symbol, {"fills": fills})

    def _slot_key(self, symbol: str, side: str, quote_slot: str) -> tuple[str, str, str]:
        return (symbol, side, quote_slot)

    def _request_cancel(
        self,
        ts: float,
        symbol: str,
        order: Order,
        *,
        reason: str = "unspecified",
        details: dict[str, Any] | None = None,
    ) -> float:
        pending_ts = self._pending_cancel_ack_ts.get(order.order_id)
        if pending_ts is not None:
            return pending_ts

        ack_ts = ts + self.cfg.sim_cancel_latency_ms / 1000.0
        self._pending_cancel_ack_ts[order.order_id] = ack_ts
        self.metrics.on_cancel_requested()
        self._schedule(
            ack_ts,
            "order_cancel",
            symbol,
            {"order_id": order.order_id},
        )
        trace_details: dict[str, Any] = {
            "ack_ts": ack_ts,
            "cancel_latency_ms": self.cfg.sim_cancel_latency_ms,
            "reason": reason,
        }
        if details:
            trace_details.update(details)
        self._trace(
            ts,
            symbol,
            "cancel_requested",
            "engine",
            side=order.side,
            quote_slot=order.quote_slot,
            price_tick=order.price_tick,
            qty_lots=order.remaining_lots,
            order_id=order.order_id,
            details=trace_details,
        )
        return ack_ts

    def _schedule_decisions_up_to(self, symbol: str, now: float, *, include_now: bool) -> None:
        if self._trading_halted:
            return
        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return
        interval = self.cfg.mm_requote_ms / 1000.0
        next_due = self._next_decision.get(symbol)
        if next_due is None:
            next_due = now
        epsilon = 1e-12

        def due_for_this_phase(ts: float) -> bool:
            if include_now:
                return ts <= now + epsilon
            return ts < now - epsilon

        while due_for_this_phase(next_due):
            self._schedule(next_due, "decision", symbol, {})
            next_due += interval
        self._next_decision[symbol] = next_due

    def _parse_exchange_info(self, rec: RecordedEvent) -> SymbolSpec:
        spec = instrument_spec_from_record(rec)
        if spec is None:
            raise ValueError(f"Expected exchangeInfo record for {rec.symbol}")
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
        self._pending_cancel_ack_ts.clear()
        self._pending_replacement_slots.clear()

    def _handle_decision(self, symbol: str, ts: float) -> None:
        if self._trading_halted or not self.cfg.mm_enabled:
            return

        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return

        inventory = book.spec.lot_to_qty(self.metrics.inventory_lots(symbol))
        plan = self.strategy.propose(book, inventory_qty=inventory)
        if not plan.quotes:
            return
        self._trace(
            ts,
            symbol,
            "decision",
            "strategy",
            details={
                "inventory_qty": str(inventory),
                "quote_count": len(plan.quotes),
                "strategy_profile": self.cfg.mm_strategy_profile,
                "quotes": [
                    {
                        "side": quote.side,
                        "quote_slot": quote.quote_slot,
                        "price_tick": quote.price_tick,
                        "qty_lots": quote.qty_lots,
                        "refresh_key": quote.refresh_key,
                    }
                    for quote in plan.quotes
                ],
            },
        )

        desired_by_side: dict[str, dict[str, QuoteTarget]] = {"bid": {}, "ask": {}}
        for target in plan.quotes:
            desired_by_side[target.side][target.quote_slot] = target

        for side in ("bid", "ask"):
            desired_targets = desired_by_side[side]
            existing_orders = {
                order.quote_slot: order for order in self.fill_model.get_orders(symbol, side)
            }
            if side == "bid" and inventory > self.cfg.mm_max_position:
                for existing in existing_orders.values():
                    self._request_cancel(
                        ts,
                        symbol,
                        existing,
                        reason="position_limit",
                        details={
                            "inventory_qty": str(inventory),
                            "max_position": str(self.cfg.mm_max_position),
                        },
                    )
                continue
            if side == "ask" and inventory < -self.cfg.mm_max_position:
                for existing in existing_orders.values():
                    self._request_cancel(
                        ts,
                        symbol,
                        existing,
                        reason="position_limit",
                        details={
                            "inventory_qty": str(inventory),
                            "max_position": str(self.cfg.mm_max_position),
                        },
                    )
                continue

            for slot, existing in existing_orders.items():
                if slot in desired_targets:
                    continue
                self._request_cancel(ts, symbol, existing, reason="stale_slot")

            for slot, target in desired_targets.items():
                existing = existing_orders.get(slot)
                slot_key = self._slot_key(symbol, side, slot)
                replacement_ack_ts: float | None = None
                replacement_pending = slot_key in self._pending_replacement_slots
                if existing is None and replacement_pending:
                    continue
                observed_queue_ahead_lots = 0
                strategy_existing = existing
                if existing is not None:
                    observed_queue_ahead_lots = self.fill_model.queue_ahead_lots(symbol, existing)
                    strategy_existing = replace(existing, queue_ahead_lots=observed_queue_ahead_lots)
                    pending_cancel_ack_ts = self._pending_cancel_ack_ts.get(existing.order_id)
                else:
                    pending_cancel_ack_ts = None
                refresh = self.strategy.should_refresh(target, strategy_existing)
                if existing is not None and (
                    existing.price_tick != target.price_tick
                    or existing.qty_lots != target.qty_lots
                    or refresh
                    or pending_cancel_ack_ts is not None
                ):
                    replacement_ack_ts = self._request_cancel(
                        ts,
                        symbol,
                        existing,
                        reason="replace_quote",
                        details={
                            "target_price_tick": target.price_tick,
                            "target_qty_lots": target.qty_lots,
                            "target_refresh_key": target.refresh_key,
                            "current_refresh_key": existing.refresh_key,
                            "queue_ahead_lots": observed_queue_ahead_lots,
                            "price_changed": existing.price_tick != target.price_tick,
                            "qty_changed": existing.qty_lots != target.qty_lots,
                            "refresh_requested": refresh,
                            "pending_cancel": pending_cancel_ack_ts is not None,
                        },
                    )
                    existing = None
                    if replacement_pending:
                        continue
                    self._pending_replacement_slots.add(slot_key)

                if existing is not None and existing.price_tick == target.price_tick and existing.qty_lots == target.qty_lots:
                    continue

                arrival_ts = ts + self.cfg.sim_order_latency_ms / 1000.0
                if replacement_ack_ts is not None:
                    arrival_ts = replacement_ack_ts + self.cfg.sim_order_latency_ms / 1000.0
                self._schedule(
                    arrival_ts,
                    "order_arrival",
                    symbol,
                    {
                        "side": side,
                        "quote_slot": slot,
                        "price_tick": target.price_tick,
                        "qty_lots": target.qty_lots,
                        "refresh_key": target.refresh_key,
                    },
                )
                self._trace(
                    ts,
                    symbol,
                    "order_arrival_scheduled",
                    "engine",
                    side=side,
                    quote_slot=slot,
                    price_tick=target.price_tick,
                    qty_lots=target.qty_lots,
                    details={
                        "arrival_ts": arrival_ts,
                        "order_latency_ms": self.cfg.sim_order_latency_ms,
                        "cancel_ack_ts": replacement_ack_ts,
                    },
                )

    def _handle_arrival(self, symbol: str, payload: Dict[str, Any], now: float) -> None:
        side = payload["side"]
        quote_slot = str(payload.get("quote_slot", "base"))
        self._pending_replacement_slots.discard(self._slot_key(symbol, side, quote_slot))
        if self._trading_halted:
            return

        price_tick = int(payload["price_tick"])
        qty_lots = int(payload["qty_lots"])
        refresh_key = str(payload.get("refresh_key", ""))
        book = self._books.get(symbol)
        if book is None or qty_lots <= 0:
            return

        order = Order(
            order_id=f"{symbol}-{side}-{int(now * 1_000_000)}-{next(self._id_counter)}",
            symbol=symbol,
            side=side,
            price_tick=price_tick,
            qty_lots=qty_lots,
            quote_slot=quote_slot,
            queue_ahead_lots=0,
            created_ts=now,
            remaining_lots=qty_lots,
            refresh_key=refresh_key,
        )
        fills = self.fill_model.place_order(order)
        resting_order = self.fill_model.get_order(symbol, side, quote_slot)
        resting_after_arrival = resting_order is not None and resting_order.order_id == order.order_id
        queue_ahead_after_arrival = (
            self.fill_model.queue_ahead_lots(symbol, resting_order) if resting_after_arrival else 0
        )
        if fills:
            self._emit_trade_event(now, symbol, fills)
        self.metrics.on_quote_requested()
        arrival_details = {
            "refresh_key": refresh_key,
            "remaining_lots_after_arrival": order.remaining_lots,
            "resting_after_arrival": resting_after_arrival,
            "queue_ahead_lots_after_arrival": queue_ahead_after_arrival,
            "immediate_fills": len(fills),
        }
        if self.fill_model.last_self_trade_prevented:
            arrival_details["self_trade_prevented"] = True
        self._trace(
            now,
            symbol,
            "order_arrival",
            "engine",
            side=side,
            quote_slot=quote_slot,
            price_tick=price_tick,
            qty_lots=qty_lots,
            order_id=order.order_id,
            details=arrival_details,
        )

    def _handle_cancel(self, payload: Dict[str, Any], now: float, symbol: str) -> None:
        order_id = payload.get("order_id")
        if order_id is None:
            return
        self._pending_cancel_ack_ts.pop(str(order_id), None)
        self.fill_model.cancel_order(str(order_id))
        self._trace(now, symbol, "cancel_ack", "engine", order_id=str(order_id))

    def _handle_trades(self, fills: list) -> None:
        for fill in fills:
            book = self._books.get(fill.symbol)
            if book is None:
                continue
            self.metrics.on_fill(fill, book, book.mid_price())
            self._trace(
                fill.ts_local,
                fill.symbol,
                "fill",
                "fill_model",
                side=fill.side,
                price_tick=fill.price_tick,
                qty_lots=fill.qty_lots,
                order_id=fill.order_id,
                fill_source=fill.source,
                details={
                    "maker": fill.maker,
                    "queue_ahead_lots": fill.queue_ahead_lots,
                    "created_ts": fill.created_ts,
                },
            )

    def _drain_events(self, now: float) -> None:
        while self._actions and self._actions[0].ts <= now:
            event = heappop(self._actions)
            if event.kind == "decision":
                self._handle_decision(event.symbol, event.ts)
            elif event.kind == "order_arrival":
                self._handle_arrival(event.symbol, event.payload, event.ts)
            elif event.kind == "order_cancel":
                self._handle_cancel(event.payload, event.ts, event.symbol)
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
            self.metrics.on_record(rec.type)
            now = float(rec.ts_local)
            if now > last_ts:
                last_ts = now
            symbol_now = max(now, self._symbol_time_watermark.get(rec.symbol, now))
            self._symbol_time_watermark[rec.symbol] = symbol_now

            self._drain_events(now)
            self._trace_market_record(rec)
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

            self._schedule_decisions_up_to(rec.symbol, symbol_now, include_now=False)
            self._drain_events(now)

            if rec.type == "snapshot":
                spec = self._specs[rec.symbol]
                snapshot = snapshot_from_record(rec, spec)
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

                event = depth_update_from_record(rec, spec)
                gap_count_before = syncer.gap_count
                try:
                    changes: list[LevelChange] = syncer.on_depth_update(event)
                except BookSyncGapError:
                    self.metrics.on_book_gap(rec.symbol)
                    self._trace_book_gap(now, event)
                    if self.cfg.resync_on_gap:
                        continue
                    changes = []
                else:
                    if syncer.gap_count > gap_count_before:
                        self.metrics.on_book_gap(rec.symbol)
                        self._trace_book_gap(now, event)

                self.metrics.on_depth_changes(len(changes))
                if changes:
                    fills = self.fill_model.apply_depth_changes(rec.symbol, changes, now)
                    if fills:
                        self._emit_trade_event(now, rec.symbol, fills)

            if rec.type == "aggTrade":
                spec = self._specs[rec.symbol]
                trade = agg_trade_from_record(rec, spec)
                self.strategy.observe_trade(trade)
                fills = self.fill_model.apply_agg_trade(trade, now)
                if fills:
                    self._emit_trade_event(now, rec.symbol, fills)

            self._schedule_decisions_up_to(rec.symbol, symbol_now, include_now=True)
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

    def _write_event_trace(self, path: Path) -> None:
        def _cell(value: Any) -> Any:
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, sort_keys=True, default=str)
            return value

        with path.open("w", encoding="utf-8", newline="") as trace_file:
            writer = csv.DictWriter(trace_file, fieldnames=EVENT_TRACE_FIELDS)
            writer.writeheader()
            rows = sorted(self.event_trace, key=lambda row: (float(row["ts_local"]), int(row["seq"])))
            for export_seq, row in enumerate(rows):
                export_row = {**row, "seq": export_seq}
                writer.writerow({field: _cell(export_row.get(field)) for field in EVENT_TRACE_FIELDS})

    def write_outputs(self, file_path: str, metrics: SimulationMetrics) -> tuple[dict[str, Path], dict]:
        summary = metrics.get_summary(self._books)
        output_dir = self.cfg.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(file_path).stem.replace(".ndjson", "")
        summary_path = output_dir / f"summary_{stem}.json"
        summary_csv_path = output_dir / f"summary_{stem}.csv"
        trades_path = output_dir / f"trades_{stem}.csv"
        event_trace_path = output_dir / f"event_trace_{stem}.csv"
        manifest_path = output_dir / f"manifest_{stem}.json"
        output_files = {
            "event_trace": event_trace_path,
            "summary": summary_path,
            "summary_csv": summary_csv_path,
            "trades": trades_path,
            "manifest": manifest_path,
        }
        manifest_seed = build_run_manifest(file_path, self.cfg, output_files)
        summary["run_id"] = manifest_seed.run_id
        summary["input_sha256"] = manifest_seed.input["sha256"]
        summary["output_files"] = {name: str(path) for name, path in output_files.items()}
        summary["event_trace_count"] = len(self.event_trace)

        with open(summary_path, "w", encoding="utf-8") as fh:
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
                    "fill_source",
                    "fee_bps",
                    "fee",
                    "fee_currency",
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
        self._write_event_trace(event_trace_path)

        manifest = build_run_manifest(
            file_path,
            self.cfg,
            output_files,
            created_at_utc=manifest_seed.created_at_utc,
            source=manifest_seed.source,
        )
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest.as_dict(), fh, indent=2)
        return output_files, summary
