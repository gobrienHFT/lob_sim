"""Auditable confidence/confirmation state; independent of strategy policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .validation import finite, integer, probabilities, require_keys


@dataclass(frozen=True)
class HysteresisConfig:
    enter_probability: float = 0.70
    confirmation_samples: int = 3
    minimum_state_age_samples: int = 3
    maximum_normalized_entropy: float = 0.95

    def __post_init__(self) -> None:
        probability = finite(self.enter_probability, "enter_probability")
        entropy = finite(self.maximum_normalized_entropy, "maximum_normalized_entropy")
        if not 0.5 < probability <= 1 or not 0 <= entropy <= 1:
            raise ValueError("invalid hysteresis probability/entropy threshold")
        integer(self.confirmation_samples, "confirmation_samples", minimum=1)
        integer(self.minimum_state_age_samples, "minimum_state_age_samples")
        object.__setattr__(self, "enter_probability", probability)
        object.__setattr__(self, "maximum_normalized_entropy", entropy)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enter_probability": self.enter_probability,
            "confirmation_samples": self.confirmation_samples,
            "minimum_state_age_samples": self.minimum_state_age_samples,
            "maximum_normalized_entropy": self.maximum_normalized_entropy,
        }


@dataclass(frozen=True)
class HysteresisResult:
    raw_map_state: int
    active_state: int | None
    candidate_state: int | None
    confirmation_streak: int
    state_age_samples: int
    switched: bool
    reason: str
    confident: bool


class RegimeHysteresis:
    def __init__(self, state_count: int, config: HysteresisConfig = HysteresisConfig()) -> None:
        integer(state_count, "state_count", minimum=2)
        if state_count > 5:
            raise ValueError("state_count must be in 2..5")
        self.state_count, self.config = state_count, config
        self.reset()

    def reset(self) -> None:
        self.active_state: int | None = None
        self.candidate_state: int | None = None
        self.confirmation_streak = 0
        self.state_age_samples = 0

    def update(self, posterior: tuple[float, ...]) -> HysteresisResult:
        p = probabilities(posterior, "hysteresis posterior", self.state_count)
        raw = max(range(self.state_count), key=p.__getitem__)
        entropy = -math.fsum(value * math.log(value) for value in p if value > 0) / math.log(self.state_count)
        confident = p[raw] >= self.config.enter_probability and entropy <= self.config.maximum_normalized_entropy
        if self.active_state is not None:
            self.state_age_samples += 1
        switched = False
        if not confident:
            self.candidate_state, self.confirmation_streak = None, 0
            reason = "uncertain"
        elif raw == self.active_state:
            self.candidate_state, self.confirmation_streak = None, 0
            reason = "active_state_confirmed"
        else:
            self.confirmation_streak = self.confirmation_streak + 1 if self.candidate_state == raw else 1
            self.candidate_state = raw
            ready = self.confirmation_streak >= self.config.confirmation_samples
            old_enough = self.active_state is None or self.state_age_samples >= self.config.minimum_state_age_samples
            if ready and old_enough:
                self.active_state = raw
                self.candidate_state, self.confirmation_streak, self.state_age_samples = None, 0, 0
                switched, reason = True, "confirmed_switch"
            else:
                reason = "minimum_state_age" if ready and not old_enough else "confirming_candidate"
        return HysteresisResult(
            raw,
            self.active_state,
            self.candidate_state,
            self.confirmation_streak,
            self.state_age_samples,
            switched,
            reason,
            confident,
        )

    def checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_hysteresis_checkpoint.v1",
            "state_count": self.state_count,
            "config": self.config.as_dict(),
            "active_state": self.active_state,
            "candidate_state": self.candidate_state,
            "confirmation_streak": self.confirmation_streak,
            "state_age_samples": self.state_age_samples,
        }

    def restore(self, checkpoint: object) -> None:
        data = require_keys(checkpoint, set(self.checkpoint()), "hysteresis checkpoint")
        config = HysteresisConfig(
            **require_keys(data["config"], set(self.config.as_dict()), "hysteresis configuration")
        )
        if (
            data["schema_version"] != "lob_sim.hmm_hysteresis_checkpoint.v1"
            or integer(data["state_count"], "state_count", minimum=2) != self.state_count
            or config != self.config
        ):
            raise ValueError("hysteresis checkpoint configuration mismatch")
        active, candidate = data["active_state"], data["candidate_state"]
        for name, state in (("active_state", active), ("candidate_state", candidate)):
            if state is not None and integer(state, name) >= self.state_count:
                raise ValueError("hysteresis checkpoint state out of range")
        age = integer(data["state_age_samples"], "state_age_samples")
        streak = integer(data["confirmation_streak"], "confirmation_streak")
        if (
            (active is None and age != 0)
            or ((candidate is None) != (streak == 0))
            or (candidate is not None and candidate == active)
        ):
            raise ValueError("inconsistent hysteresis checkpoint state")
        self.active_state, self.candidate_state = active, candidate
        self.confirmation_streak, self.state_age_samples = streak, age
