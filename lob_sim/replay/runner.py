from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict
import logging
import time

from ..book.local_book import BookInvariantError, LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import SymbolSpec
from ..config import Config
from ..replay.reader import RecordedEvent, iter_records
from .adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter

logger = logging.getLogger(__name__)


_STREAM_FAILURE_EVENTS = frozenset({"disconnect", "connect_failure", "parse_failure", "overflow"})
_CAPTURE_INVALIDATION_EVENTS = frozenset({"parse_failure", "overflow", "writer_failure", "capture_abort"})


@dataclass
class _MutableSymbolValidity:
    """Validity state accumulated while replaying one symbol.

    This is deliberately separate from ``BookSynchronizer``.  A book can be
    reconstructed while the capture clock or a companion trade route is
    invalid, and the audit output must expose that distinction instead of
    collapsing everything into ``synced``.
    """

    depth_stream_valid: bool = True
    trade_stream_valid: bool = True
    public_stream_epoch: int | None = None
    market_stream_epoch: int | None = None
    capture_sync_epoch: int | None = None
    snapshot_rejections: int = 0
    invalid_reasons: list[str] = field(default_factory=list)

    def invalidate(self, reason: str, *, route: str | None = None) -> None:
        if route == "market":
            self.trade_stream_valid = False
        elif route == "public":
            self.depth_stream_valid = False
        if reason not in self.invalid_reasons:
            self.invalid_reasons.append(reason)

    def recover(self, route: str) -> None:
        if route == "market":
            self.trade_stream_valid = True
        elif route == "public":
            self.depth_stream_valid = True


@dataclass(frozen=True)
class ReplaySymbolValidity:
    depth_stream_valid: bool
    trade_stream_valid: bool
    trade_stream_required: bool
    clock_valid: bool
    capture_valid: bool
    execution_inputs_valid: bool
    public_stream_epoch: int | None
    market_stream_epoch: int | None
    capture_sync_epoch: int | None
    snapshot_rejections: int
    invalid_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "depth_stream_valid": self.depth_stream_valid,
            "trade_stream_valid": self.trade_stream_valid,
            "trade_stream_required": self.trade_stream_required,
            "clock_valid": self.clock_valid,
            "capture_valid": self.capture_valid,
            "execution_inputs_valid": self.execution_inputs_valid,
            "public_stream_epoch": self.public_stream_epoch,
            "market_stream_epoch": self.market_stream_epoch,
            "capture_sync_epoch": self.capture_sync_epoch,
            "snapshot_rejections": self.snapshot_rejections,
            "invalid_reasons": list(self.invalid_reasons),
        }


@dataclass(frozen=True)
class ReplayValidityBoundary:
    """One receipt-anchored validity transition in the replay timeline."""

    kind: str
    scope: str
    event: str
    symbol: str
    route: str | None
    reason: str
    ts_local: float
    recv_seq: int | None
    recv_monotonic_ns: int | None
    stream_epoch: int | None
    sync_epoch: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "scope": self.scope,
            "event": self.event,
            "symbol": self.symbol,
            "route": self.route,
            "reason": self.reason,
            "ts_local": self.ts_local,
            "recv_seq": self.recv_seq,
            "recv_monotonic_ns": self.recv_monotonic_ns,
            "stream_epoch": self.stream_epoch,
            "sync_epoch": self.sync_epoch,
        }


@dataclass(frozen=True)
class ReplayValidity:
    """Capture-level integrity observed during a diagnostic replay.

    ``claim_ready`` is intentionally stricter than a successful parser run:
    schema-v3 receipt identity, a monotonic receive clock, a complete trailer,
    and valid execution inputs for every symbol are required.  Legacy tapes
    remain replayable for compatibility but cannot silently become claim-ready
    evidence.
    """

    schema_version: int
    receive_clock: bool
    capture_valid: bool
    clock_valid: bool
    capture_trailer_seen: bool
    last_receive_seq: int | None
    last_receive_monotonic_ns: int | None
    receive_sequence_gaps: int
    receive_sequence_regressions: int
    receive_clock_regressions: int
    capture_invalidations: int
    invalid_reasons: tuple[str, ...]
    boundaries: tuple[ReplayValidityBoundary, ...]
    boundary_count: int
    boundaries_omitted: int
    claim_ready: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "receive_clock": self.receive_clock,
            "capture_valid": self.capture_valid,
            "clock_valid": self.clock_valid,
            "capture_trailer_seen": self.capture_trailer_seen,
            "last_receive_seq": self.last_receive_seq,
            "last_receive_monotonic_ns": self.last_receive_monotonic_ns,
            "receive_sequence_gaps": self.receive_sequence_gaps,
            "receive_sequence_regressions": self.receive_sequence_regressions,
            "receive_clock_regressions": self.receive_clock_regressions,
            "capture_invalidations": self.capture_invalidations,
            "invalid_reasons": list(self.invalid_reasons),
            "boundaries": [boundary.as_dict() for boundary in self.boundaries],
            "boundary_count": self.boundary_count,
            "boundaries_omitted": self.boundaries_omitted,
            "claim_ready": self.claim_ready,
        }


class _ReplayValidityTracker:
    """Small independent validity reducer for replay/audit diagnostics."""

    _MAX_BOUNDARIES = 4096

    def __init__(self, *, trade_stream_required: bool) -> None:
        self.trade_stream_required = trade_stream_required
        self.schema_version = 1
        self.receive_clock = False
        self.capture_valid = True
        self.clock_valid = True
        self.capture_trailer_seen = False
        self.last_receive_seq: int | None = None
        self.last_receive_monotonic_ns: int | None = None
        self.receive_sequence_gaps = 0
        self.receive_sequence_regressions = 0
        self.receive_clock_regressions = 0
        self.capture_invalidations = 0
        self.invalid_reasons: list[str] = []
        self.boundaries: list[ReplayValidityBoundary] = []
        self.boundary_count = 0
        self.boundaries_omitted = 0
        self.symbols: dict[str, _MutableSymbolValidity] = {}

    def _symbol(self, symbol: str) -> _MutableSymbolValidity:
        return self.symbols.setdefault(
            symbol,
            _MutableSymbolValidity(
                depth_stream_valid=self.schema_version < 3,
                trade_stream_valid=self.schema_version < 3,
            ),
        )

    def _record_boundary(
        self,
        rec: RecordedEvent,
        capture: dict[str, object],
        *,
        kind: str,
        scope: str,
        reason: str,
        symbol: str | None = None,
    ) -> None:
        self.boundary_count += 1
        if len(self.boundaries) >= self._MAX_BOUNDARIES:
            self.boundaries_omitted += 1
            return
        self.boundaries.append(
            ReplayValidityBoundary(
                kind=kind,
                scope=scope,
                event=str(rec.data.get("event") or rec.type),
                symbol=rec.symbol if symbol is None else symbol,
                route=(str(capture.get("route")) if capture.get("route") is not None else None),
                reason=reason,
                ts_local=rec.ts_local,
                recv_seq=self._int_or_none(capture.get("recvSeq")),
                recv_monotonic_ns=self._int_or_none(capture.get("recvMonotonicNs")),
                stream_epoch=self._int_or_none(capture.get("streamEpoch")),
                sync_epoch=self._int_or_none(capture.get("syncEpoch")),
            )
        )

    def _invalidate_capture(self, reason: str, rec: RecordedEvent, capture: dict[str, object]) -> None:
        if self.capture_valid:
            self.capture_invalidations += 1
        self.capture_valid = False
        if reason not in self.invalid_reasons:
            self.invalid_reasons.append(reason)
        self._record_boundary(rec, capture, kind="invalidated", scope="capture", reason=reason)

    def _invalidate_clock(self, reason: str, rec: RecordedEvent, capture: dict[str, object]) -> None:
        self.clock_valid = False
        if reason not in self.invalid_reasons:
            self.invalid_reasons.append(reason)
        self._record_boundary(rec, capture, kind="invalidated", scope="clock", reason=reason)

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed

    def _observe_receipt(self, rec: RecordedEvent, capture: dict[str, object]) -> None:
        if self.schema_version < 3:
            return
        missing = [key for key in ("recvSeq", "recvMonotonicNs", "route") if capture.get(key) is None]
        if missing:
            self._invalidate_capture("missing_capture_metadata:" + ",".join(missing), rec, capture)
            return

        sequence = self._int_or_none(capture.get("recvSeq"))
        if sequence is None or sequence < 0:
            self._invalidate_capture("invalid_capture_metadata:recvSeq", rec, capture)
        elif self.last_receive_seq is not None and sequence <= self.last_receive_seq:
            self.receive_sequence_regressions += 1
            self._invalidate_capture("non_increasing_receive_sequence", rec, capture)
        else:
            if self.last_receive_seq is not None and sequence > self.last_receive_seq + 1:
                self.receive_sequence_gaps += sequence - self.last_receive_seq - 1
                self._invalidate_capture("receive_sequence_gap", rec, capture)
            self.last_receive_seq = sequence

        monotonic_ns = self._int_or_none(capture.get("recvMonotonicNs"))
        if monotonic_ns is None or monotonic_ns < 0:
            self._invalidate_capture("invalid_capture_metadata:recvMonotonicNs", rec, capture)
        elif self.last_receive_monotonic_ns is not None and monotonic_ns < self.last_receive_monotonic_ns:
            self.receive_clock_regressions += 1
            self._invalidate_clock("receive_monotonic_regression", rec, capture)
        else:
            self.last_receive_monotonic_ns = monotonic_ns

    def _observe_epoch(self, rec: RecordedEvent, capture: dict[str, object], symbol: _MutableSymbolValidity) -> None:
        route = str(capture.get("route") or rec.data.get("route") or "")
        stream_epoch = self._int_or_none(capture.get("streamEpoch"))
        sync_epoch = self._int_or_none(capture.get("syncEpoch"))
        event_name = str(rec.data.get("event", "")) if rec.type == "captureEvent" else ""
        if route == "public":
            previous_epoch = symbol.public_stream_epoch
            if stream_epoch is not None:
                symbol.public_stream_epoch = stream_epoch
                if previous_epoch is not None and stream_epoch != previous_epoch:
                    reason = "public_stream_epoch_changed"
                    symbol.invalidate(reason, route=route)
                    self._record_boundary(rec, capture, kind="invalidated", scope="stream", reason=reason)
            if sync_epoch is not None:
                if symbol.capture_sync_epoch is not None and sync_epoch != symbol.capture_sync_epoch:
                    reason = "capture_sync_epoch_changed"
                    symbol.invalidate(reason, route=route)
                    self._record_boundary(rec, capture, kind="invalidated", scope="sync", reason=reason)
                symbol.capture_sync_epoch = sync_epoch
        elif route == "market" and stream_epoch is not None:
            previous_epoch = symbol.market_stream_epoch
            symbol.market_stream_epoch = stream_epoch
            if previous_epoch is not None and stream_epoch != previous_epoch:
                reason = "market_stream_epoch_changed"
                symbol.invalidate(reason, route=route)
                self._record_boundary(rec, capture, kind="invalidated", scope="stream", reason=reason)
        reason = str(
            rec.data.get("validationError")
            or capture.get("validationError")
            or rec.data.get("reason")
            or capture.get("reason")
            or event_name
            or "unspecified"
        )
        if route in {"public", "market"}:
            if event_name in _STREAM_FAILURE_EVENTS:
                failure_reason = f"{route}_stream_{event_name}: {reason}"
                symbol.invalidate(failure_reason, route=route)
                self._record_boundary(rec, capture, kind="invalidated", scope="stream", reason=failure_reason)
            elif event_name in {"connect", "reconnect"}:
                was_valid = symbol.depth_stream_valid if route == "public" else symbol.trade_stream_valid
                symbol.recover(route)
                if not was_valid:
                    self._record_boundary(rec, capture, kind="recovered", scope="stream", reason=f"{route}_connected")

        if rec.type == "snapshot" and capture.get("snapshotAccepted") is False:
            symbol.snapshot_rejections += 1
            rejection_reason = f"snapshot_rejected: {reason}"
            symbol.invalidate(rejection_reason, route="public")
            self._record_boundary(rec, capture, kind="invalidated", scope="book", reason=rejection_reason)

    def note_symbol_boundary(
        self,
        rec: RecordedEvent,
        capture: dict[str, object],
        *,
        reason: str,
        scope: str = "book",
    ) -> None:
        """Retain a book boundary detected by the sync state machine.

        Some legacy or hand-authored tapes do not increment ``syncEpoch`` when
        a new snapshot replaces an already-synced book.  The synchronizer can
        still recover the next epoch, but the validity reducer must preserve
        the boundary instead of treating the recovered book as uninterrupted.
        The stream validity bit is intentionally left unchanged here: a
        successful subsequent bridge may make the final book usable while the
        claim gate remains blocked by this recorded boundary.
        """

        symbol = self._symbol(rec.symbol)
        symbol.invalidate(reason)
        self._record_boundary(rec, capture, kind="invalidated", scope=scope, reason=reason)

    def observe(self, rec: RecordedEvent) -> None:
        if rec.type == "captureMeta":
            version = self._int_or_none(rec.data.get("schemaVersion"))
            if version is None or version < 1:
                self._invalidate_capture("invalid_capture_schema_version", rec, {})
                return
            self.schema_version = version
            self.receive_clock = rec.data.get("clock") == "receive_time"
            if version >= 3 and not self.receive_clock:
                self._invalidate_clock("schema_v3_without_receive_clock", rec, {})
            return

        capture_value = rec.data.get("_capture")
        capture = dict(capture_value) if isinstance(capture_value, dict) else {}
        if rec.type == "captureEvent" and not capture:
            capture = {key: rec.data[key] for key in ("recvSeq", "recvMonotonicNs", "route") if key in rec.data}
        if self.schema_version >= 3:
            self._observe_receipt(rec, capture)
        if rec.type == "captureEvent" and rec.data.get("event") in _CAPTURE_INVALIDATION_EVENTS:
            event_name = str(rec.data.get("event"))
            reason = str(
                rec.data.get("validationError")
                or capture.get("validationError")
                or rec.data.get("reason")
                or capture.get("reason")
                or event_name
            )
            self._invalidate_capture(f"{event_name}: {reason}", rec, capture)
        if rec.type == "captureEvent" and rec.data.get("event") == "capture_trailer":
            self.capture_trailer_seen = True

        symbol_name = rec.symbol
        if symbol_name not in {"", "*"}:
            symbol = self._symbol(symbol_name)
            depth_was_valid = symbol.depth_stream_valid
            trade_was_valid = symbol.trade_stream_valid
            had_public_epoch = symbol.public_stream_epoch is not None
            had_market_epoch = symbol.market_stream_epoch is not None
            self._observe_epoch(rec, capture, symbol)
            if rec.type in {"depthUpdate", "snapshot"} and capture.get("snapshotAccepted") is not False:
                if depth_was_valid or not had_public_epoch:
                    symbol.recover("public")
                    if not depth_was_valid:
                        self._record_boundary(rec, capture, kind="recovered", scope="stream", reason="public_observed")
            elif rec.type == "aggTrade":
                if trade_was_valid or not had_market_epoch:
                    symbol.recover("market")
                    if not trade_was_valid:
                        self._record_boundary(rec, capture, kind="recovered", scope="stream", reason="market_observed")

    def symbol_validity(self, symbol: str, *, synced: bool, gap_count: int) -> ReplaySymbolValidity:
        state = self.symbols.get(symbol, _MutableSymbolValidity(self.schema_version < 3, self.schema_version < 3))
        reasons = list(state.invalid_reasons)
        if gap_count:
            reason = f"book_gap_count:{gap_count}"
            if reason not in reasons:
                reasons.append(reason)
        book_valid = synced and state.depth_stream_valid and gap_count == 0
        execution_valid = (
            book_valid
            and (not self.trade_stream_required or state.trade_stream_valid)
            and self.clock_valid
            and self.capture_valid
        )
        return ReplaySymbolValidity(
            depth_stream_valid=state.depth_stream_valid,
            trade_stream_valid=state.trade_stream_valid,
            trade_stream_required=self.trade_stream_required,
            clock_valid=self.clock_valid,
            capture_valid=self.capture_valid,
            execution_inputs_valid=execution_valid,
            public_stream_epoch=state.public_stream_epoch,
            market_stream_epoch=state.market_stream_epoch,
            capture_sync_epoch=state.capture_sync_epoch,
            snapshot_rejections=state.snapshot_rejections,
            invalid_reasons=tuple(reasons),
        )

    def summary(self, symbol_validities: dict[str, ReplaySymbolValidity]) -> ReplayValidity:
        claim_ready = (
            self.schema_version >= 3
            and self.receive_clock
            and self.capture_trailer_seen
            and self.capture_valid
            and self.clock_valid
            and bool(symbol_validities)
            and all(value.execution_inputs_valid for value in symbol_validities.values())
            and all(not value.invalid_reasons for value in symbol_validities.values())
            and not any(boundary.kind == "invalidated" for boundary in self.boundaries)
            and self.boundaries_omitted == 0
        )
        return ReplayValidity(
            schema_version=self.schema_version,
            receive_clock=self.receive_clock,
            capture_valid=self.capture_valid,
            clock_valid=self.clock_valid,
            capture_trailer_seen=self.capture_trailer_seen,
            last_receive_seq=self.last_receive_seq,
            last_receive_monotonic_ns=self.last_receive_monotonic_ns,
            receive_sequence_gaps=self.receive_sequence_gaps,
            receive_sequence_regressions=self.receive_sequence_regressions,
            receive_clock_regressions=self.receive_clock_regressions,
            capture_invalidations=self.capture_invalidations,
            invalid_reasons=tuple(self.invalid_reasons),
            boundaries=tuple(self.boundaries),
            boundary_count=self.boundary_count,
            boundaries_omitted=self.boundaries_omitted,
            claim_ready=claim_ready,
        )


@dataclass
class ReplaySymbolResult:
    snapshot_seen: bool
    synced: bool
    gap_count: int
    total_levels: int
    last_update_id: int | None
    validity: ReplaySymbolValidity | None = None


@dataclass
class ReplayResult:
    events_processed: int
    depth_events: int
    gap_count: int
    events_per_sec: float
    elapsed_seconds: float
    symbols: dict[str, ReplaySymbolResult]
    validity: ReplayValidity | None = None


def symbol_spec_from_record(
    record: RecordedEvent,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> SymbolSpec | None:
    spec = adapter.instrument_spec_from_record(record)
    if spec is None:
        return None
    return spec


def parse_symbol_spec_from_record(
    record: RecordedEvent,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> tuple[str, Decimal, Decimal] | None:
    spec = symbol_spec_from_record(record, adapter)
    if spec is None:
        return None
    return spec.symbol, spec.tick_size, spec.step_size


def replay(
    path: str | Path,
    config: Config | None = None,
    verbose: bool = False,
    progress_every: int = 5000,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> ReplayResult:
    path = Path(path)
    start = time.perf_counter()
    symbols: Dict[str, SymbolSpec] = {}
    syncers: Dict[str, BookSynchronizer] = {}
    top_n = config.book_top_n if config else 50
    resync = bool(config.resync_on_gap) if config else True
    trade_stream_required = (
        True
        if config is None
        else bool(
            config.effective_fill_assumption.agg_trades_consume_queue
            or config.mm_strategy_profile in {"layered_mm", "research_mm"}
        )
    )
    validity_tracker = _ReplayValidityTracker(trade_stream_required=trade_stream_required)

    events_processed = 0
    depth_events = 0
    gap_count = 0

    if verbose:
        print(f"[replay] starting replay for {path}", flush=True)

    for rec in iter_records(path):
        events_processed += 1
        validity_tracker.observe(rec)

        if rec.type == "captureEvent":
            event_name = str(rec.data.get("event", ""))
            capture_value = rec.data.get("_capture")
            capture = dict(capture_value) if isinstance(capture_value, dict) else rec.data
            route = str(capture.get("route") or rec.data.get("route") or "")
            if event_name in {"writer_failure", "capture_abort", "parse_failure", "overflow"} and route == "control":
                for active_syncer in syncers.values():
                    active_syncer.begin_resync(f"capture_{event_name}")
            if (
                rec.symbol in syncers
                and route == "public"
                and (
                    event_name in _STREAM_FAILURE_EVENTS or event_name in {"connect", "reconnect", "snapshot_rejected"}
                )
            ):
                syncers[rec.symbol].begin_resync(f"capture_{event_name}")
            continue

        if rec.type == "exchangeInfo":
            spec = symbol_spec_from_record(rec, adapter)
            if spec is None:
                continue
            symbols[spec.symbol] = spec
            if spec.symbol not in syncers:
                syncers[spec.symbol] = BookSynchronizer(
                    LocalOrderBook(symbol=spec.symbol, spec=spec, top_n=top_n),
                    resync_on_gap=resync,
                )
            if verbose:
                print(
                    f"[replay] loaded symbol={spec.symbol} tick_size={spec.tick_size} step_size={spec.step_size}",
                    flush=True,
                )
            continue

        if rec.symbol not in symbols and rec.type != "exchangeInfo":
            continue

        spec = symbols[rec.symbol]
        syncer = syncers.get(rec.symbol)
        if syncer is None and rec.type in {"snapshot", "depthUpdate", "aggTrade"}:
            syncer = BookSynchronizer(
                LocalOrderBook(symbol=rec.symbol, spec=spec, top_n=top_n),
                resync_on_gap=resync,
            )
            syncers[rec.symbol] = syncer

        if rec.type == "snapshot":
            capture_value = rec.data.get("_capture", {})
            capture = capture_value if isinstance(capture_value, dict) else {}
            if capture.get("snapshotAccepted") is False:
                if syncer is not None:
                    syncer.begin_resync("snapshot_rejected")
                continue
            evt = adapter.snapshot_from_record(rec, spec)
            if syncer is not None:
                try:
                    if syncer.synced:
                        reason = "snapshot_replaced_synced_book"
                        syncer.begin_resync(reason)
                        validity_tracker.note_symbol_boundary(rec, capture, reason=reason)
                    syncer.on_snapshot(evt)
                except (BookSyncGapError, BookInvariantError) as exc:
                    logger.warning("Invalid snapshot while replaying %s: %s", rec.symbol, exc)
            continue

        if rec.type == "depthUpdate":
            depth_events += 1
            if syncer is None:
                continue
            depth = adapter.depth_update_from_record(rec, spec)
            gap_count_before = syncer.gap_count
            try:
                if syncer is not None:
                    syncer.on_depth_update(depth)
            except (BookSyncGapError, BookInvariantError):
                logger.warning("Gap while replaying %s", rec.symbol)
            gap_count += max(0, syncer.gap_count - gap_count_before)

        if verbose and progress_every > 0 and events_processed % progress_every == 0:
            print(
                f"[replay] events={events_processed} depth={depth_events} gaps={gap_count} "
                f"last={rec.symbol}:{rec.type}",
                flush=True,
            )

    elapsed = time.perf_counter() - start
    events_per_sec = events_processed / elapsed if elapsed > 0 else 0.0
    symbol_results = {
        symbol: ReplaySymbolResult(
            snapshot_seen=syn.snapshot_id is not None,
            synced=syn.synced,
            gap_count=syn.gap_count,
            total_levels=syn.book.total_levels(),
            last_update_id=syn.last_update_id,
            validity=validity_tracker.symbol_validity(
                symbol,
                synced=syn.synced,
                gap_count=syn.gap_count,
            ),
        )
        for symbol, syn in sorted(syncers.items())
    }
    symbol_validities = {
        symbol: result.validity for symbol, result in symbol_results.items() if result.validity is not None
    }
    validity = validity_tracker.summary(symbol_validities)
    if verbose:
        print(f"[replay] processed {events_processed} events in {elapsed:.2f}s ({events_per_sec:.2f} events/sec)")
        for symbol, result in symbol_results.items():
            print(
                f"[replay] symbol={symbol} snapshot={'yes' if result.snapshot_seen else 'no'} "
                f"synced={'yes' if result.synced else 'no'} gaps={result.gap_count} "
                f"levels={result.total_levels} last_update_id={result.last_update_id} "
                f"execution_inputs_valid={result.validity.execution_inputs_valid if result.validity else False}"
            )
    return ReplayResult(
        events_processed=events_processed,
        depth_events=depth_events,
        gap_count=gap_count,
        events_per_sec=events_per_sec,
        elapsed_seconds=elapsed,
        symbols=symbol_results,
        validity=validity,
    )
