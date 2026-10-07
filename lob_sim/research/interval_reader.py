"""Independent serialized interval census; never calls the interval producer.

This re-reader verifies geometry, flags, parent identities and duration totals.
It is not an independent reconstruction of the exchange book or proof of raw
provenance. Book semantics remain covered by the existing book/oracle tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lob_sim.regime.validation import identity, integer, require_keys, strict_json
from lob_sim.research.capture_clock import CaptureClock, timestamp

REPORT_LIMIT = 8 * 1024 * 1024
ROW_LIMIT = 64 * 1024
FLAGS = {"depth_valid", "trade_valid", "mark_available", "clock_valid", "capture_valid", "joint_valid"}
SYMBOL_FIELDS = FLAGS | {"instrument_sha256", "epochs", "reasons"}
REASONS = {
    "wall_or_monotonic_clock_inconsistent",
    "instrument_unobserved",
    "book_or_depth_stream_invalid",
    "depth_stale_or_unobserved",
    "trade_stream_invalid",
    "trade_stale_or_unobserved",
    "clock_invalid",
    "capture_invalid",
    "capture_stopped",
    "mark_unavailable",
}
REPORT_FIELDS = {
    "schema_version",
    "capture_id",
    "input_sha256",
    "segments",
    "clock",
    "clock_sha256",
    "first_logical_ns",
    "last_logical_ns",
    "stop_ns",
    "clock_invalid_from_ns",
    "empirical_source",
    "integrity",
    "liveness",
    "capture_runtime",
    "input_path",
    "code_identity",
    "configuration",
    "freshness",
    "duration_ns",
    "joint_valid_ns",
    "joint_mark_available_ns",
    "joint_valid_fraction",
    "symbols",
    "instruments",
    "epoch_changes",
    "native_book_gap_count",
    "native_book_gaps_by_symbol",
    "intervals",
    "research_usable",
    "memory_contract",
    "non_claims",
    "report_sha256",
}
ROW_FIELDS = {
    "schema_version",
    "input_sha256",
    "capture_id",
    "clock_sha256",
    "logical_start_ns",
    "logical_end_ns",
    "start_ns",
    "end_ns",
    "symbols",
    "joint_valid",
    "joint_mark_available",
}


def digest(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(name + " must be a lowercase SHA-256 identity")
    return value


def load_audit_report(directory: Path) -> dict[str, Any]:
    if (directory / "failure.json").exists() or any(directory.glob("*.partial")):
        raise ValueError("capture audit is incomplete")
    with (directory / "report.json").open("rb") as handle:
        raw = handle.read(REPORT_LIMIT + 1)
    if len(raw) > REPORT_LIMIT:
        raise ValueError("capture audit report exceeds size limit")
    report = dict(require_keys(strict_json(raw.decode("utf-8")), REPORT_FIELDS, "capture audit report"))
    if report.get("schema_version") != "lob_sim.capture_audit.v1":
        raise ValueError("unsupported capture audit report")
    if report.get("report_sha256") != identity({k: v for k, v in report.items() if k != "report_sha256"}):
        raise ValueError("capture audit content identity mismatch")
    digest(report.get("input_sha256"), "capture input identity")
    if not isinstance(report.get("symbols"), dict) or not 1 <= len(report["symbols"]) <= 2:
        raise ValueError("capture audit requires one or two explicit symbols")
    if any(not isinstance(s, str) or not s or len(s) > 64 for s in report["symbols"]):
        raise ValueError("invalid capture audit symbol")
    for name in ("duration_ns", "joint_valid_ns", "joint_mark_available_ns", "native_book_gap_count"):
        integer(report[name], name)
    fraction = report["joint_valid_fraction"]
    if fraction is not None and (type(fraction) is not float or not 0 <= fraction <= 1):
        raise ValueError("capture valid fraction must be a finite float or null")
    for totals in report["symbols"].values():
        for count in require_keys(
            totals,
            {"depth_valid_ns", "trade_valid_ns", "mark_available_ns", "joint_valid_ns"},
            "symbol duration totals",
        ).values():
            integer(count, "symbol duration")
    clock = CaptureClock.from_dict(report["clock"])
    if report["clock_sha256"] != clock.digest:
        raise ValueError("capture audit clock identity mismatch")
    first = timestamp(report["first_logical_ns"], "first source time")
    last = timestamp(report["last_logical_ns"], "last source time")
    if first != clock.origin_logical_ns or last < first:
        raise ValueError("capture audit source clock range mismatch")
    if report.get("freshness") != {
        "depth_ns": 5_000_000_000,
        "trade_ns": 60_000_000_000,
        "interval": "left-known [start,end);no future rescue or EOF extrapolation",
    }:
        raise ValueError("unsupported capture freshness contract")
    config = report["configuration"]
    if not isinstance(config, dict) or config.get("mm_enabled") is not False or "hmm" in config:
        raise ValueError("capture audit cannot depend on strategy/HMM execution")
    if sorted(config["symbols"]) != sorted(report["symbols"]):
        raise ValueError("capture audit symbol/config mismatch")
    if type(report.get("empirical_source")) not in {bool, type(None)}:
        raise ValueError("invalid capture provenance classification")
    integrity = report["integrity"]
    if not isinstance(integrity, dict) or type(integrity.get("ok")) is not bool:
        raise ValueError("capture audit integrity is missing")
    if not isinstance(integrity.get("reasons"), list) or any(not isinstance(r, str) for r in integrity["reasons"]):
        raise ValueError("capture audit integrity reasons missing")
    if integrity["ok"] != (not integrity["reasons"]):
        raise ValueError("capture audit integrity/reason contradiction")
    if report.get("research_usable") is not (integrity["ok"] and report["empirical_source"] is True):
        raise ValueError("capture audit research admission contradiction")
    if not isinstance(report["instruments"], dict) or not set(report["instruments"]).issubset(report["symbols"]):
        raise ValueError("capture audit instrument universe mismatch")
    for symbol, entry in report["instruments"].items():
        require_keys(entry, {"spec", "sha256"}, "instrument identity")
        spec = require_keys(
            entry["spec"],
            {"symbol", "venue", "price_currency", "quantity_unit", "tick_size", "step_size", "contract_multiplier"},
            "instrument specification",
        )
        # Unknown units remain explicit empty strings in native diagnostics;
        # the stricter USD-M admission rule rejects them, not this reader.
        if any(not isinstance(v, str) or len(v) > 128 for v in spec.values()) or spec["symbol"] != symbol:
            raise ValueError("invalid instrument specification strings")
        for name in ("tick_size", "step_size", "contract_multiplier"):
            try:
                number = Decimal(spec[name])
            except InvalidOperation as exc:
                raise ValueError("invalid instrument numeric metadata") from exc
            if not number.is_finite() or number <= 0:
                raise ValueError("instrument metadata must be finite and positive")
        if digest(entry["sha256"], "instrument metadata identity") != identity(spec):
            raise ValueError("instrument metadata identity mismatch")
    artifact = require_keys(report["intervals"], {"path", "sha256", "rows"}, "interval artifact")
    if artifact["path"] != "intervals.jsonl":
        raise ValueError("unsafe/noncanonical capture interval path")
    digest(artifact["sha256"], "interval file identity")
    integer(artifact["rows"], "interval rows")
    return report


def iter_verified_intervals(directory: Path, report: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Drain the generator to verify every byte and the final census.

    Consumers must not publish output before exhausting it. Only one decoded
    row and running totals are retained; there is no row/history collection.
    """
    import hashlib

    clock = CaptureClock.from_dict(report["clock"])
    expected_symbols = set(report["symbols"])
    expected_next = report["first_logical_ns"]
    count = duration = joint = marks = 0
    totals = {
        s: {"depth_valid_ns": 0, "trade_valid_ns": 0, "mark_available_ns": 0, "joint_valid_ns": 0}
        for s in expected_symbols
    }
    checksum = hashlib.sha256()
    with (directory / "intervals.jsonl").open("rb") as handle:
        while raw := handle.readline(ROW_LIMIT + 1):
            if len(raw) > ROW_LIMIT or not raw.endswith(b"\n"):
                raise ValueError("interval row exceeds limit or has an incomplete tail")
            checksum.update(raw)
            row = dict(require_keys(strict_json(raw.decode("utf-8")), ROW_FIELDS, "capture interval"))
            if (
                any(row[k] != report[k] for k in ("input_sha256", "capture_id", "clock_sha256"))
                or row["schema_version"] != "lob_sim.capture_interval.v1"
            ):
                raise ValueError("interval parent identity mismatch")
            start, end = (
                timestamp(row["logical_start_ns"], "interval start"),
                timestamp(row["logical_end_ns"], "interval end"),
            )
            if start != expected_next or end <= start or end > report["last_logical_ns"]:
                raise ValueError("interval gap,overlap,reordering or source extrapolation")
            if timestamp(row["start_ns"], "UTC start") != clock.project(start) or timestamp(
                row["end_ns"], "UTC end"
            ) != clock.project(end):
                raise ValueError("interval clock projection mismatch")
            states = row["symbols"]
            if not isinstance(states, dict) or set(states) != expected_symbols:
                raise ValueError("interval symbol universe mismatch")
            for symbol, raw_state in states.items():
                state = require_keys(raw_state, SYMBOL_FIELDS, "interval symbol state")
                if any(type(state[k]) is not bool for k in FLAGS):
                    raise ValueError("interval validity must contain exact booleans")
                if state["joint_valid"] != all(state[k] for k in FLAGS - {"joint_valid"}):
                    raise ValueError("interval validity intersection mismatch")
                if state["mark_available"] and not state["depth_valid"]:
                    raise ValueError("a mark cannot be available without fresh valid depth")
                epochs = state["epochs"]
                if epochs is None:
                    if state["instrument_sha256"] is not None or any(state[k] for k in FLAGS):
                        raise ValueError("unobserved instrument cannot have valid state")
                else:
                    if not isinstance(epochs, list) or len(epochs) != 3:
                        raise ValueError("interval needs independent book/public/market epochs")
                    for epoch in epochs:
                        integer(epoch, "interval epoch")
                    digest(state["instrument_sha256"], "interval instrument identity")
                    if report["research_usable"] and state["instrument_sha256"] != report["instruments"].get(
                        symbol, {}
                    ).get("sha256"):
                        raise ValueError("admitted interval differs from stable instrument metadata")
                if not isinstance(state["reasons"], list) or any(
                    not isinstance(r, str) or not r or len(r) > 512 for r in state["reasons"]
                ):
                    raise ValueError("invalid interval exclusion reasons")
                if len(state["reasons"]) != len(set(state["reasons"])) or any(
                    r not in REASONS and not r.startswith("native:") for r in state["reasons"]
                ):
                    raise ValueError("unsupported/duplicate interval exclusion reason")
                if state["joint_valid"] != (not state["reasons"]):
                    raise ValueError("interval validity/exclusion contradiction")
                for field, flag in (
                    ("depth_valid_ns", "depth_valid"),
                    ("trade_valid_ns", "trade_valid"),
                    ("mark_available_ns", "mark_available"),
                    ("joint_valid_ns", "joint_valid"),
                ):
                    totals[symbol][field] += end - start if state[flag] else 0
            for key in ("joint_valid", "joint_mark_available"):
                if type(row[key]) is not bool:
                    raise ValueError("joint validity must be boolean")
            if row["joint_valid"] != all(s["joint_valid"] for s in states.values()) or row[
                "joint_mark_available"
            ] != all(s["mark_available"] and s["clock_valid"] and s["capture_valid"] for s in states.values()):
                raise ValueError("joint interval intersection mismatch")
            count += 1
            duration += end - start
            joint += end - start if row["joint_valid"] else 0
            marks += end - start if row["joint_mark_available"] else 0
            expected_next = end
            yield row
    if expected_next != report["last_logical_ns"] or duration != report["last_logical_ns"] - report["first_logical_ns"]:
        raise ValueError("interval source coverage mismatch")
    expected_fraction = joint / duration if duration else None
    if any(
        report[k] != value
        for k, value in (
            ("duration_ns", duration),
            ("joint_valid_ns", joint),
            ("joint_mark_available_ns", marks),
            ("symbols", totals),
            ("joint_valid_fraction", expected_fraction),
        )
    ):
        raise ValueError("serialized interval census differs from recomputed duration totals")
    if count != report["intervals"]["rows"] or checksum.hexdigest() != report["intervals"]["sha256"]:
        raise ValueError("interval file identity/count mismatch")
