from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..record.schema import RecordValidationError, validate_record_object
from ..record.segmented import recover_valid_envelopes, validate_segment


@dataclass(frozen=True)
class RecordedEvent:
    ts_local: float
    symbol: str
    type: str
    data: dict


def _record_from_envelope(envelope: object) -> RecordedEvent:
    from ..record.envelope import EventEnvelope

    if not isinstance(envelope, EventEnvelope):
        raise TypeError("expected EventEnvelope")
    data = dict(envelope.payload)
    capture_value = data.get("_capture")
    capture = dict(capture_value) if isinstance(capture_value, dict) else {}
    capture.update(
        {
            "captureId": envelope.capture_id,
            "recvSeq": envelope.recv_seq,
            "recvMonotonicNs": envelope.recv_monotonic_ns,
            "streamEpoch": envelope.stream_epoch,
            "syncEpoch": envelope.sync_epoch,
            "route": envelope.route,
            "payloadChecksum": envelope.raw_payload_checksum,
        }
    )
    data["_capture"] = capture
    return RecordedEvent(
        ts_local=envelope.recv_wall_ns / 1_000_000_000,
        symbol=envelope.instrument,
        type=envelope.event_kind,
        data=data,
    )


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_segment(path: Path, *, validate: bool) -> Iterator[RecordedEvent]:
    if validate:
        report = validate_segment(path)
        if not report.ok:
            raise RecordValidationError("invalid capture segment: " + "; ".join(report.issues), path=path)
    for envelope in recover_valid_envelopes(path):
        record = _record_from_envelope(envelope)
        if validate:
            validate_record_object(
                {
                    "ts_local": record.ts_local,
                    "symbol": record.symbol,
                    "type": record.type,
                    "data": record.data,
                },
                path=path,
            )
        yield record


def _iter_manifest(path: Path, *, validate: bool) -> Iterator[RecordedEvent]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "lob_sim.capture_manifest.v1":
        raise RecordValidationError("unsupported capture manifest schema", path=path)
    claimed_hash = value.get("manifest_sha256")
    unsigned = dict(value)
    unsigned.pop("manifest_sha256", None)
    actual_hash = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if claimed_hash != actual_hash:
        raise RecordValidationError("capture manifest checksum mismatch", path=path)
    previous_seq: int | None = None
    segments = value.get("segments")
    if not isinstance(segments, list):
        raise RecordValidationError("capture manifest segments must be a list", path=path)
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("path"), str):
            raise RecordValidationError("capture manifest contains an invalid segment entry", path=path)
        segment_path = path.parent / segment["path"]
        if not segment_path.exists():
            raise RecordValidationError(f"capture segment missing: {segment_path.name}", path=path)
        expected_file_hash = segment.get("file_sha256")
        actual_file_hash = _file_sha256(segment_path)
        if expected_file_hash != actual_file_hash:
            raise RecordValidationError(f"capture segment hash mismatch: {segment_path.name}", path=path)
        for record in _iter_segment(segment_path, validate=validate):
            capture = record.data.get("_capture", {})
            sequence = int(capture["recvSeq"])
            if previous_seq is not None and sequence <= previous_seq:
                raise RecordValidationError("manifest receive sequence is not strictly increasing", path=path)
            previous_seq = sequence
            yield record


def iter_records(path: str | Path, *, validate: bool = True) -> Iterator[RecordedEvent]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Replay file missing: {p}")

    if p.name.endswith(".manifest.json"):
        yield from _iter_manifest(p, validate=validate)
        return
    if p.name.endswith(".ndjson.zst") or p.name.endswith(".ndjson.zst.partial"):
        yield from _iter_segment(p, validate=validate)
        return
    if p.suffix == ".ndjson" or p.name.endswith(".ndjson.partial"):
        with p.open("r", encoding="utf-8") as probe:
            first_nonempty = next((line for line in probe if line.strip()), "")
        if first_nonempty:
            try:
                first_value = json.loads(first_nonempty)
            except json.JSONDecodeError:
                first_value = None
            if isinstance(first_value, dict) and first_value.get("record") == "segment_header":
                yield from _iter_segment(p, validate=validate)
                return

    opener = gzip.open if p.suffix == ".gz" else open
    mode = "rt"
    with opener(p, mode, encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecordValidationError(
                    f"invalid JSON: {exc.msg}",
                    path=p,
                    line_number=line_number,
                ) from exc
            if validate:
                validate_record_object(obj, path=p, line_number=line_number)
            yield RecordedEvent(
                ts_local=float(obj["ts_local"]),
                symbol=str(obj["symbol"]),
                type=str(obj["type"]),
                data=obj["data"],
            )
