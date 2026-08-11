"""Deterministic scenario latency models.

These are configurable assumptions for replay sensitivity, never exchange
latency measurements.  A seeded empirical sample is useful for stress testing
while keeping a run reproducible.
"""

from __future__ import annotations

import random
import math
from dataclasses import dataclass
from typing import Literal

LatencyMode = Literal["fixed", "empirical", "stress_tail"]


@dataclass
class LatencyModel:
    mode: LatencyMode = "fixed"
    new_order_ms: float = 25.0
    cancel_ms: float = 25.0
    samples_ms: tuple[float, ...] = ()
    stress_multiplier: float = 1.0
    seed: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"fixed", "empirical", "stress_tail"}:
            raise ValueError("latency mode must be fixed, empirical, or stress_tail")
        if not math.isfinite(self.new_order_ms) or not math.isfinite(self.cancel_ms):
            raise ValueError("fixed latencies must be finite")
        if self.new_order_ms < 0 or self.cancel_ms < 0:
            raise ValueError("fixed latencies must be >= 0")
        if any(not math.isfinite(value) for value in self.samples_ms):
            raise ValueError("latency samples must be finite")
        if any(value < 0 for value in self.samples_ms):
            raise ValueError("latency samples must be >= 0")
        if self.mode != "fixed" and not self.samples_ms:
            raise ValueError("empirical/stress_tail latency modes require samples")
        if not math.isfinite(self.stress_multiplier) or self.stress_multiplier < 1:
            raise ValueError("stress_multiplier must be >= 1")
        self._rng = random.Random(self.seed)

    def draw(self, component: Literal["new_order", "cancel"]) -> float:
        fixed = self.new_order_ms if component == "new_order" else self.cancel_ms
        if self.mode == "fixed":
            return fixed
        if self.mode == "stress_tail":
            return max(self.samples_ms) * self.stress_multiplier
        return self._rng.choice(self.samples_ms)

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "new_order_ms": self.new_order_ms,
            "cancel_ms": self.cancel_ms,
            "samples_ms": list(self.samples_ms),
            "stress_multiplier": self.stress_multiplier,
            "seed": self.seed,
            "claim": "scenario latency draw; not measured exchange/network latency",
        }
