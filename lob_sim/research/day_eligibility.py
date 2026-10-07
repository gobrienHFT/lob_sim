"""Fixed full-UTC-day admission rule, separately re-applied from interval files."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lob_sim.regime.dataset import utc_day
from lob_sim.regime.validation import identity
from lob_sim.research.interval_reader import iter_verified_intervals, load_audit_report
from lob_sim.research.day_decision import DAY_NS, coverage_exclusions

REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
MAX_RESEARCH_DAYS = 4096


def eligibility_rule() -> dict[str, Any]:
    return {
        "schema_version": "lob_sim.utc_research_eligibility.v1",
        "required_symbols": list(REQUIRED_SYMBOLS),
        "venue": "BINANCE_USDM",
        "utc_day_ns": DAY_NS,
        "minimum_coverage_ppm": 990_000,
        "minimum_joint_valid_ppm": 950_000,
        "minimum_joint_mark_ppm": 950_000,
        "clock": "first-receipt UTC-labelled projection;observed deviation<=50ms;absolute accuracy unmeasured",
        "source": "finalized schema-v3;continuous receipts;complete writer/trailer;declared public_binance_network",
        "freshness": {"depth_ns": 5_000_000_000, "trade_ns": 60_000_000_000},
        "overlap": "reject overlapping independent paired-symbol capture spans;no hidden deduplication",
        "instrument": "stable full symbol/venue/currency/unit/tick/lot/multiplier identity",
        "denominator": "full 86400-second UTC day,never observed snippet duration",
    }


def summarize_days(audits: tuple[Path, ...]) -> dict[str, Any]:
    """Read every interval before deciding or publishing eligibility.

    Memory is bounded by the explicit day/source caps, not receipt count.
    Unknown coverage is reported, not implicitly credited as valid time.
    """
    if not isinstance(audits, tuple) or not audits or len(audits) > 256:
        raise ValueError("day admission requires 1..256 independently finalized capture audits")
    parents = [(root, load_audit_report(root)) for root in audits]
    parents.sort(key=lambda p: (p[1]["clock"]["origin_wall_ns"], p[1]["input_sha256"]))
    if len({p[1]["input_sha256"] for p in parents}) != len(parents) or len(
        {p[1]["capture_id"] for p in parents}
    ) != len(parents):
        raise ValueError("duplicate capture identity/content cannot duplicate research time")
    previous_end = None
    days: dict[int, dict[str, Any]] = {}
    source_entries = []
    for root, report in parents:
        if set(report["symbols"]) != set(REQUIRED_SYMBOLS):
            raise ValueError("research days require the BTCUSDT+ETHUSDT capture universe")
        start = report["clock"]["origin_wall_ns"]
        end = start + report["duration_ns"]
        if previous_end is not None and start < previous_end:
            raise ValueError("independent capture spans overlap;no implicit deduplication")
        previous_end = end
        instrument_metadata_valid = all(
            isinstance(report["instruments"].get(symbol), dict)
            and report["instruments"][symbol]["spec"].get("venue") == "BINANCE_USDM"
            and report["instruments"][symbol]["spec"].get("price_currency") == "USDT"
            and report["instruments"][symbol]["spec"].get("quantity_unit") == symbol.removesuffix("USDT")
            and report["instruments"][symbol]["spec"].get("contract_multiplier") == "1"
            for symbol in REQUIRED_SYMBOLS
        )
        source_entries.append(
            {
                "capture_id": report["capture_id"],
                "input_sha256": report["input_sha256"],
                "report_sha256": report["report_sha256"],
                "clock_sha256": report["clock_sha256"],
                "research_usable": report["research_usable"],
            }
        )
        for row in iter_verified_intervals(root, report):
            cursor = row["start_ns"]
            while cursor < row["end_ns"]:
                day_start = cursor // DAY_NS * DAY_NS
                stop = min(row["end_ns"], day_start + DAY_NS)
                if day_start not in days:
                    if len(days) >= MAX_RESEARCH_DAYS:
                        raise ValueError("explicit research day cap exceeded")
                    days[day_start] = {
                        "utc_day": utc_day(day_start),
                        "utc_start_ns": day_start,
                        "total_utc_ns": DAY_NS,
                        "covered_ns": 0,
                        "joint_valid_ns": 0,
                        "joint_mark_available_ns": 0,
                        "symbols": {
                            s: {"depth_valid_ns": 0, "trade_valid_ns": 0, "mark_available_ns": 0, "joint_valid_ns": 0}
                            for s in REQUIRED_SYMBOLS
                        },
                        "invalid_reason_ns": {},
                        "source_ids": [],
                        "instrument_ids": {s: None for s in REQUIRED_SYMBOLS},
                        "source_exclusions": [],
                        "coverage_intervals": [],
                    }
                cell = days[day_start]
                dt = stop - cursor
                cell["covered_ns"] += dt
                coverage = cell["coverage_intervals"]
                if (
                    coverage
                    and coverage[-1]["end_ns"] == cursor
                    and coverage[-1]["input_sha256"] == report["input_sha256"]
                ):
                    coverage[-1]["end_ns"] = stop
                else:
                    coverage.append({"start_ns": cursor, "end_ns": stop, "input_sha256": report["input_sha256"]})
                cell["joint_valid_ns"] += dt if row["joint_valid"] else 0
                cell["joint_mark_available_ns"] += dt if row["joint_mark_available"] else 0
                if report["input_sha256"] not in cell["source_ids"]:
                    cell["source_ids"].append(report["input_sha256"])
                if not report["research_usable"]:
                    reason = (
                        "source_integrity_invalid"
                        if not report["integrity"]["ok"]
                        else "synthetic_source"
                        if report["empirical_source"] is False
                        else "empirical_provenance_undeclared"
                    )
                    if reason not in cell["source_exclusions"]:
                        cell["source_exclusions"].append(reason)
                if (
                    not instrument_metadata_valid
                    and "instrument_metadata_not_linear_usdm" not in cell["source_exclusions"]
                ):
                    cell["source_exclusions"].append("instrument_metadata_not_linear_usdm")
                for symbol, state in row["symbols"].items():
                    instrument = state["instrument_sha256"]
                    if instrument is not None:
                        known = cell["instrument_ids"][symbol]
                        if (
                            known is not None
                            and known != instrument
                            and "instrument_identity_changed" not in cell["source_exclusions"]
                        ):
                            cell["source_exclusions"].append("instrument_identity_changed")
                        cell["instrument_ids"][symbol] = instrument
                    for field, flag in (
                        ("depth_valid_ns", "depth_valid"),
                        ("trade_valid_ns", "trade_valid"),
                        ("mark_available_ns", "mark_available"),
                        ("joint_valid_ns", "joint_valid"),
                    ):
                        cell["symbols"][symbol][field] += dt if state[flag] else 0
                    for reason in state["reasons"]:
                        # Detailed native messages may contain arbitrarily
                        # many update IDs. Keep them in the interval stream,
                        # not in an event-count-sized in-memory histogram.
                        key = symbol + ":" + ("native_invalidity_detail" if reason.startswith("native:") else reason)
                        cell["invalid_reason_ns"][key] = cell["invalid_reason_ns"].get(key, 0) + dt
                cursor = stop
    # Include fully uncaptured days between sources, not just days with rows.
    if days:
        first, last = min(days), max(days)
        if (last - first) // DAY_NS + 1 > MAX_RESEARCH_DAYS:
            raise ValueError("explicit research day span cap exceeded")
        for day_start in range(first, last + DAY_NS, DAY_NS):
            if day_start not in days:
                days[day_start] = {
                    "utc_day": utc_day(day_start),
                    "utc_start_ns": day_start,
                    "total_utc_ns": DAY_NS,
                    "covered_ns": 0,
                    "joint_valid_ns": 0,
                    "joint_mark_available_ns": 0,
                    "symbols": {
                        s: {"depth_valid_ns": 0, "trade_valid_ns": 0, "mark_available_ns": 0, "joint_valid_ns": 0}
                        for s in REQUIRED_SYMBOLS
                    },
                    "invalid_reason_ns": {},
                    "source_ids": [],
                    "instrument_ids": {s: None for s in REQUIRED_SYMBOLS},
                    "source_exclusions": [],
                    "coverage_intervals": [],
                }
    result = []
    rule = eligibility_rule()
    for _, cell in sorted(days.items()):
        if cell["covered_ns"] > DAY_NS:
            raise ValueError("day coverage exceeds its full UTC denominator")
        exclusions = sorted(cell.pop("source_exclusions"))
        exclusions.extend(
            coverage_exclusions(cell["covered_ns"], cell["joint_valid_ns"], cell["joint_mark_available_ns"])
        )
        if any(value is None for value in cell["instrument_ids"].values()):
            exclusions.append("required_instrument_metadata_missing")
        cell.update(uncovered_ns=DAY_NS - cell["covered_ns"], eligible=not exclusions, exclusions=exclusions)
        cursor = cell["utc_start_ns"]
        unknown_intervals = []
        for span in cell["coverage_intervals"]:
            if span["start_ns"] > cursor:
                unknown_intervals.append({"start_ns": cursor, "end_ns": span["start_ns"], "reason": "uncaptured"})
            cursor = span["end_ns"]
        if cursor < cell["utc_start_ns"] + DAY_NS:
            unknown_intervals.append(
                {"start_ns": cursor, "end_ns": cell["utc_start_ns"] + DAY_NS, "reason": "uncaptured"}
            )
        cell["uncovered_intervals"] = unknown_intervals
        cell["source_ids"].sort()
        cell["invalid_reason_ns"] = dict(sorted(cell["invalid_reason_ns"].items()))
        result.append(cell)
    eligible = [d for d in result if d["eligible"]]
    stable_instruments = all(len({d["instrument_ids"][s] for d in eligible}) <= 1 for s in REQUIRED_SYMBOLS)
    registration_blockers = []
    if len(eligible) < 10:
        registration_blockers.append("fewer_than_ten_eligible_utc_days")
    if not stable_instruments:
        registration_blockers.append("instrument_identity_changed_between_eligible_days")
    report = {
        "schema_version": "lob_sim.research_days.v1",
        "rule": rule,
        "rule_sha256": identity(rule),
        "sources": source_entries,
        "days": result,
        "eligible_days": [d["utc_day"] for d in eligible],
        "minimum_study_days": 10,
        "ready_for_registration": not registration_blockers,
        "registration_blockers": registration_blockers,
        "interval_detail": "parent intervals retain exact invalid spans;reason durations overlap and must not be summed as disjoint time",
        "claims": "day admission under declared host clock/provenance and explicit public-L2 validity;not empirical execution truth",
    }
    report["report_sha256"] = identity(report)
    return report


def verify_day_report(audits: tuple[Path, ...], expected: Mapping[str, Any]) -> None:
    if summarize_days(audits) != dict(expected):
        raise ValueError("research day report differs from independently re-read interval parents")
