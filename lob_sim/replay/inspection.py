from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .reader import iter_records


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        }


def inspect_stream(path: str | Path) -> StreamInspection:
    p = Path(path)
    type_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    symbol_specs: dict[str, dict[str, str]] = {}
    first_ts: float | None = None
    last_ts: float | None = None
    records = 0

    for record in iter_records(p):
        records += 1
        type_counts[record.type] += 1
        symbol_counts[record.symbol] += 1
        first_ts = record.ts_local if first_ts is None else min(first_ts, record.ts_local)
        last_ts = record.ts_local if last_ts is None else max(last_ts, record.ts_local)

        if record.type == "exchangeInfo":
            symbol_specs[record.symbol] = {
                "tick_size": str(record.data["tickSize"]),
                "step_size": str(record.data["stepSize"]),
            }

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
    )
