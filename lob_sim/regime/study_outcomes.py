"""Matched-clock execution outcomes from verified native audit streams.

This offline consumer neither generates fills nor revalues inventory. Rational
sufficient statistics are pooled before presentation/resampling. Missing
markouts remain missing, zero-activity minutes remain on the clock grid, and
all consumed rows must reproduce their parent audit identities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..research.clock_bootstrap import (
    PairedClockPeriod,
    PairedClockRatioPeriod,
    paired_clock_bootstrap,
    paired_clock_ratio_bootstrap,
)
from ..sim.export import iter_fill_audit_rows
from ..sim.metrics import FILL_AUDIT_CHAIN_DOMAIN, advance_audit_digest
from .economics import EconomicLedger
from .execution import CHAIN_DOMAIN, iter_execution_rows, verify_execution_trace
from .study_periods import MAX_PERIODS, PERIOD_NS
from .validation import canonical_json

ADDITIVE_UNITS = {
    "fill_count": "fills per complete UTC minute;not quote fill rate",
    "fees_quote": "quote currency per complete UTC minute;negative means rebate",
    "turnover_quote": "absolute traded notional in quote currency per complete UTC minute",
}
RATIO_UNITS = {
    "pending_cancel_fill_fraction": "pending-cancel fills / all fills",
    "mean_quote_age_ms": "sum modeled fill quote age ms / all fills",
    "mean_queue_ahead_lots": "sum modeled queue-ahead lots / fills with queue evidence",
    "queue_coverage": "fills with modeled queue evidence / all fills",
    "mean_spread_capture": "sum native marked spread capture value / marked quantity",
    "spread_coverage": "marked fills / all fills",
}


def outcome_contract(horizons: Sequence[int]) -> dict[str, Any]:
    ratios = dict(RATIO_UNITS)
    for horizon in horizons:
        for name, unit in (
            ("signed_markout", "sum native signed markout * resolved quantity / resolved quantity"),
            ("adverse_fraction", "negative signed markout observations / resolved observations"),
            ("coverage", "resolved observations / all fills"),
            ("mean_actual_lag_seconds", "sum actual observation lag seconds / resolved observations"),
        ):
            ratios[f"{name}_{horizon}ms"] = unit
    return {
        "schema_version": "lob_sim.hmm_clock_outcome_contract.v1",
        "additive_units": dict(ADDITIVE_UNITS),
        "ratio_units": ratios,
        "assignment": "[UTC minute start,end);original integer logical fill time plus verified stable wall offset",
        "eligibility": "same jointly complete risk/mark/regime-valid minutes;zero activity retained",
        "resampling": "paired source/day/epoch/contiguous blocks;ratio of sums;no iid fills or average of minute ratios",
        "missing": "resolved,invalidated,unresolved counts retained;missing signed markouts never zero-filled",
        "scope": "fees/turnover are cash-flow diagnostics,not marked net PnL or full-path drawdown",
    }


def _ratio(numerator: Fraction | int, denominator: Fraction | int) -> dict[str, str]:
    return {"numerator_rational": str(numerator), "denominator_rational": str(denominator)}


def _blank() -> dict[str, Any]:
    return {
        "fill_count": 0,
        "qty": Fraction(0),
        "fees_quote": Fraction(0),
        "turnover_quote": Fraction(0),
        "pending_cancel_fills": 0,
        "quote_age_ms_sum": Fraction(0),
        "queue_samples": 0,
        "queue_ahead_lots_sum": 0,
        "marked_fills": 0,
        "marked_qty": Fraction(0),
        "spread_sum": Fraction(0),
    }


def read_execution_periods(
    execution_path: Path,
    trades_path: Path,
    *,
    execution_summary: Mapping[str, Any],
    economic_summary: Mapping[str, Any],
    risk_periods: Sequence[Mapping[str, Any]],
    span: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Single-symbol bounded table; drain/verify even sources with no minutes.

    The existing economic ledger checks each joined fill against exact grid,
    quantity, fee, currency and source evidence. Both streams are independently
    rehashed while consumed, so a post-verification mutation cannot leak into
    a returned result. No per-fill history is retained by this reducer.
    """
    verify_execution_trace(execution_path, execution_summary)
    parents = economic_summary["parents"]
    if (
        economic_summary["symbol"] != execution_summary["symbol"]
        or economic_summary["model_sha256"] != execution_summary["model_sha256"]
        or parents["execution_trace_count"] != execution_summary["trace_count"]
        or parents["execution_trace_sha256"] != execution_summary["trace_sha256"]
        or parents["global_fill_count"] != execution_summary["fill_count"]
    ):
        raise ValueError("clock outcomes require matching single-symbol economic/execution parents")
    if type(span.get("wall_offset_ns")) is not int or span.get("clock_basis") != economic_summary["clock_basis"]:
        raise ValueError("clock outcomes require a stable matching wall/logical anchor")
    if len(risk_periods) > MAX_PERIODS:
        raise ValueError("offline clock outcome period cap exceeded")
    starts = [p["utc_start_ns"] for p in risk_periods]
    if starts != sorted(set(starts)) or any(
        type(start) is not int or start % PERIOD_NS or p["period_ns"] != PERIOD_NS
        for start, p in zip(starts, risk_periods)
    ):
        raise ValueError("clock outcome grid must be ordered unique complete UTC minutes")
    buckets = {start: _blank() for start in starts}
    horizons = execution_summary["horizons_ms"]
    for empty_cell in buckets.values():
        empty_cell["markouts"] = {
            str(h): {
                "resolved": 0,
                "invalidated": 0,
                "adverse": 0,
                "qty": Fraction(0),
                "weighted_sum": Fraction(0),
                "lag_sum": Fraction(0),
            }
            for h in horizons
        }
    ledger = EconomicLedger(economic_summary["grid"], len(execution_summary["conditioned"]["pre_fill"]) - 2)
    execution_digest, execution_count = sha256(CHAIN_DOMAIN).hexdigest(), 0
    fill_digest, fill_count = sha256(FILL_AUDIT_CHAIN_DOMAIN.encode()).digest(), 0
    fills = iter_fill_audit_rows(trades_path)
    try:
        for row in iter_execution_rows(execution_path):
            execution_digest = sha256(bytes.fromhex(execution_digest) + canonical_json(row).encode()).hexdigest()
            execution_count += 1
            start = (row["logical_ns"] + span["wall_offset_ns"]) // PERIOD_NS * PERIOD_NS
            cell = buckets.get(start)
            if row["event_type"] == "fill":
                fill = next(fills, None)
                if fill is None:
                    raise ValueError("clock outcome fill missing global trade evidence")
                fill_count += 1
                fill_digest = advance_audit_digest(fill_digest, fill)
                if row["fill_id"] != fill_count or fill["symbol"] != execution_summary["symbol"]:
                    raise ValueError("clock outcomes cannot combine unmatched or other-symbol fills")
                ledger.on_fill(fill, row, row["logical_ns"])
                if cell is not None:
                    cell["fill_count"] += 1
                    cell["qty"] += Fraction(row["qty"])
                    cell["fees_quote"] += Fraction(row["fee"])
                    cell["turnover_quote"] += Fraction(fill["notional"])
                    cell["pending_cancel_fills"] += int(row["order_state_at_fill"] == "pending_cancel")
                    cell["quote_age_ms_sum"] += Fraction(str(row["time_in_book_ms"]))
                    if row["queue_ahead_lots"] is not None:
                        cell["queue_samples"] += 1
                        cell["queue_ahead_lots_sum"] += row["queue_ahead_lots"]
                    if row["spread_capture_value"] is not None:
                        cell["marked_fills"] += 1
                        cell["marked_qty"] += Fraction(row["qty"])
                        cell["spread_sum"] += Fraction(row["spread_capture_value"])
            elif row["event_type"] == "markout":
                if row["horizon_ms"] not in horizons or row["status"] not in {"resolved", "invalidated"}:
                    raise ValueError("clock outcome markout differs from the verified horizon/status contract")
                if cell is None:
                    continue
                stats = cell["markouts"][str(row["horizon_ms"])]
                if row["status"] == "resolved":
                    value, quantity = Fraction(row["markout"]), Fraction(row["qty"])
                    stats["resolved"] += 1
                    stats["adverse"] += int(value < 0)
                    stats["qty"] += quantity
                    stats["weighted_sum"] += value * quantity
                    stats["lag_sum"] += Fraction(str(row["actual_lag_seconds"]))
                else:
                    stats["invalidated"] += 1
            else:
                raise ValueError("clock outcome consumed unknown execution event")
        if (
            execution_count != execution_summary["trace_count"]
            or execution_digest != execution_summary["trace_sha256"]
            or fill_count != parents["global_fill_count"]
            or fill_digest.hex() != parents["global_fill_sha256"]
            or next(fills, None) is not None
        ):
            raise ValueError("clock outcome consumed audit identity/census mismatch")
    finally:
        close = getattr(fills, "close", None)
        if close is not None:
            close()
    result = []
    for period in risk_periods:
        cell = buckets[period["utc_start_ns"]]
        count = cell["fill_count"]
        ratios = {
            "pending_cancel_fill_fraction": _ratio(cell["pending_cancel_fills"], count),
            "mean_quote_age_ms": _ratio(cell["quote_age_ms_sum"], count),
            "mean_queue_ahead_lots": _ratio(cell["queue_ahead_lots_sum"], cell["queue_samples"]),
            "queue_coverage": _ratio(cell["queue_samples"], count),
            "mean_spread_capture": _ratio(cell["spread_sum"], cell["marked_qty"]),
            "spread_coverage": _ratio(cell["marked_fills"], count),
        }
        coverage = {}
        for horizon, stats in cell["markouts"].items():
            pending = count - stats["resolved"] - stats["invalidated"]
            if pending < 0:
                raise ValueError("clock outcome horizon census exceeds period fills")
            coverage[horizon] = {
                "resolved": stats["resolved"],
                "invalidated": stats["invalidated"],
                "unresolved": pending,
                "resolved_quantity_rational": str(stats["qty"]),
            }
            ratios[f"signed_markout_{horizon}ms"] = _ratio(stats["weighted_sum"], stats["qty"])
            ratios[f"adverse_fraction_{horizon}ms"] = _ratio(stats["adverse"], stats["resolved"])
            ratios[f"coverage_{horizon}ms"] = _ratio(stats["resolved"], count)
            ratios[f"mean_actual_lag_seconds_{horizon}ms"] = _ratio(stats["lag_sum"], stats["resolved"])
        result.append(
            {
                "utc_start_ns": period["utc_start_ns"],
                "period_ns": PERIOD_NS,
                "epoch": period["epoch"],
                "excluded_reason": period["excluded_reason"],
                "additive": {key: str(cell[key]) for key in ADDITIVE_UNITS},
                "ratios": ratios,
                "markout_census": coverage,
                "fill_quantity_rational": str(cell["qty"]),
            }
        )
    return tuple(result)


def compare_execution_periods(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    source_sha256: str,
    *,
    horizons: Sequence[int],
    replicates: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    """Policy-minus-baseline; native values and their actual denominators."""
    if [(p["utc_start_ns"], p["period_ns"]) for p in left] != [(p["utc_start_ns"], p["period_ns"]) for p in right]:
        raise ValueError("paired outcomes do not cover the same UTC clock grid")
    contract = outcome_contract(horizons)
    metrics: dict[str, Any] = {}
    for kind, units in (("additive", contract["additive_units"]), ("ratios", contract["ratio_units"])):
        for metric, unit in units.items():
            pairs, ratio_pairs = [], []
            epoch_id, previous_epochs = 0, None
            for a, b in zip(left, right):
                epochs = (a["epoch"], b["epoch"])
                if epochs != previous_epochs:
                    epoch_id += 1
                previous_epochs = epochs
                reason = a["excluded_reason"] or b["excluded_reason"]
                if kind == "additive":
                    ln, rn = float(Fraction(a[kind][metric])), float(Fraction(b[kind][metric]))
                    ld = rd = 1.0
                else:
                    ln, ld = (
                        float(Fraction(a[kind][metric][key])) for key in ("numerator_rational", "denominator_rational")
                    )
                    rn, rd = (
                        float(Fraction(b[kind][metric][key])) for key in ("numerator_rational", "denominator_rational")
                    )
                clock = PairedClockPeriod(source_sha256, a["utc_start_ns"], a["period_ns"], epoch_id, ln, rn, reason)
                pairs.append(clock)
                ratio_pairs.append(PairedClockRatioPeriod(clock, ln, ld, rn, rd))
            reports = {}
            for minutes in (30, 5, 60):
                if not pairs:
                    reports[str(minutes)] = {
                        "estimate": None,
                        "interval": None,
                        "eligible_period_count": 0,
                        "period_count": 0,
                        "block_minutes": minutes,
                        "claim_ready": False,
                        "unavailable_reason": "source has no complete one-minute UTC periods",
                    }
                else:
                    reports[str(minutes)] = (
                        paired_clock_bootstrap(pairs, block_minutes=minutes, replicates=replicates, seed=seed)
                        if kind == "additive"
                        else paired_clock_ratio_bootstrap(
                            ratio_pairs, block_minutes=minutes, replicates=replicates, seed=seed
                        )
                    )
            metrics[metric] = {"unit": unit, "sensitivities": reports}
    return {"contract": contract, "metrics": metrics, "claim_ready": False}
