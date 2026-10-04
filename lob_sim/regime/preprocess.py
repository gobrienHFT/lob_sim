"""Frozen train-only clipping and standardization, with explicit feature identity."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .validation import finite, require_keys, vector


def _quantile(values: list[float], probability: float) -> float:
    position = (len(values) - 1) * probability
    left, right = math.floor(position), math.ceil(position)
    return values[left] * (right - position) + values[right] * (position - left) if left != right else values[left]


@dataclass(frozen=True)
class TrainOnlyScaler:
    feature_names: tuple[str, ...]
    feature_identity: str
    center: tuple[float, ...]
    scale: tuple[float, ...]
    lower: tuple[float, ...] | None = None
    upper: tuple[float, ...] | None = None
    constant_features: tuple[bool, ...] = ()
    training_rows: int = 0
    clip_quantiles: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        from .validation import integer

        names = tuple(self.feature_names)
        if (
            not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError("feature_names must be distinct nonempty strings")
        if (
            not isinstance(self.feature_identity, str)
            or len(self.feature_identity) != 64
            or any(char not in "0123456789abcdef" for char in self.feature_identity)
        ):
            raise ValueError("feature_identity must be a SHA-256 hex digest")
        center, scale = vector(self.center, "center", len(names)), vector(self.scale, "scale", len(names))
        if any(value <= 0 for value in scale):
            raise ValueError("scale must be strictly positive")
        if (self.lower is None) != (self.upper is None):
            raise ValueError("both clipping bounds must be present or absent")
        if (self.lower is None) != (self.clip_quantiles is None):
            raise ValueError("clipping bounds and quantile configuration must agree")
        if self.clip_quantiles is not None:
            lo, hi = vector(self.clip_quantiles, "clip_quantiles", 2)
            if not 0 <= lo < hi <= 1:
                raise ValueError("clip_quantiles must satisfy 0 <= low < high <= 1")
            object.__setattr__(self, "clip_quantiles", (lo, hi))
        if self.lower is not None and self.upper is not None:
            lower, upper = vector(self.lower, "lower", len(names)), vector(self.upper, "upper", len(names))
            if any(left > right for left, right in zip(lower, upper)):
                raise ValueError("clipping bounds are reversed")
            object.__setattr__(self, "lower", lower)
            object.__setattr__(self, "upper", upper)
        if len(self.constant_features) != len(names) or any(
            type(value) is not bool for value in self.constant_features
        ):
            raise ValueError("constant_features dimension/type mismatch")
        integer(self.training_rows, "training_rows", minimum=1)
        object.__setattr__(self, "feature_names", names)
        object.__setattr__(self, "center", center)
        object.__setattr__(self, "scale", scale)
        object.__setattr__(self, "constant_features", tuple(self.constant_features))

    @classmethod
    def fit_training(
        cls,
        training_rows: Sequence[Sequence[float]],
        *,
        feature_names: tuple[str, ...],
        feature_identity: str,
        clip_quantiles: tuple[float, float] | None = (0.005, 0.995),
    ) -> TrainOnlyScaler:
        """No validation/test inputs are accepted here. Dataset partitioning owns provenance.

        Fitting is intentionally offline and may retain training columns. The
        frozen scaler's runtime transform has O(feature_count) memory.
        """

        rows = tuple(vector(row, "training row", len(feature_names)) for row in training_rows)
        if not rows:
            raise ValueError("training rows must be nonempty")
        lower = upper = None
        columns = [sorted(row[index] for row in rows) for index in range(len(feature_names))]
        if clip_quantiles is not None:
            lo, hi = vector(clip_quantiles, "clip_quantiles", 2)
            if not 0 <= lo < hi <= 1:
                raise ValueError("clip_quantiles must satisfy 0 <= low < high <= 1")
            lower = tuple(_quantile(column, lo) for column in columns)
            upper = tuple(_quantile(column, hi) for column in columns)
            columns = [
                [min(max(value, lower[index]), upper[index]) for value in column]
                for index, column in enumerate(columns)
            ]
        try:
            center = tuple(math.fsum(column) / len(rows) for column in columns)
            variance = tuple(
                math.fsum((value - center[index]) ** 2 for value in column) / len(rows)
                for index, column in enumerate(columns)
            )
        except (OverflowError, ValueError) as exc:
            raise ValueError("training statistics are non-finite") from exc
        constant = tuple(value == 0 for value in variance)
        scale = tuple(1.0 if value == 0 else math.sqrt(value) for value in variance)
        return cls(feature_names, feature_identity, center, scale, lower, upper, constant, len(rows), clip_quantiles)

    def transform(
        self, values: Sequence[float], *, feature_names: tuple[str, ...], feature_identity: str
    ) -> tuple[float, ...]:
        if feature_names != self.feature_names or feature_identity != self.feature_identity:
            raise ValueError("feature order/formula identity mismatch")
        row = vector(values, "feature row", len(self.feature_names))
        if self.lower is not None and self.upper is not None:
            row = tuple(min(max(value, left), right) for value, left, right in zip(row, self.lower, self.upper))
        return tuple(
            finite((value - center) / scale, "scaled feature")
            for value, center, scale in zip(row, self.center, self.scale)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_preprocess.v1",
            "feature_names": list(self.feature_names),
            "feature_identity": self.feature_identity,
            "center": list(self.center),
            "scale": list(self.scale),
            "lower": list(self.lower) if self.lower is not None else None,
            "upper": list(self.upper) if self.upper is not None else None,
            "constant_features": list(self.constant_features),
            "training_rows": self.training_rows,
            "clip_quantiles": list(self.clip_quantiles) if self.clip_quantiles is not None else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> TrainOnlyScaler:
        data = require_keys(
            value,
            {
                "schema_version",
                "feature_names",
                "feature_identity",
                "center",
                "scale",
                "lower",
                "upper",
                "constant_features",
                "training_rows",
                "clip_quantiles",
            },
            "preprocessor",
        )
        if data["schema_version"] != "lob_sim.hmm_preprocess.v1":
            raise ValueError("unsupported preprocessor schema")
        if any(not isinstance(data[key], list) for key in ("feature_names", "center", "scale", "constant_features")):
            raise ValueError("preprocessor fields must be arrays")
        if any(
            data[key] is not None and not isinstance(data[key], list) for key in ("lower", "upper", "clip_quantiles")
        ):
            raise ValueError("preprocessor clipping bounds must be arrays")
        return cls(
            tuple(data["feature_names"]),
            data["feature_identity"],
            tuple(data["center"]),
            tuple(data["scale"]),
            tuple(data["lower"]) if data["lower"] is not None else None,
            tuple(data["upper"]) if data["upper"] is not None else None,
            tuple(data["constant_features"]),
            data["training_rows"],
            tuple(data["clip_quantiles"]) if data["clip_quantiles"] is not None else None,
        )
