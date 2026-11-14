"""Exclusive physical day/role feature streams; bounded open handles."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
from typing import Any, BinaryIO

from lob_sim.regime.validation import canonical_json

FEATURE_ROW_LIMIT = 32 * 1024
MAX_DAY_FILES = 8192


@dataclass
class _File:
    role: str
    day: str
    symbol: str
    partial: Path
    handle: BinaryIO
    digest: Any = field(default_factory=sha256)
    rows: int = 0
    admitted: int = 0
    reasons: Counter[str] = field(default_factory=Counter)


class PartitionWriter:
    """Two live symbol handles; file metadata bounded by explicit date cap."""

    def __init__(self, root: Path):
        self.root = root
        self.open_files: dict[str, _File] = {}
        self.entries: list[dict[str, Any]] = []
        self.finalized: set[tuple[str, str]] = set()

    def _finish(self, symbol: str) -> None:
        item = self.open_files[symbol]
        item.handle.flush()
        os.fsync(item.handle.fileno())
        item.handle.close()
        final = item.partial.with_name(item.partial.name.removesuffix(".partial"))
        os.link(item.partial, final)
        item.partial.unlink()
        self.open_files.pop(symbol)
        self.finalized.add((item.day, item.symbol))
        self.entries.append(
            {
                "role": item.role,
                "utc_day": item.day,
                "symbol": item.symbol,
                "path": final.relative_to(self.root).as_posix(),
                "sha256": item.digest.hexdigest(),
                "rows": item.rows,
                "admitted_rows": item.admitted,
                "exclusion_counts": dict(sorted(item.reasons.items())),
            }
        )

    def write(self, row: dict[str, Any]) -> None:
        native = row["native"]
        day, symbol, role = native["utc_day"], native["symbol"], row["role"]
        item = self.open_files.get(symbol)
        if item is not None and (item.day, item.role) != (day, role):
            self._finish(symbol)
            item = None
        if item is None:
            if (day, symbol) in self.finalized:
                raise ValueError("prepared feature days were not emitted chronologically")
            if len(self.entries) + len(self.open_files) >= MAX_DAY_FILES:
                raise ValueError("explicit prepared-day artifact cap exceeded")
            directory = self.root / role
            directory.mkdir(parents=True, exist_ok=True)
            partial = directory / (day + "__" + symbol + ".jsonl.partial")
            item = _File(role, day, symbol, partial, partial.open("xb"))
            self.open_files[symbol] = item
        raw = (canonical_json(row) + "\n").encode("utf-8")
        if len(raw) > FEATURE_ROW_LIMIT:
            raise ValueError("prepared feature row exceeds explicit record limit")
        item.handle.write(raw)
        item.digest.update(raw)
        item.rows += 1
        item.admitted += int(row["admitted"])
        item.reasons.update(row["exclusions"])

    def finalize(self) -> list[dict[str, Any]]:
        for symbol in sorted(self.open_files):
            self._finish(symbol)
        return sorted(self.entries, key=lambda e: (e["utc_day"], e["symbol"]))

    def abandon(self, error: BaseException) -> None:
        for item in self.open_files.values():
            try:
                item.handle.close()
            except BaseException as secondary:
                error.add_note("Prepared feature close failed: " + type(secondary).__name__)
