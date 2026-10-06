"""Immutable diagonal Gaussian HMM parameters, independent of fitting libraries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validation import integer, probabilities, require_keys, vector


@dataclass(frozen=True)
class GaussianHMMParameters:
    start: tuple[float, ...]
    transition: tuple[tuple[float, ...], ...]
    means: tuple[tuple[float, ...], ...]
    variances: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        k = integer(len(self.start), "state_count", minimum=2)
        if k > 5:
            raise ValueError("state_count must be in 2..5")
        start = probabilities(self.start, "start", k)
        if any(len(rows) != k for rows in (self.transition, self.means, self.variances)):
            raise ValueError("model state dimension mismatch")
        transition = tuple(probabilities(row, "transition", k) for row in self.transition)
        d = len(self.means[0])
        means = tuple(vector(row, "means", d) for row in self.means)
        variances = tuple(vector(row, "variances", d) for row in self.variances)
        if any(value <= 0 for row in variances for value in row):
            raise ValueError("variances must be strictly positive")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "transition", transition)
        object.__setattr__(self, "means", means)
        object.__setattr__(self, "variances", variances)

    @property
    def state_count(self) -> int:
        return len(self.start)

    @property
    def feature_count(self) -> int:
        return len(self.means[0])

    @property
    def parameter_count(self) -> int:
        k, d = self.state_count, self.feature_count
        return (k - 1) + k * (k - 1) + 2 * k * d

    def expected_durations(self, interval_ns: int) -> tuple[float | None, ...]:
        """Geometric dwell time in seconds; absorbing states are explicitly unbounded."""

        integer(interval_ns, "interval_ns", minimum=1)
        return tuple(
            None if row[state] == 1 else (interval_ns / 1e9) / (1 - row[state])
            for state, row in enumerate(self.transition)
        )

    def permute(self, order: tuple[int, ...]) -> GaussianHMMParameters:
        if any(type(value) is not int for value in order) or sorted(order) != list(range(self.state_count)):
            raise ValueError("state order must be a permutation")
        return GaussianHMMParameters(
            start=tuple(self.start[index] for index in order),
            transition=tuple(tuple(self.transition[left][right] for right in order) for left in order),
            means=tuple(self.means[index] for index in order),
            variances=tuple(self.variances[index] for index in order),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_type": "gaussian_hmm",
            "covariance_type": "diag",
            "state_count": self.state_count,
            "start": list(self.start),
            "transition": [list(row) for row in self.transition],
            "means": [list(row) for row in self.means],
            "variances": [list(row) for row in self.variances],
        }

    @classmethod
    def from_dict(cls, value: object) -> GaussianHMMParameters:
        data = require_keys(
            value,
            {"model_type", "covariance_type", "state_count", "start", "transition", "means", "variances"},
            "model",
        )
        if data["model_type"] != "gaussian_hmm" or data["covariance_type"] != "diag":
            raise ValueError("unsupported model type/covariance")
        if any(not isinstance(data[key], list) for key in ("start", "transition", "means", "variances")):
            raise ValueError("model parameters must be arrays")
        if any(not isinstance(row, list) for key in ("transition", "means", "variances") for row in data[key]):
            raise ValueError("model parameter rows must be arrays")
        result = cls(
            tuple(data["start"]),
            tuple(tuple(row) for row in data["transition"]),
            tuple(tuple(row) for row in data["means"]),
            tuple(tuple(row) for row in data["variances"]),
        )
        if integer(data["state_count"], "state_count", minimum=2) != result.state_count:
            raise ValueError("state_count dimension mismatch")
        return result
