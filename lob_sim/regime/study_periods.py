"""Clock-period risk statistics from verified native boundary audits.

Exact integer integrals precede floating presentation. Partial UTC edges,
warming/gapped marks and epoch crossings cannot become complete valid periods.
The stream reducer retains one interval/bucket; offline comparison tables have
an explicit cap, rather than retaining event traces for arbitrarily long tapes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..research.clock_bootstrap import DAY, SECOND, PairedClockPeriod, paired_clock_bootstrap
from .risk import CHAIN_DOMAIN, iter_risk_rows, verify_risk_trace
from .validation import canonical_json, integer

PERIOD_NS = 60 * SECOND
MAX_PERIODS = 100_000
METRICS = ("mean_absolute_inventory_lots", "inventory_variance_lots_squared", "mean_reserved_notional_quote")


def risk_clock_periods(
    rows: Iterator[Mapping[str, Any]], span: Mapping[str, Any], *, period_ns: int = PERIOD_NS
) -> Iterator[dict[str, Any]]:
    """Integrate known intervals only, clipping EOF to actual market receipts."""
    integer(period_ns, "study period_ns", minimum=1)
    if DAY % period_ns:
        raise ValueError("study period must divide a UTC day")
    first = integer(span["first_wall_ns"], "first wall ns")
    last = integer(span["last_wall_ns"], "last wall ns", minimum=first)
    first_complete = (first + period_ns - 1) // period_ns * period_ns
    last_complete = last // period_ns * period_ns
    if first_complete >= last_complete:
        return  # No complete UTC bucket; no projection or extrapolation needed.
    offset = span.get("wall_offset_ns")
    if type(offset) is not int or span.get("clock_basis") not in {
        "receive_nanoseconds",
        "legacy_compatibility_nanoseconds",
    }:
        raise ValueError("study needs one stable observed wall/logical anchor; clock changes cannot be interpolated")
    previous = None
    bucket = None
    for row in rows:
        if row["clock_basis"] != span["clock_basis"]:
            raise ValueError("risk and source clock bases differ")
        if previous is None:
            previous = row
            continue
        start = max(previous["logical_ns"] + offset, first_complete)
        end = min(row["logical_ns"] + offset, last_complete)
        if row["logical_ns"] < previous["logical_ns"]:
            raise ValueError("regressing risk boundary")
        while start < end:
            grid_start = start // period_ns * period_ns
            stop = min(end, grid_start + period_ns)
            if bucket is None:
                bucket = {
                    "utc_start_ns": grid_start,
                    "period_ns": period_ns,
                    "duration_ns": 0,
                    "valid_ns": 0,
                    "epoch": None,
                    "epoch_crossing": False,
                    "inventory_lots_ns": 0,
                    "absolute_inventory_lots_ns": 0,
                    "squared_inventory_lots_ns": 0,
                    "reserved_twice_tick_lots_ns": 0,
                    "halted_ns": 0,
                }
            if grid_start != bucket["utc_start_ns"]:
                raise ValueError("unaccounted risk clock period gap")
            dt = stop - start
            inventory = previous["inventory_lots"]
            bucket["duration_ns"] += dt
            bucket["inventory_lots_ns"] += inventory * dt
            bucket["absolute_inventory_lots_ns"] += abs(inventory) * dt
            bucket["squared_inventory_lots_ns"] += inventory * inventory * dt
            bucket["halted_ns"] += dt if previous["halted"] else 0
            stage = previous["stage"]
            valid_until = (
                min(
                    previous["mark_until_ns"] or previous["logical_ns"],
                    previous["regime_until_ns"] or previous["logical_ns"],
                )
                + offset
            )
            valid_dt = max(0, min(stop, valid_until) - start) if stage["status"] == "VALID" else 0
            bucket["valid_ns"] += valid_dt
            if valid_dt:
                epoch = tuple(stage["epochs"])
                if bucket["epoch"] is not None and epoch != bucket["epoch"]:
                    bucket["epoch_crossing"] = True
                bucket["epoch"] = epoch
                reserve = abs(inventory) * previous["mid_twice_tick"] + 2 * previous["order_notional_tick_lots"]
                bucket["reserved_twice_tick_lots_ns"] += reserve * valid_dt
            if stop == grid_start + period_ns:
                grid = (
                    Fraction(previous["tick_size"]),
                    Fraction(previous["step_size"]),
                    Fraction(previous["contract_multiplier"]),
                )
                mean = Fraction(bucket["inventory_lots_ns"], period_ns)
                reason = (
                    "incomplete clock coverage"
                    if bucket["duration_ns"] != period_ns
                    else "epoch crossing"
                    if bucket["epoch_crossing"]
                    else "invalid, warming or stale market/mark interval"
                    if bucket["valid_ns"] != period_ns
                    else None
                )
                yield {
                    **bucket,
                    "epoch": list(bucket["epoch"]) if bucket["epoch"] is not None else None,
                    "excluded_reason": reason,
                    "mean_absolute_inventory_lots": float(Fraction(bucket["absolute_inventory_lots_ns"], period_ns)),
                    "inventory_variance_lots_squared": float(
                        Fraction(bucket["squared_inventory_lots_ns"], period_ns) - mean * mean
                    ),
                    "mean_reserved_notional_quote": float(
                        Fraction(bucket["reserved_twice_tick_lots_ns"], 2 * period_ns) * grid[0] * grid[1] * grid[2]
                    )
                    if reason is None
                    else None,
                }
                bucket = None
            start = stop
        previous = row


def read_risk_periods(path: Path, summary: Mapping[str, Any], span: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    verify_risk_trace(path, summary)
    periods: list[dict[str, Any]] = []
    digest, count = sha256(CHAIN_DOMAIN).hexdigest(), 0

    def consumed_rows() -> Iterator[Mapping[str, Any]]:
        nonlocal digest, count
        for row in iter_risk_rows(path):
            digest = sha256(bytes.fromhex(digest) + canonical_json(row).encode()).hexdigest()
            count += 1
            yield row

    rows = consumed_rows()
    for period in risk_clock_periods(rows, span):
        if len(periods) >= MAX_PERIODS:
            raise ValueError("offline clock-period cap exceeded")
        periods.append(period)
    for _ in rows:
        pass  # Short sources still verify every consumed byte-semantic row.
    if count != summary["trace_count"] or digest != summary["trace_sha256"]:
        raise ValueError("risk clock-period consumed audit identity mismatch")
    return tuple(periods)


def compare_risk_periods(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    source_sha256: str,
    *,
    replicates: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    """Same absolute UTC periods, joint eligibility, no strategy-specific clocks."""
    if [(p["utc_start_ns"], p["period_ns"]) for p in left] != [(p["utc_start_ns"], p["period_ns"]) for p in right]:
        raise ValueError("paired variants do not cover the same UTC clock grid")
    result = {}
    for metric in METRICS:
        pairs = []
        epoch_id = 0
        last_epochs = None
        for a, b in zip(left, right):
            epochs = (a["epoch"], b["epoch"])
            if epochs != last_epochs:
                epoch_id += 1
            last_epochs = epochs
            reason = a["excluded_reason"] or b["excluded_reason"]
            pairs.append(
                PairedClockPeriod(
                    source_sha256,
                    a["utc_start_ns"],
                    a["period_ns"],
                    epoch_id,
                    a[metric] if reason is None else None,
                    b[metric] if reason is None else None,
                    reason,
                )
            )
        result[metric] = {
            str(minutes): paired_clock_bootstrap(pairs, block_minutes=minutes, replicates=replicates, seed=seed)
            if pairs
            else {
                "interval": None,
                "estimate": None,
                "unavailable_reason": "source has no complete one-minute UTC periods",
                "eligible_period_count": 0,
                "period_count": 0,
                "block_minutes": minutes,
                "claim_ready": False,
            }
            for minutes in (30, 5, 60)
        }
    return result
