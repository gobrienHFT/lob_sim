"""Streaming admission of causal features against certified interval parents.

The interval parent stays separate from native feature formulas.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lob_sim.regime.dataset import utc_day
from lob_sim.regime.validation import integer
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_decision import DAY_NS
from lob_sim.research.interval_reader import iter_verified_intervals


class FeatureAdmission:
    """One interval cursor per symbol; no interval deque or full-tape history.

    FeatureDatasetObserver emits each symbol's sample grid monotonically but
    can interleave older samples from another symbol. Separate cursors avoid
    a shared cursor accidentally using a future interval for that symbol.
    """

    def __init__(self, root: Path, report: dict[str, Any]):
        self.report = report
        self.clock = CaptureClock.from_dict(report["clock"])
        self.rows: Iterator[dict[str, Any]] = iter_verified_intervals(root, report)
        self.current: dict[str, Any] | None = None
        self.previous_key: Any = None
        self.valid_since: int | None = None
        self.last_target: int | None = None

    def _advance(self, target: int) -> None:
        if self.last_target is not None and target < self.last_target:
            raise ValueError("regressing feature-admission cursor")
        self.last_target = target
        while self.current is None or self.current["logical_end_ns"] <= target:
            row = next(self.rows, None)
            if row is None:
                self.current = None
                self.valid_since = None
                return
            key = tuple(
                (symbol, tuple(state["epochs"]) if state["epochs"] is not None else None)
                for symbol, state in sorted(row["symbols"].items())
            )
            if not row["joint_valid"]:
                self.valid_since = None
            elif self.valid_since is None or self.previous_key != key:
                self.valid_since = row["logical_start_ns"]
            self.previous_key, self.current = key, row

    def exclusions(self, row: Mapping[str, Any], window_ns: int, eligible_days: set[str]) -> list[str]:
        sample = integer(row["sample_ns"], "feature sample")
        available = integer(row["available_at_ns"], "feature availability", minimum=sample)
        integer(window_ns, "feature trailing window", minimum=1)
        # Left-limit availability excludes the receipt not yet observed when
        # native before_record closed this sample. No same-time future rescue.
        target = max(sample, available - 1)
        self._advance(target)
        start = sample - window_ns
        wall = self.clock.project(sample)
        day_start = wall // DAY_NS * DAY_NS
        reasons = []
        if row["status"] != "VALID":
            reasons.append("native_feature_" + row["status"].lower())
        if (
            self.current is None
            or self.current["logical_start_ns"] > target
            or self.valid_since is None
            or start < self.valid_since
        ):
            reasons.append("trailing_window_or_availability_not_joint_valid")
        if utc_day(wall) not in eligible_days:
            reasons.append("not_eligible_full_utc_day")
        if start < self.clock.origin_logical_ns:
            reasons.append("trailing_window_before_source")
        else:
            if (
                self.clock.project(start) < day_start + self.clock.tolerance_ns
                or self.clock.project(available) >= day_start + DAY_NS - self.clock.tolerance_ns
            ):
                reasons.append("utc_day_or_partition_boundary_within_clock_tolerance")
        return reasons

    def finish(self) -> None:
        # The independent interval reader validates its final hash/census only
        # at exhaustion. Publication must not bypass that terminal validation.
        for _ in self.rows:
            pass
