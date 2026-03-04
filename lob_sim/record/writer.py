from __future__ import annotations

import gzip
from pathlib import Path
from typing import TextIO

from .format import NDJSONRecord


class NDJSONWriter:
    def __init__(self, path: Path, flush_every: int = 2000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.flush_every = max(1, flush_every)
        self._count = 0
        if path.suffix == ".gz":
            self._fh = gzip.open(path, "at", encoding="utf-8")
        else:
            self._fh: TextIO = open(path, "a", encoding="utf-8")

    def write(self, record: NDJSONRecord) -> None:
        self._fh.write(record.to_json())
        self._fh.write("\n")
        self._count += 1
        if self._count % self.flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "NDJSONWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
