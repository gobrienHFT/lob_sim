"""Causal equity left limits for matched [start,end) UTC-minute PnL.

Only verified accounting boundary points are consumed. No next-observation
interpolation, stale-price extrapolation or invalid-epoch return bridge is
permitted. This is analysis of native scenario results, not a new valuation
authority or an estimator of continuous-path drawdown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..research.clock_bootstrap import PairedClockPeriod, paired_clock_bootstrap
from .economics import EconomicPoint, reconstruct_economics
from .study_periods import MAX_PERIODS, PERIOD_NS
from .validation import integer

PNL_CONTRACT = {
    "schema_version": "lob_sim.hmm_clock_pnl_contract.v1",
    "period_ns": PERIOD_NS,
    "assignment": "[start,end);equity left limits before ALL same-time market observations/actions/fills",
    "valuation": "cash plus inventory at the latest strictly-earlier fresh native mid;net subtracts cumulative fees",
    "expiry": "open-inventory left limit requires mark_until_ns >= endpoint;no next-observation interpolation",
    "eligibility": "complete common risk/mark/regime-valid minute plus known causal valuation at both endpoints",
    "estimand": "mean per-minute marked-equity delta:left-minus-right;not whole-path drawdown",
    "scope": "linear single-symbol scenario,zero opening inventory/cash;no funding or private fill truth",
}


def _endpoint(point: EconomicPoint | None, logical_ns: int) -> dict[str, Any]:
    if point is None:
        return {
            "gross": None,
            "net": None,
            "fees": None,
            "anchor_ns": None,
            "reason": "no strictly-earlier economic boundary",
        }
    if point.logical_ns >= logical_ns:
        raise ValueError("PnL endpoint cannot use same-time or future accounting state")
    gross = point.cash_quote
    reason = None
    if point.inventory_lots:
        if point.mid_twice_tick is None or point.mark_until_ns is None or point.mark_until_ns < logical_ns:
            reason = "open inventory has no fresh left-limit mark"
        else:
            gross += Fraction(point.inventory_lots * point.mid_twice_tick, 2) * point.quantum_quote_per_tick_lot
    return {
        "gross": gross if reason is None else None,
        "net": gross - point.fees_quote if reason is None else None,
        "fees": point.fees_quote,
        "anchor_ns": point.logical_ns,
        "reason": reason,
    }


class EconomicClockSampler:
    """One current point; explicitly capped offline endpoint/period tables."""

    def __init__(self, periods: Sequence[Mapping[str, Any]], span: Mapping[str, Any]) -> None:
        if len(periods) > MAX_PERIODS:
            raise ValueError("offline PnL clock-period cap exceeded")
        if type(span.get("wall_offset_ns")) is not int or span.get("clock_basis") not in {
            "receive_nanoseconds",
            "legacy_compatibility_nanoseconds",
        }:
            raise ValueError("PnL periods require a stable wall/logical anchor")
        first = integer(span["first_wall_ns"], "PnL source first wall ns")
        last = integer(span["last_wall_ns"], "PnL source last wall ns", minimum=first)
        starts = [p["utc_start_ns"] for p in periods]
        if any(
            type(t) is not int
            or t % PERIOD_NS
            or type(p["period_ns"]) is not int
            or p["period_ns"] != PERIOD_NS
            or t < first
            or t + PERIOD_NS > last
            for t, p in zip(starts, periods)
        ) or starts != sorted(set(starts)):
            raise ValueError("PnL clock grid must contain unique ordered complete captured UTC minutes")
        self.periods = tuple({**p, "epoch": list(p["epoch"]) if p["epoch"] is not None else None} for p in periods)
        self.offset = span["wall_offset_ns"]
        self.cutoffs = tuple(sorted({t for start in starts for t in (start, start + PERIOD_NS)}))
        self.index = 0
        self.previous: EconomicPoint | None = None
        self.endpoints: dict[int, dict[str, Any]] = {}

    def observe(self, point: EconomicPoint) -> None:
        now = integer(point.logical_ns, "economic point time")
        if self.previous is not None and now < self.previous.logical_ns:
            raise ValueError("regressing economic boundary")
        # Flush before replacing the previous point, including the FIRST row
        # at an exact grid time. Every later same-time row belongs to the new
        # period; it cannot rewrite the already sampled left limit.
        while self.index < len(self.cutoffs) and self.cutoffs[self.index] - self.offset <= now:
            wall = self.cutoffs[self.index]
            self.endpoints[wall] = _endpoint(self.previous, wall - self.offset)
            self.index += 1
        self.previous = point

    def periods_result(self) -> tuple[dict[str, Any], ...]:
        result = []
        for period in self.periods:
            start, end = period["utc_start_ns"], period["utc_start_ns"] + PERIOD_NS
            a = self.endpoints.get(start, _endpoint(None, start - self.offset))
            b = self.endpoints.get(end, _endpoint(None, end - self.offset))
            reason = period["excluded_reason"] or a["reason"] or b["reason"]
            gross = b["gross"] - a["gross"] if reason is None else None
            net = b["net"] - a["net"] if reason is None else None
            fees = b["fees"] - a["fees"] if a["fees"] is not None and b["fees"] is not None else None
            if reason is None:
                assert gross is not None and net is not None and fees is not None
                if net != gross - fees:
                    raise ValueError("PnL clock gross/fee/net conservation failed")
            result.append(
                {
                    "utc_start_ns": start,
                    "period_ns": PERIOD_NS,
                    "epoch": list(period["epoch"]) if period["epoch"] is not None else None,
                    "excluded_reason": reason,
                    "risk_excluded_reason": period["excluded_reason"],
                    "start_valuation_reason": a["reason"],
                    "end_valuation_reason": b["reason"],
                    "start_anchor_logical_ns": a["anchor_ns"],
                    "end_anchor_logical_ns": b["anchor_ns"],
                    "gross_marked_delta_quote_rational": str(gross) if gross is not None else None,
                    "net_marked_delta_quote_rational": str(net) if net is not None else None,
                    "fees_delta_quote_rational": str(fees) if fees is not None else None,
                }
            )
        return tuple(result)


def read_pnl_periods(
    risk_path: Path,
    trades_path: Path,
    execution_path: Path,
    *,
    risk_summary: Mapping[str, Any],
    execution_summary: Mapping[str, Any],
    economic_summary: Mapping[str, Any],
    risk_periods: Sequence[Mapping[str, Any]],
    span: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    sampler = EconomicClockSampler(risk_periods, span)
    parents = economic_summary["parents"]
    verified = reconstruct_economics(
        risk_path,
        trades_path,
        execution_path,
        risk_summary=risk_summary,
        execution_summary=execution_summary,
        fill_count=parents["global_fill_count"],
        fill_sha256=parents["global_fill_sha256"],
        on_boundary=sampler.observe,
    )
    if verified != economic_summary or span["clock_basis"] != verified["clock_basis"]:
        raise ValueError("PnL period economic parent/clock reconciliation failed")
    return sampler.periods_result()  # No provisional callback result is published before verification.


def compare_pnl_periods(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    source_sha256: str,
    *,
    replicates: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    if [(p["utc_start_ns"], p["period_ns"]) for p in left] != [(p["utc_start_ns"], p["period_ns"]) for p in right]:
        raise ValueError("paired PnL variants require the same UTC clock grid")
    result = {}
    for metric in ("gross_marked_delta_quote_rational", "net_marked_delta_quote_rational"):
        pairs = []
        epoch, previous_epochs = 0, None
        for a, b in zip(left, right):
            epochs = (a["epoch"], b["epoch"])
            if epochs != previous_epochs:
                epoch += 1
            previous_epochs = epochs
            reason = a["excluded_reason"] or b["excluded_reason"]
            pairs.append(
                PairedClockPeriod(
                    source_sha256,
                    a["utc_start_ns"],
                    a["period_ns"],
                    epoch,
                    float(Fraction(a[metric])) if reason is None else None,
                    float(Fraction(b[metric])) if reason is None else None,
                    reason,
                )
            )
        result[metric] = {
            str(minutes): paired_clock_bootstrap(pairs, block_minutes=minutes, replicates=replicates, seed=seed)
            if pairs
            else {
                "estimate": None,
                "interval": None,
                "eligible_period_count": 0,
                "period_count": 0,
                "block_minutes": minutes,
                "claim_ready": False,
                "unavailable_reason": "source has no complete one-minute UTC periods",
            }
            for minutes in (30, 5, 60)
        }
    return {"contract": dict(PNL_CONTRACT), "metrics": result, "claim_ready": False}
