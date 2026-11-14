"""Causal UTC-labelled projection; not an absolute clock-accuracy certificate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lob_sim.regime.validation import identity, integer, require_keys

MAX_TIMESTAMP_NS = 2**63 - 1


def timestamp(value: object, name: str) -> int:
    result = integer(value, name)
    if result > MAX_TIMESTAMP_NS:
        raise ValueError(name + " exceeds the supported signed-64-bit timestamp range")
    return result


@dataclass(frozen=True)
class CaptureClock:
    """A fixed first-receipt anchor, never a regression fitted to future rows.

    The observed wall/monotonic difference may vary inside a declared 50 ms
    assumption. That is checked separately by the receipt audit. The map never
    changes, even when a later receipt contradicts it and admission fails.
    """

    origin_logical_ns: int
    origin_wall_ns: int
    tolerance_ns: int = 50_000_000

    def __post_init__(self) -> None:
        timestamp(self.origin_logical_ns, "origin_logical_ns")
        timestamp(self.origin_wall_ns, "origin_wall_ns")
        integer(self.tolerance_ns, "tolerance_ns", minimum=1)
        if self.tolerance_ns > 50_000_000:
            raise ValueError("research clock tolerance cannot exceed the v1 50 ms limit")

    def project(self, logical_ns: int) -> int:
        timestamp(logical_ns, "logical_ns")
        if logical_ns < self.origin_logical_ns:
            raise ValueError("logical time precedes the capture clock origin")
        return timestamp(self.origin_wall_ns + logical_ns - self.origin_logical_ns, "projected_wall_ns")

    def deviation_ns(self, logical_ns: int, wall_ns: int) -> int:
        return timestamp(wall_ns, "wall_ns") - self.project(logical_ns)

    def within_tolerance(self, logical_ns: int, wall_ns: int) -> bool:
        return abs(self.deviation_ns(logical_ns, wall_ns)) <= self.tolerance_ns

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.capture_clock.v1",
            "origin_logical_ns": self.origin_logical_ns,
            "origin_wall_ns": self.origin_wall_ns,
            "tolerance_ns": self.tolerance_ns,
            "projection": "fixed first receipt anchor plus integer monotonic elapsed time;no future interpolation",
            "absolute_utc_accuracy": "unmeasured;UTC-labelled capture host wall clock,not exchange time",
        }

    @property
    def digest(self) -> str:
        return identity(self.as_dict())

    @classmethod
    def from_dict(cls, value: object) -> CaptureClock:
        expected = cls(0, 0).as_dict()
        data = require_keys(value, set(expected), "capture clock")
        result = cls(data["origin_logical_ns"], data["origin_wall_ns"], data["tolerance_ns"])
        if result.as_dict() != dict(data):
            raise ValueError("unsupported capture clock contract")
        return result
