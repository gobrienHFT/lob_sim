"""Independent day-view re-reader, binding every source row and causal timer.

Does not call the producer's transformation or deadline scheduler. It proves
the explicit normalized contract, not the historical venue's private fills.
"""

from __future__ import annotations

from dataclasses import replace
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from lob_sim.record.envelope import SCHEMA_V3
from lob_sim.regime.validation import identity, integer, require_keys
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_view import source_envelopes, ROW_LIMIT
from lob_sim.research.replay_engine import ReplayWindow, SYMBOLS


def verify_day_view(source: Path, path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    contract = entry["contract"]
    require_keys(
        contract,
        {
            "schema_version",
            "source_sha256",
            "source_report_sha256",
            "registry_sha256",
            "utc_day",
            "clock",
            "window",
            "depth_timeout_ns",
            "trade_timeout_ns",
            "joint_symbols",
            "scope",
        },
        "research view contract",
    )
    clock = CaptureClock.from_dict(contract["clock"])
    window = ReplayWindow(**contract["window"])
    day = datetime.fromisoformat(contract["utc_day"]).replace(tzinfo=timezone.utc)
    if day.date().isoformat() != contract["utc_day"]:
        raise ValueError("research view UTC-day label is not canonical")
    start = int(day.timestamp()) * 1_000_000_000
    if (
        not start
        <= clock.project(window.feature_start_ns)
        <= clock.project(window.score_start_ns)
        < clock.project(window.end_ns)
        <= start + 86_400_000_000_000
    ):
        raise ValueError("research view scoring/window geometry escapes its UTC day")
    if (
        contract["schema_version"] != "lob_sim.research_day_view.v1"
        or contract["source_sha256"] != file_sha256(source)
        or contract["depth_timeout_ns"] != 5_000_000_000
        or contract["trade_timeout_ns"] != 60_000_000_000
        or contract["joint_symbols"] != list(SYMBOLS)
        or entry["view_id"] != identity(contract)
        or path.name != entry["path"]
        or file_sha256(path) != entry["sha256"]
    ):
        raise ValueError("normalized view contract identity differs")
    checksum = sha256()
    sequence = linked = 0
    deadlines: dict[str, int] = {}
    phase_seen = False
    controls = {"research_day_start": 0, **{s + ":" + d: 0 for s in SYMBOLS for d in ("depth", "trade")}}
    originals = iter(source_envelopes(source))
    next_source = next(originals, None)
    last_linked_ns: int | None = None
    previous_ns = None
    with closing(source_envelopes(path, exact_fields=True)) as view_rows:
        for row in view_rows:
            raw = (row.to_json() + "\n").encode("utf-8")
            if len(raw) > ROW_LIMIT:
                raise ValueError("normalized replay-view row is oversized/truncated")
            checksum.update(raw)
            parsed = row.as_dict()
            required = {
                "capture_id",
                "schema_version",
                "venue",
                "instrument",
                "event_kind",
                "route",
                "recv_seq",
                "recv_wall_ns",
                "recv_monotonic_ns",
                "stream_epoch",
                "sync_epoch",
                "raw_payload_checksum",
                "payload",
                "exchange_event_ns",
                "exchange_transaction_ns",
            }
            if not isinstance(parsed, dict) or set(parsed) != required:
                raise ValueError("normalized view row fields differ")
            sequence += 1
            t = row.recv_monotonic_ns
            if (
                row.recv_seq != sequence
                or row.capture_id != entry["view_id"]
                or row.schema_version != SCHEMA_V3
                or row.venue != "BINANCE_USDM"
                or row.recv_wall_ns != clock.project(t)
                or t > window.end_ns
                or (previous_ns is not None and t < previous_ns)
            ):
                raise ValueError("normalized receipt/clock ordering differs")
            previous_ns = t
            if sequence == 1:
                if (
                    row.event_kind != "captureMeta"
                    or row.recv_monotonic_ns != clock.origin_logical_ns
                    or row.payload
                    != {
                        "schemaVersion": 3,
                        "clock": "receive_time",
                        "source_kind": "derived_research_view",
                        "research_view": contract,
                    }
                ):
                    raise ValueError("normalized replay-view prefix differs")
                if (
                    row.instrument != window.symbol
                    or row.route != "control"
                    or row.stream_epoch != 0
                    or row.sync_epoch != 0
                    or row.exchange_event_ns is not None
                    or row.exchange_transaction_ns is not None
                ):
                    raise ValueError("normalized prefix has extraneous route/epoch/exchange identity")
                continue
            # Pending controls must be resolved before a same-time source row.
            expected_controls = [
                (deadline, dimension) for dimension, deadline in deadlines.items() if deadline <= window.end_ns
            ]
            if not phase_seen:
                expected_controls.append((window.feature_start_ns, "research_day_start"))
            earliest = min(expected_controls, default=None)
            original = row.payload.get("_research_original")
            if original is None:
                if (
                    row.instrument not in SYMBOLS
                    or row.route != "control"
                    or row.stream_epoch != 0
                    or row.sync_epoch != 0
                    or row.exchange_event_ns is not None
                    or row.exchange_transaction_ns is not None
                ):
                    raise ValueError("study-only control carries invented exchange/route identity")
                event = row.payload.get("event")
                if event == "capture_trailer":
                    if (
                        row.instrument != window.symbol
                        or t != window.end_ns
                        or row.payload
                        != {"event": "capture_trailer", "route": "control", "source_kind": "derived_research_view"}
                        or (earliest is not None and earliest[0] <= t)
                        or (next_source is not None and next_source.recv_monotonic_ns < t)
                        or not phase_seen
                    ):
                        raise ValueError("normalized trailer skips causal source/control rows")
                    if next(view_rows, None) is not None:
                        raise ValueError("normalized data follows its trailer")
                    break
                dimension = "research_day_start" if event == "research_day_start" else row.payload.get("dimension")
                if dimension not in {"research_day_start", "depth", "trade"} or event not in {
                    "research_day_start",
                    "research_timeout",
                }:
                    raise ValueError("unknown normalized freshness control")
                assert isinstance(dimension, str)
                key = dimension if dimension == "research_day_start" else row.instrument + ":" + dimension
                if (
                    row.event_kind != "captureEvent"
                    or row.route != "control"
                    or (dimension == "research_day_start" and row.instrument != window.symbol)
                    or earliest != (t, key)
                    or (next_source is not None and next_source.recv_monotonic_ns < t)
                ):
                    raise ValueError("normalized timeout/phase is not the next causal control")
                payload: dict[str, Any] = (
                    {"event": "research_day_start", "route": "control"}
                    if dimension == "research_day_start"
                    else {"event": "research_timeout", "route": "control", "dimension": dimension}
                )
                if dict(row.payload) != payload:
                    raise ValueError("normalized control payload differs")
                if dimension == "research_day_start":
                    phase_seen = True
                else:
                    deadlines.pop(key)
                controls[key] += 1
            else:
                if (
                    next_source is None
                    or next_source.recv_monotonic_ns >= window.end_ns
                    or (earliest is not None and earliest[0] <= t)
                ):
                    raise ValueError("normalized source row bypasses a deadline")
                parent = next_source
                link = {
                    "capture_id": parent.capture_id,
                    "recv_seq": parent.recv_seq,
                    "recv_wall_ns": parent.recv_wall_ns,
                    "recv_monotonic_ns": parent.recv_monotonic_ns,
                    "stream_epoch": parent.stream_epoch,
                    "sync_epoch": parent.sync_epoch,
                    "raw_payload_checksum": parent.raw_payload_checksum,
                }
                excluded = (
                    parent.instrument not in {*SYMBOLS, "*"}
                    or parent.event_kind == "captureMeta"
                    or (parent.event_kind == "captureEvent" and parent.payload.get("event") == "capture_trailer")
                )
                payload = (
                    {
                        "event": "research_excluded_source",
                        "route": "control",
                        "original_instrument": parent.instrument,
                        "original_event_kind": parent.event_kind,
                    }
                    if excluded
                    else dict(parent.payload)
                )
                payload["_research_original"] = link
                expected = replace(
                    parent,
                    capture_id=entry["view_id"],
                    recv_seq=sequence,
                    recv_wall_ns=clock.project(parent.recv_monotonic_ns),
                    instrument=parent.instrument if parent.instrument in {*SYMBOLS, "*"} else window.symbol,
                    event_kind="captureEvent" if excluded else parent.event_kind,
                    route="control" if excluded else parent.route,
                    stream_epoch=0 if excluded or parent.route == "control" else parent.stream_epoch,
                    sync_epoch=0 if excluded or parent.route == "control" else parent.sync_epoch,
                    payload=payload,
                    raw_payload_checksum=None,
                )
                if row.as_dict() != expected.as_dict():
                    raise ValueError("normalized row changes its immutable source observation")
                linked += 1
                last_linked_ns = parent.recv_monotonic_ns
                if parent.instrument in SYMBOLS:
                    if parent.event_kind in {"snapshot", "depthUpdate"}:
                        deadlines[parent.instrument + ":depth"] = t + 5_000_000_000
                    elif parent.event_kind == "aggTrade":
                        deadlines[parent.instrument + ":trade"] = t + 60_000_000_000
                next_source = next(originals, None)
        else:
            raise ValueError("normalized replay view is missing its final trailer")
    last_source_ns = next_source.recv_monotonic_ns if next_source is not None else last_linked_ns
    for tail in originals:
        last_source_ns = tail.recv_monotonic_ns
    if last_source_ns is None or window.end_ns > last_source_ns or file_sha256(source) != contract["source_sha256"]:
        raise ValueError("normalized view extrapolates beyond or changes its source")
    if (
        checksum.hexdigest() != entry["events_sha256"]
        or file_sha256(path) != entry["sha256"]
        or sequence != integer(entry["records"], "view records")
        or linked != integer(entry["source_records"], "linked source records")
        or controls != entry["controls"]
    ):
        raise ValueError("normalized replay-view checksum/source/control census differs")
    return {"verified": True, "view_id": entry["view_id"], "records": sequence, "source_records": linked}
