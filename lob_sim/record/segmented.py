"""Crash-visible segmented schema-v3 capture writer.

The writer is intentionally independent of the websocket loop.  Callers can
place envelopes behind a bounded queue and treat ``QueueFull`` as a capture
integrity failure rather than silently dropping market data.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

from .envelope import EventEnvelope, SCHEMA_V3, canonical_json, payload_checksum

try:  # Optional for source installs; production capture should install it.
    import zstandard as zstd
except ImportError:  # pragma: no cover - exercised only on minimal installs
    zstd = None  # type: ignore[assignment]


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a finalized segment without materializing it in memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SegmentIntegrityError(RuntimeError):
    """Raised when a segment cannot be finalized without losing evidence."""


@dataclass(frozen=True)
class SegmentValidationReport:
    path: str
    complete: bool
    count: int
    first_recv_seq: int | None
    last_recv_seq: int | None
    content_sha256: str
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.complete and not self.issues


class SegmentedCaptureWriter:
    """Write checksummed schema-v3 envelopes to atomically finalized segments."""

    def __init__(
        self,
        directory: str | Path,
        capture_id: str,
        *,
        rotate_seconds: int = 300,
        max_bytes: int = 256 * 1024 * 1024,
        compression: str = "zstd",
    ) -> None:
        if rotate_seconds <= 0 or max_bytes <= 0:
            raise ValueError("segment rotation limits must be positive")
        if compression not in {"zstd", "none"}:
            raise ValueError("compression must be zstd or none")
        if compression == "zstd" and zstd is None:
            raise SegmentIntegrityError("zstandard is required for compression='zstd'; install zstandard")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.capture_id = capture_id
        self.rotate_seconds = rotate_seconds
        self.max_bytes = max_bytes
        self.compression = compression
        self._segment_index = 0
        self._segment_started_wall_ns = 0
        self._segment_bytes = 0
        self._segment_count = 0
        self._first_seq: int | None = None
        self._last_seq: int | None = None
        self._content_hash = hashlib.sha256()
        self._fh: TextIO | None = None
        self._raw_fh: io.BufferedWriter | None = None
        self._zstd_fh: Any | None = None
        self._partial_path: Path | None = None
        self._final_path: Path | None = None
        self._global_last_seq: int | None = None
        self._finalized_segments: list[dict[str, Any]] = []
        self._manifest_metadata: dict[str, Any] = {}
        self._closed = False
        self._open_segment()

    def _paths(self) -> tuple[Path, Path]:
        suffix = ".ndjson.zst" if self.compression == "zstd" else ".ndjson"
        final = self.directory / f"{self.capture_id}_{self._segment_index:06d}{suffix}"
        return final.with_name(final.name + ".partial"), final

    def _open_segment(self) -> None:
        self._partial_path, self._final_path = self._paths()
        self._segment_started_wall_ns = time.time_ns()
        self._segment_bytes = 0
        self._segment_count = 0
        self._first_seq = None
        self._last_seq = None
        self._content_hash = hashlib.sha256()
        if self.compression == "zstd":
            raw = self._partial_path.open("wb")
            self._raw_fh = raw
            self._zstd_fh = zstd.ZstdCompressor(level=3).stream_writer(raw, closefd=False)
            self._fh = io.TextIOWrapper(self._zstd_fh, encoding="utf-8", newline="\n")
        else:
            self._raw_fh = None
            self._zstd_fh = None
            self._fh = self._partial_path.open("w", encoding="utf-8", newline="\n")
        self._write_json(
            {
                "record": "segment_header",
                "schema_version": SCHEMA_V3,
                "capture_id": self.capture_id,
                "segment_index": self._segment_index,
                "started_wall_ns": self._segment_started_wall_ns,
            }
        )

    def _write_json(self, value: dict[str, object]) -> None:
        if self._fh is None:
            raise SegmentIntegrityError("segment is closed")
        line = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self._fh.write(line)
        self._fh.write("\n")
        self._segment_bytes += len(line.encode("utf-8")) + 1

    def _should_rotate(self, envelope: EventEnvelope) -> bool:
        age_ns = envelope.recv_wall_ns - self._segment_started_wall_ns
        return self._segment_count > 0 and (
            age_ns >= self.rotate_seconds * 1_000_000_000 or self._segment_bytes >= self.max_bytes
        )

    def write(self, envelope: EventEnvelope) -> None:
        if envelope.capture_id != self.capture_id:
            raise SegmentIntegrityError("envelope capture_id does not match writer")
        if self._global_last_seq is not None and envelope.recv_seq <= self._global_last_seq:
            raise SegmentIntegrityError(
                f"receive sequence must increase: {envelope.recv_seq} after {self._global_last_seq}"
            )
        if self._should_rotate(envelope):
            self.finalize()
            self._segment_index += 1
            self._open_segment()
        payload = envelope.to_json()
        self._write_json(
            {
                "record": "event",
                "recv_seq": envelope.recv_seq,
                "payload_checksum": envelope.raw_payload_checksum or payload_checksum(envelope.payload),
                "event": json.loads(payload),
            }
        )
        self._content_hash.update(canonical_json(envelope.as_dict()))
        self._segment_count += 1
        self._first_seq = envelope.recv_seq if self._first_seq is None else self._first_seq
        self._last_seq = envelope.recv_seq
        self._global_last_seq = envelope.recv_seq

    def finalize(self) -> Path:
        if self._fh is None or self._partial_path is None or self._final_path is None:
            raise SegmentIntegrityError("segment is already finalized")
        self._write_json(
            {
                "record": "segment_trailer",
                "complete": True,
                "count": self._segment_count,
                "first_recv_seq": self._first_seq,
                "last_recv_seq": self._last_seq,
                "content_sha256": self._content_hash.hexdigest(),
            }
        )
        self._fh.flush()
        if self._raw_fh is not None:
            self._fh.close()
            self._raw_fh.flush()
            os.fsync(self._raw_fh.fileno())
            self._raw_fh.close()
        else:
            os.fsync(self._fh.fileno())
            self._fh.close()
        self._partial_path.replace(self._final_path)
        finalized = self._final_path
        self._finalized_segments.append(
            {
                "path": finalized.name,
                "segment_index": self._segment_index,
                "count": self._segment_count,
                "first_recv_seq": self._first_seq,
                "last_recv_seq": self._last_seq,
                "content_sha256": self._content_hash.hexdigest(),
                "file_sha256": _file_sha256(finalized),
                "size_bytes": finalized.stat().st_size,
            }
        )
        self._fh = None
        self._raw_fh = None
        self._zstd_fh = None
        self._partial_path = None
        self._final_path = None
        return finalized

    def close(self) -> None:
        if self._closed:
            return
        if self._fh is not None:
            self.finalize()
        self.write_manifest()
        self._closed = True

    @property
    def manifest_path(self) -> Path:
        return self.directory / f"{self.capture_id}.manifest.json"

    def update_manifest_metadata(self, metadata: Mapping[str, Any]) -> None:
        if self._closed:
            raise SegmentIntegrityError("cannot update metadata after capture close")
        self._manifest_metadata.update(dict(metadata))

    def manifest(self) -> dict[str, Any]:
        payload = {
            "schema_version": "lob_sim.capture_manifest.v1",
            "capture_schema_version": SCHEMA_V3,
            "capture_id": self.capture_id,
            "compression": self.compression,
            "segments": list(self._finalized_segments),
            "segment_count": len(self._finalized_segments),
            "event_count": sum(int(segment["count"]) for segment in self._finalized_segments),
            "first_recv_seq": (self._finalized_segments[0]["first_recv_seq"] if self._finalized_segments else None),
            "last_recv_seq": (self._finalized_segments[-1]["last_recv_seq"] if self._finalized_segments else None),
        }
        if self._manifest_metadata:
            payload["capture_runtime"] = dict(self._manifest_metadata)
        payload["manifest_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
        return payload

    def write_manifest(self) -> Path:
        target = self.manifest_path
        partial = target.with_name(target.name + ".partial")
        with partial.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.manifest(), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(target)
        return target

    def __enter__(self) -> "SegmentedCaptureWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.close()
        elif self._fh is not None:
            # Keep the `.partial` file as a visibly incomplete forensic tail.
            self._fh.flush()
            self._fh.close()
            if self._raw_fh is not None:
                if not self._raw_fh.closed:
                    self._raw_fh.flush()
                    self._raw_fh.close()
            self._fh = None
            self._raw_fh = None
            self._zstd_fh = None


def _open_text(path: Path) -> tuple[TextIO, Any | None]:
    if path.name.endswith(".zst") or path.name.endswith(".zst.partial"):
        if zstd is None:
            raise SegmentIntegrityError("zstandard is required to read compressed capture segments")
        raw = path.open("rb")
        reader = zstd.ZstdDecompressor().stream_reader(raw, closefd=True)
        return io.TextIOWrapper(reader, encoding="utf-8"), raw
    return path.open("r", encoding="utf-8"), None


def recover_valid_envelopes(path: str | Path) -> Iterator[EventEnvelope]:
    """Yield only complete, checksummed envelopes from a segment or partial tail."""

    source = Path(path)
    handle, _raw = _open_text(source)
    previous_seq: int | None = None
    try:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                break
            if row.get("record") != "event":
                continue
            event = row.get("event")
            if not isinstance(event, dict):
                break
            try:
                envelope = EventEnvelope.from_dict(event)
            except (TypeError, ValueError):
                break
            checksum = row.get("payload_checksum")
            computed_checksum = payload_checksum(envelope.payload)
            if checksum != computed_checksum or envelope.raw_payload_checksum != computed_checksum:
                break
            if previous_seq is not None and envelope.recv_seq <= previous_seq:
                break
            previous_seq = envelope.recv_seq
            yield envelope
    except (OSError, EOFError):
        return
    finally:
        handle.close()


def validate_segment(path: str | Path) -> SegmentValidationReport:
    source = Path(path)
    issues: list[str] = []
    count = 0
    first_seq: int | None = None
    last_seq: int | None = None
    digest = hashlib.sha256()
    header: dict[str, Any] | None = None
    trailer: dict[str, Any] | None = None
    handle, _raw = _open_text(source)
    try:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(f"line {line_number}: invalid or truncated JSON: {exc.msg}")
                break
            record = row.get("record")
            if record == "segment_header":
                if header is not None or count or trailer is not None:
                    issues.append(f"line {line_number}: misplaced or duplicate segment header")
                header = row
                if row.get("schema_version") != SCHEMA_V3:
                    issues.append(f"line {line_number}: unexpected schema version")
            elif record == "event":
                if header is None or trailer is not None:
                    issues.append(f"line {line_number}: event outside header/trailer boundary")
                    continue
                event = row.get("event")
                if not isinstance(event, dict):
                    issues.append(f"line {line_number}: event payload is not an object")
                    continue
                try:
                    envelope = EventEnvelope.from_dict(event)
                except (TypeError, ValueError) as exc:
                    issues.append(f"line {line_number}: invalid envelope: {exc}")
                    continue
                computed_checksum = payload_checksum(envelope.payload)
                if (
                    row.get("payload_checksum") != computed_checksum
                    or envelope.raw_payload_checksum != computed_checksum
                ):
                    issues.append(f"line {line_number}: payload checksum mismatch")
                if last_seq is not None and envelope.recv_seq <= last_seq:
                    issues.append(f"line {line_number}: receive sequence is not strictly increasing")
                digest.update(canonical_json(envelope.as_dict()))
                count += 1
                first_seq = envelope.recv_seq if first_seq is None else first_seq
                last_seq = envelope.recv_seq
            elif record == "segment_trailer":
                if trailer is not None:
                    issues.append(f"line {line_number}: duplicate segment trailer")
                trailer = row
            else:
                issues.append(f"line {line_number}: unknown record type {record!r}")
    except (OSError, EOFError) as exc:
        issues.append(f"read failure: {exc}")
    finally:
        handle.close()

    if header is None:
        issues.append("missing segment header")
    if trailer is None:
        issues.append("missing complete segment trailer")
    else:
        expected = {
            "complete": True,
            "count": count,
            "first_recv_seq": first_seq,
            "last_recv_seq": last_seq,
            "content_sha256": digest.hexdigest(),
        }
        for key, value in expected.items():
            if trailer.get(key) != value:
                issues.append(f"trailer {key} mismatch")
    return SegmentValidationReport(
        path=str(source),
        complete=bool(trailer and trailer.get("complete") is True),
        count=count,
        first_recv_seq=first_seq,
        last_recv_seq=last_seq,
        content_sha256=digest.hexdigest(),
        issues=tuple(issues),
    )
