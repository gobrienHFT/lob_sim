"""Bounded event sinks for replay metrics and audit output."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

try:  # Optional storage extra.
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - installation dependent
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]


class EventSink(Protocol):
    memory_bounded: bool

    def write(self, event: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...


class NullSink:
    """No-op sink used by hot-path benchmarks."""

    memory_bounded = True

    def write(self, event: Mapping[str, Any]) -> None:
        del event

    def close(self) -> None:
        return None


class AggregateMetricsSink:
    """Constant-cardinality event counters without retaining individual rows."""

    memory_bounded = True

    def __init__(self) -> None:
        self.count = 0
        self.by_event_type: Counter[str] = Counter()
        self.by_symbol: Counter[str] = Counter()

    def write(self, event: Mapping[str, Any]) -> None:
        self.count += 1
        self.by_event_type[str(event.get("event_type", event.get("event", "unknown")))] += 1
        self.by_symbol[str(event.get("symbol", "*"))] += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "by_event_type": dict(sorted(self.by_event_type.items())),
            "by_symbol": dict(sorted(self.by_symbol.items())),
        }

    def close(self) -> None:
        return None


class CanonicalJsonListHashSink:
    """Hash a canonical JSON list incrementally without retaining its rows.

    ``canonical_sha256(events)`` historically serialized the complete event
    list in one call.  This sink preserves that exact byte representation for
    lists of mapping rows while keeping only a digest, counters, and no
    tape-sized trace in memory.  It is intended for determinism checks, not
    as an audit export: use a streaming file sink when individual rows must
    be inspected later.
    """

    memory_bounded = True

    def __init__(self) -> None:
        self._digest = hashlib.sha256(b"[")
        self._closed = False
        self.count = 0
        self.by_event_type: Counter[str] = Counter()
        self.by_symbol: Counter[str] = Counter()

    def write(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("cannot write to a closed hash sink")
        if self.count:
            self._digest.update(b",")
        encoded = json.dumps(dict(event), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        self._digest.update(encoded)
        self.count += 1
        self.by_event_type[str(event.get("event_type", event.get("event", "unknown")))] += 1
        self.by_symbol[str(event.get("symbol", "*"))] += 1

    def hexdigest(self) -> str:
        """Return the SHA-256 of the canonical JSON list represented so far."""

        digest = self._digest.copy()
        digest.update(b"]")
        return digest.hexdigest()

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "sha256": self.hexdigest(),
            "by_event_type": dict(sorted(self.by_event_type.items())),
            "by_symbol": dict(sorted(self.by_symbol.items())),
        }

    def close(self) -> None:
        self._closed = True


class StreamingJsonlSink:
    """Flushable JSONL sink that never retains the trace in memory."""

    memory_bounded = True

    def __init__(self, path: str | Path, *, flush_every: int = 4096) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")
        self._flush_every = max(1, flush_every)
        self._count = 0

    def write(self, event: Mapping[str, Any]) -> None:
        self._fh.write(json.dumps(dict(event), sort_keys=True, default=str, separators=(",", ":")))
        self._fh.write("\n")
        self._count += 1
        if self._count % self._flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def __enter__(self) -> "StreamingJsonlSink":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class StreamingCsvSink:
    """Batch-free, atomically finalized CSV audit sink with a fixed schema."""

    memory_bounded = True

    def __init__(
        self,
        path: str | Path,
        fieldnames: Iterable[str],
        *,
        flush_every: int = 4096,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.partial_path = self.path.with_name(self.path.name + ".partial")
        self._fh = self.partial_path.open("x", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=list(fieldnames), extrasaction="ignore")
        self._writer.writeheader()
        self._flush_every = max(1, flush_every)
        self._prepared = False
        self._closed = False
        self.count = 0

    @staticmethod
    def _cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (Mapping, list, tuple)):
            return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        return value

    def write(self, event: Mapping[str, Any]) -> None:
        if self._prepared or self._closed:
            raise RuntimeError("cannot write to a closed CSV sink")
        self._writer.writerow({key: self._cell(value) for key, value in event.items()})
        self.count += 1
        if self.count % self._flush_every == 0:
            self._fh.flush()

    def prepare(self) -> None:
        """Durably close the partial file without publishing it."""

        if self._prepared or self._closed:
            return
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        self._prepared = True

    def commit(self) -> None:
        """Publish a prepared file with one same-directory atomic replace."""

        if self._closed:
            return
        self.prepare()
        self.partial_path.replace(self.path)
        self._closed = True

    def close(self) -> None:
        self.commit()

    def abort(self) -> None:
        if self._closed:
            return
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()
        self._closed = True

    def __enter__(self) -> "StreamingCsvSink":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


class StreamingParquetSink:
    """Batch-bounded Parquet sink with atomic finalization."""

    memory_bounded = True

    def __init__(
        self,
        path: str | Path,
        *,
        schema: Any | None = None,
        batch_size: int = 65_536,
        compression: str = "zstd",
    ) -> None:
        if pa is None or pq is None:
            raise RuntimeError("Parquet output requires pyarrow; install lob-sim[storage]")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.partial_path = self.path.with_name(self.path.name + ".partial")
        self._schema = schema
        self._batch_size = batch_size
        self._compression = compression
        self._rows: list[dict[str, Any]] = []
        self._writer: Any | None = None
        self._closed = False
        self.count = 0

    def write(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("cannot write to a closed Parquet sink")
        self._rows.append(dict(event))
        self.count += 1
        if len(self._rows) >= self._batch_size:
            self._flush_batch()

    def _flush_batch(self) -> None:
        if not self._rows:
            return
        table = pa.Table.from_pylist(self._rows, schema=self._schema)
        if self._writer is None:
            self._schema = table.schema
            self._writer = pq.ParquetWriter(
                str(self.partial_path),
                self._schema,
                compression=self._compression,
            )
        self._writer.write_table(table)
        self._rows.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._flush_batch()
        if self._writer is None:
            empty_schema = self._schema if self._schema is not None else pa.schema([])
            self._writer = pq.ParquetWriter(
                str(self.partial_path),
                empty_schema,
                compression=self._compression,
            )
        self._writer.close()
        with self.partial_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        self.partial_path.replace(self.path)
        self._closed = True

    def __enter__(self) -> "StreamingParquetSink":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.close()
        else:
            # The partial file remains visible and is never presented as a
            # finalized audit artifact.
            self._rows.clear()
            if self._writer is not None:
                self._writer.close()
            self._closed = True
