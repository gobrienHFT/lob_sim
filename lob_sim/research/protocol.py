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
    lengths: Sequence[int] | None = None,
) -> BootstrapInterval:
    """Percentile mean interval; optional independent contiguous strata.

    ``block_size`` counts observations, NOT minutes. With ``lengths``, each
    contiguous sequence is resampled independently at its original weight;
    no block crosses a day/source/invalidity boundary. Every sequence must
    accommodate the requested block. Legacy calls retain their exact RNG path.
    """

    observations = _finite_values(values)
    if type(block_size) is not int or block_size <= 0 or block_size > len(observations):
        raise ValueError("block_size must be between 1 and the observation count")
    if type(replicates) is not int or replicates <= 0:
        raise ValueError("replicates must be positive")
    if not 0 < confidence < 1 or not math.isfinite(confidence):
        raise ValueError("confidence must be finite and between 0 and 1")

    rng = _SplitMix64(seed)
    spans: tuple[int, ...]
    if lengths is None:
        spans = (len(observations),)
    else:
        spans = tuple(lengths)
        if not spans or any(type(n) is not int or n < block_size for n in spans) or sum(spans) != len(observations):
            raise ValueError("sequence lengths must conserve observations and each accommodate block_size")
    bootstrap_means: list[float] = []
    for _ in range(replicates):
        sample: list[float] = []
        offset = 0
        for length in spans:
            segment: list[float] = []
            for _ in range(math.ceil(length / block_size)):
                start = offset + rng.randbelow(length - block_size + 1)
                segment.extend(observations[start : start + block_size])
            sample.extend(segment[:length])
            offset += length
        bootstrap_means.append(sum(sample) / len(observations))

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
        algorithm="splitmix64_moving_blocks_v1" if lengths is None else "splitmix64_stratified_moving_blocks_v1",
    )


def paired_moving_block_bootstrap_mean_delta(
    left: Sequence[float | int],
    right: Sequence[float | int],
    *,
    block_size: int,
    replicates: int = 2_000,
    confidence: float = 0.95,
    seed: int = 1,
    lengths: Sequence[int] | None = None,
) -> BootstrapInterval:
    """Bootstrap paired ``left - right`` observations on identical events."""

    if len(left) != len(right):
        raise ValueError("paired bootstrap inputs must have equal length")
    left_values, right_values = _finite_values(left), _finite_values(right)
    deltas = [a - b for a, b in zip(left_values, right_values)]
    return moving_block_bootstrap_mean(
        deltas,
        block_size=block_size,
        replicates=replicates,
        confidence=confidence,
        seed=seed,
        lengths=lengths,
    )


def paired_moving_block_bootstrap_ratio_delta(
    left_numerators: Sequence[float | int],
    left_denominators: Sequence[float | int],
    right_numerators: Sequence[float | int],
    right_denominators: Sequence[float | int],
    *,
    block_size: int,
    replicates: int = 2_000,
    confidence: float = 0.95,
    seed: int = 7,
    lengths: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Paired ratio-of-sums, not a mean of period ratios or iid fills.

    All four components use the same sampled block indices. Zero-activity
    periods stay in the clock sequence. A replicate with a zero denominator
    is counted, never discarded/redrawn: any such replicate makes the interval
    unavailable. Independent strata retain their original number of periods.
    """
    for values in (left_numerators, left_denominators, right_numerators, right_denominators):
        if any(type(value) not in (int, float) for value in values):
            raise TypeError("ratio components must be numeric, not bool or coercible strings")
    components = tuple(
        _finite_values(values) for values in (left_numerators, left_denominators, right_numerators, right_denominators)
    )
    count = len(components[0])
    if any(len(values) != count for values in components):
        raise ValueError("paired ratio components must have equal length")
    if type(block_size) is not int or not 1 <= block_size <= count:
        raise ValueError("block_size must be between 1 and the observation count")
    if type(replicates) is not int or replicates < 1:
        raise ValueError("replicates must be positive")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (float, int))
        or not math.isfinite(confidence)
        or not 0 < confidence < 1
    ):
        raise ValueError("confidence must be finite and between 0 and 1")
    spans = (count,) if lengths is None else tuple(lengths)
    if not spans or any(type(n) is not int or n < block_size for n in spans) or sum(spans) != count:
        raise ValueError("sequence lengths must conserve observations and each accommodate block_size")
    for nums, dens in ((components[0], components[1]), (components[2], components[3])):
        if any(d < 0 or (d == 0 and n != 0) for n, d in zip(nums, dens)):
            raise ValueError("ratio denominators must be nonnegative; zero denominator requires zero numerator")

    def totals(indices: Sequence[int] | range) -> tuple[float, float, float, float]:
        result = tuple(math.fsum(values[i] for i in indices) for values in components)
        if not all(math.isfinite(value) for value in result):
            raise ValueError("ratio component totals must remain finite")
        return result[0], result[1], result[2], result[3]

    ln, ld, rn, rd = totals(range(count))
    estimate = ln / ld - rn / rd if ld and rd else None
    deltas: list[float] = []
    undefined = 0
    if estimate is not None:
        rng = _SplitMix64(seed)
        for _ in range(replicates):
            indices: list[int] = []
            offset = 0
            for length in spans:
                segment: list[int] = []
                for _ in range(math.ceil(length / block_size)):
                    start = offset + rng.randbelow(length - block_size + 1)
                    segment.extend(range(start, start + block_size))
                indices.extend(segment[:length])
                offset += length
            sn, sd, tn, td = totals(indices)
            if not sd or not td:
                undefined += 1
            else:
                delta = sn / sd - tn / td
                if not math.isfinite(delta):
                    raise ValueError("bootstrap ratio delta must remain finite")
                deltas.append(delta)
    if estimate is not None and not math.isfinite(estimate):
        raise ValueError("ratio delta must remain finite")
    interval = None
    reason = None
    if estimate is None:
        reason = "at least one strategy has zero aggregate denominator"
    elif undefined:
        reason = "zero denominator in a paired bootstrap replicate; no conditional discard or redraw"
    else:
        deltas.sort()
        tail = (1 - confidence) / 2
        interval = BootstrapInterval(
            estimate=estimate,
            lower=_linear_quantile(deltas, tail),
            upper=_linear_quantile(deltas, 1 - tail),
            confidence=confidence,
            block_size=block_size,
            replicates=replicates,
            sample_count=count,
            seed=seed,
            algorithm="splitmix64_stratified_paired_ratio_of_sums_v1",
        ).as_dict()
    return {
        "schema_version": "lob_sim.paired_ratio_bootstrap.v1",
        "estimand": "sum(left_numerator)/sum(left_denominator)-sum(right_numerator)/sum(right_denominator)",
        "estimate": estimate,
        "left_numerator_sum": ln,
        "left_denominator_sum": ld,
        "right_numerator_sum": rn,
        "right_denominator_sum": rd,
        "interval": interval,
        "unavailable_reason": reason,
        "undefined_replicates": undefined,
        "evaluated_replicates": replicates if estimate is not None else 0,
        "zero_activity_periods": {
            "left": sum(d == 0 for d in components[1]),
            "right": sum(d == 0 for d in components[3]),
        },
        "sequence_lengths": list(spans),
        "block_size": block_size,
        "replicates": replicates,
        "confidence": confidence,
        "seed": seed,
    }
