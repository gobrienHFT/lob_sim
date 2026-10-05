"""Paired fixed-clock periods: no event-count minutes or gap-spanning blocks.

Input values must be comparable period statistics on the identical tape,
execution assumptions and clock grid. This consumer does not fabricate missing
marks, joint validity or observations. Absence of enough uninterrupted time
produces a null interval with an explicit reason, not a shorter hidden block.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .protocol import paired_moving_block_bootstrap_mean_delta

SECOND = 1_000_000_000
DAY = 86_400 * SECOND


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class PairedClockPeriod:
    source_sha256: str
    utc_start_ns: int
    period_ns: int
    validity_epoch: int
    left: float | None
    right: float | None
    excluded_reason: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise ValueError("period source must be a SHA-256 identity")
        _integer(self.utc_start_ns, "UTC period start")
        _integer(self.period_ns, "period_ns", 1)
        _integer(self.validity_epoch, "validity epoch")
        if DAY % self.period_ns or self.utc_start_ns % self.period_ns:
            raise ValueError("periods must align to a fixed UTC grid dividing one day")
        if self.excluded_reason is not None and (not isinstance(self.excluded_reason, str) or not self.excluded_reason):
            raise ValueError("excluded reason must be a nonempty string")
        for value in (self.left, self.right):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise ValueError("period statistics must be finite numeric values or null")
        if (self.left is None or self.right is None) and self.excluded_reason is None:
            raise ValueError("missing paired statistic requires an excluded reason")


def paired_clock_bootstrap(
    periods: Sequence[PairedClockPeriod],
    *,
    block_minutes: int = 30,
    replicates: int = 2000,
    confidence: float = 0.95,
    seed: int = 7,
) -> dict[str, Any]:
    """Bootstrap left-minus-right mean of complete jointly eligible periods.

    The target is the mean period statistic, not necessarily a whole-path
    statistic: e.g. averaging within-period drawdowns is NOT global drawdown.
    Clock coverage and common-period denominators accompany every result.
    Short strata are not silently discarded to produce an attractive interval.
    """
    _integer(block_minutes, "block_minutes", 1)
    _integer(replicates, "replicates", 1)
    _integer(seed, "seed")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (float, int))
        or not math.isfinite(confidence)
        or not 0 < confidence < 1
    ):
        raise ValueError("confidence must be finite and between zero and one")
    if not periods:
        raise ValueError("paired clock periods cannot be empty")
    width = periods[0].period_ns
    block_ns = block_minutes * 60 * SECOND
    if block_ns % width:
        raise ValueError("requested clock block must be a whole number of periods")
    block_size = block_ns // width
    previous: PairedClockPeriod | None = None
    left, right = [], []
    lengths: list[int] = []
    excluded: dict[str, int] = {}
    sources, days = set(), set()
    for period in periods:
        if not isinstance(period, PairedClockPeriod) or period.period_ns != width:
            raise ValueError("paired clock periods must share one fixed width")
        if previous is not None and period.utc_start_ns < previous.utc_start_ns + width:
            raise ValueError("duplicate, overlapping or regressing UTC paired periods")
        sources.add(period.source_sha256)
        days.add(datetime.fromtimestamp(period.utc_start_ns // SECOND, timezone.utc).date().isoformat())
        reason = period.excluded_reason
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            assert period.left is not None and period.right is not None
            contiguous = (
                previous is not None
                and previous.excluded_reason is None
                and period.utc_start_ns == previous.utc_start_ns + width
                and period.source_sha256 == previous.source_sha256
                and period.validity_epoch == previous.validity_epoch
                and period.utc_start_ns // DAY == previous.utc_start_ns // DAY
            )
            if not contiguous:
                lengths.append(0)
            lengths[-1] += 1
            left.append(period.left)
            right.append(period.right)
        previous = period
    interval = None
    reason = None
    if not lengths:
        reason = "no jointly eligible complete periods"
    elif min(lengths) < block_size:
        reason = "at least one independent contiguous stratum is shorter than the registered clock block"
    elif sum(n // block_size for n in lengths) < 2:
        reason = "fewer than two complete clock blocks; no informative resampling interval"
    else:
        interval = paired_moving_block_bootstrap_mean_delta(
            left,
            right,
            block_size=block_size,
            replicates=replicates,
            confidence=confidence,
            seed=seed,
            lengths=lengths,
        ).as_dict()
    return {
        "schema_version": "lob_sim.paired_clock_bootstrap.v1",
        "estimand": "mean_complete_joint_period_statistic:left-minus-right;not_whole_path_drawdown",
        "period_ns": width,
        "block_minutes": block_minutes,
        "block_size": block_size,
        "confidence": confidence,
        "replicates": replicates,
        "seed": seed,
        "period_count": len(periods),
        "eligible_period_count": len(left),
        "eligible_duration_ns": len(left) * width,
        "excluded_period_counts": excluded,
        "source_count": len(sources),
        "utc_days": sorted(days),
        "sequence_lengths": lengths,
        "estimate": math.fsum(a - b for a, b in zip(left, right)) / len(left) if left else None,
        "interval": interval,
        "unavailable_reason": reason,
        "boundary_rule": "stratified by source,UTC day,validity epoch and contiguous eligible grid;no gap crossing",
        "claim_ready": False,
    }
