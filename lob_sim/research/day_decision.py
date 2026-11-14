"""Integer-only full-day admission predicate, isolated for threshold proofs."""

from __future__ import annotations

from lob_sim.regime.validation import integer

DAY_NS = 86_400_000_000_000


def coverage_exclusions(covered_ns: int, joint_ns: int, marked_ns: int) -> list[str]:
    for name, value in (("covered_ns", covered_ns), ("joint_ns", joint_ns), ("marked_ns", marked_ns)):
        integer(value, name)
        if value > DAY_NS:
            raise ValueError("duration exceeds full UTC day")
    if joint_ns > min(covered_ns, marked_ns) or marked_ns > covered_ns:
        raise ValueError("validity duration conservation violated")
    return [
        reason
        for value, ppm, reason in (
            (covered_ns, 990_000, "coverage_below_99pct_of_full_utc_day"),
            (joint_ns, 950_000, "joint_valid_below_95pct_of_full_utc_day"),
            (marked_ns, 950_000, "joint_mark_below_95pct_of_full_utc_day"),
        )
        if value * 1_000_000 < DAY_NS * ppm
    ]
