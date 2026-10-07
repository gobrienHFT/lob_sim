"""Frozen source/day units; two observed books, one quoted instrument.

Opening inventory is zero in each unit. This is a conditional experiment,
not a continuously funded cross-day portfolio. Warmup and omitted UTC edges
are explicit, even for an otherwise eligible full research day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lob_sim.regime.validation import identity
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_decision import DAY_NS
from lob_sim.research.day_view import view_specification
from lob_sim.research.registered_protocol import FrozenProtocol
from lob_sim.research.replay_engine import ReplayWindow


def expected_study_units(
    protocol: FrozenProtocol, parents: dict[str, tuple[Path, dict[str, Any]]], *, registry_sha256: str
) -> list[dict[str, Any]]:
    registered = protocol.snapshot()
    result = []
    for day in registered["split"]["test_days"]:
        protocol.authorize("test", day, registry_sha256=registry_sha256)
        day_start = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        for sha, (_, report) in sorted(parents.items(), key=lambda p: (p[1][1]["clock"]["origin_wall_ns"], p[0])):
            if report["research_usable"] is not True:
                continue
            clock = CaptureClock.from_dict(report["clock"])
            offset = clock.origin_wall_ns - clock.origin_logical_ns
            feature_start = max(day_start + clock.tolerance_ns - offset, report["first_logical_ns"])
            end = min(day_start + DAY_NS - clock.tolerance_ns - offset, report["last_logical_ns"])
            score_start = feature_start + registered["experiment"]["replay_contract"]["warmup_seconds"] * 1_000_000_000
            if score_start >= end:
                continue
            for symbol in registered["experiment"]["symbols"]:
                contract = view_specification(
                    sha,
                    report["report_sha256"],
                    protocol.digest,
                    day,
                    clock,
                    ReplayWindow(symbol, feature_start, score_start, end),
                )
                result.append(
                    {
                        "unit_id": identity(contract),
                        "symbol": symbol,
                        "utc_day": day,
                        "source_sha256": sha,
                        "contract": contract,
                    }
                )
    return result
