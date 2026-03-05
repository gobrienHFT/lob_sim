from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict
import logging
import time

from ..book.local_book import LocalOrderBook
from ..book.sync import BookSyncGapError, BookSynchronizer
from ..book.types import DepthUpdateEvent, SnapshotEvent
from ..config import Config
from ..replay.reader import RecordedEvent, iter_records
from ..book.types import SymbolSpec

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    events_processed: int
    depth_events: int
    gap_count: int
    events_per_sec: float


def parse_symbol_spec_from_record(record: RecordedEvent) -> tuple[str, Decimal, Decimal] | None:
    if record.type != "exchangeInfo":
        return None
    data = record.data
    tick_size = data.get("tickSize")
    step_size = data.get("stepSize")
    if tick_size is None or step_size is None:
        return None
    return (
        record.symbol,
        Decimal(str(tick_size)),
        Decimal(str(step_size)),
    )


def replay(
    path: str | Path,
    config: Config | None = None,
    verbose: bool = False,
    progress_every: int = 5000,
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
            parsed = parse_symbol_spec_from_record(rec)
            if parsed is None:
                continue
            symbol, tick_size, step_size = parsed
            symbols[symbol] = SymbolSpec(symbol=symbol, tick_size=tick_size, step_size=step_size)
            if symbol not in syncers:
                syncers[symbol] = BookSynchronizer(
                    LocalOrderBook(symbol=symbol, spec=symbols[symbol], top_n=top_n),
                    resync_on_gap=resync,
                )
            if verbose:
                print(
                    f"[replay] loaded symbol={symbol} tick_size={tick_size} step_size={step_size}",
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
            bids = [(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("bids", [])]
            asks = [(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("asks", [])]
            evt = SnapshotEvent(
                symbol=rec.symbol,
                last_update_id=int(rec.data["lastUpdateId"]),
                bids=bids,
                asks=asks,
            )
            if syncer is not None:
                syncer.on_snapshot(evt)
            continue

        if rec.type == "depthUpdate":
            depth_events += 1
            if syncer is None:
                continue
            depth = DepthUpdateEvent(
                symbol=rec.symbol,
                first_update_id=int(rec.data["U"]),
                final_update_id=int(rec.data["u"]),
                prev_update_id=int(rec.data.get("pu", rec.data.get("U", 0))),
                bids=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("b", [])],
                asks=[(spec.price_to_tick(p), spec.qty_to_lot(q)) for p, q in rec.data.get("a", [])],
                ts_local=float(rec.ts_local),
            )
            try:
                if syncer is not None:
                    syncer.on_depth_update(depth)
            except BookSyncGapError:
                gap_count += 1
                logger.warning("Gap while replaying %s", rec.symbol)

        if verbose and progress_every > 0 and events_processed % progress_every == 0:
            print(
                f"[replay] events={events_processed} depth={depth_events} gaps={gap_count} "
                f"last={rec.symbol}:{rec.type}",
                flush=True,
            )

    elapsed = time.perf_counter() - start
    events_per_sec = events_processed / elapsed if elapsed > 0 else 0.0
    print(f"Replay processed {events_processed} events in {elapsed:.2f}s ({events_per_sec:.2f} events/sec)")
    for symbol, syn in syncers.items():
        print(
            f"Symbol {symbol}: snapshot={'yes' if syn.snapshot_id is not None else 'no'}, "
            f"gaps={syn.gap_count}, levels={syn.book.total_levels()}"
        )
    return ReplayResult(
        events_processed=events_processed,
        depth_events=depth_events,
        gap_count=gap_count,
        events_per_sec=events_per_sec,
    )
