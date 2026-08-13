"""Versioned, causality-preserving market-data envelopes.

The legacy four-field NDJSON row remains readable for historical fixtures.  New
captures can use :class:`EventEnvelope` so receipt ordering, stream epochs and
raw-payload identity are first-class rather than inferred from exchange time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

SCHEMA_V3 = "lob_sim.record.v3"


def _require_nonnegative_int(value: object, field_name: str) -> int:
    """Validate an exact non-negative JSON integer.

    Receipt identity is part of the causal key.  Coercing ``1.5`` to ``1`` or
    accepting ``True`` as ``1`` would silently rewrite ordering at the storage
    boundary, so envelope construction deliberately rejects both cases.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _optional_nonnegative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value, field_name)


def _crc32c(data: bytes) -> int:
    """Small dependency-free CRC32C (Castagnoli) implementation."""

    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_checksum(payload: Mapping[str, Any] | list[Any] | str | int | float | bool | None) -> str:
    raw = canonical_json(payload)
    return f"crc32c:{_crc32c(raw):08x}"


@dataclass(frozen=True, order=True)
class LogicalTime:
    recv_monotonic_ns: int
    recv_seq: int

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.recv_monotonic_ns, "recv_monotonic_ns")
        _require_nonnegative_int(self.recv_seq, "recv_seq")


@dataclass(frozen=True)
class ValidityState:
    """Independent validity dimensions for a reconstructed execution state."""

    book_valid: bool = False
    trade_stream_valid: bool = False
    clock_valid: bool = False
    capture_valid: bool = False
    trade_stream_required: bool = True
    reason: str | None = None

    @property
    def execution_valid(self) -> bool:
        trade_valid = self.trade_stream_valid or not self.trade_stream_required
        return self.book_valid and trade_valid and self.clock_valid and self.capture_valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "book_valid": self.book_valid,
            "trade_stream_valid": self.trade_stream_valid,
            "clock_valid": self.clock_valid,
            "capture_valid": self.capture_valid,
            "trade_stream_required": self.trade_stream_required,
            "execution_valid": self.execution_valid,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EventEnvelope:
    capture_id: str
    schema_version: str
    venue: str
    instrument: str
    event_kind: str
    route: str
    recv_seq: int
    recv_wall_ns: int
    recv_monotonic_ns: int
    payload: Mapping[str, Any]
    exchange_event_ns: int | None = None
    exchange_transaction_ns: int | None = None
    stream_epoch: int = 0
    sync_epoch: int = 0
    raw_payload_checksum: str | None = None

    def __post_init__(self) -> None:
        if not self.capture_id:
            raise ValueError("capture_id must be non-empty")
        if not self.schema_version:
            raise ValueError("schema_version must be non-empty")
        if not self.venue:
            raise ValueError("venue must be non-empty")
        if not self.instrument:
            raise ValueError("instrument must be non-empty")
        if not self.event_kind or not self.route:
            raise ValueError("event_kind and route must be non-empty")
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")
        _require_nonnegative_int(self.recv_seq, "recv_seq")
        _require_nonnegative_int(self.recv_wall_ns, "recv_wall_ns")
        _require_nonnegative_int(self.recv_monotonic_ns, "recv_monotonic_ns")
        _require_nonnegative_int(self.stream_epoch, "stream_epoch")
        _require_nonnegative_int(self.sync_epoch, "sync_epoch")
        if self.exchange_event_ns is not None:
            _require_nonnegative_int(self.exchange_event_ns, "exchange_event_ns")
        if self.exchange_transaction_ns is not None:
            _require_nonnegative_int(self.exchange_transaction_ns, "exchange_transaction_ns")
        if self.raw_payload_checksum is None:
            object.__setattr__(self, "raw_payload_checksum", payload_checksum(self.payload))
        elif not self.raw_payload_checksum:
            raise ValueError("raw_payload_checksum must be non-empty")

    @property
    def logical_time(self) -> LogicalTime:
        return LogicalTime(self.recv_monotonic_ns, self.recv_seq)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["payload"] = dict(self.payload)
        return result

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventEnvelope":
        required = (
            "capture_id",
            "schema_version",
            "venue",
            "instrument",
            "event_kind",
            "route",
            "recv_seq",
            "recv_wall_ns",
            "recv_monotonic_ns",
            "payload",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"EventEnvelope missing required fields: {', '.join(missing)}")
        return cls(
            capture_id=str(value["capture_id"]),
            schema_version=str(value["schema_version"]),
            venue=str(value["venue"]),
            instrument=str(value["instrument"]),
            event_kind=str(value["event_kind"]),
            route=str(value["route"]),
            recv_seq=_require_nonnegative_int(value["recv_seq"], "recv_seq"),
            recv_wall_ns=_require_nonnegative_int(value["recv_wall_ns"], "recv_wall_ns"),
            recv_monotonic_ns=_require_nonnegative_int(value["recv_monotonic_ns"], "recv_monotonic_ns"),
            payload=value["payload"],
            exchange_event_ns=_optional_nonnegative_int(value.get("exchange_event_ns"), "exchange_event_ns"),
            exchange_transaction_ns=_optional_nonnegative_int(
                value.get("exchange_transaction_ns"), "exchange_transaction_ns"
            ),
            stream_epoch=_require_nonnegative_int(value.get("stream_epoch", 0), "stream_epoch"),
            sync_epoch=_require_nonnegative_int(value.get("sync_epoch", 0), "sync_epoch"),
            raw_payload_checksum=(
                str(value["raw_payload_checksum"]) if value.get("raw_payload_checksum") is not None else None
            ),
        )


def capture_id_for_path(path: str) -> str:
    """Return a stable identifier for a source path without reading secrets."""

    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
