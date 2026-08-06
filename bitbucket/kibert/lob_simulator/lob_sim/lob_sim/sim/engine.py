from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from heapq import heappop, heappush
from itertools import count
from pathlib import Path
from typing import Any

from ..book.local_book import LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import AggTradeEvent, DepthUpdateEvent, LevelChange, SnapshotEvent, SymbolSpec
from ..config import Config
from ..provenance import build_run_provenance
from ..replay.reader import RecordedEvent, iter_records
from .fill_model import DuplicateActiveOrderError, PassiveFillModel
from .metrics import SimulationMetrics
from .mm_strategy import compute_quotes
from .orders import Fill, Order, OrderState


class ReplayClockError(RuntimeError):
    """Raised when a recording cannot be replayed without ambiguous time travel."""


class CaptureIntegrityError(RuntimeError):
    """Raised when capture metadata contradicts file order or sync epochs."""


@dataclass(order=True)
class _Action:
    ts: float
    sequence: int
    kind: str
    symbol: str
    data: dict[str, Any]


class SimulationEngine:
    """Deterministic market-by-price replay with an explicit causal tie policy.

    File order is the observation order.  Before each market record, venue and
    strategy actions strictly earlier than that record are drained against the
    previous book.  The market record is then applied, followed by actions at
    the same timestamp.  This conservative tie-break prevents an order from
    filling on a market event that arrived at the same coarse timestamp as its
    acknowledgement.
    """

    _MAX_TIMER_CATCHUP = 100_000

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.metrics = SimulationMetrics(cfg)
        self.fill_model = PassiveFillModel(getattr(cfg, "sim_fill_model", "trade"))
        self._specs: dict[str, SymbolSpec] = {}
        self._books: dict[str, LocalOrderBook] = {}
        self._syncers: dict[str, BookSynchronizer] = {}
        self._next_decision: dict[str, float] = {}
        self._quote_revision: dict[tuple[str, str], int] = {}
        self._actions: list[_Action] = []
        self._id_counter = count()
        self._last_clock: float | None = None
        self._capture_schema_version = 1
        self._receive_clock = False
        self._clock_regressions = 0
        self._gap_count = 0
        self._events_processed = 0
        self._stale_actions = 0
        self._post_only_rejects = 0
        self._capture_sync_epochs: dict[str, int] = {}
        self._last_receive_seq: int | None = None
        self._sync_epoch_transitions = 0
        self._snapshot_rejections = 0
        self._has_run = False

    def _metric(self, method: str, *args: Any, **kwargs: Any) -> None:
        callback = getattr(self.metrics, method, None)
        if callback is not None:
            callback(*args, **kwargs)

    @staticmethod
    def _seconds(value: Any) -> float:
        try:
            timestamp = float(value)
        except (TypeError, ValueError) as exc:
            raise ReplayClockError(f"Invalid timestamp: {value!r}") from exc
        if timestamp < 0:
            raise ReplayClockError(f"Negative timestamp: {timestamp}")
        if not math.isfinite(timestamp):
            raise ReplayClockError(f"Non-finite timestamp: {timestamp}")
        # Unix milliseconds are currently ~1e12; Unix seconds are ~1e9.
        if timestamp >= 100_000_000_000:
            timestamp /= 1000.0
        return timestamp

    def _event_time(self, rec: RecordedEvent) -> float:
        if self._receive_clock:
            candidate = self._seconds(rec.ts_local)
        else:
            # Legacy captures called exchange event time `ts_local`, and one
            # bundled capture even stored it in milliseconds. Prefer raw E.
            candidate = self._seconds(rec.data.get("E", rec.ts_local))

        if self._last_clock is not None and candidate < self._last_clock:
            self._clock_regressions += 1
            candidate = self._last_clock
        self._last_clock = candidate
        return candidate

    def _capture_fields(self, rec: RecordedEvent) -> dict[str, Any] | None:
        capture = rec.data.get("_capture")
        if capture is None:
            return None
        if not isinstance(capture, dict):
            raise CaptureIntegrityError(f"_capture must be an object for {rec.symbol} {rec.type}")
        return capture

    def _validate_receive_sequence(self, rec: RecordedEvent) -> None:
        capture = self._capture_fields(rec)
        if capture is None or capture.get("recvSeq") is None:
            return
        sequence = capture["recvSeq"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise CaptureIntegrityError(f"Invalid recvSeq for {rec.symbol} {rec.type}: {sequence!r}")
        if self._last_receive_seq is not None and sequence <= self._last_receive_seq:
            raise CaptureIntegrityError(
                f"recvSeq must increase in file order: previous={self._last_receive_seq}, current={sequence}"
            )
        self._last_receive_seq = sequence

    def _apply_capture_epoch(self, rec: RecordedEvent, now: float) -> None:
        capture = self._capture_fields(rec)
        if capture is None or capture.get("syncEpoch") is None:
            return
        epoch = capture["syncEpoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise CaptureIntegrityError(f"Invalid syncEpoch for {rec.symbol} {rec.type}: {epoch!r}")

        previous = self._capture_sync_epochs.get(rec.symbol)
        if previous is not None and epoch < previous:
            raise CaptureIntegrityError(
                f"syncEpoch regressed for {rec.symbol}: previous={previous}, current={epoch}"
            )
        self._capture_sync_epochs[rec.symbol] = epoch
        if previous is None or epoch == previous:
            return

        syncer = self._get_sync(rec.symbol)
        if syncer is not None:
            reason = str(capture.get("reason") or "capture_sync_epoch_transition")
            syncer.begin_resync(reason)
            self._invalidate_symbol(rec.symbol, now, reason)
            self._sync_epoch_transitions += 1

    def _schedule(self, ts: float, kind: str, symbol: str, data: dict[str, Any]) -> None:
        heappush(
            self._actions,
            _Action(ts=ts, sequence=next(self._id_counter), kind=kind, symbol=symbol, data=data),
        )

    def _schedule_decisions(self, symbol: str, now: float, *, inclusive: bool) -> None:
        interval = self.cfg.mm_requote_ms / 1000.0
        next_due = self._next_decision.get(symbol)
        if next_due is None:
            next_due = now

        scheduled = 0
        while next_due < now or (inclusive and next_due <= now):
            self._schedule(next_due, "decision", symbol, {})
            next_due += interval
            scheduled += 1
            if scheduled > self._MAX_TIMER_CATCHUP:
                raise ReplayClockError(
                    f"More than {self._MAX_TIMER_CATCHUP} timer actions were required before {now}; "
                    "recording clock units or a market-data outage are likely invalid"
                )
        self._next_decision[symbol] = next_due

    def _parse_exchange_info(self, rec: RecordedEvent) -> SymbolSpec:
        spec = SymbolSpec(
            symbol=rec.symbol,
            tick_size=Decimal(str(rec.data["tickSize"])),
            step_size=Decimal(str(rec.data["stepSize"])),
        )
        self._specs[rec.symbol] = spec
        return spec

    def _get_or_create_book(self, symbol: str) -> LocalOrderBook | None:
        spec = self._specs.get(symbol)
        if spec is None:
            return None
        if symbol not in self._books:
            book = LocalOrderBook(symbol=symbol, spec=spec, top_n=self.cfg.book_top_n)
            self._books[symbol] = book
            self._syncers[symbol] = BookSynchronizer(book, resync_on_gap=self.cfg.resync_on_gap)
            self.metrics.register_symbol(symbol)
        return self._books[symbol]

    def _get_sync(self, symbol: str) -> BookSynchronizer | None:
        if self._get_or_create_book(symbol) is None:
            return None
        return self._syncers[symbol]

    def _next_revision(self, symbol: str, side: str) -> int:
        key = (symbol, side)
        revision = self._quote_revision.get(key, 0) + 1
        self._quote_revision[key] = revision
        return revision

    def _max_position_lots(self, spec: SymbolSpec) -> int:
        return int((self.cfg.mm_max_position / spec.step_size).to_integral_value(rounding=ROUND_FLOOR))

    def _risk_capacity_lots(self, symbol: str, side: str) -> int:
        book = self._books[symbol]
        inventory = self.metrics.inventory_lots(symbol)
        maximum = self._max_position_lots(book.spec)
        return maximum - inventory if side == "bid" else maximum + inventory

    def _request_cancel(self, order: Order, ts: float) -> float:
        latency = self.cfg.sim_cancel_latency_ms / 1000.0
        if order.state is OrderState.LIVE:
            order.request_cancel(ts)
            self._schedule(ts + latency, "cancel", order.symbol, {"order_id": order.order_id})
        requested = order.cancel_requested_ts if order.cancel_requested_ts is not None else ts
        return requested + latency

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
        configured_lots = book.spec.qty_to_lot_floor(self.cfg.mm_order_qty)
        if configured_lots <= 0:
            return

        for side, desired_price in (("bid", bid_tick), ("ask", ask_tick)):
            capacity = self._risk_capacity_lots(symbol, side)
            desired_lots = min(configured_lots, max(0, capacity))
            existing = self.fill_model.get_order(symbol, side)

            if desired_lots <= 0:
                self._next_revision(symbol, side)
                if existing is not None:
                    self._request_cancel(existing, ts)
                continue

            if (
                existing is not None
                and existing.state is OrderState.LIVE
                and existing.price_tick == desired_price
                and existing.remaining_lots == desired_lots
            ):
                continue

            revision = self._next_revision(symbol, side)
            place_at = ts + self.cfg.sim_order_latency_ms / 1000.0
            if existing is not None:
                cancel_at = self._request_cancel(existing, ts)
                # Explicit cancel-then-new policy: one live order per side.
                place_at = max(place_at, cancel_at + self.cfg.sim_order_latency_ms / 1000.0)
            self._schedule(
                place_at,
                "place",
                symbol,
                {
                    "side": side,
                    "price_tick": desired_price,
                    "qty_lots": desired_lots,
                    "revision": revision,
                    "decision_ts": ts,
                },
            )

    def _reject_order(self, reason: str) -> None:
        if reason == "post_only_would_cross":
            self._post_only_rejects += 1
        self._metric("on_order_rejected", reason)

    def _handle_place(self, symbol: str, payload: dict[str, Any], now: float) -> None:
        side = str(payload["side"])
        revision = int(payload["revision"])
        if revision != self._quote_revision.get((symbol, side)):
            self._stale_actions += 1
            self._metric("on_stale_action")
            return

        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if book is None or syncer is None or not syncer.synced:
            self._reject_order("unsynced_book")
            return
        if self.fill_model.get_order(symbol, side) is not None:
            self._reject_order("active_order_exists")
            return

        price_tick = int(payload["price_tick"])
        qty_lots = int(payload["qty_lots"])
        if qty_lots <= 0 or qty_lots > self._risk_capacity_lots(symbol, side):
            self._reject_order("risk_limit")
            return

        best = book.best_ticks()
        if best is None:
            self._reject_order("empty_book")
            return
        best_bid, best_ask = best
        if (side == "bid" and price_tick >= best_ask) or (side == "ask" and price_tick <= best_bid):
            self._reject_order("post_only_would_cross")
            return

        queue_side = "bids" if side == "bid" else "asks"
        order = Order(
            order_id=f"{symbol}-{side}-{int(now * 1e6)}-{next(self._id_counter)}",
            symbol=symbol,
            side=side,
            price_tick=price_tick,
            qty_lots=qty_lots,
            queue_ahead_lots=book.get_level_size(queue_side, price_tick),
            created_ts=now,
            remaining_lots=qty_lots,
        )
        try:
            self.fill_model.place_order(order)
        except DuplicateActiveOrderError:
            self._reject_order("active_order_exists")
            return
        self.metrics.on_quote_requested()
        self._metric("on_order_accepted", order)

    def _handle_cancel(self, payload: dict[str, Any], now: float) -> None:
        order_id = payload.get("order_id")
        if order_id is None:
            return
        if self.fill_model.cancel_order(str(order_id), now):
            self._metric("on_order_cancelled", str(order_id))

    def _drain_actions(self, now: float, *, inclusive: bool) -> None:
        def due(action: _Action) -> bool:
            return action.ts <= now if inclusive else action.ts < now

        while self._actions and due(self._actions[0]):
            action = heappop(self._actions)
            if action.kind == "decision":
                self._handle_decision(action.symbol, action.ts)
            elif action.kind == "place":
                self._handle_place(action.symbol, action.data, action.ts)
            elif action.kind == "cancel":
                self._handle_cancel(action.data, action.ts)

    def _invalidate_symbol(self, symbol: str, now: float, reason: str) -> None:
        self._gap_count += 1
        self.fill_model.cancel_all_for_symbol(symbol, now)
        self._next_revision(symbol, "bid")
        self._next_revision(symbol, "ask")
        self._metric("on_book_invalidated", symbol, now, reason)

    def _on_fills(self, fills: list[Fill]) -> None:
        for fill in fills:
            book = self._books.get(fill.symbol)
            if book is not None:
                self.metrics.on_fill(fill, book, book.mid_price())

    def _observe_mid(self, symbol: str, now: float) -> None:
        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return
        mid = book.mid_price()
        if mid is not None:
            self._metric("observe_mid", symbol, now, mid)

    def _process_market_record(self, rec: RecordedEvent, now: float) -> None:
        if rec.type == "snapshot":
            capture = self._capture_fields(rec)
            if capture is not None and capture.get("snapshotAccepted") is False:
                self._snapshot_rejections += 1
                reason = str(capture.get("validationError") or "collector_rejected_snapshot")
                self._invalidate_symbol(rec.symbol, now, f"snapshot_rejected: {reason}")
                return
            spec = self._specs[rec.symbol]
            snapshot = SnapshotEvent(
                symbol=rec.symbol,
                last_update_id=int(rec.data["lastUpdateId"]),
                bids=[
                    (spec.price_to_tick_exact(p), spec.qty_to_lot_exact(q))
                    for p, q in rec.data.get("bids", [])
                ],
                asks=[
                    (spec.price_to_tick_exact(p), spec.qty_to_lot_exact(q))
                    for p, q in rec.data.get("asks", [])
                ],
            )
            syncer = self._get_sync(rec.symbol)
            if syncer is not None:
                # A snapshot replacing a currently synced legacy book is an
                # implicit epoch boundary even when old captures lack v2
                # `_capture.syncEpoch` metadata.
                if syncer.synced:
                    syncer.begin_resync("snapshot_replaced_synced_book")
                    self._invalidate_symbol(rec.symbol, now, "snapshot_replaced_synced_book")
                try:
                    syncer.on_snapshot(snapshot)
                except BookSyncGapError as exc:
                    # A too-old REST attempt is expected during stream-first
                    # bootstrap. Keep buffered diffs for the next snapshot and
                    # make the rejected attempt visible instead of aborting.
                    self._snapshot_rejections += 1
                    self._invalidate_symbol(rec.symbol, now, f"snapshot_rejected: {exc}")
            return

        if rec.type == "depthUpdate":
            spec = self._specs[rec.symbol]
            syncer = self._get_sync(rec.symbol)
            if syncer is None:
                return
            event = DepthUpdateEvent(
                symbol=rec.symbol,
                first_update_id=int(rec.data["U"]),
                final_update_id=int(rec.data["u"]),
                prev_update_id=int(rec.data.get("pu", rec.data["U"])),
                bids=[
                    (spec.price_to_tick_exact(p), spec.qty_to_lot_exact(q)) for p, q in rec.data.get("b", [])
                ],
                asks=[
                    (spec.price_to_tick_exact(p), spec.qty_to_lot_exact(q)) for p, q in rec.data.get("a", [])
                ],
                ts_local=now,
            )
            try:
                changes: list[LevelChange] = syncer.on_depth_update(event)
            except BookSyncGapError as exc:
                self._invalidate_symbol(rec.symbol, now, str(exc))
                return
            if changes:
                self._on_fills(self.fill_model.apply_depth_changes(rec.symbol, changes, now))
            return

        if rec.type == "aggTrade":
            syncer = self._syncers.get(rec.symbol)
            if syncer is None or not syncer.synced:
                return
            spec = self._specs[rec.symbol]
            trade = AggTradeEvent(
                symbol=rec.symbol,
                price_tick=spec.price_to_tick_exact(rec.data["p"]),
                qty_lots=spec.qty_to_lot_exact(rec.data["q"]),
                buyer_is_maker=bool(rec.data["m"]),
                ts_local=now,
            )
            self._on_fills(self.fill_model.apply_agg_trade(trade, now))

    def run(self, file_path: str | Path) -> SimulationMetrics:
        if self._has_run:
            raise RuntimeError("SimulationEngine instances are single-use")
        self._has_run = True

        for rec in iter_records(file_path):
            self._events_processed += 1

            if rec.type == "captureMeta":
                self._capture_schema_version = int(rec.data.get("schemaVersion", 1))
                self._receive_clock = rec.data.get("clock") == "receive_time"
                continue

            now = self._event_time(rec)
            if rec.type == "exchangeInfo":
                self._parse_exchange_info(rec)
                self._get_or_create_book(rec.symbol)
                continue
            if rec.symbol not in self._specs:
                continue

            self._validate_receive_sequence(rec)

            # Catch-up decisions strictly before `now` use only the previous
            # observed book. This is the core no-look-ahead invariant.
            self._schedule_decisions(rec.symbol, now, inclusive=False)
            self._drain_actions(now, inclusive=False)
            self._apply_capture_epoch(rec, now)
            self._process_market_record(rec, now)
            self._observe_mid(rec.symbol, now)

            # Market-data-first tie policy at identical timestamps.
            self._schedule_decisions(rec.symbol, now, inclusive=True)
            self._drain_actions(now, inclusive=True)
            self.metrics.update_unrealized(self._books)

        # Do not execute venue actions after the final observation against a
        # frozen book. They remain explicitly pending at the capture boundary.
        self.metrics.update_unrealized(self._books)
        return self.metrics

    def summary(self) -> dict[str, Any]:
        summary = self.metrics.get_summary(self._books)
        clock_claim_ready = (
            self._capture_schema_version >= 2 and self._receive_clock and self._clock_regressions == 0
        )
        summary["execution_model"] = {
            "fill_source": self.fill_model.fill_source.value,
            "queue_rule": (
                "same-side aggTrade volume consumes a synthetic market-by-price queue-ahead; "
                "trade-through fills the remainder"
                if self.fill_model.fill_source.value == "trade"
                else "displayed level decreases consume a synthetic queue-ahead (optimistic sensitivity)"
            ),
            "same_timestamp_tie_break": "market_data_before_strategy_and_venue_actions",
            "replacement_policy": "cancel_ack_then_new_order",
            "post_only": True,
        }
        summary["economic_assumptions"] = {
            "order_latency_ms": self.cfg.sim_order_latency_ms,
            "cancel_latency_ms": self.cfg.sim_cancel_latency_ms,
            "maker_fee_bps": str(self.cfg.fees_maker_bps),
            "taker_fee_bps": str(self.cfg.fees_taker_bps),
            "requote_ms": self.cfg.mm_requote_ms,
            "order_quantity": str(self.cfg.mm_order_qty),
            "max_position_per_symbol": str(self.cfg.mm_max_position),
            "half_spread_bps": str(self.cfg.mm_half_spread_bps),
            "skew_bps_per_base_unit": str(self.cfg.mm_skew_bps_per_unit),
            "inventory_observation_basis": "one sample per processed replay record; not time-weighted",
        }
        book_state = {
            symbol: {
                "synced_at_end": syncer.synced,
                "sync_epoch": syncer.epoch,
                "last_update_id": syncer.last_update_id,
                "levels": syncer.book.total_levels(),
            }
            for symbol, syncer in sorted(self._syncers.items())
        }
        summary["integrity"] = {
            "capture_schema_version": self._capture_schema_version,
            "clock": "receive_time" if self._receive_clock else "legacy_exchange_event_time",
            "clock_regressions_clamped": self._clock_regressions,
            "book_invalidations": self._gap_count,
            "snapshot_attempts_rejected": self._snapshot_rejections,
            "capture_sync_epoch_transitions": self._sync_epoch_transitions,
            "last_receive_sequence": self._last_receive_seq,
            "events_processed": self._events_processed,
            "stale_actions_dropped": self._stale_actions,
            "post_only_rejects": self._post_only_rejects,
            "pending_actions_at_capture_end": len(self._actions),
            "active_orders_at_capture_end": self.fill_model.active_order_count,
            "order_state_counts": self.fill_model.order_state_counts(),
            "book_state": book_state,
            "all_books_synced_at_end": bool(book_state)
            and all(state["synced_at_end"] for state in book_state.values()),
            "feed_completeness": "not_proven_without_venue-side packet-loss telemetry",
        }
        summary["evidence_quality"] = {
            "markouts": "claim_ready" if clock_claim_ready else "diagnostic_only",
            "markout_reason": (
                "schema-v2 receive clock with no observed regression; gap-crossing markouts are invalidated"
                if clock_claim_ready
                else "legacy/exchange clock or clamped regressions make subsecond horizons non-claimable"
            ),
            "pnl": "model_output_not_a_live_or_counterfactual-trading-result",
        }
        return summary

    def write_outputs(
        self,
        file_path: str | Path,
        metrics: SimulationMetrics,
    ) -> tuple[Path, Path, dict[str, Any]]:
        if metrics is not self.metrics:
            raise ValueError("metrics must belong to this engine")
        summary = self.summary()
        provenance = build_run_provenance(file_path, self.cfg)
        identity = "|".join(
            (
                str(provenance["fixture"]["sha256"]),
                str(provenance["configuration"]["fingerprint_sha256"]),
                str(provenance["code"]["fingerprint_sha256"]),
            )
        )
        run_id = hashlib.sha256(identity.encode("ascii")).hexdigest()[:12]
        summary["run_id"] = run_id
        summary["provenance"] = provenance
        output_dir = self.cfg.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(file_path).stem.replace(".ndjson", "")
        summary_path = output_dir / f"summary_{stem}_{run_id}.json"
        trades_path = output_dir / f"trades_{stem}_{run_id}.csv"
        summary_tmp = summary_path.with_suffix(".json.tmp")
        trades_tmp = trades_path.with_suffix(".csv.tmp")

        with summary_tmp.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
            fh.write("\n")
        summary_tmp.replace(summary_path)

        fieldnames = [
            "ts_local",
            "symbol",
            "side",
            "price",
            "qty",
            "maker",
            "order_id",
            "cause",
            "queue_ahead_before_lots",
        ]
        with trades_tmp.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summary.get("fills", []))
        trades_tmp.replace(trades_path)
        return summary_path, trades_path, summary
