from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from heapq import heapify, heappush, heappop
from itertools import islice
from pathlib import Path
from typing import Any, Dict

import math

from ..book.local_book import BookInvariantError, LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import DepthUpdateEvent, SymbolSpec
from ..config import Config
from ..record.envelope import ValidityState
from ..replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter
from ..replay.reader import RecordedEvent, iter_records
from ..replay.inspection import file_sha256
from ..oracle import state_hash
from .export import (
    EVENT_TRACE_FIELDS,
    TRADE_AUDIT_FIELDS,
    atomic_write_json,
    atomic_write_summary_csv,
    verify_streaming_audit_files,
)
from .fill_model import PassiveFillModel, PublicConsumptionEvent
from .metrics import SimulationMetrics
from .mm_strategy import MarketMakingStrategy, QuoteTarget
from .orders import Order
from .run_manifest import (
    RunManifest,
    artifact_bundle_snapshot,
    build_run_manifest,
    config_digest,
    config_snapshot,
    instrument_specs_snapshot,
    simulation_assumptions_snapshot,
)
from ..oracle import Checkpoint, read_checkpoint, write_checkpoint
from .checkpoint import CHECKPOINT_SCHEMA_VERSION, decode as decode_checkpoint, encode as encode_checkpoint
from .sinks import EventSink, NullSink, StreamingCsvSink
from .latency import LatencyModel

STREAM_FAILURE_EVENTS = frozenset({"disconnect", "connect_failure", "parse_failure", "overflow"})
# A route failure invalidates only the affected market-data dimension. These
# events mean the capture itself can no longer be treated as a lossless input,
# so execution must fail closed globally.
CAPTURE_INVALIDATION_EVENTS = frozenset({"parse_failure", "overflow", "writer_failure", "capture_abort"})


@dataclass(order=True)
class _EngineEvent:
    ts: float
    order: int
    kind: str
    symbol: str
    payload: Dict[str, Any]


class SimulationEngine:
    def __init__(
        self,
        cfg: Config,
        adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
        *,
        event_sink: EventSink | None = None,
        fill_sink: EventSink | None = None,
        markout_sink: EventSink | None = None,
        retain_event_trace: bool = True,
        retain_audit_rows: bool = True,
    ) -> None:
        self.cfg = cfg
        self.adapter = adapter
        self._event_sink = event_sink or NullSink()
        self.metrics = SimulationMetrics(
            cfg,
            fill_sink=fill_sink,
            markout_sink=markout_sink,
            retain_audit_rows=retain_audit_rows,
            buffer_markout_trace_events=retain_event_trace or not isinstance(self._event_sink, NullSink),
        )
        self.fill_model = PassiveFillModel(cfg.effective_fill_assumption)
        self.latency_model = LatencyModel(
            mode=cfg.sim_latency_mode,
            new_order_ms=cfg.sim_order_latency_ms,
            cancel_ms=cfg.sim_cancel_latency_ms,
            samples_ms=cfg.sim_latency_samples_ms,
            stress_multiplier=cfg.sim_latency_stress_multiplier,
            seed=cfg.sim_seed,
        )
        self.strategy = MarketMakingStrategy(cfg)
        self._specs: Dict[str, SymbolSpec] = {}
        self._books: Dict[str, LocalOrderBook] = {}
        self._syncers: Dict[str, BookSynchronizer] = {}
        self._next_decision: Dict[str, float] = {}
        self._actions: list[_EngineEvent] = []
        self._id_counter = 0
        self._trace_counter = 0
        self.event_trace: list[dict[str, Any]] = []
        self._retain_event_trace = retain_event_trace
        self._event_trace_count = 0
        self._last_trace_ts: float | None = None
        self._trading_halted = False
        self._pending_cancel_ack_ts: dict[str, float] = {}
        self._pending_replacement_slots: set[tuple[str, str, str]] = set()
        self._symbol_time_watermark: Dict[str, float] = {}
        self._clock_regressions = 0
        self._clock_invalidated = False
        self._receive_clock_regressions = 0
        self._receive_sequence_gaps = 0
        self._last_receive_monotonic_ns: int | None = None
        self._capture_valid = True
        self._capture_invalidations = 0
        self._capture_invalid_reason: str | None = None
        self._capture_schema_version = 1
        self._receive_clock = False
        self._capture_trailer_seen = False
        self._last_receive_seq: int | None = None
        self._stream_epochs: dict[tuple[str, str], int] = {}
        self._capture_sync_epochs: dict[str, int] = {}
        self._sync_epoch_transitions = 0
        self._gap_count = 0
        self._snapshot_rejections = 0
        self._depth_stream_valid: dict[str, bool] = {}
        self._trade_stream_valid: dict[str, bool] = {}
        self._stream_invalid_reason: dict[tuple[str, str], str] = {}
        self._latest_book_evidence: dict[str, str] = {}
        self._latest_trade_evidence: dict[str, str] = {}
        self._last_ts = 0.0
        self._last_event_index = 0
        self._market_data_first = False

    def event_trace_retention(self) -> dict[str, Any]:
        sink_memory_bounded = bool(getattr(self._event_sink, "memory_bounded", False))
        return {
            "schema_version": "lob_sim.event_trace_retention.v1",
            "retained_in_memory": self._retain_event_trace,
            "rows_emitted": self._event_trace_count,
            "rows_retained": len(self.event_trace),
            "sink": type(self._event_sink).__name__,
            "sink_memory_bounded": sink_memory_bounded,
            "memory_bounded_by_tape_duration": not self._retain_event_trace and sink_memory_bounded,
        }

    @staticmethod
    def _record_has_receive_clock(rec: RecordedEvent) -> bool:
        capture = rec.data.get("_capture")
        return isinstance(capture, dict) and all(
            capture.get(field) is not None for field in ("recvSeq", "recvMonotonicNs", "route")
        )

    @staticmethod
    def _event_time(rec: RecordedEvent) -> float:
        capture = rec.data.get("_capture")
        if SimulationEngine._record_has_receive_clock(rec):
            assert isinstance(capture, dict)
            try:
                monotonic_ns = int(str(capture["recvMonotonicNs"]))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid receive monotonic timestamp for {rec.symbol}:{rec.type}: "
                    f"{capture.get('recvMonotonicNs')!r}"
                ) from exc
            if monotonic_ns < 0:
                raise ValueError(f"receive monotonic timestamp must be non-negative: {monotonic_ns!r}")
            # Schema-v3's receipt clock is the causal simulation clock.  Keep
            # the public float API for downstream metrics, but derive it from
            # monotonic nanoseconds instead of wall-clock ``ts_local`` so a
            # wall-clock adjustment cannot manufacture a false regression.
            return monotonic_ns / 1_000_000_000.0
        if rec.type == "captureMeta" and rec.data.get("schemaVersion") == 3 and rec.data.get("clock") == "receive_time":
            # Legacy-shaped schema-v3 fixtures can declare the clock before
            # the first receipt envelope. Keep that declaration at the
            # logical origin until a monotonic receipt timestamp arrives.
            return 0.0
        try:
            value = float(rec.ts_local)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid event timestamp for {rec.symbol}:{rec.type}: {rec.ts_local!r}") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"event timestamp must be finite and non-negative: {value!r}")
        return value

    def _trade_stream_required(self) -> bool:
        return self.cfg.effective_fill_assumption.agg_trades_consume_queue or self.cfg.mm_strategy_profile in {
            "layered_mm",
            "research_mm",
        }

    def _trade_stream_is_valid(self, symbol: str) -> bool:
        return self._trade_stream_valid.get(symbol, self._capture_schema_version < 3)

    def _depth_stream_is_valid(self, symbol: str) -> bool:
        return self._depth_stream_valid.get(symbol, self._capture_schema_version < 3)

    @staticmethod
    def _record_evidence_id(rec: RecordedEvent, row_number: int) -> str:
        capture = rec.data.get("_capture")
        if isinstance(capture, dict):
            capture_id = capture.get("captureId")
            recv_seq = capture.get("recvSeq")
            checksum = capture.get("payloadChecksum")
            if capture_id is not None and recv_seq is not None and checksum is not None:
                return f"capture:{capture_id}:recv:{int(recv_seq)}:payload:{checksum}"
            if recv_seq is not None:
                return f"input_row:{row_number}:recv:{int(recv_seq)}"
        return f"input_row:{row_number}"

    @staticmethod
    def _combine_evidence(*groups: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(evidence_id for group in groups for evidence_id in group))

    def _decision_evidence_ids(self, symbol: str) -> tuple[str, ...]:
        book = (self._latest_book_evidence[symbol],) if symbol in self._latest_book_evidence else ()
        trade = (self._latest_trade_evidence[symbol],) if symbol in self._latest_trade_evidence else ()
        return self._combine_evidence(book, trade)

    def _validity_state(self, symbol: str, *, require_trade: bool) -> ValidityState:
        syncer = self._syncers.get(symbol)
        book_valid = syncer is not None and syncer.synced and self._depth_stream_is_valid(symbol)
        trade_valid = self._trade_stream_is_valid(symbol)
        clock_valid = (
            not self._clock_invalidated
            and self._clock_regressions == 0
            and self._receive_clock_regressions == 0
            and (self._capture_schema_version < 3 or self._receive_clock)
        )
        invalid_reasons: list[str] = []
        if not book_valid:
            invalid_reasons.append(self._stream_invalid_reason.get((symbol, "public"), "book_invalid"))
        if require_trade and not trade_valid:
            invalid_reasons.append(self._stream_invalid_reason.get((symbol, "market"), "trade_stream_invalid"))
        if not clock_valid:
            invalid_reasons.append("clock_invalid")
        if not self._capture_valid:
            invalid_reasons.append(self._capture_invalid_reason or "capture_invalid")
        return ValidityState(
            book_valid=book_valid,
            trade_stream_valid=trade_valid,
            clock_valid=clock_valid,
            capture_valid=self._capture_valid,
            trade_stream_required=require_trade,
            reason=";".join(invalid_reasons) or None,
        )

    def _call_strategy_epoch_hook(self, hook_name: str, symbol: str) -> None:
        hook = getattr(self.strategy, hook_name, None)
        if callable(hook):
            hook(symbol)

    def _clear_symbol_execution_state(self, symbol: str) -> dict[str, int]:
        active_order_count = sum(len(self.fill_model.get_orders(symbol, side)) for side in ("bid", "ask"))
        pending_cancel_count = sum(1 for order_id in self._pending_cancel_ack_ts if order_id.startswith(f"{symbol}-"))
        pending_replacement_count = sum(1 for slot in self._pending_replacement_slots if slot[0] == symbol)
        pending_action_count = sum(1 for action in self._actions if action.symbol == symbol)
        self.fill_model.invalidate_all_for_symbol(symbol)
        self._pending_cancel_ack_ts = {
            order_id: ts
            for order_id, ts in self._pending_cancel_ack_ts.items()
            if not order_id.startswith(f"{symbol}-")
        }
        self._pending_replacement_slots = {slot for slot in self._pending_replacement_slots if slot[0] != symbol}
        self._actions = [action for action in self._actions if action.symbol != symbol]
        heapify(self._actions)
        self._next_decision.pop(symbol, None)
        return {
            "invalidated_active_order_count": active_order_count,
            "cleared_pending_cancel_count": pending_cancel_count,
            "cleared_pending_replacement_count": pending_replacement_count,
            "cleared_pending_action_count": pending_action_count,
        }

    def _invalidate_symbol(self, symbol: str, now: float, reason: str) -> None:
        self._gap_count += 1
        self._latest_book_evidence.pop(symbol, None)
        self._latest_trade_evidence.pop(symbol, None)
        self._call_strategy_epoch_hook("invalidate_book_epoch", symbol)
        details = self._clear_symbol_execution_state(symbol)
        self.metrics.on_book_invalidated(symbol, reason)
        self.metrics.invalidate_markouts(symbol, reason, ts_local=now)
        self._trace(now, symbol, "epoch_invalidated", "book_sync", details={"reason": reason, **details})

    def _invalidate_clock(
        self,
        now: float,
        observed_ts: float,
        *,
        reason: str = "clock_regression",
        observed_monotonic_ns: int | None = None,
        previous_monotonic_ns: int | None = None,
    ) -> None:
        """Fail closed once the replay clock regresses."""

        if self._clock_invalidated:
            return
        self._clock_invalidated = True
        symbols = sorted(set(self._specs) | set(self._books) | set(self.metrics.position))
        cleared_by_symbol = {symbol: self._clear_symbol_execution_state(symbol) for symbol in symbols}
        invalidated_markouts = self.metrics.invalidate_all_pending_markouts(reason, ts_local=now)
        self._trading_halted = True
        self._trace(
            now,
            "*",
            "clock_invalidated",
            "clock",
            details={
                "reason": reason,
                "observed_ts_local": observed_ts,
                "logical_ts_local": now,
                "observed_monotonic_ns": observed_monotonic_ns,
                "previous_monotonic_ns": previous_monotonic_ns,
                "invalidated_markout_count": invalidated_markouts,
                "cleared_execution_state_by_symbol": cleared_by_symbol,
            },
        )

    def _invalidate_capture(self, now: float, reason: str) -> None:
        """Fail closed when the tape reports a capture-integrity failure."""

        if not self._capture_valid:
            return
        self._capture_valid = False
        self._capture_invalidations += 1
        self._capture_invalid_reason = reason
        symbols = sorted(set(self._specs) | set(self._books) | set(self.metrics.position))
        cleared_by_symbol: dict[str, dict[str, int]] = {}
        for symbol in symbols:
            self._call_strategy_epoch_hook("invalidate_book_epoch", symbol)
            cleared_by_symbol[symbol] = self._clear_symbol_execution_state(symbol)
        invalidated_markouts = self.metrics.invalidate_all_pending_markouts("capture_invalidated", ts_local=now)
        self._trading_halted = True
        self._trace(
            now,
            "*",
            "capture_invalidated",
            "capture",
            details={
                "reason": reason,
                "invalidated_markout_count": invalidated_markouts,
                "cleared_execution_state_by_symbol": cleared_by_symbol,
            },
        )

    def _invalidate_depth_stream(self, symbol: str, now: float, reason: str) -> bool:
        if self._depth_stream_valid.get(symbol) is False:
            return False
        self._depth_stream_valid[symbol] = False
        self._stream_invalid_reason[(symbol, "public")] = reason
        syncer = self._syncers.get(symbol)
        if syncer is not None:
            syncer.begin_resync(reason)
        self._invalidate_symbol(symbol, now, reason)
        return True

    def _recover_depth_stream(self, symbol: str, now: float, epoch: int | None) -> None:
        previous = self._depth_stream_valid.get(symbol)
        self._depth_stream_valid[symbol] = True
        self._stream_invalid_reason.pop((symbol, "public"), None)
        self._trace(
            now,
            symbol,
            "depth_stream_recovered" if previous is False else "depth_stream_connected",
            "capture",
            details={"stream_epoch": epoch},
        )

    def _invalidate_trade_stream(self, symbol: str, now: float, reason: str) -> bool:
        if self._trade_stream_valid.get(symbol) is False:
            return False
        self._trade_stream_valid[symbol] = False
        self._latest_trade_evidence.pop(symbol, None)
        self._stream_invalid_reason[(symbol, "market")] = reason
        self._call_strategy_epoch_hook("invalidate_trade_epoch", symbol)
        details: dict[str, Any] = {
            "reason": reason,
            "trade_stream_required": self._trade_stream_required(),
        }
        if self._trade_stream_required():
            details.update(self._clear_symbol_execution_state(symbol))
        self.metrics.on_trade_stream_invalidated(symbol, reason)
        self._trace(now, symbol, "trade_epoch_invalidated", "trade_stream", details=details)
        return True

    def _recover_trade_stream(self, symbol: str, now: float, epoch: int | None) -> None:
        previous = self._trade_stream_valid.get(symbol)
        self._trade_stream_valid[symbol] = True
        self._stream_invalid_reason.pop((symbol, "market"), None)
        recovered = previous is False
        if recovered:
            self.metrics.on_trade_stream_recovered(symbol)
        self._trace(
            now,
            symbol,
            "trade_stream_recovered" if recovered else "trade_stream_connected",
            "capture",
            details={"stream_epoch": epoch},
        )

    def _prevalidate_capture_boundary(self, rec: RecordedEvent, now: float) -> bool:
        """Reject capture-integrity boundaries before due actions can run.

        Stream epoch transitions are intentionally applied by
        :meth:`_observe_capture_epoch` after the legacy action-drain phase so
        existing causal tie behavior remains stable.  Receipt identity and
        capture-failure boundaries are different: once an observation proves
        the tape invalid, an older due order must not execute first.

        The return value tells the full observer that the receipt fields have
        already been checked.  Invalid fields still return ``True`` because
        the invalidation is recorded here and the later observer must avoid
        incrementing sequence/clock counters a second time.
        """

        if self._capture_schema_version < 3 or rec.type == "captureMeta":
            return False
        capture = rec.data.get("_capture")
        if capture is None and rec.type == "captureEvent":
            capture = rec.data
        if not isinstance(capture, dict):
            self._invalidate_capture(now, "missing_capture_metadata")
            return True

        missing_fields = [field for field in ("recvSeq", "recvMonotonicNs", "route") if capture.get(field) is None]
        if missing_fields:
            self._invalidate_capture(now, "missing_capture_metadata:" + ",".join(missing_fields))
            return True

        recv_seq = capture.get("recvSeq")
        try:
            seq = int(str(recv_seq))
        except (TypeError, ValueError):
            self._invalidate_capture(now, "invalid_capture_metadata:recvSeq")
            return True
        if seq < 0:
            self._invalidate_capture(now, "invalid_capture_metadata:recvSeq")
            return True
        if self._last_receive_seq is not None and seq <= self._last_receive_seq:
            raise ValueError(f"non-increasing receive sequence: {seq} after {self._last_receive_seq}")
        if self._last_receive_seq is not None and seq > self._last_receive_seq + 1:
            self._receive_sequence_gaps += seq - self._last_receive_seq - 1
            self._invalidate_capture(now, "receive_sequence_gap")
        self._last_receive_seq = seq

        raw_monotonic_ns = capture.get("recvMonotonicNs")
        try:
            monotonic_ns = int(str(raw_monotonic_ns))
        except (TypeError, ValueError):
            self._invalidate_capture(now, "invalid_capture_metadata:recvMonotonicNs")
            return True
        if monotonic_ns < 0:
            self._invalidate_capture(now, "invalid_capture_metadata:recvMonotonicNs")
            return True
        previous_monotonic_ns = self._last_receive_monotonic_ns
        if previous_monotonic_ns is not None and monotonic_ns < previous_monotonic_ns:
            self._receive_clock_regressions += 1
            self._invalidate_clock(
                now,
                now,
                reason="receive_monotonic_regression",
                observed_monotonic_ns=monotonic_ns,
                previous_monotonic_ns=previous_monotonic_ns,
            )
        if previous_monotonic_ns is None or monotonic_ns >= previous_monotonic_ns:
            self._last_receive_monotonic_ns = monotonic_ns

        event_name = str(rec.data.get("event", "")) if rec.type == "captureEvent" else ""
        if event_name in CAPTURE_INVALIDATION_EVENTS:
            failure_reason = str(
                rec.data.get("validationError")
                or capture.get("validationError")
                or rec.data.get("reason")
                or capture.get("reason")
                or event_name
                or "unspecified"
            )
            self._invalidate_capture(now, f"{event_name}: {failure_reason}")
        return True

    def _observe_capture_epoch(self, rec: RecordedEvent, now: float, *, receipt_checked: bool = False) -> None:
        capture = rec.data.get("_capture")
        if capture is None and rec.type == "captureEvent":
            capture = rec.data
        if not isinstance(capture, dict):
            if self._capture_schema_version >= 3 and rec.type != "captureMeta" and not receipt_checked:
                self._invalidate_capture(now, "missing_capture_metadata")
            return
        if self._capture_schema_version >= 3 and rec.type != "captureMeta" and not receipt_checked:
            missing_fields = [field for field in ("recvSeq", "recvMonotonicNs", "route") if capture.get(field) is None]
            if missing_fields:
                self._invalidate_capture(now, "missing_capture_metadata:" + ",".join(missing_fields))
                return
        recv_seq = capture.get("recvSeq")
        if recv_seq is not None and not receipt_checked:
            try:
                seq = int(str(recv_seq))
            except (TypeError, ValueError):
                self._invalidate_capture(now, "invalid_capture_metadata:recvSeq")
                return
            if seq < 0:
                self._invalidate_capture(now, "invalid_capture_metadata:recvSeq")
                return
            if self._last_receive_seq is not None and seq <= self._last_receive_seq:
                raise ValueError(f"non-increasing receive sequence: {seq} after {self._last_receive_seq}")
            if self._last_receive_seq is not None and seq > self._last_receive_seq + 1:
                self._receive_sequence_gaps += seq - self._last_receive_seq - 1
                self._invalidate_capture(now, "receive_sequence_gap")
            self._last_receive_seq = seq
        if self._capture_schema_version >= 3 and not receipt_checked:
            raw_monotonic_ns = capture.get("recvMonotonicNs")
            if raw_monotonic_ns is not None:
                try:
                    monotonic_ns = int(str(raw_monotonic_ns))
                except (TypeError, ValueError):
                    self._invalidate_capture(now, "invalid_capture_metadata:recvMonotonicNs")
                    return
                if monotonic_ns < 0:
                    self._invalidate_capture(now, "invalid_capture_metadata:recvMonotonicNs")
                    return
                previous_monotonic_ns = self._last_receive_monotonic_ns
                if previous_monotonic_ns is not None and monotonic_ns < previous_monotonic_ns:
                    self._receive_clock_regressions += 1
                    self._invalidate_clock(
                        now,
                        now,
                        reason="receive_monotonic_regression",
                        observed_monotonic_ns=monotonic_ns,
                        previous_monotonic_ns=previous_monotonic_ns,
                    )
                if previous_monotonic_ns is None or monotonic_ns >= previous_monotonic_ns:
                    self._last_receive_monotonic_ns = monotonic_ns
        route = str(capture.get("route", ""))
        event_name = str(rec.data.get("event", "")) if rec.type == "captureEvent" else ""
        failure_reason = str(
            rec.data.get("validationError")
            or capture.get("validationError")
            or rec.data.get("reason")
            or capture.get("reason")
            or event_name
            or "unspecified"
        )
        if event_name in CAPTURE_INVALIDATION_EVENTS:
            self._invalidate_capture(now, f"{event_name}: {failure_reason}")
        stream_epoch = capture.get("streamEpoch")
        epoch: int | None = None
        previous: int | None = None
        epoch_changed = False
        if stream_epoch is not None:
            key = (rec.symbol, route)
            epoch = int(stream_epoch)
            if epoch < 0:
                raise ValueError(f"negative stream epoch for {rec.symbol}:{route}: {epoch}")
            previous = self._stream_epochs.get(key)
            if previous is not None and epoch < previous:
                raise ValueError(f"regressing stream epoch for {rec.symbol}:{route}: {epoch} after {previous}")
            epoch_changed = previous is not None and epoch != previous
            if epoch_changed:
                self._sync_epoch_transitions += 1
            self._stream_epochs[key] = epoch

        capture_sync_epoch = capture.get("syncEpoch")
        capture_sync_changed = False
        if route == "public" and capture_sync_epoch is not None:
            sync_epoch = int(capture_sync_epoch)
            if sync_epoch < 0:
                raise ValueError(f"negative sync epoch for {rec.symbol}: {sync_epoch}")
            previous_sync = self._capture_sync_epochs.get(rec.symbol)
            if previous_sync is not None and sync_epoch < previous_sync:
                raise ValueError(f"regressing sync epoch for {rec.symbol}: {sync_epoch} after {previous_sync}")
            capture_sync_changed = previous_sync is not None and sync_epoch != previous_sync
            if capture_sync_changed and not epoch_changed:
                self._sync_epoch_transitions += 1
            self._capture_sync_epochs[rec.symbol] = sync_epoch

        if route == "public":
            if event_name in STREAM_FAILURE_EVENTS:
                self._invalidate_depth_stream(rec.symbol, now, f"depth_stream_{event_name}:{failure_reason}")
                return
            if event_name == "snapshot_rejected":
                self._snapshot_rejections += 1
                self._invalidate_symbol(rec.symbol, now, f"snapshot_rejected: {failure_reason}")
                return
            if epoch_changed and self._depth_stream_is_valid(rec.symbol):
                self._invalidate_depth_stream(rec.symbol, now, "depth_stream_reconnect")
            elif capture_sync_changed:
                syncer = self._syncers.get(rec.symbol)
                if syncer is not None and syncer.synced:
                    syncer.begin_resync("capture_sync_epoch_changed")
                    self._invalidate_symbol(rec.symbol, now, "capture_sync_epoch_changed")
            if event_name in {"connect", "reconnect"} or (
                rec.type in {"snapshot", "depthUpdate"} and (previous is None or epoch_changed)
            ):
                self._recover_depth_stream(rec.symbol, now, epoch)
            return

        if route == "market":
            if event_name in STREAM_FAILURE_EVENTS:
                self._invalidate_trade_stream(rec.symbol, now, f"trade_stream_{event_name}:{failure_reason}")
                return
            if epoch_changed and self._trade_stream_is_valid(rec.symbol):
                self._invalidate_trade_stream(rec.symbol, now, "trade_stream_reconnect")
            should_recover = event_name in {"connect", "reconnect"} or (
                rec.type == "aggTrade" and (previous is None or epoch_changed)
            )
            if should_recover:
                self._recover_trade_stream(rec.symbol, now, epoch)

    def _schedule(self, ts: float, kind: str, symbol: str, payload: Dict[str, Any]) -> None:
        order = self._id_counter
        self._id_counter += 1
        heappush(self._actions, _EngineEvent(ts=ts, order=order, kind=kind, symbol=symbol, payload=payload))

    def _next_id(self) -> int:
        value = self._id_counter
        self._id_counter += 1
        return value

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
        if self._last_trace_ts is not None and ts < self._last_trace_ts:
            raise RuntimeError(f"event trace violated causal order: timestamp {ts!r} followed {self._last_trace_ts!r}")
        self._last_trace_ts = ts
        sequence = self._trace_counter
        self._trace_counter += 1
        event = {
            "ts_local": ts,
            "seq": sequence,
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
        self._event_trace_count += 1
        self._event_sink.write(event)
        if self._retain_event_trace:
            self.event_trace.append(event)

    def _trace_market_record(self, rec: RecordedEvent, logical_ts: float, observed_ts: float) -> None:
        details: dict[str, Any] = {"record_type": rec.type}
        capture = rec.data.get("_capture")
        if isinstance(capture, dict) and capture.get("recvMonotonicNs") is not None:
            details.update(
                {
                    "logical_time_source": "capture_receive_monotonic_ns",
                    "observed_wall_ts_local": rec.ts_local,
                    "recv_monotonic_ns": capture.get("recvMonotonicNs"),
                    "recv_seq": capture.get("recvSeq"),
                }
            )
        if logical_ts != observed_ts:
            details.update({"observed_ts_local": observed_ts, "clock_clamped": True})
        if rec.type == "exchangeInfo":
            details.update(
                {
                    "tick_size": rec.data.get("tickSize"),
                    "step_size": rec.data.get("stepSize"),
                    "base_asset": rec.data.get("baseAsset"),
                    "quote_asset": rec.data.get("quoteAsset"),
                }
            )
        elif rec.type == "captureMeta":
            details.update(
                {
                    "schema_version": rec.data.get("schemaVersion"),
                    "clock": rec.data.get("clock"),
                    "validity": rec.data.get("validity"),
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
        elif rec.type == "captureEvent":
            capture = rec.data.get("_capture", {})
            details.update(
                {
                    "event": rec.data.get("event"),
                    "route": rec.data.get("route", capture.get("route") if isinstance(capture, dict) else None),
                    "reason": rec.data.get("reason"),
                    "stream_epoch": (
                        capture.get("streamEpoch") if isinstance(capture, dict) else rec.data.get("streamEpoch")
                    ),
                    "sync_epoch": (
                        capture.get("syncEpoch") if isinstance(capture, dict) else rec.data.get("syncEpoch")
                    ),
                }
            )
        self._trace(logical_ts, rec.symbol, "market_record", rec.type, details=details)

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

    def _trace_public_consumption(self, events: list[PublicConsumptionEvent]) -> None:
        for event in events:
            self._trace(
                event.ts_local,
                event.symbol,
                "queue_consumption",
                event.source,
                side=event.side,
                price_tick=event.price_tick,
                qty_lots=event.observed_lots,
                details={
                    "observed_lots": event.observed_lots,
                    "modeled_lots": event.modeled_lots,
                    "overlap_netted_lots": event.overlap_netted_lots,
                    "queue_consumed_lots": event.queue_consumed_lots,
                    "unmatched_lots": event.unmatched_lots,
                    "overlap_window_seconds": self.cfg.fill_assumption.overlap_window_seconds,
                    "fill_assumption_profile": event.fill_assumption_profile,
                },
            )

    def _trace_markout_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            symbol = str(event["symbol"])
            self._trace(
                float(event.get("markout_ts_local", event.get("ts_local", 0.0))),
                symbol,
                "markout",
                "metrics",
                side=str(event["side"]),
                price_tick=int(event["price_tick"]) if event.get("price_tick") is not None else None,
                qty_lots=int(event["qty_lots"]) if event.get("qty_lots") is not None else None,
                order_id=str(event["order_id"]) if event.get("order_id") is not None else None,
                fill_source=str(event["fill_source"]),
                details={
                    "fill_ts_local": event.get("ts_local"),
                    "deadline_ts": event.get("deadline_ts"),
                    "horizon": event.get("horizon"),
                    "fill_price": event.get("fill_price"),
                    "qty": event.get("qty"),
                    "fill_mid": event.get("fill_mid"),
                    "mid_after": event.get("mid_after"),
                    "markout": event.get("markout"),
                    "contract_multiplier": event.get("contract_multiplier"),
                    "adverse": event.get("adverse"),
                    "regime": event.get("regime"),
                    "status": event.get("status", "resolved"),
                    "invalid_reason": event.get("invalid_reason"),
                },
            )

    def _verbose(self, enabled: bool, message: str) -> None:
        if enabled:
            print(message, flush=True)

    def _emit_trade_event(self, ts: float, symbol: str, fills: list, *, market_observation: bool = False) -> None:
        if not fills:
            return
        if market_observation and self._market_data_first:
            # A public market observation is applied before venue actions in
            # schema-v3. Execute its modeled queue fills now; scheduling them
            # behind a same-time cancel acknowledgement would reintroduce an
            # action-first race inside a single receipt timestamp.
            self._handle_trades(fills)
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

        cancel_latency_ms = self.latency_model.draw("cancel")
        ack_ts = ts + cancel_latency_ms / 1000.0
        order.mark_pending_cancel()
        order.cancel_latency_ms = cancel_latency_ms
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
            "cancel_latency_ms": cancel_latency_ms,
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
        if self._trade_stream_required() and not self._trade_stream_is_valid(symbol):
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
        spec = self.adapter.instrument_spec_from_record(rec)
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

    def _disable_trading(self) -> dict[str, Any]:
        self._trading_halted = True
        canceled_by_symbol: dict[str, dict[str, int]] = {}
        pending_cancel_ack_count = len(self._pending_cancel_ack_ts)
        pending_replacement_slot_count = len(self._pending_replacement_slots)
        for symbol in list(self._books):
            bid_count = len(self.fill_model.get_orders(symbol, "bid"))
            ask_count = len(self.fill_model.get_orders(symbol, "ask"))
            if bid_count or ask_count:
                canceled_by_symbol[symbol] = {
                    "bid": bid_count,
                    "ask": ask_count,
                    "total": bid_count + ask_count,
                }
            self.fill_model.cancel_all_for_symbol_side(symbol, "bid")
            self.fill_model.cancel_all_for_symbol_side(symbol, "ask")
        self._pending_cancel_ack_ts.clear()
        self._pending_replacement_slots.clear()
        return {
            "canceled_order_count": sum(counts["total"] for counts in canceled_by_symbol.values()),
            "canceled_orders_by_symbol": canceled_by_symbol,
            "cleared_pending_cancel_ack_count": pending_cancel_ack_count,
            "cleared_pending_replacement_slot_count": pending_replacement_slot_count,
        }

    def _handle_kill_switch(self, ts: float, symbol: str, phase: str, verbose: bool) -> None:
        if not self.metrics.kill_switch_triggered or self._trading_halted:
            return
        halt_details = self._disable_trading()
        halt_details.update(
            {
                "reason": self.metrics.kill_switch_reason,
                "phase": phase,
                "realized_pnl": str(self.metrics.realized_pnl),
                "unrealized_pnl": str(self.metrics.unrealized_pnl),
                "max_drawdown": str(self.metrics.max_drawdown),
                "max_consecutive_loss_count": self.metrics.max_consecutive_loss_count,
            }
        )
        self._trace(ts, symbol, "risk_halt", "risk", details=halt_details)
        self._verbose(
            verbose,
            f"[simulate] kill switch triggered: {self.metrics.kill_switch_reason}",
        )

    def _handle_decision(self, symbol: str, ts: float) -> None:
        if self._trading_halted or not self.cfg.mm_enabled:
            return
        if self._trade_stream_required() and not self._trade_stream_is_valid(symbol):
            return

        syncer = self._syncers.get(symbol)
        book = self._books.get(symbol)
        if syncer is None or book is None or not syncer.synced:
            return

        inventory = book.spec.lot_to_qty(self.metrics.inventory_lots(symbol))
        plan = self.strategy.propose(book, inventory_qty=inventory)
        decision_evidence_ids = self._decision_evidence_ids(symbol)
        decision_details: dict[str, Any] = {
            "inventory_qty": str(inventory),
            "quote_count": len(plan.quotes),
            "strategy_profile": self.cfg.mm_strategy_profile,
            "evidence_ids": list(decision_evidence_ids),
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
        }
        if plan.reason:
            decision_details["reason"] = plan.reason
        if plan.diagnostics:
            decision_details["diagnostics"] = plan.diagnostics
        self._trace(ts, symbol, "decision", "strategy", details=decision_details)

        desired_by_side: dict[str, dict[str, QuoteTarget]] = {"bid": {}, "ask": {}}
        for target in plan.quotes:
            desired_by_side[target.side][target.quote_slot] = target

        for side in ("bid", "ask"):
            desired_targets = desired_by_side[side]
            existing_orders = {order.quote_slot: order for order in self.fill_model.get_orders(symbol, side)}
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
                current_existing: Order | None = existing_orders.get(slot)
                slot_key = self._slot_key(symbol, side, slot)
                replacement_ack_ts: float | None = None
                replacement_pending = slot_key in self._pending_replacement_slots
                if current_existing is None and replacement_pending:
                    continue
                observed_queue_ahead_lots = 0
                strategy_existing = current_existing
                if current_existing is not None:
                    observed_queue_ahead_lots = self.fill_model.queue_ahead_lots(symbol, current_existing)
                    strategy_existing = replace(current_existing, queue_ahead_lots=observed_queue_ahead_lots)
                    pending_cancel_ack_ts = self._pending_cancel_ack_ts.get(current_existing.order_id)
                else:
                    pending_cancel_ack_ts = None
                refresh = self.strategy.should_refresh(target, strategy_existing)
                if current_existing is not None and (
                    current_existing.price_tick != target.price_tick
                    or current_existing.qty_lots != target.qty_lots
                    or refresh
                    or pending_cancel_ack_ts is not None
                ):
                    replacement_ack_ts = self._request_cancel(
                        ts,
                        symbol,
                        current_existing,
                        reason="replace_quote",
                        details={
                            "target_price_tick": target.price_tick,
                            "target_qty_lots": target.qty_lots,
                            "target_refresh_key": target.refresh_key,
                            "current_refresh_key": current_existing.refresh_key,
                            "queue_ahead_lots": observed_queue_ahead_lots,
                            "price_changed": current_existing.price_tick != target.price_tick,
                            "qty_changed": current_existing.qty_lots != target.qty_lots,
                            "refresh_requested": refresh,
                            "pending_cancel": pending_cancel_ack_ts is not None,
                        },
                    )
                    current_existing = None
                    if replacement_pending:
                        continue
                    self._pending_replacement_slots.add(slot_key)

                if (
                    current_existing is not None
                    and current_existing.price_tick == target.price_tick
                    and current_existing.qty_lots == target.qty_lots
                ):
                    continue

                order_latency_ms = self.latency_model.draw("new_order")
                arrival_ts = ts + order_latency_ms / 1000.0
                if replacement_ack_ts is not None:
                    arrival_ts = replacement_ack_ts + order_latency_ms / 1000.0
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
                        "decision_evidence_ids": decision_evidence_ids,
                        "new_order_latency_ms": order_latency_ms,
                    },
                )
                self.metrics.on_order_arrival_scheduled()
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
                        "order_latency_ms": order_latency_ms,
                        "cancel_ack_ts": replacement_ack_ts,
                    },
                )

    def _reject_arrival(
        self,
        *,
        now: float,
        symbol: str,
        side: str,
        quote_slot: str,
        price_tick: int,
        qty_lots: int,
        reason: str,
        source: str,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        remaining_lots = max(0, qty_lots)
        details = dict(extra_details or {})
        self.metrics.on_order_rejected(reason)
        self.metrics.on_order_arrival(
            resting_after_arrival=False,
            immediate_fills=0,
            remaining_lots_after_arrival=remaining_lots,
            state="rejected",
        )
        self._trace(
            now,
            symbol,
            "order_rejected",
            source,
            side=side,
            quote_slot=quote_slot,
            price_tick=price_tick,
            qty_lots=qty_lots,
            details={"reason": reason, **details},
        )
        self._trace(
            now,
            symbol,
            "order_arrival",
            "engine",
            side=side,
            quote_slot=quote_slot,
            price_tick=price_tick,
            qty_lots=qty_lots,
            details={
                "rejected": True,
                "rejection_reason": reason,
                "self_trade_prevented": reason == "self_trade_prevented",
                "remaining_lots_after_arrival": remaining_lots,
                "immediate_fills": 0,
                "resting_after_arrival": False,
                **details,
            },
        )

    def _portfolio_notional_reservation(
        self,
        *,
        extra_symbol: str | None = None,
        extra_price_tick: int | None = None,
        extra_qty_lots: int = 0,
    ) -> tuple[Decimal | None, dict[str, Decimal], tuple[str, ...]]:
        """Return gross marked inventory plus live/pending order notional.

        This is deliberately conservative: bid and ask reservations are both
        counted instead of assuming that an offsetting fill will arrive first.
        Inventory is marked at the current book midpoint; live and pending
        orders use their limit price.  A missing mark makes the reservation
        unavailable rather than silently treating exposure as zero.
        """

        symbols = set(self._specs) | set(self._books) | set(self.metrics.position)
        symbols.update(action.symbol for action in self._actions if action.kind == "order_arrival")
        if extra_symbol is not None:
            symbols.add(extra_symbol)

        reserved_by_symbol: dict[str, Decimal] = {}
        missing_marks: list[str] = []
        for symbol in sorted(symbols):
            spec = self._specs.get(symbol)
            if spec is None:
                continue
            inventory_lots = self.metrics.inventory_lots(symbol)
            live_orders = [
                order
                for side in ("bid", "ask")
                for order in self.fill_model.get_orders(symbol, side)
                if order.remaining_lots > 0 and order.price_tick is not None
            ]
            pending_orders = [
                action
                for action in self._actions
                if action.kind == "order_arrival"
                and action.symbol == symbol
                and int(action.payload.get("qty_lots", 0)) > 0
            ]
            extra_applies = extra_symbol == symbol and extra_qty_lots > 0 and extra_price_tick is not None
            has_exposure = bool(inventory_lots or live_orders or pending_orders or extra_applies)
            if not has_exposure:
                continue

            book = self._books.get(symbol)
            mid = book.mid_price() if book is not None else None
            if mid is None:
                missing_marks.append(symbol)
                continue

            subtotal = abs(spec.lot_to_qty(inventory_lots)) * mid * spec.contract_multiplier
            subtotal += sum(
                (
                    spec.lot_to_qty(order.remaining_lots)
                    * spec.tick_to_price(order.price_tick)
                    * spec.contract_multiplier
                    for order in live_orders
                    if order.price_tick is not None
                ),
                Decimal("0"),
            )
            subtotal += sum(
                (
                    spec.lot_to_qty(int(action.payload.get("qty_lots", 0)))
                    * spec.tick_to_price(int(action.payload["price_tick"]))
                    * spec.contract_multiplier
                    for action in pending_orders
                ),
                Decimal("0"),
            )
            if extra_applies:
                assert extra_price_tick is not None
                subtotal += (
                    spec.lot_to_qty(extra_qty_lots) * spec.tick_to_price(extra_price_tick) * spec.contract_multiplier
                )
            reserved_by_symbol[symbol] = subtotal

        if missing_marks:
            return None, reserved_by_symbol, tuple(missing_marks)
        return sum(reserved_by_symbol.values(), Decimal("0")), reserved_by_symbol, ()

    def _handle_arrival(self, symbol: str, payload: Dict[str, Any], now: float) -> None:
        side = payload["side"]
        quote_slot = str(payload.get("quote_slot", "base"))
        self._pending_replacement_slots.discard(self._slot_key(symbol, side, quote_slot))
        if self._trading_halted:
            return

        price_tick = int(payload["price_tick"])
        qty_lots = int(payload["qty_lots"])
        refresh_key = str(payload.get("refresh_key", ""))
        decision_evidence_ids = tuple(str(value) for value in payload.get("decision_evidence_ids", ()))
        arrival_market_evidence_ids = self._decision_evidence_ids(symbol)
        arrival_evidence_ids = self._combine_evidence(decision_evidence_ids, arrival_market_evidence_ids)
        arrival_validity = self._validity_state(
            symbol,
            require_trade=self._trade_stream_required(),
        ).as_dict()
        new_order_latency_ms = float(payload.get("new_order_latency_ms", 0.0))
        book = self._books.get(symbol)
        syncer = self._syncers.get(symbol)
        if book is None or syncer is None or not syncer.synced or qty_lots <= 0:
            self._reject_arrival(
                now=now,
                symbol=symbol,
                side=side,
                quote_slot=quote_slot,
                price_tick=price_tick,
                qty_lots=qty_lots,
                reason="unsynced_book" if syncer is None or not syncer.synced else "invalid_quantity",
                source="risk",
            )
            return

        # Re-run venue/risk checks at modeled arrival, not only at strategy
        # decision time.  Pending cancels and other due orders still consume
        # capacity until their acknowledgements are observed.
        best_ticks = book.best_ticks()
        if best_ticks is None:
            self._reject_arrival(
                now=now,
                symbol=symbol,
                side=side,
                quote_slot=quote_slot,
                price_tick=price_tick,
                qty_lots=qty_lots,
                reason="empty_book",
                source="risk",
            )
            return
        best_bid, best_ask = best_ticks
        opposite_side = "ask" if side == "bid" else "bid"
        own_cross = any(
            existing.price_tick is not None
            and (
                (side == "bid" and price_tick >= existing.price_tick)
                or (side == "ask" and price_tick <= existing.price_tick)
            )
            for existing in self.fill_model.get_orders(symbol, opposite_side)
        )
        if own_cross:
            self.metrics.on_self_trade_prevented()
            self._reject_arrival(
                now=now,
                symbol=symbol,
                side=side,
                quote_slot=quote_slot,
                price_tick=price_tick,
                qty_lots=qty_lots,
                reason="self_trade_prevented",
                source="risk",
            )
            return
        if (side == "bid" and price_tick >= best_ask) or (side == "ask" and price_tick <= best_bid):
            self._reject_arrival(
                now=now,
                symbol=symbol,
                side=side,
                quote_slot=quote_slot,
                price_tick=price_tick,
                qty_lots=qty_lots,
                reason="post_only_would_cross",
                source="venue",
                extra_details={"best_bid": best_bid, "best_ask": best_ask},
            )
            return
        inventory_lots = self.metrics.inventory_lots(symbol)
        max_position_lots = book.spec.qty_to_lot_floor(self.cfg.mm_max_position)
        same_side_live = sum(order.remaining_lots for order in self.fill_model.get_orders(symbol, side))
        same_side_pending = sum(
            int(action.payload.get("qty_lots", 0))
            for action in self._actions
            if action.symbol == symbol and action.kind == "order_arrival" and action.payload.get("side") == side
        )
        capacity = (
            max_position_lots - inventory_lots - same_side_live - same_side_pending
            if side == "bid"
            else max_position_lots + inventory_lots - same_side_live - same_side_pending
        )
        if qty_lots > max(0, capacity):
            self._reject_arrival(
                now=now,
                symbol=symbol,
                side=side,
                quote_slot=quote_slot,
                price_tick=price_tick,
                qty_lots=qty_lots,
                reason="risk_limit",
                source="risk",
                extra_details={"capacity_lots": max(0, capacity)},
            )
            return

        if self.cfg.mm_max_portfolio_notional > 0:
            reserved_notional, reserved_by_symbol, missing_marks = self._portfolio_notional_reservation(
                extra_symbol=symbol,
                extra_price_tick=price_tick,
                extra_qty_lots=qty_lots,
            )
            if reserved_notional is None:
                self._reject_arrival(
                    now=now,
                    symbol=symbol,
                    side=side,
                    quote_slot=quote_slot,
                    price_tick=price_tick,
                    qty_lots=qty_lots,
                    reason="portfolio_mark_unavailable",
                    source="risk",
                    extra_details={
                        "max_portfolio_notional": str(self.cfg.mm_max_portfolio_notional),
                        "missing_mark_symbols": list(missing_marks),
                    },
                )
                return
            if reserved_notional > self.cfg.mm_max_portfolio_notional:
                self._reject_arrival(
                    now=now,
                    symbol=symbol,
                    side=side,
                    quote_slot=quote_slot,
                    price_tick=price_tick,
                    qty_lots=qty_lots,
                    reason="portfolio_notional_limit",
                    source="risk",
                    extra_details={
                        "max_portfolio_notional": str(self.cfg.mm_max_portfolio_notional),
                        "projected_reserved_notional": str(reserved_notional),
                        "reserved_notional_by_symbol": {key: str(value) for key, value in reserved_by_symbol.items()},
                        "reservation_basis": "absolute_marked_inventory_plus_live_and_pending_order_notional",
                    },
                )
                return

        order = Order(
            order_id=f"{symbol}-{side}-{int(now * 1_000_000)}-{self._next_id()}",
            symbol=symbol,
            side=side,
            price_tick=price_tick,
            qty_lots=qty_lots,
            quote_slot=quote_slot,
            queue_ahead_lots=book.get_level_size("bids" if side == "bid" else "asks", price_tick),
            created_ts=now,
            remaining_lots=qty_lots,
            refresh_key=refresh_key,
            decision_evidence_ids=decision_evidence_ids,
            arrival_evidence_ids=arrival_evidence_ids,
            arrival_validity=arrival_validity,
            new_order_latency_ms=new_order_latency_ms,
        )
        fills = self.fill_model.place_order(order)
        resting_order = self.fill_model.get_order(symbol, side, quote_slot)
        resting_after_arrival = resting_order is not None and resting_order.order_id == order.order_id
        queue_ahead_after_arrival = (
            self.fill_model.queue_ahead_lots(symbol, resting_order) if resting_after_arrival else 0
        )
        if fills:
            self._emit_trade_event(now, symbol, fills)
        arrival_details = {
            "refresh_key": refresh_key,
            "remaining_lots_after_arrival": order.remaining_lots,
            "resting_after_arrival": resting_after_arrival,
            "queue_ahead_lots_after_arrival": queue_ahead_after_arrival,
            "immediate_fills": len(fills),
            "decision_evidence_ids": list(decision_evidence_ids),
            "arrival_evidence_ids": list(arrival_evidence_ids),
            "arrival_validity": arrival_validity,
            "new_order_latency_ms": new_order_latency_ms,
        }
        self.metrics.on_order_arrival(
            resting_after_arrival=resting_after_arrival,
            immediate_fills=len(fills),
            remaining_lots_after_arrival=order.remaining_lots,
            queue_ahead_lots_after_arrival=queue_ahead_after_arrival,
        )
        if self.fill_model.last_self_trade_prevented:
            self.metrics.on_self_trade_prevented()
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
        self.metrics.on_cancel_acknowledged()
        self._trace(now, symbol, "cancel_ack", "engine", order_id=str(order_id))

    def _handle_trades(self, fills: list) -> None:
        for fill in fills:
            book = self._books.get(fill.symbol)
            if book is None:
                continue
            fill_audit = self.metrics.on_fill(fill, book, book.mid_price())
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
                    "provenance_schema_version": fill_audit["provenance_schema_version"],
                    "scenario_id": fill_audit["scenario_id"],
                    "evidence_ids": fill_audit["evidence_ids"],
                    "validity": fill_audit["validity"],
                    "queue_trajectory": fill_audit["queue_trajectory"],
                    "latency_draws_ms": fill_audit["latency_draws_ms"],
                    "latency_model": fill_audit["latency_model"],
                    "order_state_at_fill": fill_audit["order_state_at_fill"],
                    "fee_model_id": fill_audit["fee_model_id"],
                    "price": fill_audit["price"],
                    "qty": fill_audit["qty"],
                    "notional": fill_audit["notional"],
                    "contract_multiplier": fill_audit["contract_multiplier"],
                    "fee_bps": fill_audit["fee_bps"],
                    "fee": fill_audit["fee"],
                    "fee_currency": fill_audit["fee_currency"],
                    "mid_at_fill": fill_audit["mid_at_fill"],
                    "spread_capture": fill_audit["spread_capture"],
                    "spread_capture_value": fill_audit["spread_capture_value"],
                    "time_in_book_ms": fill_audit["time_in_book_ms"],
                    "markout_horizon": fill_audit["markout_horizon"],
                    "regime": fill_audit["regime"],
                    "book_bid_tick": fill_audit["book_bid_tick"],
                    "book_ask_tick": fill_audit["book_ask_tick"],
                },
            )

    def _drain_events(self, now: float, *, inclusive: bool = True) -> None:
        while self._actions and (self._actions[0].ts <= now if inclusive else self._actions[0].ts < now):
            event = heappop(self._actions)
            if event.kind == "decision":
                self._handle_decision(event.symbol, event.ts)
            elif event.kind == "order_arrival":
                self._handle_arrival(event.symbol, event.payload, event.ts)
            elif event.kind == "order_cancel":
                self._handle_cancel(event.payload, event.ts, event.symbol)
            elif event.kind == "trade_execution":
                self._handle_trades(event.payload.get("fills", []))

    def _checkpoint_mutable_state(self) -> dict[str, Any]:
        """Capture every mutable input to the deterministic continuation."""

        metric_excluded = {"cfg", "fee_model", "_fill_sink", "_markout_sink"}
        metric_state = {key: value for key, value in self.metrics.__dict__.items() if key not in metric_excluded}
        strategy_state = {
            "returns": dict(self.strategy._returns),
            "prev_mid": dict(self.strategy._prev_mid),
            "recent_trade_signals": dict(self.strategy._recent_trade_signals),
        }
        return {
            "id_counter": self._id_counter,
            "trace_counter": self._trace_counter,
            "capture_schema_version": self._capture_schema_version,
            "receive_clock": self._receive_clock,
            "capture_trailer_seen": self._capture_trailer_seen,
            "last_receive_seq": self._last_receive_seq,
            "clock_regressions": self._clock_regressions,
            "clock_invalidated": self._clock_invalidated,
            "receive_clock_regressions": self._receive_clock_regressions,
            "receive_sequence_gaps": self._receive_sequence_gaps,
            "last_receive_monotonic_ns": self._last_receive_monotonic_ns,
            "capture_valid": self._capture_valid,
            "capture_invalidations": self._capture_invalidations,
            "capture_invalid_reason": self._capture_invalid_reason,
            "next_decision": self._next_decision,
            "actions": self._actions,
            "event_trace": self.event_trace,
            "event_trace_count": self._event_trace_count,
            "last_trace_ts": self._last_trace_ts,
            "trading_halted": self._trading_halted,
            "pending_cancel_ack_ts": self._pending_cancel_ack_ts,
            "pending_replacement_slots": self._pending_replacement_slots,
            "symbol_time_watermark": self._symbol_time_watermark,
            "stream_epochs": self._stream_epochs,
            "capture_sync_epochs": self._capture_sync_epochs,
            "sync_epoch_transitions": self._sync_epoch_transitions,
            "gap_count": self._gap_count,
            "snapshot_rejections": self._snapshot_rejections,
            "depth_stream_valid": self._depth_stream_valid,
            "trade_stream_valid": self._trade_stream_valid,
            "stream_invalid_reason": self._stream_invalid_reason,
            "latest_book_evidence": self._latest_book_evidence,
            "latest_trade_evidence": self._latest_trade_evidence,
            "specs": self._specs,
            "books": self._books,
            "syncers": self._syncers,
            "fill_model": self.fill_model.__dict__,
            "metrics": metric_state,
            "strategy": strategy_state,
            "latency_sampler_state": self.latency_model.sampler_state(),
        }

    def _restore_checkpoint_mutable_state(self, encoded_state: object) -> None:
        state = decode_checkpoint(encoded_state)
        if not isinstance(state, dict):
            raise ValueError("simulation checkpoint engine state must be an object")

        self._id_counter = int(state["id_counter"])
        self._trace_counter = int(state["trace_counter"])
        self._capture_schema_version = int(state["capture_schema_version"])
        self._receive_clock = bool(state["receive_clock"])
        self._capture_trailer_seen = bool(state.get("capture_trailer_seen", False))
        raw_receive_seq = state.get("last_receive_seq")
        self._last_receive_seq = None if raw_receive_seq is None else int(raw_receive_seq)
        self._clock_regressions = int(state["clock_regressions"])
        self._clock_invalidated = bool(state.get("clock_invalidated", self._clock_regressions > 0))
        self._receive_clock_regressions = int(state.get("receive_clock_regressions", 0))
        self._receive_sequence_gaps = int(state.get("receive_sequence_gaps", 0))
        raw_receive_monotonic_ns = state.get("last_receive_monotonic_ns")
        self._last_receive_monotonic_ns = None if raw_receive_monotonic_ns is None else int(raw_receive_monotonic_ns)
        self._capture_valid = bool(state.get("capture_valid", True))
        self._capture_invalidations = int(state.get("capture_invalidations", 0))
        raw_capture_reason = state.get("capture_invalid_reason")
        self._capture_invalid_reason = None if raw_capture_reason is None else str(raw_capture_reason)
        self._next_decision = dict(state["next_decision"])
        self._actions = list(state["actions"])
        heapify(self._actions)
        self.event_trace = list(state["event_trace"])
        self._event_trace_count = int(state["event_trace_count"])
        self._last_trace_ts = state["last_trace_ts"]
        self._trading_halted = bool(state["trading_halted"])
        self._pending_cancel_ack_ts = dict(state["pending_cancel_ack_ts"])
        self._pending_replacement_slots = set(state["pending_replacement_slots"])
        self._symbol_time_watermark = dict(state["symbol_time_watermark"])
        self._stream_epochs = dict(state["stream_epochs"])
        self._capture_sync_epochs = dict(state["capture_sync_epochs"])
        self._sync_epoch_transitions = int(state["sync_epoch_transitions"])
        self._gap_count = int(state["gap_count"])
        self._snapshot_rejections = int(state["snapshot_rejections"])
        self._depth_stream_valid = dict(state["depth_stream_valid"])
        self._trade_stream_valid = dict(state["trade_stream_valid"])
        self._stream_invalid_reason = dict(state["stream_invalid_reason"])
        self._latest_book_evidence = dict(state["latest_book_evidence"])
        self._latest_trade_evidence = dict(state["latest_trade_evidence"])
        self._specs = dict(state["specs"])
        self._books = dict(state["books"])
        self._syncers = dict(state["syncers"])
        for symbol, syncer in self._syncers.items():
            if symbol not in self._books:
                raise ValueError(f"checkpoint syncer has no matching book: {symbol}")
            syncer.book = self._books[symbol]

        self.fill_model.__dict__.clear()
        self.fill_model.__dict__.update(dict(state["fill_model"]))

        metric_sink = self.metrics._fill_sink
        markout_sink = self.metrics._markout_sink
        metric_state = dict(state["metrics"])
        self.metrics.__dict__.update(metric_state)
        self.metrics.cfg = self.cfg
        self.metrics._fill_sink = metric_sink
        self.metrics._markout_sink = markout_sink
        for field_name in (
            "book_gap_count_by_symbol",
            "book_invalidation_by_symbol",
            "book_invalidation_reasons",
            "trade_stream_invalidation_by_symbol",
            "trade_stream_invalidation_reasons",
            "trade_stream_recovery_by_symbol",
            "order_rejected_by_reason",
            "order_state_counts",
            "record_type_counts",
            "_markout_by_side",
            "_markout_adverse_by_side",
            "_markout_by_source",
            "_markout_adverse_by_source",
            "_regime_fill_counts",
            "_regime_markout_counts",
            "_regime_adverse_counts",
        ):
            setattr(self.metrics, field_name, defaultdict(int, getattr(self.metrics, field_name)))
        for field_name in (
            "_markout_qty_by_source",
            "_markout_sum_by_source",
            "_regime_fill_qty",
            "_regime_spread_capture",
        ):
            setattr(
                self.metrics,
                field_name,
                defaultdict(lambda: Decimal("0"), getattr(self.metrics, field_name)),
            )

        strategy_state = dict(state["strategy"])
        self.strategy._returns.clear()
        for symbol, returns in dict(strategy_state["returns"]).items():
            self.strategy._returns[symbol].extend(returns)
        self.strategy._prev_mid = dict(strategy_state["prev_mid"])
        self.strategy._recent_trade_signals.clear()
        for symbol, signals in dict(strategy_state["recent_trade_signals"]).items():
            self.strategy._recent_trade_signals[symbol].extend(signals)
        sampler_state = state.get("latency_sampler_state")
        if sampler_state is None:
            raise ValueError("checkpoint is missing deterministic latency sampler state")
        self.latency_model.restore_sampler_state(int(sampler_state))

    def write_state_checkpoint(
        self,
        input_path: str | Path,
        checkpoint_path: str | Path,
        *,
        event_index: int | None = None,
        last_ts: float | None = None,
        market_data_first: bool | None = None,
    ) -> Checkpoint:
        """Persist a validated JSON checkpoint for deterministic continuation."""

        input_file = Path(input_path)
        index = self._last_event_index if event_index is None else event_index
        logical_ts = self._last_ts if last_ts is None else last_ts
        market_first = self._market_data_first if market_data_first is None else market_data_first
        state = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "input_path": str(input_file.resolve()),
            "input_sha256": file_sha256(input_file),
            "config_sha256": config_digest(config_snapshot(self.cfg)),
            "event_index": index,
            "last_ts": logical_ts,
            "market_data_first": market_first,
            "engine": encode_checkpoint(self._checkpoint_mutable_state()),
        }
        checkpoint = Checkpoint.create(
            event_index=index,
            logical_time=(int(round(logical_ts * 1_000_000_000)), int(self._last_receive_seq or index)),
            state=state,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
        )
        write_checkpoint(checkpoint_path, checkpoint)
        return checkpoint

    def _load_state_checkpoint(self, input_path: str | Path, checkpoint_path: str | Path) -> Checkpoint:
        checkpoint = read_checkpoint(checkpoint_path)
        state = checkpoint.state
        if state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported simulation checkpoint schema: {state.get('schema_version')!r}")
        if int(state.get("event_index", -1)) != checkpoint.event_index:
            raise ValueError("simulation checkpoint event index is inconsistent")
        input_file = Path(input_path)
        if state.get("input_sha256") != file_sha256(input_file):
            raise ValueError("simulation checkpoint input SHA-256 does not match replay input")
        if state.get("config_sha256") != config_digest(config_snapshot(self.cfg)):
            raise ValueError("simulation checkpoint configuration digest does not match current config")
        self._restore_checkpoint_mutable_state(state["engine"])
        self._last_ts = float(state["last_ts"])
        self._last_event_index = int(state["event_index"])
        self._market_data_first = bool(state["market_data_first"])
        return checkpoint

    def run(
        self,
        file_path: str | Path,
        verbose: bool = False,
        progress_every: int = 5000,
        *,
        checkpoint_path: str | Path | None = None,
        checkpoint_every: int = 0,
        resume_from: str | Path | None = None,
        stop_after_records: int | None = None,
    ) -> SimulationMetrics:
        if checkpoint_every < 0:
            raise ValueError("checkpoint_every must be >= 0")
        if checkpoint_every > 0 and checkpoint_path is None:
            raise ValueError("checkpoint_path is required when checkpoint_every is positive")
        if stop_after_records is not None and stop_after_records <= 0:
            raise ValueError("stop_after_records must be positive")
        if stop_after_records is not None and checkpoint_path is None:
            raise ValueError("checkpoint_path is required when stop_after_records is set")
        input_file = Path(file_path)
        if input_file.name.endswith(".partial"):
            raise ValueError(
                "economic simulation requires a finalized capture; visible .partial tails are recovery inputs only"
            )
        if resume_from is not None and any(
            not isinstance(sink, NullSink)
            for sink in (self._event_sink, self.metrics._fill_sink, self.metrics._markout_sink)
        ):
            raise ValueError("checkpoint resume requires NullSink outputs; resume into a new audit bundle explicitly")

        start_index = 0
        if resume_from is not None:
            checkpoint = self._load_state_checkpoint(file_path, resume_from)
            start_index = checkpoint.event_index
            last_ts = self._last_ts
            records_processed = self._last_event_index
            market_data_first = self._market_data_first
        else:
            last_ts = 0.0
            records_processed = 0
            market_data_first = False
            self._last_ts = last_ts
            self._last_event_index = records_processed
            self._market_data_first = market_data_first

        records = iter_records(file_path)
        if start_index:
            records = islice(records, start_index, None)
        self._verbose(verbose, f"[simulate] starting simulation for {file_path}")
        interrupted = False
        for rec in records:
            records_processed += 1
            record_evidence_id = self._record_evidence_id(rec, records_processed)
            self.metrics.on_record(rec.type)
            if rec.type == "captureMeta":
                self._capture_schema_version = int(rec.data.get("schemaVersion", 1))
                market_data_first = self._capture_schema_version >= 3
                self._receive_clock = rec.data.get("clock") == "receive_time"
                self._market_data_first = market_data_first

            observed_ts = self._event_time(rec)
            now = observed_ts
            receive_clock_bootstrap = (
                self._capture_schema_version >= 3
                and self._receive_clock
                and self._record_has_receive_clock(rec)
                and self._last_receive_monotonic_ns is None
            )
            if receive_clock_bootstrap:
                # A legacy-shaped schema-v3 NDJSON fixture may declare
                # captureMeta without receipt metadata.  Do not compare that
                # wall-clock prefix with the first monotonic receipt value.
                last_ts = now
            elif now < last_ts:
                self._clock_regressions += 1
                if self._capture_schema_version >= 3:
                    self._invalidate_clock(last_ts, observed_ts)
                else:
                    # Legacy tapes retain their historical compatibility
                    # replay policy.  Their clock-dependent fills/markouts
                    # remain diagnostic-only, but do not retroactively remove
                    # the legacy strategy trace that existing fixtures expose.
                    self.metrics.invalidate_all_pending_markouts("legacy_clock_regression", ts_local=last_ts)
                now = last_ts
            else:
                last_ts = now
            symbol_now = max(now, self._symbol_time_watermark.get(rec.symbol, now))
            self._symbol_time_watermark[rec.symbol] = symbol_now

            # Legacy v1 fixtures preserve their historical action-first tie
            # policy. Schema-v3 captures use market-data-first ties.
            receipt_checked = self._prevalidate_capture_boundary(rec, now)
            self._schedule_decisions_up_to(rec.symbol, symbol_now, include_now=not market_data_first)
            self._drain_events(now, inclusive=not market_data_first)
            self._observe_capture_epoch(rec, now, receipt_checked=receipt_checked)
            self._trace_market_record(rec, now, observed_ts)
            if rec.type == "captureEvent" and rec.data.get("event") == "capture_trailer":
                self._capture_trailer_seen = True
            if rec.type in {"captureMeta", "captureEvent"}:
                continue
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

            if rec.type in {"snapshot", "depthUpdate"} and not self._depth_stream_is_valid(rec.symbol):
                self._trace(
                    now,
                    rec.symbol,
                    "depth_ignored_invalid_epoch",
                    "public",
                    details={
                        "record_type": rec.type,
                        "reason": self._stream_invalid_reason.get((rec.symbol, "public")),
                    },
                )
                continue

            if rec.type == "snapshot":
                capture = rec.data.get("_capture", {})
                if isinstance(capture, dict) and capture.get("snapshotAccepted") is False:
                    self._snapshot_rejections += 1
                    self._invalidate_symbol(
                        rec.symbol,
                        now,
                        f"snapshot_rejected: {capture.get('validationError', 'collector_rejected_snapshot')}",
                    )
                else:
                    spec = self._specs[rec.symbol]
                    snapshot = self.adapter.snapshot_from_record(rec, spec)
                    syncer = self._get_sync(rec.symbol)
                    if syncer is not None:
                        if syncer.synced:
                            syncer.begin_resync("snapshot_replaced_synced_book")
                            self._invalidate_symbol(rec.symbol, now, "snapshot_replaced_synced_book")
                        try:
                            changes = syncer.on_snapshot(snapshot)
                        except (BookSyncGapError, BookInvariantError) as exc:
                            self._snapshot_rejections += 1
                            self._invalidate_symbol(rec.symbol, now, f"snapshot_rejected: {exc}")
                        else:
                            self.fill_model.seed_from_snapshot(rec.symbol, snapshot.bids, snapshot.asks)
                            self._latest_book_evidence[rec.symbol] = record_evidence_id
                            if changes:
                                buffered_fills = self.fill_model.apply_depth_changes(
                                    rec.symbol,
                                    changes,
                                    now,
                                    evidence_ids=(record_evidence_id,),
                                    validity=self._validity_state(rec.symbol, require_trade=False).as_dict(),
                                )
                                self._trace_public_consumption(self.fill_model.drain_public_consumption_events())
                                if buffered_fills:
                                    self._emit_trade_event(now, rec.symbol, buffered_fills, market_observation=True)
                            self._verbose(
                                verbose,
                                f"[simulate] snapshot synced for {rec.symbol} "
                                f"bids={len(snapshot.bids)} asks={len(snapshot.asks)}",
                            )

            elif rec.type == "depthUpdate":
                spec = self._specs[rec.symbol]
                syncer = self._get_sync(rec.symbol)
                if syncer is not None:
                    event = self.adapter.depth_update_from_record(rec, spec)
                    try:
                        changes = syncer.on_depth_update(event)
                    except (BookSyncGapError, BookInvariantError) as exc:
                        self.metrics.on_book_gap(rec.symbol)
                        self._trace_book_gap(now, event)
                        self._invalidate_symbol(rec.symbol, now, str(exc))
                        changes = []
                    else:
                        self._latest_book_evidence[rec.symbol] = record_evidence_id
                    self.metrics.on_depth_changes(len(changes))
                    if changes and syncer.synced:
                        fills = self.fill_model.apply_depth_changes(
                            rec.symbol,
                            changes,
                            now,
                            evidence_ids=(record_evidence_id,),
                            validity=self._validity_state(rec.symbol, require_trade=False).as_dict(),
                        )
                        self._trace_public_consumption(self.fill_model.drain_public_consumption_events())
                        if fills:
                            self._emit_trade_event(now, rec.symbol, fills, market_observation=True)

            elif rec.type == "aggTrade":
                if self._trade_stream_is_valid(rec.symbol):
                    spec = self._specs[rec.symbol]
                    trade = self.adapter.agg_trade_from_record(rec, spec)
                    self._latest_trade_evidence[rec.symbol] = record_evidence_id
                    self.strategy.observe_trade(trade)
                    fills = self.fill_model.apply_agg_trade(
                        trade,
                        now,
                        evidence_ids=(record_evidence_id,),
                        validity=self._validity_state(rec.symbol, require_trade=True).as_dict(),
                    )
                    self._trace_public_consumption(self.fill_model.drain_public_consumption_events())
                    if fills:
                        self._emit_trade_event(now, rec.symbol, fills, market_observation=True)
                else:
                    self._trace(
                        now,
                        rec.symbol,
                        "trade_ignored_invalid_epoch",
                        "market",
                        details={"reason": self._stream_invalid_reason.get((rec.symbol, "market"))},
                    )

            self._schedule_decisions_up_to(rec.symbol, symbol_now, include_now=True)
            self._drain_events(now, inclusive=not market_data_first)
            if self._books:
                self.metrics.update_unrealized(self._books, now_ts=now, specs=self._specs)
                self._trace_markout_events(self.metrics.drain_new_markout_events())
            self._handle_kill_switch(now, rec.symbol, "market_record", verbose)

            self._last_ts = last_ts
            self._last_event_index = records_processed
            self._market_data_first = market_data_first
            should_checkpoint = checkpoint_every > 0 and records_processed % checkpoint_every == 0
            should_stop = stop_after_records is not None and records_processed >= stop_after_records
            if should_checkpoint or should_stop:
                if checkpoint_path is None:
                    raise AssertionError("checkpoint path missing after checkpoint validation")
                self.write_state_checkpoint(
                    file_path,
                    checkpoint_path,
                    event_index=records_processed,
                    last_ts=last_ts,
                    market_data_first=market_data_first,
                )
            if should_stop:
                interrupted = True
                break

            if verbose and progress_every > 0 and records_processed % progress_every == 0:
                total_pnl = float(self.metrics.realized_pnl + self.metrics.unrealized_pnl)
                self._verbose(
                    verbose,
                    f"[simulate] records={records_processed} fills={self.metrics.fill_count} "
                    f"quotes={self.metrics.quote_count} pnl={total_pnl:.6f} pending_events={len(self._actions)} "
                    f"last={rec.symbol}:{rec.type}",
                )

        if interrupted:
            self._verbose(verbose, f"[simulate] checkpointed after records={records_processed}")
            return self.metrics

        final_ts = last_ts + max(
            self.cfg.mm_requote_ms / 1000.0,
            max(
                self.cfg.sim_order_latency_ms,
                self.cfg.sim_cancel_latency_ms,
                max(self.cfg.sim_latency_samples_ms, default=0.0),
            )
            / 1000.0,
            self.cfg.sim_adverse_markout_seconds,
            1.0,
        )
        if not market_data_first:
            self._drain_events(final_ts)
            mark_ts = final_ts
        else:
            # A schema-v3 capture ends at its last observation.  Actions after
            # that boundary remain pending rather than being filled against a
            # frozen book.
            mark_ts = last_ts
        self.metrics.update_unrealized(self._books, now_ts=mark_ts, specs=self._specs)
        self._trace_markout_events(self.metrics.drain_new_markout_events())
        shutdown_symbol = next(iter(self._books), "")
        self._handle_kill_switch(mark_ts, shutdown_symbol, "shutdown", verbose)
        self._verbose(
            verbose,
            f"[simulate] completed records={records_processed} fills={self.metrics.fill_count} "
            f"quotes={self.metrics.quote_count}",
        )
        self.metrics.public_consumption_summary = self.fill_model.public_consumption_summary()
        return self.metrics

    def _write_event_trace(self, path: Path) -> None:
        with StreamingCsvSink(path, EVENT_TRACE_FIELDS) as sink:
            rows = sorted(self.event_trace, key=lambda row: (float(row["ts_local"]), int(row["seq"])))
            for export_seq, row in enumerate(rows):
                sink.write({**row, "seq": export_seq})

    def _summary_annotations(self) -> dict[str, Any]:
        book_state = {
            symbol: {
                "synced_at_end": syncer.synced,
                "sync_epoch": syncer.epoch,
                "last_update_id": syncer.last_update_id,
                "invalid_reason": syncer.invalid_reason,
                "levels": syncer.book.total_levels(),
            }
            for symbol, syncer in sorted(self._syncers.items())
        }
        stream_symbols = sorted(
            set(self._specs)
            | set(self._depth_stream_valid)
            | set(self._trade_stream_valid)
            | {symbol for symbol, route in self._stream_epochs if route in {"public", "market"} and symbol != "*"}
        )
        trade_stream_required = self._trade_stream_required()
        clock_valid = (
            not self._clock_invalidated
            and self._clock_regressions == 0
            and self._receive_clock_regressions == 0
            and (self._capture_schema_version < 3 or self._receive_clock)
        )
        stream_state = {
            symbol: {
                "depth_stream_valid": self._depth_stream_is_valid(symbol),
                "trade_stream_valid": self._trade_stream_is_valid(symbol),
                "trade_stream_required": trade_stream_required,
                "public_stream_epoch": self._stream_epochs.get((symbol, "public")),
                "market_stream_epoch": self._stream_epochs.get((symbol, "market")),
                "capture_sync_epoch": self._capture_sync_epochs.get(symbol),
                "public_invalid_reason": self._stream_invalid_reason.get((symbol, "public")),
                "market_invalid_reason": self._stream_invalid_reason.get((symbol, "market")),
                "clock_valid": clock_valid,
                "capture_valid": self._capture_valid,
                "execution_inputs_valid": (
                    bool(book_state.get(symbol, {}).get("synced_at_end"))
                    and self._depth_stream_is_valid(symbol)
                    and (not trade_stream_required or self._trade_stream_is_valid(symbol))
                    and clock_valid
                    and self._capture_valid
                ),
            }
            for symbol in stream_symbols
        }
        clock_claim_ready = (
            self._capture_schema_version >= 3
            and self._receive_clock
            and self._capture_trailer_seen
            and self._clock_regressions == 0
            and self._receive_clock_regressions == 0
            and not self._clock_invalidated
            and self._last_receive_seq is not None
            and self._capture_valid
        )
        execution_claim_ready = (
            clock_claim_ready
            and bool(stream_state)
            and all(state["execution_inputs_valid"] for state in stream_state.values())
            and self._gap_count == 0
            and self.metrics.trade_stream_invalidation_count == 0
        )
        return {
            "execution_model": {
                "fill_source": self.fill_model.fill_assumption.profile,
                "scenario": self.cfg.sim_fill_model,
                "queue_rule": (
                    "same-price public trade volume consumes a synthetic queue-ahead; trade-through fills the remainder"
                    if self.cfg.sim_fill_model == "trade"
                    else "displayed level decreases consume a synthetic queue-ahead (optimistic sensitivity)"
                ),
                "same_timestamp_tie_break": (
                    "market_data_before_strategy_and_venue_actions"
                    if self._capture_schema_version >= 3
                    else "legacy_action_first"
                ),
                "post_only": True,
                "historical_fifo_claim": False,
                "trade_stream_required": trade_stream_required,
            },
            "economic_assumptions": {
                "order_latency_ms": self.cfg.sim_order_latency_ms,
                "cancel_latency_ms": self.cfg.sim_cancel_latency_ms,
                "latency_model": self.latency_model.as_dict(),
                "maker_fee_bps": str(self.cfg.fees_maker_bps),
                "taker_fee_bps": str(self.cfg.fees_taker_bps),
                "requote_ms": self.cfg.mm_requote_ms,
                "order_quantity": str(self.cfg.mm_order_qty),
                "max_position_per_symbol": str(self.cfg.mm_max_position),
                "max_portfolio_notional": str(self.cfg.mm_max_portfolio_notional),
                "portfolio_notional_basis": (
                    "absolute_marked_inventory_plus_live_and_pending_order_notional; "
                    "positive cap is enforced at modeled arrival"
                ),
                "half_spread_bps": str(self.cfg.mm_half_spread_bps),
                "skew_bps_per_base_unit": str(self.cfg.mm_skew_bps_per_unit),
                "inventory_observation_basis": "time-weighted between causal market observations",
            },
            "integrity": {
                "capture_schema_version": self._capture_schema_version,
                "clock": "receive_time" if self._receive_clock else "legacy_exchange_or_local_time",
                "clock_regressions_clamped": self._clock_regressions,
                "receive_clock_regressions": self._receive_clock_regressions,
                "receive_sequence_gaps": self._receive_sequence_gaps,
                "last_receive_monotonic_ns": self._last_receive_monotonic_ns,
                "clock_invalidated": self._clock_invalidated,
                "capture_valid": self._capture_valid,
                "capture_invalidations": self._capture_invalidations,
                "capture_invalid_reason": self._capture_invalid_reason,
                "capture_trailer_seen": self._capture_trailer_seen,
                "claim_ready": execution_claim_ready,
                "book_invalidations": self._gap_count,
                "snapshot_attempts_rejected": self._snapshot_rejections,
                "sync_epoch_transitions": self._sync_epoch_transitions,
                "last_receive_sequence": self._last_receive_seq,
                "events_processed": self.metrics.records_processed,
                "active_orders_at_capture_end": sum(
                    len(self.fill_model.get_orders(symbol, side)) for symbol in self._books for side in ("bid", "ask")
                ),
                "book_state": book_state,
                "stream_state": stream_state,
                "all_books_synced_at_end": bool(book_state)
                and all(state["synced_at_end"] for state in book_state.values()),
                "all_required_execution_inputs_valid_at_end": bool(stream_state)
                and all(state["execution_inputs_valid"] for state in stream_state.values()),
                "feed_completeness": "not proven without venue-side packet-loss telemetry",
                "portfolio_risk": self._portfolio_risk_summary(),
            },
            "evidence_quality": {
                "markouts": "claim_ready" if clock_claim_ready else "diagnostic_only",
                "markout_reason": (
                    "schema-v3 receive clock with monotonic sequence and no clamped regressions"
                    if clock_claim_ready
                    else f"capture integrity invalidated: {self._capture_invalid_reason}"
                    if not self._capture_valid
                    else "receipt monotonic clock regression invalidated execution"
                    if self._receive_clock_regressions
                    else "schema-v3 capture trailer missing; finalized capture required"
                    if self._capture_schema_version >= 3 and not self._capture_trailer_seen
                    else "legacy/exchange clock or invalid intervals make subsecond horizons non-claimable"
                ),
                "pnl": "model_output_not_a_live_or_counterfactual_trading_result",
            },
            "claim_matrix": {
                "public_l2_reconstruction": "valid_epochs_only",
                "historical_private_fifo": "not_claimed",
                "fill_truth": "scenario_envelope_only",
                "latency": "configured_scenario_not_exchange_measurement",
                "live_profitability": "not_claimed",
            },
            "event_trace_retention": self.event_trace_retention(),
        }

    def _portfolio_risk_summary(self) -> dict[str, Any]:
        if self.cfg.mm_max_portfolio_notional <= 0:
            return {
                "enabled": False,
                "max_portfolio_notional": str(self.cfg.mm_max_portfolio_notional),
                "basis": "absolute_marked_inventory_plus_live_and_pending_order_notional",
            }
        reserved_notional, reserved_by_symbol, missing_marks = self._portfolio_notional_reservation()
        return {
            "enabled": True,
            "max_portfolio_notional": str(self.cfg.mm_max_portfolio_notional),
            "reserved_notional": None if reserved_notional is None else str(reserved_notional),
            "reserved_notional_by_symbol": {key: str(value) for key, value in reserved_by_symbol.items()},
            "missing_mark_symbols": list(missing_marks),
            "basis": "absolute_marked_inventory_plus_live_and_pending_order_notional",
            "fail_closed_on_missing_mark": True,
        }

    def _deterministic_state(self) -> dict[str, Any]:
        return {
            "books": {
                symbol: {
                    "bids": sorted(book.bids.items()),
                    "asks": sorted(book.asks.items()),
                    "last_update_id": book.last_update_id,
                    "sync_epoch": self._syncers[symbol].epoch,
                    "synced": self._syncers[symbol].synced,
                }
                for symbol, book in sorted(self._books.items())
            },
            "fill_audit": {
                "count": self.metrics.fill_count,
                "sha256": self.metrics.fill_audit_sha256,
            },
            "markout_audit": {
                "count": self.metrics.markout_event_count,
                "sha256": self.metrics.markout_audit_sha256,
                "pending": self.metrics.pending_markout_state(),
            },
            "inventory_lots": {symbol: self.metrics.inventory_lots(symbol) for symbol in sorted(self._books)},
            "realized_pnl": str(self.metrics.realized_pnl),
            "total_fees": str(self.metrics.total_fees),
            "pending_actions": [
                {"ts": event.ts, "order": event.order, "kind": event.kind, "symbol": event.symbol}
                for event in sorted(self._actions)
            ],
            "stream_state": {
                f"{symbol}:{route}": {
                    "epoch": epoch,
                    "valid": (
                        self._depth_stream_is_valid(symbol)
                        if route == "public"
                        else self._trade_stream_is_valid(symbol)
                    ),
                    "invalid_reason": self._stream_invalid_reason.get((symbol, route)),
                }
                for (symbol, route), epoch in sorted(self._stream_epochs.items())
                if route in {"public", "market"}
            },
            "clock_state": {
                "regressions": self._clock_regressions,
                "invalidated": self._clock_invalidated,
                "receive_clock_regressions": self._receive_clock_regressions,
                "last_receive_monotonic_ns": self._last_receive_monotonic_ns,
            },
            "capture_state": {
                "valid": self._capture_valid,
                "invalidations": self._capture_invalidations,
                "invalid_reason": self._capture_invalid_reason,
                "receive_sequence_gaps": self._receive_sequence_gaps,
                "capture_trailer_seen": self._capture_trailer_seen,
            },
            "continuation_state": encode_checkpoint(
                {
                    "id_counter": self._id_counter,
                    "trace_counter": self._trace_counter,
                    "last_event_index": self._last_event_index,
                    "actions": self._actions,
                    "active_fill_model": self.fill_model.__dict__,
                    "strategy": {
                        "returns": dict(self.strategy._returns),
                        "prev_mid": dict(self.strategy._prev_mid),
                        "recent_trade_signals": dict(self.strategy._recent_trade_signals),
                    },
                    "latency_sampler_state": self.latency_model.sampler_state(),
                }
            ),
        }

    def state_sha256(self) -> str:
        """Hash the complete deterministic kernel-facing state."""

        return state_hash(self._deterministic_state())

    def _prepare_output_summary(
        self,
        file_path: str | Path,
        metrics: SimulationMetrics,
        output_files: dict[str, Path],
        *,
        export_mode: str,
        manifest_seed: RunManifest | None = None,
    ) -> tuple[dict[str, Any], RunManifest]:
        # Keep the output helper compatible with small test/demonstration
        # metric doubles that implement the original one-argument contract;
        # the production metrics object receives the authoritative specs so
        # open positions remain visible even while their book is unavailable.
        if isinstance(metrics, SimulationMetrics):
            summary = metrics.get_summary(self._books, specs=self._specs)
        else:
            summary = metrics.get_summary(self._books)
        summary.update(self._summary_annotations())
        summary["state_sha256"] = self.state_sha256()
        seed = manifest_seed or build_run_manifest(file_path, self.cfg, output_files, adapter=self.adapter)
        summary["run_id"] = seed.run_id
        summary["input_sha256"] = seed.input["sha256"]
        summary["config_sha256"] = seed.config_sha256
        summary["code_identity"] = seed.code_identity
        summary["feed_adapter"] = seed.feed_adapter
        summary["instrument_specs"] = instrument_specs_snapshot(self._specs)
        summary["simulation_assumptions"] = simulation_assumptions_snapshot(self.fill_model.fill_assumption)
        summary["fill_assumption_profile"] = self.fill_model.fill_assumption.profile
        summary["fill_assumption"] = self.fill_model.fill_assumption.as_dict()
        summary["fill_assumption_diagnostics"] = self.fill_model.fill_assumption_diagnostics()
        summary["public_consumption_summary"] = self.fill_model.public_consumption_summary()
        summary["output_files"] = {name: str(path) for name, path in output_files.items()}
        summary["event_trace_count"] = self._event_trace_count
        summary["simulation_export"] = {
            "schema_version": "lob_sim.simulation_export.v1",
            "mode": export_mode,
            "memory_bounded_by_tape_duration": export_mode == "bounded_streaming",
            "detail_rows_complete_in_summary": export_mode == "fixture_scale_in_memory",
            "detail_rows_streamed": export_mode == "bounded_streaming",
            "markout_audit_file": "markouts" if "markouts" in output_files else None,
            "completion_record": (
                "manifest_with_absent_incomplete_sentinel" if export_mode == "bounded_streaming" else "manifest"
            ),
            "intended_use": (
                "ordinary_and_large_tape_simulation"
                if export_mode == "bounded_streaming"
                else "small_fixture_and_committed_evidence_generation"
            ),
        }
        return summary, seed

    def _write_manifest(
        self,
        file_path: str | Path,
        output_files: dict[str, Path],
        manifest_seed: RunManifest,
        *,
        include_artifact_bundle: bool = False,
    ) -> None:
        manifest = build_run_manifest(
            file_path,
            self.cfg,
            output_files,
            created_at_utc=manifest_seed.created_at_utc,
            source=manifest_seed.source,
            adapter=self.adapter,
            instrument_specs=self._specs,
        )
        if manifest.run_id != manifest_seed.run_id:
            raise RuntimeError(f"run identity changed during export: {manifest_seed.run_id!r} -> {manifest.run_id!r}")
        payload = manifest.as_dict()
        if include_artifact_bundle:
            payload["artifact_bundle"] = artifact_bundle_snapshot(payload["output_artifacts"])
        atomic_write_json(output_files["manifest"], payload)

    def write_outputs(self, file_path: str, metrics: SimulationMetrics) -> tuple[dict[str, Path], dict]:
        """Write the fixture-scale, full-retention compatibility artifact set."""

        if not getattr(metrics, "retain_audit_rows", True):
            raise RuntimeError(
                "write_outputs requires retained audit rows; use bounded streaming export for ordinary runs"
            )
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
        summary, manifest_seed = self._prepare_output_summary(
            file_path,
            metrics,
            output_files,
            export_mode="fixture_scale_in_memory",
        )
        atomic_write_json(summary_path, summary)
        atomic_write_summary_csv(summary_csv_path, summary)

        with StreamingCsvSink(trades_path, TRADE_AUDIT_FIELDS) as sink:
            for row in summary.get("fills", []):
                sink.write(row)
        self._write_event_trace(event_trace_path)
        self._write_manifest(file_path, output_files, manifest_seed)
        return output_files, summary

    def finalize_streaming_outputs(
        self,
        file_path: str | Path,
        metrics: SimulationMetrics,
        output_files: dict[str, Path],
        manifest_seed: RunManifest,
    ) -> tuple[dict[str, Path], dict[str, Any]]:
        """Finalize aggregate summaries and the manifest after streamed audits close."""

        if self._retain_event_trace or getattr(metrics, "retain_audit_rows", True):
            raise RuntimeError("bounded streaming finalization requires all detail retention to be disabled")
        required = {"event_trace", "markouts", "summary", "summary_csv", "trades", "manifest"}
        if set(output_files) != required:
            raise RuntimeError(f"unexpected streaming output contract: {sorted(output_files)}")
        audit_names = ("event_trace", "trades", "markouts")
        missing_audits = [name for name in audit_names if not output_files[name].is_file()]
        partial_audits = [
            str(output_files[name].with_name(output_files[name].name + ".partial"))
            for name in audit_names
            if output_files[name].with_name(output_files[name].name + ".partial").exists()
        ]
        if missing_audits or partial_audits:
            raise RuntimeError(
                f"streaming audits are not finalized: missing={missing_audits}, partials={partial_audits}"
            )
        verify_streaming_audit_files(
            output_files,
            event_trace_count=self._event_trace_count,
            fill_count=metrics.fill_count,
            fill_sha256=metrics.fill_audit_sha256,
            markout_count=metrics.markout_event_count,
            markout_sha256=metrics.markout_audit_sha256,
        )

        summary, seed = self._prepare_output_summary(
            file_path,
            metrics,
            output_files,
            export_mode="bounded_streaming",
            manifest_seed=manifest_seed,
        )
        atomic_write_json(output_files["summary"], summary)
        atomic_write_summary_csv(output_files["summary_csv"], summary)
        self._write_manifest(file_path, output_files, seed, include_artifact_bundle=True)
        return output_files, summary
