"""Small, dependency-free contracts for defensible research studies.

The simulator remains responsible for event semantics and accounting.  This
module only makes study design explicit: whole UTC-day partitions, a
content-addressed registry of variants frozen before the test partition, and
deterministic moving-block bootstrap intervals for paired observations.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

_UINT64_MASK = (1 << 64) - 1
_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("research metadata must be finite JSON-compatible values") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class UTCDaySplit:
    """Chronological whole-day partition for one joint-valid data universe."""

    all_days: tuple[str, ...]
    calibration_days: tuple[str, ...]
    validation_days: tuple[str, ...]
    test_days: tuple[str, ...]
    minimum_joint_valid_days: int
    claim_ready: bool
    reason: str | None

    @property
    def digest(self) -> str:
        return _sha256(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.utc_day_split.v1",
            "all_days": list(self.all_days),
            "calibration_days": list(self.calibration_days),
            "validation_days": list(self.validation_days),
            "test_days": list(self.test_days),
            "minimum_joint_valid_days": self.minimum_joint_valid_days,
            "claim_ready": self.claim_ready,
            "reason": self.reason,
        }


def _normalized_days(days: Iterable[str | date]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in days:
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
        normalized.add(parsed.isoformat())
    return tuple(sorted(normalized))


def chronological_day_split(
    days: Iterable[str | date],
    *,
    minimum_joint_valid_days: int = 10,
) -> UTCDaySplit:
    """Split sorted UTC days 60/20/20 without shuffling or leakage.

    Fewer than ``minimum_joint_valid_days`` days returns a valid diagnostic
    split with ``claim_ready=False``.  At least three distinct days are needed
    for non-empty calibration, validation, and test partitions.
    """

    if minimum_joint_valid_days < 3:
        raise ValueError("minimum_joint_valid_days must be at least 3")
    all_days = _normalized_days(days)
    if len(all_days) < 3:
        raise ValueError("at least three distinct UTC days are required")

    calibration_count = max(1, math.floor(len(all_days) * 0.60))
    validation_count = max(1, math.floor(len(all_days) * 0.20))
    if calibration_count + validation_count >= len(all_days):
        validation_count = 1
        calibration_count = len(all_days) - 2
    calibration = all_days[:calibration_count]
    validation_end = calibration_count + validation_count
    validation = all_days[calibration_count:validation_end]
    test = all_days[validation_end:]
    claim_ready = len(all_days) >= minimum_joint_valid_days
    reason = None if claim_ready else f"only {len(all_days)} joint-valid UTC days; need {minimum_joint_valid_days}"
    return UTCDaySplit(
        all_days=all_days,
        calibration_days=calibration,
        validation_days=validation,
        test_days=test,
        minimum_joint_valid_days=minimum_joint_valid_days,
        claim_ready=claim_ready,
        reason=reason,
    )


@dataclass(frozen=True)
class _RegisteredVariant:
    variant_id: str
    name: str
    config: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"variant_id": self.variant_id, "name": self.name, "config": deepcopy(dict(self.config))}


class ResearchRegistry:
    """Content-addressed strategy/config registry with an explicit freeze."""

    schema_version = "lob_sim.research_registry.v1"

    def __init__(self) -> None:
        self._variants: dict[str, _RegisteredVariant] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, name: str, config: Mapping[str, Any]) -> str:
        if self._frozen:
            raise RuntimeError("research registry is frozen; register variants before opening test data")
        if not str(name).strip():
            raise ValueError("variant name must be non-empty")
        if not isinstance(config, Mapping):
            raise TypeError("variant config must be a mapping")
        frozen_config = deepcopy(dict(config))
        variant_id = _sha256({"name": str(name), "config": frozen_config})[:16]
        existing = self._variants.get(variant_id)
        if existing is not None and (existing.name != str(name) or dict(existing.config) != frozen_config):
            raise ValueError(f"variant identity collision: {variant_id}")
        self._variants[variant_id] = _RegisteredVariant(variant_id, str(name), frozen_config)
        return variant_id

    def freeze(self) -> dict[str, Any]:
        self._frozen = True
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        variants = [self._variants[key].as_dict() for key in sorted(self._variants)]
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "frozen": self._frozen,
            "variants": variants,
        }
        payload["registry_sha256"] = _sha256(payload)
        return payload


@dataclass(frozen=True)
class BootstrapInterval:
    """Deterministic percentile interval for a mean statistic."""

    estimate: float
    lower: float
    upper: float
    confidence: float
    block_size: int
    replicates: int
    sample_count: int
    seed: int
    algorithm: str = "splitmix64_moving_blocks_v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.bootstrap_interval.v1",
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "confidence": self.confidence,
            "block_size": self.block_size,
            "replicates": self.replicates,
            "sample_count": self.sample_count,
            "seed": self.seed,
            "algorithm": self.algorithm,
        }


class _SplitMix64:
    def __init__(self, seed: int) -> None:
        self.state = int(seed) & _UINT64_MASK

    def next_u64(self) -> int:
        self.state = (self.state + _SPLITMIX_INCREMENT) & _UINT64_MASK
        value = self.state
        value = ((value ^ (value >> 30)) * _SPLITMIX_MULTIPLIER_1) & _UINT64_MASK
        value = ((value ^ (value >> 27)) * _SPLITMIX_MULTIPLIER_2) & _UINT64_MASK
        return (value ^ (value >> 31)) & _UINT64_MASK

    def randbelow(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper must be positive")
        return self.next_u64() % upper


def _finite_values(values: Sequence[float | int]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            raise TypeError("bootstrap observations must be numeric, not bool")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("bootstrap observations must be finite")
        result.append(parsed)
    if not result:
        raise ValueError("at least one bootstrap observation is required")
    return tuple(result)


def _linear_quantile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] + weight * (sorted_values[upper] - sorted_values[lower]))


def moving_block_bootstrap_mean(
    values: Sequence[float | int],
    *,
    block_size: int,
    replicates: int = 2_000,
    confidence: float = 0.95,
    seed: int = 1,
) -> BootstrapInterval:
    """Return a deterministic percentile interval using overlapping blocks."""

    observations = _finite_values(values)
    if block_size <= 0 or block_size > len(observations):
        raise ValueError("block_size must be between 1 and the observation count")
    if replicates <= 0:
        raise ValueError("replicates must be positive")
    if not 0 < confidence < 1 or not math.isfinite(confidence):
        raise ValueError("confidence must be finite and between 0 and 1")

    rng = _SplitMix64(seed)
    max_start = len(observations) - block_size + 1
    bootstrap_means: list[float] = []
    block_count = math.ceil(len(observations) / block_size)
    for _ in range(replicates):
        sample: list[float] = []
        for _ in range(block_count):
            start = rng.randbelow(max_start)
            sample.extend(observations[start : start + block_size])
        bootstrap_means.append(sum(sample[: len(observations)]) / len(observations))

    bootstrap_means.sort()
    tail = (1.0 - confidence) / 2.0
    return BootstrapInterval(
        estimate=sum(observations) / len(observations),
        lower=_linear_quantile(bootstrap_means, tail),
        upper=_linear_quantile(bootstrap_means, 1.0 - tail),
        confidence=confidence,
        block_size=block_size,
        replicates=replicates,
        sample_count=len(observations),
        seed=int(seed),
    )


def paired_moving_block_bootstrap_mean_delta(
    left: Sequence[float | int],
    right: Sequence[float | int],
    *,
    block_size: int,
    replicates: int = 2_000,
    confidence: float = 0.95,
    seed: int = 1,
) -> BootstrapInterval:
    """Bootstrap paired ``left - right`` observations on identical events."""

    if len(left) != len(right):
        raise ValueError("paired bootstrap inputs must have equal length")
    deltas = [float(a) - float(b) for a, b in zip(left, right)]
    return moving_block_bootstrap_mean(
        deltas,
        block_size=block_size,
        replicates=replicates,
        confidence=confidence,
        seed=seed,
    )
