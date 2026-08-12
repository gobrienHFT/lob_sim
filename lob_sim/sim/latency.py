"""Deterministic scenario latency models.

These are configurable assumptions for replay sensitivity, never exchange
latency measurements.  A seeded empirical sample is useful for stress testing
while keeping a run reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from ..oracle_kernel import ScenarioLatencyOracle, latency_ms_to_us

LatencyMode = Literal["fixed", "empirical", "stress_tail"]


@dataclass
class LatencyModel:
    mode: LatencyMode = "fixed"
    new_order_ms: float = 25.0
    cancel_ms: float = 25.0
    samples_ms: tuple[float, ...] = ()
    stress_multiplier: float = 1.0
    seed: int = 1

    _sampler: ScenarioLatencyOracle = field(init=False, repr=False)

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
        self._sampler = ScenarioLatencyOracle(
            mode=self.mode,
            fixed_new_us=latency_ms_to_us(self.new_order_ms),
            fixed_cancel_us=latency_ms_to_us(self.cancel_ms),
            samples_us=tuple(latency_ms_to_us(value) for value in self.samples_ms),
            stress_multiplier_ppm=int(round(self.stress_multiplier * 1_000_000.0)),
            seed=self.seed,
        )

    def draw(self, component: Literal["new_order", "cancel"]) -> float:
        return self._sampler.draw(component) / 1_000.0

    def sampler_state(self) -> int:
        """Return checkpoint-safe state for the deterministic scenario sampler."""

        return self._sampler.state

    def restore_sampler_state(self, state: int) -> None:
        self._sampler.set_state(state)

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
