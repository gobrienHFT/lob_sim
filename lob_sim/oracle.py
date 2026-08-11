"""Independent deterministic serialization and checkpoint primitives.

The oracle intentionally has no Rust import.  Differential tests can compare
its canonical state bytes against the kernel once the optional extension is
available.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


def canonicalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): canonicalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite float cannot enter deterministic state")
        return value
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def state_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class Checkpoint:
    schema_version: str
    event_index: int
    logical_time: tuple[int, int]
    state: Mapping[str, Any]
    state_sha256: str

    @classmethod
    def create(
        cls,
        event_index: int,
        logical_time: tuple[int, int],
        state: Mapping[str, Any],
        *,
        schema_version: str = "lob_sim.checkpoint.v1",
    ) -> "Checkpoint":
        if event_index < 0:
            raise ValueError("event_index must be >= 0")
        normalized = canonicalize(state)
        return cls(schema_version, event_index, logical_time, normalized, state_hash(normalized))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_index": self.event_index,
            "logical_time": list(self.logical_time),
            "state": canonicalize(self.state),
            "state_sha256": self.state_sha256,
        }

    def verify(self) -> None:
        actual = state_hash(self.state)
        if actual != self.state_sha256:
            raise ValueError(f"checkpoint state hash mismatch: expected {self.state_sha256}, got {actual}")


def write_checkpoint(path: str | Path, checkpoint: Checkpoint) -> None:
    checkpoint.verify()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(checkpoint.as_dict(), handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(target)


def read_checkpoint(path: str | Path) -> Checkpoint:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    checkpoint = Checkpoint(
        schema_version=str(value["schema_version"]),
        event_index=int(value["event_index"]),
        logical_time=(int(value["logical_time"][0]), int(value["logical_time"][1])),
        state=value["state"],
        state_sha256=str(value["state_sha256"]),
    )
    checkpoint.verify()
    return checkpoint
