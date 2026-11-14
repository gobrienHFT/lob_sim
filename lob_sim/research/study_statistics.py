"""Paired multi-source/day clock statistics; no IID-fill inference or tuning.

Every source/day/epoch/gap stays a resampling boundary. Symbols, execution
scenarios, latencies and feature cadences are never pooled into one best cell.
Results remain descriptive conditional scenarios, not profitability claims.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import Any

from lob_sim.regime.study_outcomes import outcome_contract
from lob_sim.regime.study_periods import METRICS
from lob_sim.research.study_primary import ADDITIVE, RATIOS
from lob_sim.research.clock_bootstrap import (
    PairedClockPeriod,
    PairedClockRatioPeriod,
    paired_clock_bootstrap,
    paired_clock_ratio_bootstrap,
)

UnitPair = tuple[str, Sequence[dict[str, Any]], Sequence[dict[str, Any]]]


def compare_units(units: Sequence[UnitPair], statistics: dict[str, Any], horizons: Sequence[int]) -> dict[str, Any]:
    """Policy minus observed baseline, after independently proven core parity.

    Ratio estimates pool exact sufficient statistics before float presentation;
    zero-activity periods remain on the common clock. Missing valuation is
    excluded explicitly. A short stratum cannot be silently dropped or bridged.
    """
    outcome = outcome_contract(horizons)
    definitions = [("risk", name, "mean", name) for name in METRICS]
    definitions += [("outcomes", name, "additive", unit) for name, unit in outcome["additive_units"].items()]
    definitions += [("outcomes", name, "ratios", unit) for name, unit in outcome["ratio_units"].items()]
    definitions += [("primary", name, "additive", unit) for name, unit in ADDITIVE.items()]
    definitions += [("primary", name, "ratios", unit) for name, unit in RATIOS.items()]
    definitions += [
        ("pnl", name, "mean", "quote currency per complete UTC minute")
        for name in ("gross_marked_delta_quote_rational", "net_marked_delta_quote_rational")
    ]
    reports = {}
    for table, metric, kind, unit in definitions:
        pairs, ratios = [], []
        epoch, previous_key = 0, None
        for source_sha, left, right in units:
            if [(r[table]["utc_start_ns"], r[table]["period_ns"]) for r in left] != [
                (r[table]["utc_start_ns"], r[table]["period_ns"]) for r in right
            ]:
                raise ValueError("paired units do not share the same complete UTC clock grid")
            for lrow, rrow in zip(left, right, strict=True):
                a, b = lrow[table], rrow[table]
                key = (source_sha, a["epoch"], b["epoch"])
                if key != previous_key:
                    epoch += 1
                previous_key = key
                reason = a["excluded_reason"] or b["excluded_reason"]
                ln: float | None
                rn: float | None
                if kind == "ratios":
                    ln, ld = (
                        float(Fraction(a[kind][metric][k])) for k in ("numerator_rational", "denominator_rational")
                    )
                    rn, rd = (
                        float(Fraction(b[kind][metric][k])) for k in ("numerator_rational", "denominator_rational")
                    )
                elif kind == "additive":
                    ln, rn = float(Fraction(a[kind][metric])), float(Fraction(b[kind][metric]))
                    ld = rd = 1.0
                else:
                    ln, rn = (float(Fraction(row[metric])) if reason is None else None for row in (a, b))
                    ld = rd = 1.0
                clock = PairedClockPeriod(source_sha, a["utc_start_ns"], a["period_ns"], epoch, ln, rn, reason)
                pairs.append(clock)
                if kind == "ratios":
                    assert ln is not None and rn is not None
                    ratios.append(PairedClockRatioPeriod(clock, ln, ld, rn, rd))
        sensitivities = {}
        for minutes in (statistics["primary_block_minutes"], *statistics["sensitivity_block_minutes"]):
            if not pairs:
                sensitivities[str(minutes)] = {
                    "estimate": None,
                    "interval": None,
                    "eligible_period_count": 0,
                    "period_count": 0,
                    "block_minutes": minutes,
                    "claim_ready": False,
                    "unavailable_reason": "no complete paired UTC-minute periods",
                }
            else:
                kwargs = {
                    "block_minutes": minutes,
                    "replicates": statistics["replications"],
                    "confidence": statistics["confidence_level"],
                    "seed": statistics["seed"],
                }
                sensitivities[str(minutes)] = (
                    paired_clock_ratio_bootstrap(ratios, **kwargs)
                    if kind == "ratios"
                    else paired_clock_bootstrap(pairs, **kwargs)
                )
        reports[table + ":" + metric] = {"unit": unit, "sensitivities": sensitivities}
    return {
        "metrics": reports,
        "execution_contract": outcome,
        "statistics": statistics,
        "source_day_units": len(units),
        "claim_ready": False,
        "scope": "paired conditional public-L2 scenario differences;serial clock-block uncertainty;not unadjusted significance,profitable alpha or full-path drawdown confidence",
    }
