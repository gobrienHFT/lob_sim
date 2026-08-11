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
        if self.recv_monotonic_ns < 0:
            raise ValueError("recv_monotonic_ns must be >= 0")
        if self.recv_seq < 0:
            raise ValueError("recv_seq must be >= 0")


@dataclass(frozen=True)
class ValidityState:
    """Independent validity dimensions for a reconstructed execution state."""

    book_valid: bool = False
    trade_stream_valid: bool = False
    clock_valid: bool = False
    capture_valid: bool = False
    reason: str | None = None

    @property
    def execution_valid(self) -> bool:
        return self.book_valid and self.trade_stream_valid and self.clock_valid and self.capture_valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "book_valid": self.book_valid,
            "trade_stream_valid": self.trade_stream_valid,
            "clock_valid": self.clock_valid,
            "capture_valid": self.capture_valid,
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
        if self.recv_seq < 0 or self.recv_wall_ns < 0 or self.recv_monotonic_ns < 0:
            raise ValueError("receipt fields must be non-negative")
        if self.stream_epoch < 0 or self.sync_epoch < 0:
            raise ValueError("epochs must be non-negative")
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
            recv_seq=int(value["recv_seq"]),
            recv_wall_ns=int(value["recv_wall_ns"]),
            recv_monotonic_ns=int(value["recv_monotonic_ns"]),
            payload=value["payload"],
            exchange_event_ns=(int(value["exchange_event_ns"]) if value.get("exchange_event_ns") is not None else None),
            exchange_transaction_ns=(
                int(value["exchange_transaction_ns"]) if value.get("exchange_transaction_ns") is not None else None
            ),
            stream_epoch=int(value.get("stream_epoch", 0)),
            sync_epoch=int(value.get("sync_epoch", 0)),
            raw_payload_checksum=(
                str(value["raw_payload_checksum"]) if value.get("raw_payload_checksum") is not None else None
            ),
        )


def capture_id_for_path(path: str) -> str:
    """Return a stable identifier for a source path without reading secrets."""

    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
