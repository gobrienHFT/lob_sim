"""Strict finalized-capture admission and bounded receipt/liveness reduction."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
import re
from typing import Any

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.schema import validate_record_object
from lob_sim.record.segmented import _open_text, validate_segment
from lob_sim.regime.validation import identity, integer, strict_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import _record_from_envelope
from lob_sim.research.capture_clock import CaptureClock, timestamp

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SEGMENTS = 4096
MAX_CAPTURE_ROW_CHARS = 2 * 1024 * 1024
KNOWN_EVENTS = frozenset(
    {
        "connect",
        "reconnect",
        "disconnect",
        "connect_failure",
        "parse_failure",
        "snapshot_attempt",
        "snapshot_rejected",
        "writer_failure",
        "overflow",
        "capture_abort",
        "capture_trailer",
        "clock_anchor",
        "capture_stopping",
    }
)


def strict_envelopes(path: Path) -> Iterator[EventEnvelope]:
    """Bound wire size and reject duplicate fields before permissive decoding.

    The native segment validator still verifies header/trailer and row CRC
    correspondence. Research admission adds mandatory epoch/checksum presence;
    legacy import defaults must not manufacture missing causal identity here.
    """
    handle, _raw = _open_text(path)
    try:
        while line := handle.readline(MAX_CAPTURE_ROW_CHARS + 1):
            if len(line) > MAX_CAPTURE_ROW_CHARS or not line.endswith("\n"):
                raise ValueError("capture row exceeds the research limit or has an incomplete tail")
            row = strict_json(line)
            if not isinstance(row, dict):
                raise ValueError("capture row must be an object")
            if row.get("record") == "event":
                event = row.get("event")
                mandatory = {
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
                }
                if not isinstance(event, dict) or not mandatory.issubset(event):
                    raise ValueError("research envelope is missing causal/checksum fields")
                if not isinstance(event["raw_payload_checksum"], str) or not event["raw_payload_checksum"]:
                    raise ValueError("research envelope must carry its raw checksum,not compute a missing one")
                yield EventEnvelope.from_dict(event)
            elif row.get("record") not in {"segment_header", "segment_trailer"}:
                raise ValueError("unknown research capture record")
    finally:
        handle.close()


def load_capture_manifest(path: Path) -> dict[str, Any]:
    if not path.name.endswith(".manifest.json"):
        raise ValueError("research admission requires a finalized schema-v3 capture manifest")
    with path.open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("capture manifest exceeds the research size limit")
    data = strict_json(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "lob_sim.capture_manifest.v1":
        raise ValueError("unsupported capture manifest")
    if data.get("capture_schema_version") != SCHEMA_V3:
        raise ValueError("legacy tapes are diagnostic,not eligible schema-v3 captures")
    if data.get("manifest_sha256") != identity({k: v for k, v in data.items() if k != "manifest_sha256"}):
        raise ValueError("capture manifest checksum mismatch")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments or len(segments) > MAX_SEGMENTS:
        raise ValueError("capture needs 1..4096 finalized segments")
    if integer(data.get("segment_count"), "segment_count") != len(segments):
        raise ValueError("capture segment census mismatch")
    if (
        not isinstance(data.get("capture_id"), str)
        or len(data["capture_id"]) > 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", data["capture_id"]) is None
    ):
        raise ValueError("capture identity must be a bounded single filename component")
    capture_id = data["capture_id"]
    if any((path.parent / (capture_id + suffix)).exists() for suffix in (".failure.json", ".reserve")) or any(
        path.parent.glob(capture_id + "_*.partial")
    ):
        raise ValueError("capture retains failure,reservation or partial finalization evidence")
    if (path.parent / "request.json").exists():
        from lob_sim.research.soak_reader import verify_soak

        verify_soak(path.parent, capture_manifest=path)
    return data


def audit_receipts(path: Path, symbols: tuple[str, ...]) -> dict[str, Any]:
    manifest = load_capture_manifest(path)
    initial_hash = file_sha256(path)
    clock: CaptureClock | None = None
    previous_seq = previous_mono = previous_wall = None
    first_seq = first_mono = last_mono = None
    records = gap_count = mono_regressions = wall_regressions = max_idle = max_deviation = 0
    schema_count = trailer_count = 0
    stop_ns = clock_invalid_from_ns = None
    reasons: set[str] = set()
    event_counts: Counter[str] = Counter()
    market_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    bin_ns = None
    bin_count = peak_second_count = 0
    empirical: bool | None = None
    instrument_metadata: dict[str, str] = {}
    segment_ids = []
    root = path.parent.resolve()
    for index, segment in enumerate(manifest["segments"]):
        if not isinstance(segment, dict) or not isinstance(segment.get("path"), str):
            raise ValueError("invalid capture segment entry")
        target = (root / segment["path"]).resolve()
        if not target.is_relative_to(root) or target.name.endswith(".partial"):
            raise ValueError("capture segment path escapes the capture or is incomplete")
        if integer(segment.get("segment_index"), "segment_index") != index:
            raise ValueError("capture segment ordering mismatch")
        size = target.stat().st_size
        digest = file_sha256(target)
        if size != integer(segment.get("size_bytes"), "segment size") or digest != segment.get("file_sha256"):
            raise ValueError("capture segment file identity mismatch")
        # Check bounded strict decoding first; native validation must not be
        # the first reader of a hostile unbounded JSON line.
        for _ in strict_envelopes(target):
            pass
        validation = validate_segment(target)
        if not validation.ok:
            raise ValueError("invalid capture segment: " + ";".join(validation.issues))
        for name, observed in (
            ("count", validation.count),
            ("first_recv_seq", validation.first_recv_seq),
            ("last_recv_seq", validation.last_recv_seq),
            ("content_sha256", validation.content_sha256),
        ):
            if name != "content_sha256":
                integer(segment.get(name), "segment " + name)
            if segment.get(name) != observed:
                raise ValueError("capture segment metadata mismatch: " + name)
        segment_ids.append({"path": segment["path"], "sha256": digest, "size_bytes": size})
        for envelope in strict_envelopes(target):
            if envelope.capture_id != manifest["capture_id"] or envelope.venue != "BINANCE_USDM":
                raise ValueError("capture/venue identity mismatch")
            rec = _record_from_envelope(envelope)
            validate_record_object({"ts_local": rec.ts_local, "symbol": rec.symbol, "type": rec.type, "data": rec.data})
            mono = timestamp(envelope.recv_monotonic_ns, "recv_monotonic_ns")
            wall = timestamp(envelope.recv_wall_ns, "recv_wall_ns")
            sequence = envelope.recv_seq
            if clock is None:
                clock = CaptureClock(mono, wall)
                first_seq, first_mono = sequence, mono
                if sequence != 1 or rec.type != "captureMeta":
                    reasons.add("missing_capture_prefix")
            if previous_seq is not None:
                if sequence <= previous_seq:
                    raise ValueError("non-increasing global receive sequence")
                if sequence != previous_seq + 1:
                    gap_count += sequence - previous_seq - 1
                    reasons.add("receive_sequence_gap")
                assert previous_mono is not None and previous_wall is not None
                if mono < previous_mono:
                    mono_regressions += 1
                    reasons.add("receive_monotonic_regression")
                    clock_invalid_from_ns = mono if clock_invalid_from_ns is None else min(clock_invalid_from_ns, mono)
                else:
                    max_idle = max(max_idle, mono - previous_mono)
                if wall < previous_wall:
                    wall_regressions += 1
                    reasons.add("receive_wall_regression")
                    clock_invalid_from_ns = mono if clock_invalid_from_ns is None else min(clock_invalid_from_ns, mono)
            if mono < clock.origin_logical_ns:
                reasons.add("receive_monotonic_before_origin")
            else:
                deviation = abs(clock.deviation_ns(mono, wall))
                max_deviation = max(max_deviation, deviation)
                if deviation > clock.tolerance_ns:
                    reasons.add("wall_clock_deviation")
                    clock_invalid_from_ns = mono if clock_invalid_from_ns is None else min(clock_invalid_from_ns, mono)
            if trailer_count:
                reasons.add("record_after_capture_trailer")
            if rec.symbol not in (*symbols, "*"):
                reasons.add("unexpected_instrument")
            expected_route = {
                "depthUpdate": "public",
                "snapshot": "public",
                "aggTrade": "market",
                "exchangeInfo": "control",
                "captureMeta": "control",
            }.get(rec.type)
            if expected_route is not None and envelope.route != expected_route:
                reasons.add("market_route_mismatch")
            if rec.type == "captureMeta":
                schema_count += 1
                if rec.data.get("schemaVersion") != 3 or rec.data.get("clock") != "receive_time":
                    reasons.add("missing_schema_v3_receive_clock")
                kind = rec.data.get("source_kind")
                empirical = (
                    True
                    if kind == "public_binance_network"
                    else False
                    if kind in {"synthetic_test", "synthetic"}
                    else None
                )
            if rec.type == "exchangeInfo" and rec.symbol in symbols:
                entry = rec.data.get("exchangeInfo")
                if entry is not None:
                    from decimal import Decimal
                    from lob_sim.research.capture_instruments import validate_instrument_entry
                    from lob_sim.binance.symbols import parse_exchange_info_for_symbol

                    validate_instrument_entry(rec.symbol, entry)
                    metadata_id = identity(entry)
                    spec = parse_exchange_info_for_symbol({"symbols": [entry]}, rec.symbol)
                    if (
                        rec.data.get("metadata_sha256") != metadata_id
                        or Decimal(rec.data["tickSize"]) != spec.tick_size
                        or Decimal(rec.data["stepSize"]) != spec.step_size
                        or rec.data.get("baseAsset") != spec.quantity_unit
                        or rec.data.get("quoteAsset") != spec.price_currency
                    ):
                        raise ValueError("captured instrument metadata/filter identity mismatch")
                    if rec.symbol in instrument_metadata and instrument_metadata[rec.symbol] != metadata_id:
                        reasons.add("instrument_metadata_changed")
                    instrument_metadata[rec.symbol] = metadata_id
            if rec.type == "captureEvent":
                name = str(rec.data["event"])
                event_counts[name if name in KNOWN_EVENTS else "other"] += 1
                if rec.data["route"] != envelope.route:
                    reasons.add("control_route_mismatch")
                if name == "capture_trailer":
                    trailer_count += 1
                elif name == "capture_stopping":
                    stop_ns = mono if stop_ns is None else min(stop_ns, mono)
                elif name in {"writer_failure", "capture_abort", "overflow"}:
                    reasons.add(name)
            elif rec.type in {"depthUpdate", "aggTrade"}:
                key = rec.symbol + ":" + rec.type if rec.symbol in symbols else "unexpected"
                market_counts[key] += 1
                current_bin = mono // 1_000_000_000
                if current_bin != bin_ns:
                    peak_second_count = max(peak_second_count, bin_count)
                    bin_ns, bin_count = current_bin, 0
                bin_count += 1
            route_counts[envelope.route if envelope.route in {"public", "market", "control"} else "other"] += 1
            records += 1
            previous_seq, previous_mono, previous_wall = sequence, mono, wall
            last_mono = mono if last_mono is None else max(last_mono, mono)
        if file_sha256(target) != digest:
            raise ValueError("capture segment changed during receipt audit")
    if clock is None or first_mono is None or last_mono is None:
        raise ValueError("capture contains no receipts")
    if schema_count != 1:
        reasons.add("capture_metadata_census")
    if empirical is True and set(instrument_metadata) != set(symbols):
        reasons.add("public_instrument_metadata_missing")
    if trailer_count != 1:
        reasons.add("capture_trailer_missing" if not trailer_count else "capture_trailer_census")
    runtime = manifest.get("capture_runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("capture runtime must be an object")
    writer = runtime.get("writer", {})
    if not isinstance(writer, dict) or writer.get("complete") is not True:
        reasons.add("writer_completion_missing")
    elif type(writer.get("overflow_count")) is not int or writer["overflow_count"] != 0:
        reasons.add("writer_overflow_or_missing_count")
    for name in ("records_enqueued", "records_written"):
        if isinstance(writer, dict) and name in writer and (type(writer[name]) is not int or writer[name] != records):
            reasons.add("writer_record_census")
    if (
        records != integer(manifest.get("event_count"), "event_count")
        or first_seq != integer(manifest.get("first_recv_seq"), "first_recv_seq")
        or previous_seq != integer(manifest.get("last_recv_seq"), "last_recv_seq")
    ):
        raise ValueError("capture manifest receive/event census mismatch")
    if file_sha256(path) != initial_hash:
        raise ValueError("capture manifest changed during receipt audit")
    return {
        "capture_id": manifest["capture_id"],
        "input_sha256": initial_hash,
        "segments": segment_ids,
        "clock": clock.as_dict(),
        "clock_sha256": clock.digest,
        "first_logical_ns": first_mono,
        "last_logical_ns": last_mono,
        "stop_ns": stop_ns,
        "clock_invalid_from_ns": clock_invalid_from_ns,
        "empirical_source": empirical,
        "integrity": {
            "ok": not reasons,
            "reasons": sorted(reasons),
            "records": records,
            "receive_sequence_gaps": gap_count,
            "monotonic_regressions": mono_regressions,
            "wall_regressions": wall_regressions,
            "trailer_count": trailer_count,
        },
        "liveness": {
            "initial_instrument_metadata_sha256": dict(sorted(instrument_metadata.items())),
            "event_counts": dict(sorted(event_counts.items())),
            "market_counts": dict(sorted(market_counts.items())),
            "routes": dict(sorted(route_counts.items())),
            "max_interarrival_ns": max_idle,
            "peak_aligned_monotonic_second_market_records": max(peak_second_count, bin_count),
            "max_wall_projection_deviation_ns": max_deviation,
            "writer": writer,
            "venue_packet_loss": "unknown;receipt continuity does not certify venue-side delivery",
        },
        "capture_runtime": manifest.get("capture_runtime", {}),
    }
