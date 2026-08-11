from __future__ import annotations

import gzip
import os
from pathlib import Path
from typing import TextIO

from .format import NDJSONRecord


class NDJSONWriter:
    def __init__(self, path: Path, flush_every: int = 2000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.partial_path = path.with_name(path.name + ".partial")
        self.flush_every = max(1, flush_every)
        self._count = 0
        self._fh: TextIO
        if path.suffix == ".gz":
            self._fh = gzip.open(self.partial_path, "xt", encoding="utf-8")
        else:
            self._fh = self.partial_path.open("x", encoding="utf-8")

    def write(self, record: NDJSONRecord) -> None:
        self._fh.write(record.to_json())
        self._fh.write("\n")
        self._count += 1
        if self._count % self.flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        if self._fh.closed:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except (AttributeError, OSError):
            # Some text/gzip wrappers do not expose a descriptor; close and
            # atomic rename still preserve a visibly complete final path.
            pass
        self._fh.close()
        os.replace(self.partial_path, self.path)

    def __enter__(self) -> "NDJSONWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self._fh.flush()
            self._fh.close()
