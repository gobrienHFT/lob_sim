"""Source-bound normalized day views with causal study-only controls.

No prices, trades, snapshots or exchange IDs are invented. The unquoted source
prefix reconstructs both books. Only the chosen symbol can quote; both feeds
gate validity. Original receipt/checksum identities remain linked on every row.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import shutil
from typing import Any

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.regime.validation import identity
from lob_sim.record.segmented import SegmentedCaptureWriter, validate_segment
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.capture_receipts import load_capture_manifest, strict_envelopes, MAX_CAPTURE_ROW_CHARS
from lob_sim.research.replay_engine import ReplayWindow, SYMBOLS

DEPTH_NS, TRADE_NS = 5_000_000_000, 60_000_000_000
ROW_LIMIT = MAX_CAPTURE_ROW_CHARS + 16 * 1024


def source_envelopes(source: Path, *, exact_fields: bool = False) -> Generator[EventEnvelope, None, None]:
    manifest = load_capture_manifest(source)
    initial = file_sha256(source)
    root = source.parent.resolve()
    for segment in manifest["segments"]:
        path = (root / segment["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("replay-view source segment escapes capture")
        if file_sha256(path) != segment["file_sha256"] or path.stat().st_size != segment["size_bytes"]:
            raise ValueError("replay-view source segment identity differs")
        yield from strict_envelopes(path, exact_fields=exact_fields)
        validation = validate_segment(path)
        if (
            not validation.ok
            or any(
                segment[k] != getattr(validation, k)
                for k in ("count", "first_recv_seq", "last_recv_seq", "content_sha256")
            )
            or file_sha256(path) != segment["file_sha256"]
        ):
            raise ValueError("replay-view source failed terminal segment validation")
    if file_sha256(source) != initial:
        raise ValueError("replay-view source manifest changed during reading")


def original_link(row: EventEnvelope) -> dict[str, Any]:
    return {
        "capture_id": row.capture_id,
        "recv_seq": row.recv_seq,
        "recv_wall_ns": row.recv_wall_ns,
        "recv_monotonic_ns": row.recv_monotonic_ns,
        "stream_epoch": row.stream_epoch,
        "sync_epoch": row.sync_epoch,
        "raw_payload_checksum": row.raw_payload_checksum,
    }


def projected_source(row: EventEnvelope, symbol: str) -> tuple[str, str, dict[str, Any]]:
    if "_research_original" in row.payload:
        raise ValueError("source collides with reserved research linkage namespace")
    payload: dict[str, Any]
    if (
        row.instrument not in {*SYMBOLS, "*"}
        or row.event_kind == "captureMeta"
        or (row.event_kind == "captureEvent" and row.payload.get("event") == "capture_trailer")
    ):
        kind, route, payload = (
            "captureEvent",
            "control",
            {
                "event": "research_excluded_source",
                "route": "control",
                "original_instrument": row.instrument,
                "original_event_kind": row.event_kind,
            },
        )
    else:
        kind, route, payload = row.event_kind, row.route, dict(row.payload)
    payload["_research_original"] = original_link(row)
    return kind, route, payload


def view_specification(
    source_sha256: str, report_sha256: str, registry_sha256: str, day: str, clock: CaptureClock, window: ReplayWindow
) -> dict[str, Any]:
    return {
        "schema_version": "lob_sim.research_day_view.v1",
        "source_sha256": source_sha256,
        "source_report_sha256": report_sha256,
        "registry_sha256": registry_sha256,
        "utc_day": day,
        "clock": clock.as_dict(),
        "window": asdict(window),
        "depth_timeout_ns": DEPTH_NS,
        "trade_timeout_ns": TRADE_NS,
        "joint_symbols": list(SYMBOLS),
        "scope": "single quoting symbol/source/day with joint BTC/ETH observation;book-only prefix;fixed causal UTC projection;study timeout invalidations;not a new empirical capture or exchange order cancellation",
    }


def write_day_view(
    source: Path, directory: Path, contract: dict[str, Any], *, minimum_free_disk_bytes: int
) -> dict[str, Any]:
    clock = CaptureClock.from_dict(contract["clock"])
    window = ReplayWindow(**contract["window"])
    view_id = identity(contract)
    if file_sha256(source) != contract["source_sha256"] or window.feature_start_ns < clock.origin_logical_ns:
        raise ValueError("normalized view source/window binding differs")
    directory.mkdir(parents=True, exist_ok=False)
    checksum = sha256()
    sequence = source_count = 0
    pending: dict[str, int] = {"research_day_start": window.feature_start_ns}
    controls = {"research_day_start": 0, **{s + ":" + d: 0 for s in SYMBOLS for d in ("depth", "trade")}}
    with SegmentedCaptureWriter(directory, view_id, compression="zstd") as writer:

        def emit(
            t: int,
            kind: str,
            route: str,
            payload: dict[str, Any],
            *,
            stream: int = 0,
            sync: int = 0,
            instrument: str | None = None,
            exchange_event_ns: int | None = None,
            exchange_transaction_ns: int | None = None,
        ) -> None:
            nonlocal sequence
            sequence += 1
            envelope = EventEnvelope(
                view_id,
                SCHEMA_V3,
                "BINANCE_USDM",
                instrument or window.symbol,
                kind,
                route,
                sequence,
                clock.project(t),
                t,
                payload,
                exchange_event_ns=exchange_event_ns,
                exchange_transaction_ns=exchange_transaction_ns,
                stream_epoch=stream,
                sync_epoch=sync,
            )
            raw = (envelope.to_json() + "\n").encode("utf-8")
            if len(raw) > ROW_LIMIT:
                raise ValueError("normalized replay-view row exceeds its explicit limit")
            writer.write(envelope)
            checksum.update(raw)
            if sequence % 10_000 == 0 and shutil.disk_usage(directory).free < minimum_free_disk_bytes:
                raise RuntimeError("frozen replay-view disk-resource floor reached")

        def due(t: int) -> None:
            while pending and min(pending.values()) <= t:
                name = min(pending, key=lambda key: (pending[key], key))
                deadline = pending.pop(name)
                if deadline > window.end_ns:
                    continue
                controls[name] += 1
                symbol, dimension = (window.symbol, None) if name == "research_day_start" else name.split(":")
                emit(
                    deadline,
                    "captureEvent",
                    "control",
                    {"event": "research_day_start", "route": "control"}
                    if name == "research_day_start"
                    else {"event": "research_timeout", "route": "control", "dimension": dimension},
                    instrument=symbol,
                )

        emit(
            clock.origin_logical_ns,
            "captureMeta",
            "control",
            {
                "schemaVersion": 3,
                "clock": "receive_time",
                "source_kind": "derived_research_view",
                "research_view": contract,
            },
        )
        last_source_ns = None
        for row in source_envelopes(source):
            last_source_ns = row.recv_monotonic_ns
            if row.recv_monotonic_ns >= window.end_ns:
                continue  # Drain original integrity checks; never infer an unobserved tail.
            due(row.recv_monotonic_ns)
            kind, route, payload = projected_source(row, window.symbol)
            emit(
                row.recv_monotonic_ns,
                kind,
                route,
                payload,
                stream=row.stream_epoch if route != "control" else 0,
                sync=row.sync_epoch if route != "control" else 0,
                instrument=row.instrument if row.instrument in {*SYMBOLS, "*"} else window.symbol,
                exchange_event_ns=row.exchange_event_ns,
                exchange_transaction_ns=row.exchange_transaction_ns,
            )
            source_count += 1
            if row.instrument in SYMBOLS:
                if row.event_kind in {"snapshot", "depthUpdate"}:
                    pending[row.instrument + ":depth"] = row.recv_monotonic_ns + DEPTH_NS
                elif row.event_kind == "aggTrade":
                    pending[row.instrument + ":trade"] = row.recv_monotonic_ns + TRADE_NS
        if last_source_ns is None or window.end_ns > last_source_ns:
            raise ValueError("normalized view cannot extend beyond actual source observations")
        due(window.end_ns)
        emit(
            window.end_ns,
            "captureEvent",
            "control",
            {"event": "capture_trailer", "route": "control", "source_kind": "derived_research_view"},
        )
        writer.update_manifest_metadata({"research_view": contract, "writer": {"complete": True, "overflow_count": 0}})
    path = writer.manifest_path
    return {
        "path": path.name,
        "view_id": view_id,
        "sha256": file_sha256(path),
        "events_sha256": checksum.hexdigest(),
        "records": sequence,
        "source_records": source_count,
        "controls": controls,
        "contract": contract,
    }
