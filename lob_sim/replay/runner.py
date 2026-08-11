from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class ReplaySymbolResult:
    snapshot_seen: bool
    synced: bool
    gap_count: int
    total_levels: int
    last_update_id: int | None


@dataclass
class ReplayResult:
    events_processed: int
    depth_events: int
    gap_count: int
    events_per_sec: float
    elapsed_seconds: float
    symbols: dict[str, ReplaySymbolResult]


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

    events_processed = 0
    depth_events = 0
    gap_count = 0

    if verbose:
        print(f"[replay] starting replay for {path}", flush=True)

    for rec in iter_records(path):
        events_processed += 1
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
            evt = adapter.snapshot_from_record(rec, spec)
            if syncer is not None:
                try:
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
        )
        for symbol, syn in sorted(syncers.items())
    }
    if verbose:
        print(f"[replay] processed {events_processed} events in {elapsed:.2f}s ({events_per_sec:.2f} events/sec)")
        for symbol, result in symbol_results.items():
            print(
                f"[replay] symbol={symbol} snapshot={'yes' if result.snapshot_seen else 'no'} "
                f"synced={'yes' if result.synced else 'no'} gaps={result.gap_count} "
                f"levels={result.total_levels} last_update_id={result.last_update_id}"
            )
    return ReplayResult(
        events_processed=events_processed,
        depth_events=depth_events,
        gap_count=gap_count,
        events_per_sec=events_per_sec,
        elapsed_seconds=elapsed,
        symbols=symbol_results,
    )
