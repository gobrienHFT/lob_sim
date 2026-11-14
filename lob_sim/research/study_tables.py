"""Bounded serialized clock-period tables, not in-memory event traces."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
import os
from typing import Any

from lob_sim.regime.validation import canonical_json, strict_json, require_keys
from lob_sim.regime.study_periods import read_risk_periods
from lob_sim.regime.study_outcomes import read_execution_periods
from lob_sim.regime.study_pnl import read_pnl_periods
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.replay_engine import ReplayWindow
from lob_sim.research.study_primary import primary_clock_periods

MAX_ROWS, MAX_ROW_BYTES = 1440, 64 * 1024


def wall_span(contract: dict[str, Any]) -> dict[str, Any]:
    clock, window = CaptureClock.from_dict(contract["clock"]), ReplayWindow(**contract["window"])
    return {
        "utc_day": contract["utc_day"],
        "first_wall_ns": clock.project(window.score_start_ns),
        "last_wall_ns": clock.project(window.end_ns),
        "wall_offset_ns": clock.origin_wall_ns - clock.origin_logical_ns,
        "clock_basis": "receive_nanoseconds",
    }


def reduce_tables(
    files: dict[str, Path], summary: dict[str, Any], contract: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    """Independent native consumers join serialized risk, fills and accounting."""
    span = wall_span(contract)
    risk = read_risk_periods(files["regime_risk"], summary["hmm_risk"], span)
    outcomes = read_execution_periods(
        files["regime_execution"],
        files["trades"],
        execution_summary=summary["hmm_execution"],
        economic_summary=summary["hmm_economics"],
        risk_periods=risk,
        span=span,
    )
    pnl = read_pnl_periods(
        files["regime_risk"],
        files["trades"],
        files["regime_execution"],
        risk_summary=summary["hmm_risk"],
        execution_summary=summary["hmm_execution"],
        economic_summary=summary["hmm_economics"],
        risk_periods=risk,
        span=span,
    )
    if len(risk) > MAX_ROWS:
        raise ValueError("source/day table exceeds one UTC day")
    primary = primary_clock_periods(files, summary, risk, span)
    return tuple(
        {"risk": a, "outcomes": b, "pnl": c, "primary": d}
        for a, b, c, d in zip(risk, outcomes, pnl, primary, strict=True)
    )


def publish_tables(path: Path, rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if len(rows) > MAX_ROWS or path.exists():
        raise ValueError("period table exists or exceeds its cap")
    partial, checksum = path.with_name(path.name + ".partial"), sha256()
    with partial.open("xb") as handle:
        for row in rows:
            raw = (canonical_json(row) + "\n").encode()
            if len(raw) > MAX_ROW_BYTES:
                raise ValueError("clock table row exceeds its explicit cap")
            handle.write(raw)
            checksum.update(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.link(partial, path)  # No-clobber, even if another process races us.
    partial.unlink()
    return {"path": path.name, "sha256": checksum.hexdigest(), "rows": len(rows)}


def iter_tables(path: Path, binding: dict[str, Any]) -> Iterator[dict[str, Any]]:
    require_keys(binding, {"path", "sha256", "rows"}, "period table binding")
    if path.name != binding["path"] or type(binding["rows"]) is not int or not 0 <= binding["rows"] <= MAX_ROWS:
        raise ValueError("period table identity/cap differs")
    checksum, count = sha256(), 0
    with path.open("rb") as handle:
        while raw := handle.readline(MAX_ROW_BYTES + 1):
            if len(raw) > MAX_ROW_BYTES or not raw.endswith(b"\n") or count >= MAX_ROWS:
                raise ValueError("period table row is oversized or truncated")
            row = dict(
                require_keys(strict_json(raw.decode()), {"risk", "outcomes", "pnl", "primary"}, "clock table row")
            )
            checksum.update(raw)
            count += 1
            yield row
    if checksum.hexdigest() != binding["sha256"] or count != binding["rows"]:
        raise ValueError("period table terminal checksum/census differs")
