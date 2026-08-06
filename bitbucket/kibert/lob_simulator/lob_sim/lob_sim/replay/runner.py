from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..book.local_book import LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import DepthUpdateEvent, SnapshotEvent, SymbolSpec
from ..config import Config
from .reader import RecordedEvent, iter_records

logger = logging.getLogger(__name__)


class ReplayIntegrityError(RuntimeError):
    """Raised when capture ordering metadata contradicts file order."""


@dataclass(frozen=True)
class ReplaySymbolState:
    synced_at_end: bool
    snapshot_id: int | None
    last_update_id: int | None
    gap_count: int
    sync_epoch: int
    total_levels: int
    best_bid_tick: int | None
    best_ask_tick: int | None
    book_checksum_sha256: str


@dataclass(frozen=True)
class ReplayResult:
    events_processed: int
    depth_events: int
    gap_count: int
    events_per_sec: float
    capture_schema_version: int
    capture_clock: str
    capture_sync_epoch_transitions: int
    last_receive_sequence: int | None
    symbols: dict[str, ReplaySymbolState]

    @property
    def integrity_ok(self) -> bool:
        return bool(self.symbols) and all(state.synced_at_end for state in self.symbols.values())


def parse_symbol_spec_from_record(record: RecordedEvent) -> tuple[str, Decimal, Decimal] | None:
    if record.type != "exchangeInfo":
        return None
    data = record.data
    tick_size = data.get("tickSize")
    step_size = data.get("stepSize")
    if tick_size is None or step_size is None:
        return None
    return record.symbol, Decimal(str(tick_size)), Decimal(str(step_size))


def _book_checksum(book: LocalOrderBook) -> str:
    payload = {
        "symbol": book.symbol,
        "last_update_id": book.last_update_id,
        "bids": sorted(book.bids.items()),
        "asks": sorted(book.asks.items()),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _capture_fields(record: RecordedEvent) -> dict | None:
    capture = record.data.get("_capture")
    if capture is None:
        return None
    if not isinstance(capture, dict):
        raise ReplayIntegrityError(f"_capture must be an object for {record.symbol} {record.type}")
    return capture


def replay(path: str | Path, config: Config | None = None) -> ReplayResult:
    replay_path = Path(path)
    start = time.perf_counter()
    specs: dict[str, SymbolSpec] = {}
    syncers: dict[str, BookSynchronizer] = {}
    top_n = config.book_top_n if config else 50
    resync = bool(config.resync_on_gap) if config else True

    events_processed = 0
    depth_events = 0
    capture_schema_version = 1
    capture_clock = "legacy_exchange_event_time"
    capture_epochs: dict[str, int] = {}
    capture_epoch_transitions = 0
    last_receive_sequence: int | None = None

    for record in iter_records(replay_path):
        events_processed += 1
        if record.type == "captureMeta":
            capture_schema_version = int(record.data.get("schemaVersion", 1))
            capture_clock = str(record.data.get("clock", "unspecified"))
            continue
        if record.type == "exchangeInfo":
            parsed = parse_symbol_spec_from_record(record)
            if parsed is None:
                continue
            symbol, tick_size, step_size = parsed
            specs[symbol] = SymbolSpec(symbol=symbol, tick_size=tick_size, step_size=step_size)
            syncers.setdefault(
                symbol,
                BookSynchronizer(
                    LocalOrderBook(symbol=symbol, spec=specs[symbol], top_n=top_n),
                    resync_on_gap=resync,
                ),
            )
            continue
        if record.symbol not in specs:
            continue

        spec = specs[record.symbol]
        syncer = syncers[record.symbol]

        capture = _capture_fields(record)
        if capture is not None and capture.get("recvSeq") is not None:
            receive_sequence = capture["recvSeq"]
            if (
                isinstance(receive_sequence, bool)
                or not isinstance(receive_sequence, int)
                or receive_sequence <= 0
                or (last_receive_sequence is not None and receive_sequence <= last_receive_sequence)
            ):
                raise ReplayIntegrityError(
                    f"recvSeq must be a positive integer increasing in file order: {receive_sequence!r}"
                )
            last_receive_sequence = receive_sequence

        if capture is not None and capture.get("syncEpoch") is not None:
            epoch = capture["syncEpoch"]
            if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
                raise ReplayIntegrityError(f"Invalid syncEpoch: {epoch!r}")
            previous_epoch = capture_epochs.get(record.symbol)
            if previous_epoch is not None and epoch < previous_epoch:
                raise ReplayIntegrityError(
                    f"syncEpoch regressed for {record.symbol}: {previous_epoch} -> {epoch}"
                )
            capture_epochs[record.symbol] = epoch
            if previous_epoch is not None and epoch > previous_epoch:
                syncer.begin_resync(str(capture.get("reason") or "capture_sync_epoch_transition"))
                capture_epoch_transitions += 1

        if record.type == "snapshot":
            if capture is not None and capture.get("snapshotAccepted") is False:
                logger.warning(
                    "Collector rejected snapshot while recording %s: %s",
                    record.symbol,
                    capture.get("validationError", "unspecified validation error"),
                )
                continue
            snapshot = SnapshotEvent(
                symbol=record.symbol,
                last_update_id=int(record.data["lastUpdateId"]),
                bids=[
                    (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty))
                    for price, qty in record.data.get("bids", [])
                ],
                asks=[
                    (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty))
                    for price, qty in record.data.get("asks", [])
                ],
            )
            if syncer.synced:
                syncer.begin_resync("snapshot_replaced_synced_book")
            try:
                syncer.on_snapshot(snapshot)
            except BookSyncGapError as exc:
                logger.warning("Rejected snapshot while replaying %s: %s", record.symbol, exc)
            continue

        if record.type == "depthUpdate":
            depth_events += 1
            depth = DepthUpdateEvent(
                symbol=record.symbol,
                first_update_id=int(record.data["U"]),
                final_update_id=int(record.data["u"]),
                prev_update_id=int(record.data.get("pu", record.data.get("U", 0))),
                bids=[
                    (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty))
                    for price, qty in record.data.get("b", [])
                ],
                asks=[
                    (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty))
                    for price, qty in record.data.get("a", [])
                ],
                ts_local=float(record.ts_local),
            )
            try:
                syncer.on_depth_update(depth)
            except BookSyncGapError as exc:
                logger.warning("Gap while replaying %s: %s", record.symbol, exc)

    elapsed = time.perf_counter() - start
    symbol_states = {
        symbol: ReplaySymbolState(
            synced_at_end=syncer.synced,
            snapshot_id=syncer.snapshot_id,
            last_update_id=syncer.last_update_id,
            gap_count=syncer.gap_count,
            sync_epoch=syncer.epoch,
            total_levels=syncer.book.total_levels(),
            best_bid_tick=syncer.book.best_bid(),
            best_ask_tick=syncer.book.best_ask(),
            book_checksum_sha256=_book_checksum(syncer.book),
        )
        for symbol, syncer in sorted(syncers.items())
    }
    return ReplayResult(
        events_processed=events_processed,
        depth_events=depth_events,
        gap_count=sum(state.gap_count for state in symbol_states.values()),
        events_per_sec=events_processed / elapsed if elapsed > 0 else 0.0,
        capture_schema_version=capture_schema_version,
        capture_clock=capture_clock,
        capture_sync_epoch_transitions=capture_epoch_transitions,
        last_receive_sequence=last_receive_sequence,
        symbols=symbol_states,
    )
