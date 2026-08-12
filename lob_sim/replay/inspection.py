from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .reader import iter_records


_CAPTURE_INVALIDATION_EVENTS = frozenset({"parse_failure", "overflow", "writer_failure", "capture_abort"})


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CaptureLiveness:
    """Bounded receipt/lifecycle diagnostics derived in one tape pass."""

    schema_version: int | None
    receive_clock: bool
    records_with_receipt: int
    records_missing_receipt: int
    first_recv_seq: int | None
    last_recv_seq: int | None
    receive_sequence_gaps: int
    receive_sequence_regressions: int
    first_recv_monotonic_ns: int | None
    last_recv_monotonic_ns: int | None
    monotonic_regressions: int
    max_interarrival_ns: int | None
    routes: dict[str, int]
    capture_event_counts: dict[str, int]
    invalidation_event_count: int
    trailer_seen: bool
    receipt_integrity_ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receive_clock": self.receive_clock,
            "records_with_receipt": self.records_with_receipt,
            "records_missing_receipt": self.records_missing_receipt,
            "first_recv_seq": self.first_recv_seq,
            "last_recv_seq": self.last_recv_seq,
            "receive_sequence_gaps": self.receive_sequence_gaps,
            "receive_sequence_regressions": self.receive_sequence_regressions,
            "first_recv_monotonic_ns": self.first_recv_monotonic_ns,
            "last_recv_monotonic_ns": self.last_recv_monotonic_ns,
            "monotonic_regressions": self.monotonic_regressions,
            "max_interarrival_ns": self.max_interarrival_ns,
            "routes": self.routes,
            "capture_event_counts": self.capture_event_counts,
            "invalidation_event_count": self.invalidation_event_count,
            "trailer_seen": self.trailer_seen,
            "receipt_integrity_ok": self.receipt_integrity_ok,
        }


def _empty_capture_liveness() -> CaptureLiveness:
    return CaptureLiveness(
        schema_version=None,
        receive_clock=False,
        records_with_receipt=0,
        records_missing_receipt=0,
        first_recv_seq=None,
        last_recv_seq=None,
        receive_sequence_gaps=0,
        receive_sequence_regressions=0,
        first_recv_monotonic_ns=None,
        last_recv_monotonic_ns=None,
        monotonic_regressions=0,
        max_interarrival_ns=None,
        routes={},
        capture_event_counts={},
        invalidation_event_count=0,
        trailer_seen=False,
        receipt_integrity_ok=False,
    )


@dataclass(frozen=True)
class StreamInspection:
    path: str
    file_size_bytes: int
    sha256: str
    records: int
    counts_by_type: dict[str, int]
    counts_by_symbol: dict[str, int]
    first_ts_local: float | None
    last_ts_local: float | None
    duration_seconds: float | None
    symbols: list[str]
    symbol_specs: dict[str, dict[str, str]]
    capture_liveness: CaptureLiveness = field(default_factory=_empty_capture_liveness)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_size_bytes": self.file_size_bytes,
            "sha256": self.sha256,
            "records": self.records,
            "counts_by_type": self.counts_by_type,
            "counts_by_symbol": self.counts_by_symbol,
            "first_ts_local": self.first_ts_local,
            "last_ts_local": self.last_ts_local,
            "duration_seconds": self.duration_seconds,
            "symbols": self.symbols,
            "symbol_specs": self.symbol_specs,
            "capture_liveness": self.capture_liveness.as_dict(),
        }


def inspect_stream(path: str | Path) -> StreamInspection:
    p = Path(path)
    type_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    symbol_specs: dict[str, dict[str, str]] = {}
    schema_version: int | None = None
    receive_clock = False
    records_with_receipt = 0
    records_missing_receipt = 0
    first_recv_seq: int | None = None
    last_recv_seq: int | None = None
    receive_sequence_gaps = 0
    receive_sequence_regressions = 0
    first_recv_monotonic_ns: int | None = None
    last_recv_monotonic_ns: int | None = None
    monotonic_regressions = 0
    max_interarrival_ns: int | None = None
    route_counts: Counter[str] = Counter()
    capture_event_counts: Counter[str] = Counter()
    invalidation_event_count = 0
    trailer_seen = False
    first_ts: float | None = None
    last_ts: float | None = None
    records = 0

    for record in iter_records(p):
        records += 1
        type_counts[record.type] += 1
        symbol_counts[record.symbol] += 1
        first_ts = record.ts_local if first_ts is None else min(first_ts, record.ts_local)
        last_ts = record.ts_local if last_ts is None else max(last_ts, record.ts_local)

        if record.type == "captureMeta":
            raw_schema_version = record.data.get("schemaVersion")
            try:
                schema_version = int(str(raw_schema_version))
            except (TypeError, ValueError):
                schema_version = None
            receive_clock = record.data.get("clock") == "receive_time"

        capture_value = record.data.get("_capture")
        capture = dict(capture_value) if isinstance(capture_value, dict) else {}
        if record.type == "captureEvent" and not capture:
            capture = {key: record.data[key] for key in ("recvSeq", "recvMonotonicNs", "route") if key in record.data}
        receipt_candidate = record.type != "captureMeta" and (
            (schema_version is not None and schema_version >= 3) or bool(capture)
        )
        if receipt_candidate:
            raw_seq = capture.get("recvSeq")
            raw_monotonic = capture.get("recvMonotonicNs")
            route = capture.get("route") or record.data.get("route")
            if raw_seq is None or raw_monotonic is None or route is None:
                records_missing_receipt += 1
            else:
                try:
                    recv_seq = int(str(raw_seq))
                    recv_monotonic_ns = int(str(raw_monotonic))
                except (TypeError, ValueError):
                    records_missing_receipt += 1
                else:
                    records_with_receipt += 1
                    route_counts[str(route)] += 1
                    if first_recv_seq is None:
                        first_recv_seq = recv_seq
                        last_recv_seq = recv_seq
                    elif recv_seq <= (last_recv_seq if last_recv_seq is not None else recv_seq):
                        receive_sequence_regressions += 1
                    elif last_recv_seq is not None and recv_seq > last_recv_seq + 1:
                        receive_sequence_gaps += recv_seq - last_recv_seq - 1
                    last_recv_seq = recv_seq
                    if first_recv_monotonic_ns is None:
                        first_recv_monotonic_ns = recv_monotonic_ns
                        last_recv_monotonic_ns = recv_monotonic_ns
                    elif last_recv_monotonic_ns is not None:
                        delta = recv_monotonic_ns - last_recv_monotonic_ns
                        if delta < 0:
                            monotonic_regressions += 1
                        elif max_interarrival_ns is None or delta > max_interarrival_ns:
                            max_interarrival_ns = delta
                    last_recv_monotonic_ns = recv_monotonic_ns

        if record.type == "captureEvent":
            event_name = str(record.data.get("event", ""))
            capture_event_counts[event_name] += 1
            if event_name in _CAPTURE_INVALIDATION_EVENTS:
                invalidation_event_count += 1
            if event_name == "capture_trailer":
                trailer_seen = True

        if record.type == "exchangeInfo":
            spec = {
                "tick_size": str(record.data["tickSize"]),
                "step_size": str(record.data["stepSize"]),
            }
            for source_key, output_key in [
                ("baseAsset", "quantity_unit"),
                ("quoteAsset", "price_currency"),
                ("contractMultiplier", "contract_multiplier"),
                ("venue", "venue"),
            ]:
                value = record.data.get(source_key)
                if value is not None:
                    spec[output_key] = str(value)
            symbol_specs[record.symbol] = spec

    duration = None if first_ts is None or last_ts is None else max(0.0, last_ts - first_ts)
    return StreamInspection(
        path=str(p),
        file_size_bytes=p.stat().st_size,
        sha256=file_sha256(p),
        records=records,
        counts_by_type=dict(sorted(type_counts.items())),
        counts_by_symbol=dict(sorted(symbol_counts.items())),
        first_ts_local=first_ts,
        last_ts_local=last_ts,
        duration_seconds=duration,
        symbols=sorted(symbol_counts),
        symbol_specs=dict(sorted(symbol_specs.items())),
        capture_liveness=CaptureLiveness(
            schema_version=schema_version,
            receive_clock=receive_clock,
            records_with_receipt=records_with_receipt,
            records_missing_receipt=records_missing_receipt,
            first_recv_seq=first_recv_seq,
            last_recv_seq=last_recv_seq,
            receive_sequence_gaps=receive_sequence_gaps,
            receive_sequence_regressions=receive_sequence_regressions,
            first_recv_monotonic_ns=first_recv_monotonic_ns,
            last_recv_monotonic_ns=last_recv_monotonic_ns,
            monotonic_regressions=monotonic_regressions,
            max_interarrival_ns=max_interarrival_ns,
            routes=dict(sorted(route_counts.items())),
            capture_event_counts=dict(sorted(capture_event_counts.items())),
            invalidation_event_count=invalidation_event_count,
            trailer_seen=trailer_seen,
            receipt_integrity_ok=(
                schema_version is not None
                and schema_version >= 3
                and receive_clock
                and records_missing_receipt == 0
                and receive_sequence_gaps == 0
                and receive_sequence_regressions == 0
                and monotonic_regressions == 0
                and trailer_seen
            ),
        ),
    )
