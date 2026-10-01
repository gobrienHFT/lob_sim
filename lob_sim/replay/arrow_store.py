"""Streaming normalization from validated capture rows to Arrow IPC."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterator

from ..record.envelope import (
    SCHEMA_V3,
    canonical_json,
    payload_checksum,
    require_nonempty_string,
    require_nonnegative_int,
    require_nonnegative_timestamp_ns,
)
from .inspection import file_sha256
from .reader import iter_records

try:  # Optional at import time; the CLI gives an actionable error.
    import pyarrow as pa
    import pyarrow.ipc as ipc
except ImportError:  # pragma: no cover - depends on installation extras
    pa = None  # type: ignore[assignment]
    ipc = None  # type: ignore[assignment]


def _require_arrow() -> None:
    if pa is None or ipc is None:
        raise RuntimeError("Arrow normalization requires pyarrow; install lob-sim[storage]")


def _schema() -> Any:
    _require_arrow()
    return pa.schema(
        [
            ("source_schema_version", pa.string()),
            ("source_row_seq", pa.int64()),
            ("symbol", pa.string()),
            ("event_kind", pa.string()),
            ("route", pa.string()),
            ("recv_seq", pa.int64()),
            ("recv_wall_ns", pa.int64()),
            ("recv_monotonic_ns", pa.int64()),
            ("exchange_event_ns", pa.int64()),
            ("exchange_transaction_ns", pa.int64()),
            ("stream_epoch", pa.int32()),
            ("sync_epoch", pa.int32()),
            ("logical_time_source", pa.string()),
            ("payload_checksum", pa.string()),
            ("payload_json", pa.large_string()),
        ],
        metadata={
            b"lob_sim_schema": b"lob_sim.normalized_arrow.v1",
            b"causal_order": b"recv_monotonic_ns,recv_seq",
        },
    )


def _row(record: Any, source_row_seq: int) -> dict[str, Any]:
    capture_value = record.data.get("_capture")
    if capture_value is None:
        capture: dict[str, object] = {}
    elif isinstance(capture_value, Mapping):
        capture = dict(capture_value)
    else:
        raise ValueError("_capture must be a mapping")
    has_recv_seq = capture.get("recvSeq") is not None
    has_recv_monotonic = capture.get("recvMonotonicNs") is not None
    if has_recv_seq != has_recv_monotonic:
        raise ValueError("recvSeq and recvMonotonicNs must be provided together")
    has_receive_clock = has_recv_seq and has_recv_monotonic
    recv_seq = (
        require_nonnegative_int(capture["recvSeq"], "recvSeq") if capture.get("recvSeq") is not None else source_row_seq
    )
    recv_wall_ns = (
        require_nonnegative_int(capture["recvWallNs"], "recvWallNs")
        if capture.get("recvWallNs") is not None
        else require_nonnegative_timestamp_ns(record.ts_local, "ts_local")
    )
    recv_monotonic_ns = (
        require_nonnegative_int(capture["recvMonotonicNs"], "recvMonotonicNs")
        if capture.get("recvMonotonicNs") is not None
        else recv_wall_ns
    )
    event_ms = record.data.get("E")
    transaction_ms = record.data.get("T")
    payload = dict(record.data)
    return {
        "source_schema_version": SCHEMA_V3 if has_receive_clock else "lob_sim.record.legacy",
        "source_row_seq": source_row_seq,
        "symbol": record.symbol,
        "event_kind": record.type,
        "route": (require_nonempty_string(capture["route"], "route") if capture.get("route") is not None else "legacy"),
        "recv_seq": recv_seq,
        "recv_wall_ns": recv_wall_ns,
        "recv_monotonic_ns": recv_monotonic_ns,
        "exchange_event_ns": int(event_ms) * 1_000_000 if event_ms is not None else None,
        "exchange_transaction_ns": int(transaction_ms) * 1_000_000 if transaction_ms is not None else None,
        "stream_epoch": require_nonnegative_int(capture.get("streamEpoch", 0), "streamEpoch"),
        "sync_epoch": require_nonnegative_int(capture.get("syncEpoch", 0), "syncEpoch"),
        "logical_time_source": "capture_receive_clock" if has_receive_clock else "legacy_row_order",
        "payload_checksum": payload_checksum(payload),
        "payload_json": canonical_json(payload).decode("utf-8"),
    }


def normalize_to_arrow(
    input_path: str | Path,
    output_path: str | Path,
    *,
    batch_size: int = 65_536,
) -> dict[str, Any]:
    """Validate and stream records into a new, no-clobber finalized IPC file.

    Failures retain the exclusively created partial for inspection. Existing
    final and partial outputs must never be reused as scratch space.
    """

    _require_arrow()
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    source = Path(input_path)
    target = Path(output_path)
    if source.resolve() == target.resolve():
        raise ValueError("normalization source and destination must differ")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"normalization output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    schema = _schema()
    rows: list[dict[str, Any]] = []
    count = 0
    input_sha256 = file_sha256(source)
    with partial.open("xb") as raw:
        with pa.PythonFile(raw, mode="w") as sink:
            with ipc.new_file(sink, schema) as writer:
                for source_row_seq, record in enumerate(iter_records(source), start=1):
                    rows.append(_row(record, source_row_seq))
                    count += 1
                    if len(rows) >= batch_size:
                        writer.write_batch(pa.RecordBatch.from_pylist(rows, schema=schema))
                        rows.clear()
                if rows:
                    writer.write_batch(pa.RecordBatch.from_pylist(rows, schema=schema))
            sink.flush()
            raw.flush()
            os.fsync(raw.fileno())
    if file_sha256(source) != input_sha256:
        raise ValueError("normalization source changed during processing")
    # Same-directory link creation is atomic and fails if a competing writer
    # published this destination after the initial check. No replace fallback.
    os.link(partial, target)
    partial.unlink()
    return {
        "schema_version": "lob_sim.normalization_report.v1",
        "input_path": str(source),
        "input_sha256": input_sha256,
        "output_path": str(target),
        "output_sha256": file_sha256(target),
        "records": count,
        "format": "arrow_ipc_file",
    }


def iter_arrow_rows(path: str | Path) -> Iterator[dict[str, Any]]:
    _require_arrow()
    with pa.memory_map(str(Path(path)), "r") as source:
        reader = ipc.RecordBatchFileReader(source)
        for batch_index in range(reader.num_record_batches):
            for row in reader.get_batch(batch_index).to_pylist():
                yield row


def arrow_metadata(path: str | Path) -> dict[str, str]:
    _require_arrow()
    with pa.memory_map(str(Path(path)), "r") as source:
        reader = ipc.RecordBatchFileReader(source)
        metadata = reader.schema.metadata or {}
    return {key.decode("utf-8"): value.decode("utf-8") for key, value in metadata.items()}
