from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import iter_records
from lob_sim.record.schema import RecordValidationError
from lob_sim.sim.fill_model import TRADE_DEPTH_OVERLAP_WINDOW_SECONDS
from lob_sim.sim.metrics import (
    FILL_AUDIT_CHAIN_DOMAIN,
    MARKOUT_AUDIT_CHAIN_DOMAIN,
    audit_chain_sha256,
)


PACK_AUDIT_SCHEMA_VERSION = "lob_sim.futures_pack_audit.v1"
RUN_MANIFEST_SCHEMA_VERSION = "lob_sim.simulation_run.v2"
SIMULATION_ASSUMPTIONS_SCHEMA_VERSION = "lob_sim.simulation_assumptions.v2"
FILL_SOURCES = ("depth_update", "agg_trade", "taker_order")
FILL_ASSUMPTION_PROFILES = ("conservative", "base", "aggressive")
PUBLIC_CONSUMPTION_SOURCES = ("depth_update", "agg_trade")
PUBLIC_CONSUMPTION_FIELDS = (
    "observed_lots",
    "modeled_lots",
    "overlap_netted_lots",
    "queue_consumed_lots",
    "unmatched_lots",
)
EVENT_COUNT_FIELDS = (
    "records_processed",
    "exchange_info",
    "snapshot",
    "depth_update",
    "agg_trade",
    "depth_changes_applied",
    "book_gap_count",
)
MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD = {
    "exchangeInfo": "exchange_info",
    "snapshot": "snapshot",
    "depthUpdate": "depth_update",
    "aggTrade": "agg_trade",
}
MARKET_RECORD_SOURCES = (*MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD, "captureMeta", "captureEvent")
ORDER_LIFECYCLE_KEYS = (
    "arrival_scheduled",
    "arrived",
    "rested_after_arrival",
    "immediate_fill_arrivals",
    "expired_unfilled_arrivals",
    "cancel_requested",
    "cancel_acknowledged",
    "self_trade_prevented",
)
EVENT_TRACE_FIELDS = (
    "ts_local",
    "seq",
    "symbol",
    "event_type",
    "source",
    "side",
    "quote_slot",
    "price_tick",
    "qty_lots",
    "order_id",
    "fill_source",
    "details",
)
TRADE_CSV_FIELDS = (
    "provenance_schema_version",
    "ts_local",
    "symbol",
    "side",
    "price",
    "qty",
    "notional",
    "contract_multiplier",
    "maker",
    "fill_source",
    "fee_bps",
    "fee",
    "fee_currency",
    "order_id",
    "created_ts",
    "mid_at_fill",
    "spread_capture",
    "spread_capture_value",
    "regime",
    "queue_ahead_lots",
    "time_in_book_ms",
    "markout_horizon",
    "book_bid_tick",
    "book_ask_tick",
    "scenario_id",
    "evidence_ids",
    "validity",
    "queue_trajectory",
    "latency_draws_ms",
    "latency_model",
    "order_state_at_fill",
    "fee_model_id",
)
TRADE_JSON_FIELDS = {
    "evidence_ids",
    "validity",
    "queue_trajectory",
    "latency_draws_ms",
    "latency_model",
}
FILL_VALIDITY_FIELDS = {
    "book_valid",
    "trade_stream_valid",
    "clock_valid",
    "capture_valid",
    "trade_stream_required",
    "execution_valid",
    "reason",
}
PASSIVE_QUEUE_TRAJECTORY_FIELDS = {
    "queue_ahead_before_trigger_lots",
    "queue_ahead_at_fill_lots",
    "queue_consumed_before_fill_lots",
    "public_consumption_trigger_lots",
    "fill_lots",
    "remaining_order_lots_after_fill",
}
TAKER_QUEUE_TRAJECTORY_FIELDS = {
    "visible_level_before_lots",
    "fill_lots",
    "visible_level_after_lots",
    "remaining_order_lots_after_fill",
}
LATENCY_MODEL_FIELDS = {"mode", "seed", "source", "measured"}
FILL_TRACE_ROW_FIELDS = ("ts_local", "symbol", "side", "order_id", "fill_source")
FILL_TRACE_DETAIL_FIELDS = tuple(
    field for field in TRADE_CSV_FIELDS if field not in {"ts_local", "symbol", "side", "fill_source", "order_id"}
)
MARKOUT_TRACE_ROW_FIELDS = ("symbol", "side", "price_tick", "qty_lots", "order_id", "fill_source")
MARKOUT_TRACE_DETAIL_FIELDS = (
    ("fill_ts_local", "ts_local"),
    ("deadline_ts", "deadline_ts"),
    ("horizon", "horizon"),
    ("fill_price", "fill_price"),
    ("qty", "qty"),
    ("fill_mid", "fill_mid"),
    ("mid_after", "mid_after"),
    ("markout", "markout"),
    ("contract_multiplier", "contract_multiplier"),
    ("adverse", "adverse"),
    ("regime", "regime"),
)
COMMITTED_FUTURES_PACKS = (
    Path("docs/sample_outputs/futures_replay_walkthrough"),
    Path("docs/sample_outputs/futures_recorded_clip_case"),
    Path("docs/sample_outputs/futures_stress_case"),
)
EXPECTED_SIMULATION_ASSUMPTION_FIELDS = {
    "schema_version",
    "fill_assumption_profile",
    "fill_assumption",
    "data_scope",
    "private_exchange_execution_reports",
    "queue_priority_model",
    "snapshot_seed",
    "depth_increase",
    "depth_decrease",
    "agg_trade_consumption",
    "overlap_netting",
    "cancel_model",
    "same_timestamp_ordering",
    "marketable_limits",
    "self_trade_prevention",
    "markout",
    "limitations",
}
EXPECTED_SIMULATION_LIMITATIONS = {
    "no_private_queue_ids",
    "no_hidden_liquidity",
    "not_private_exchange_fill_truth",
    "public_l2_cannot_distinguish_all_cancels_from_trades",
}
SUMMARY_CSV_EXACT_FIELDS = ("strategy_profile", "fill_assumption_profile", "run_id", "input_sha256")
SUMMARY_CSV_INT_FIELDS = (
    "fill_count",
    "quote_count",
    "cancel_count",
    "self_trade_prevention_count",
    "event_trace_count",
)
SUMMARY_CSV_JSON_FIELDS = (
    "event_counts",
    "book_gap_count_by_symbol",
    "fill_source_counts",
    "fill_provenance",
    "audit_retention",
    "event_trace_retention",
    "order_lifecycle_counts",
    "adverse_fill_rate_1s_by_side",
    "markout_by_fill_source",
    "inventory_by_symbol",
    "regime_performance",
    "public_consumption_summary",
    "fill_assumption",
    "fill_assumption_diagnostics",
    "feed_adapter",
    "instrument_specs",
    "simulation_assumptions",
    "output_files",
)
FILL_FREQUENCY_REPLACEMENT_FIELDS = {
    "quote_fill_probability",
    "fills_per_quote_request",
    "fills_per_arrived_order",
}


def _audit_retention_contract(summary: dict[str, Any], issues: list[str]) -> None:
    fills = summary.get("fills")
    markouts = summary.get("markout_events")
    retention = summary.get("audit_retention")
    if not isinstance(fills, list) or not isinstance(markouts, list):
        return
    if not isinstance(retention, dict):
        issues.append("summary.json is missing audit_retention")
        return

    expected = {
        "schema_version": "lob_sim.audit_retention.v1",
        "mode": "in_memory",
        "memory_bounded_by_tape_duration": False,
        "built_in_sinks_memory_bounded": True,
        "detail_rows_complete_in_summary": True,
        "fill_rows_emitted": len(fills),
        "fill_rows_retained": len(fills),
        "fill_audit_sha256": audit_chain_sha256(FILL_AUDIT_CHAIN_DOMAIN, fills),
        "fill_sink": "NullSink",
        "markout_rows_emitted": len(markouts),
        "markout_rows_retained": len(markouts),
        "markout_audit_sha256": audit_chain_sha256(MARKOUT_AUDIT_CHAIN_DOMAIN, markouts),
        "markout_sink": "NullSink",
        "markout_trace_buffering": True,
        "pending_markouts": summary.get("markout_samples_remaining"),
    }
    for field, expected_value in expected.items():
        if retention.get(field) != expected_value:
            issues.append(
                f"summary.audit_retention.{field}={retention.get(field)!r} does not match expected {expected_value!r}"
            )
    if set(retention) != {*expected, "max_pending_markouts"}:
        issues.append("summary.audit_retention has unexpected fields")
    max_pending = retention.get("max_pending_markouts")
    if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending <= 0:
        issues.append("summary.audit_retention.max_pending_markouts must be a positive integer")

    trace_retention = summary.get("event_trace_retention")
    event_trace_count = summary.get("event_trace_count")
    expected_trace = {
        "schema_version": "lob_sim.event_trace_retention.v1",
        "retained_in_memory": True,
        "rows_emitted": event_trace_count,
        "rows_retained": event_trace_count,
        "sink": "NullSink",
        "sink_memory_bounded": True,
        "memory_bounded_by_tape_duration": False,
    }
    if not isinstance(trace_retention, dict):
        issues.append("summary.json is missing event_trace_retention")
    elif trace_retention != expected_trace:
        issues.append("summary.event_trace_retention does not match the in-memory pack export")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _record_evidence_id(record: Any, row_number: int) -> str:
    capture = record.data.get("_capture")
    if isinstance(capture, dict):
        capture_id = capture.get("captureId")
        recv_seq = capture.get("recvSeq")
        checksum = capture.get("payloadChecksum")
        if capture_id is not None and recv_seq is not None and checksum is not None:
            return f"capture:{capture_id}:recv:{int(recv_seq)}:payload:{checksum}"
        if recv_seq is not None:
            return f"input_row:{row_number}:recv:{int(recv_seq)}"
    return f"input_row:{row_number}"


def _expected_fill_scenario_id(summary: dict[str, Any], fill_source: object) -> str | None:
    if fill_source == "taker_order":
        return "public_l2:taker_visible_depth"
    assumption = summary.get("fill_assumption")
    if not isinstance(assumption, dict):
        return None
    profile = assumption.get("profile")
    trade = assumption.get("agg_trades_consume_queue")
    depth = assumption.get("depth_reductions_consume_queue")
    overlap_enabled = assumption.get("overlap_netting_enabled")
    overlap_seconds = assumption.get("overlap_window_seconds")
    if not isinstance(profile, str) or not isinstance(trade, bool) or not isinstance(depth, bool):
        return None
    if trade and depth:
        signal = "trade_and_depth"
    elif trade:
        signal = "trade"
    elif depth:
        signal = "depth"
    else:
        signal = "none"
    overlap_active = overlap_enabled is True and trade and depth
    if overlap_active:
        if not isinstance(overlap_seconds, (int, float)) or isinstance(overlap_seconds, bool):
            return None
        if not math.isfinite(float(overlap_seconds)) or float(overlap_seconds) < 0:
            return None
        overlap_us = round(float(overlap_seconds) * 1_000_000)
    else:
        overlap_us = 0
    return f"public_l2:{profile}:signal={signal}:overlap_us={overlap_us}"


def _resolve_artifact_path(raw_path: str, pack_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [
        pack_dir / path.name,
        pack_dir / path,
        _repo_root() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _load_json_object(path: Path, issues: list[str]) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append(f"Missing required file: {_display_path(path)}")
        return {}
    except json.JSONDecodeError as exc:
        issues.append(f"{_display_path(path)} is invalid JSON: {exc.msg}")
        return {}
    if not isinstance(decoded, dict):
        issues.append(f"{_display_path(path)} must contain a JSON object")
        return {}
    return decoded


def _read_csv_rows(path: Path, required_fields: tuple[str, ...], issues: list[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            missing = [field for field in required_fields if field not in fieldnames]
            if missing:
                issues.append(f"{_display_path(path)} is missing column(s): {', '.join(missing)}")
                return []
            return list(reader)
    except FileNotFoundError:
        issues.append(f"Missing required file: {_display_path(path)}")
    return []


def _parse_details(path: Path, row_index: int, raw: str, issues: list[str]) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        issues.append(f"{_display_path(path)}:{row_index} details is invalid JSON: {exc.msg}")
        return {}
    if not isinstance(decoded, dict):
        issues.append(f"{_display_path(path)}:{row_index} details must be a JSON object")
        return {}
    return decoded


def _is_close(left: Any, right: float, *, tolerance: float = 1e-12) -> bool:
    return isinstance(left, (int, float)) and math.isclose(float(left), right, rel_tol=tolerance, abs_tol=tolerance)


def _decimal(value: Any) -> Decimal | None:
    try:
        decoded = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not decoded.is_finite():
        return None
    return decoded


def _scalar_matches(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):

        def _bool_value(value: Any) -> bool | None:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value in {"True", "False"}:
                return value == "True"
            return None

        left_bool = _bool_value(left)
        right_bool = _bool_value(right)
        return left_bool is not None and right_bool is not None and left_bool == right_bool
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    return str(left) == str(right)


def _has_explicit_deprecated_fill_rate(summary: dict[str, Any]) -> bool:
    marker = summary.get("deprecated_fields")
    if not isinstance(marker, dict):
        return False
    fill_rate_marker = marker.get("fill_rate")
    if not isinstance(fill_rate_marker, dict):
        return False
    replacements = fill_rate_marker.get("replacement_fields")
    if not isinstance(replacements, list):
        return False
    return fill_rate_marker.get("status") == "deprecated" and FILL_FREQUENCY_REPLACEMENT_FIELDS <= {
        str(field) for field in replacements
    }


def _audit_manifest(pack_dir: Path, summary: dict[str, Any], manifest: dict[str, Any], issues: list[str]) -> None:
    if not manifest:
        return
    manifest_path = pack_dir / "manifest.json"
    if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        issues.append(f"{_display_path(manifest_path)} has unexpected schema_version")
    for field in ("run_id", "feed_adapter", "instrument_specs", "simulation_assumptions"):
        if manifest.get(field) != summary.get(field):
            issues.append(f"{_display_path(manifest_path)} {field} does not match summary.json")
    manifest_input = manifest.get("input")
    if isinstance(manifest_input, dict) and manifest_input.get("sha256") != summary.get("input_sha256"):
        issues.append(f"{_display_path(manifest_path)} input.sha256 does not match summary input_sha256")
    manifest_config = manifest.get("config")
    if isinstance(manifest_config, dict):
        if manifest_config.get("fill_assumption_profile") != summary.get("fill_assumption_profile"):
            issues.append(f"{_display_path(manifest_path)} fill_assumption_profile does not match summary.json")
        if manifest_config.get("fill_assumption") != summary.get("fill_assumption"):
            issues.append(f"{_display_path(manifest_path)} fill_assumption does not match summary.json")

    artifacts = manifest.get("output_artifacts")
    if not isinstance(artifacts, dict):
        issues.append(f"{_display_path(manifest_path)} is missing output_artifacts")
        return
    for label in ("summary", "summary_csv", "trades", "event_trace"):
        artifact = artifacts.get(label)
        if not isinstance(artifact, dict):
            issues.append(f"{_display_path(manifest_path)} output_artifacts[{label}] is missing")
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            issues.append(f"{_display_path(manifest_path)} output_artifacts[{label}].path is missing")
            continue
        target = _resolve_artifact_path(raw_path, pack_dir)
        if not target.exists():
            issues.append(f"{_display_path(manifest_path)} output_artifacts[{label}] target is missing: {raw_path}")
            continue
        if artifact.get("size_bytes") != target.stat().st_size:
            issues.append(f"{_display_path(manifest_path)} output_artifacts[{label}].size_bytes is stale")
        if artifact.get("sha256") != file_sha256(target):
            issues.append(f"{_display_path(manifest_path)} output_artifacts[{label}].sha256 is stale")


def _audit_fixture_provenance(
    pack_dir: Path,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    issues: list[str],
) -> None:
    summary_path = pack_dir / "summary.json"
    manifest_path = pack_dir / "manifest.json"
    relative_dir = _display_path(pack_dir)
    provenance = summary.get("fixture_provenance")
    if relative_dir.endswith("futures_recorded_clip_case"):
        expected_data_class = "recorded_public_data"
        expected_source = "recorded_public_data_clip"
        required_doc_tokens = ("recorded", "public-data clip")
    elif relative_dir.endswith("futures_stress_case") or relative_dir.endswith("futures_replay_walkthrough"):
        expected_data_class = "synthetic"
        expected_source = (
            "synthetic_exchange_shaped" if relative_dir.endswith("futures_stress_case") else "synthetic_walkthrough"
        )
        required_doc_tokens = ("synthetic",)
    elif isinstance(provenance, dict) and provenance.get("data_class") == "recorded_public_data":
        expected_data_class = "recorded_public_data"
        expected_source = str(provenance.get("source", ""))
        required_doc_tokens = ("recorded", "public-data")
    elif isinstance(provenance, dict) and provenance.get("data_class") == "synthetic":
        expected_data_class = "synthetic"
        expected_source = str(provenance.get("source", ""))
        required_doc_tokens = ("synthetic",)
    else:
        expected_data_class = ""
        expected_source = ""
        required_doc_tokens = ()

    if not isinstance(provenance, dict):
        issues.append(f"{_display_path(summary_path)} is missing fixture_provenance")
        return
    if manifest.get("fixture_provenance") != provenance:
        issues.append(f"{_display_path(manifest_path)} fixture_provenance does not match summary.json")
    if provenance.get("data_class") != expected_data_class:
        issues.append(f"{_display_path(summary_path)} fixture_provenance.data_class must be {expected_data_class}")
    if provenance.get("source") != expected_source:
        issues.append(f"{_display_path(summary_path)} fixture_provenance.source must be {expected_source}")
    if not isinstance(provenance.get("purpose"), str) or not provenance["purpose"]:
        issues.append(f"{_display_path(summary_path)} fixture_provenance.purpose is missing")

    doc_names = ("README.md", "case_notes.md") if expected_data_class == "recorded_public_data" else ("README.md",)
    if relative_dir.endswith("futures_replay_walkthrough"):
        doc_names = ("README.md", "walkthrough.md")
    for name in doc_names:
        doc_path = pack_dir / name
        if not doc_path.exists():
            issues.append(f"Missing provenance doc: {_display_path(doc_path)}")
            continue
        text = doc_path.read_text(encoding="utf-8").lower()
        for token in required_doc_tokens:
            if token not in text:
                issues.append(f"{_display_path(doc_path)} is missing provenance token: {token}")


def _audit_fill_assumption_metadata(
    pack_dir: Path,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    issues: list[str],
) -> None:
    summary_path = pack_dir / "summary.json"
    manifest_path = pack_dir / "manifest.json"
    profile = summary.get("fill_assumption_profile")
    fill_assumption = summary.get("fill_assumption")
    diagnostics = summary.get("fill_assumption_diagnostics")
    if profile not in FILL_ASSUMPTION_PROFILES:
        issues.append(f"{_display_path(summary_path)} is missing fill_assumption_profile")
    if not isinstance(fill_assumption, dict) or fill_assumption.get("profile") != profile:
        issues.append(f"{_display_path(summary_path)} has invalid fill_assumption")
    if not isinstance(diagnostics, dict) or diagnostics.get("profile") != profile:
        issues.append(f"{_display_path(summary_path)} has invalid fill_assumption_diagnostics")

    manifest_config = manifest.get("config")
    if not isinstance(manifest_config, dict):
        issues.append(f"{_display_path(manifest_path)} is missing config")
        return
    if manifest_config.get("fill_assumption_profile") != profile:
        issues.append(f"{_display_path(manifest_path)} is missing matching fill_assumption_profile")
    if manifest_config.get("fill_assumption") != fill_assumption:
        issues.append(f"{_display_path(manifest_path)} is missing matching fill_assumption")


def _audit_lifecycle(
    summary: dict[str, Any],
    lifecycle_counts: dict[str, int],
    arrival_queue: dict[str, float | int],
    issues: list[str],
) -> None:
    expected = summary.get("order_lifecycle_counts")
    if not isinstance(expected, dict):
        issues.append("summary.json is missing order_lifecycle_counts")
        return
    if set(expected) != set(ORDER_LIFECYCLE_KEYS):
        issues.append(f"summary.json has unexpected order_lifecycle_counts keys: {sorted(expected)}")
        return
    for key in ORDER_LIFECYCLE_KEYS:
        if expected.get(key) != lifecycle_counts[key]:
            issues.append(
                f"order_lifecycle_counts.{key}={expected.get(key)!r} does not match trace value {lifecycle_counts[key]}"
            )
    if expected.get("arrived") != summary.get("quote_count"):
        issues.append("order_lifecycle_counts.arrived does not match quote_count")
    if expected.get("cancel_requested") != summary.get("cancel_count"):
        issues.append("order_lifecycle_counts.cancel_requested does not match cancel_count")
    if expected.get("self_trade_prevented") != summary.get("self_trade_prevention_count"):
        issues.append("order_lifecycle_counts.self_trade_prevented does not match self_trade_prevention_count")

    for field in (
        "resting_arrival_queue_samples",
        "arrival_with_queue_ahead_count",
        "max_arrival_queue_ahead_lots",
    ):
        if summary.get(field) != arrival_queue[field]:
            issues.append(f"summary.{field}={summary.get(field)!r} does not match trace value {arrival_queue[field]}")
    if not _is_close(summary.get("avg_arrival_queue_ahead_lots"), float(arrival_queue["avg_arrival_queue_ahead_lots"])):
        issues.append(
            "summary.avg_arrival_queue_ahead_lots="
            f"{summary.get('avg_arrival_queue_ahead_lots')!r} does not match trace value "
            f"{arrival_queue['avg_arrival_queue_ahead_lots']}"
        )


def _audit_public_consumption(
    summary: dict[str, Any],
    consumption: dict[str, dict[str, int]],
    issues: list[str],
) -> None:
    public_summary = summary.get("public_consumption_summary")
    if not isinstance(public_summary, dict):
        issues.append("summary.json is missing public_consumption_summary")
        return
    source_summary = public_summary.get("sources")
    if not isinstance(source_summary, dict):
        issues.append("summary.public_consumption_summary is missing sources")
        return

    totals = {field: 0 for field in PUBLIC_CONSUMPTION_FIELDS}
    for source in PUBLIC_CONSUMPTION_SOURCES:
        observed = source_summary.get(source)
        if not isinstance(observed, dict):
            issues.append(f"summary.public_consumption_summary is missing source {source}")
            continue
        for field in PUBLIC_CONSUMPTION_FIELDS:
            totals[field] += consumption[source][field]
            if observed.get(field) != consumption[source][field]:
                issues.append(
                    f"public_consumption_summary.{source}.{field}={observed.get(field)!r} "
                    f"does not match trace value {consumption[source][field]}"
                )
    total_mapping = {
        "observed_lots": "total_observed_lots",
        "modeled_lots": "total_modeled_lots",
        "overlap_netted_lots": "total_overlap_netted_lots",
        "queue_consumed_lots": "total_queue_consumed_lots",
        "unmatched_lots": "total_unmatched_lots",
    }
    for field, total_field in total_mapping.items():
        if public_summary.get(total_field) != totals[field]:
            issues.append(
                f"public_consumption_summary.{total_field}={public_summary.get(total_field)!r} does not match trace value {totals[field]}"
            )


def _audit_markouts(
    summary: dict[str, Any],
    markouts: dict[str, dict[str, Decimal | int]],
    markout_row_count: int,
    issues: list[str],
) -> None:
    markout_events = summary.get("markout_events")
    if isinstance(markout_events, list) and len(markout_events) != markout_row_count:
        issues.append(f"summary.markout_events has {len(markout_events)} row(s), trace has {markout_row_count}")
    by_source = summary.get("markout_by_fill_source")
    if not isinstance(by_source, dict):
        issues.append("summary.json is missing markout_by_fill_source")
        return
    for source in FILL_SOURCES:
        observed = by_source.get(source)
        if not isinstance(observed, dict):
            issues.append(f"summary.markout_by_fill_source is missing source {source}")
            continue
        expected = markouts[source]
        samples = int(expected["samples"])
        adverse = int(expected["adverse_samples"])
        qty = expected["qty"]
        markout_sum = expected["markout_sum"]
        avg = float(markout_sum / qty) if isinstance(qty, Decimal) and qty > 0 else 0.0
        rate = float(Decimal(adverse) / Decimal(samples)) if samples else 0.0
        expected_values = {
            "samples": samples,
            "adverse_samples": adverse,
            "qty": float(qty),
            "avg_markout_1s": avg,
            "adverse_fill_rate_1s": rate,
        }
        for field, value in expected_values.items():
            actual = observed.get(field)
            if isinstance(value, float):
                if not _is_close(actual, value):
                    issues.append(
                        f"markout_by_fill_source.{source}.{field}={actual!r} does not match trace value {value}"
                    )
            elif actual != value:
                issues.append(f"markout_by_fill_source.{source}.{field}={actual!r} does not match trace value {value}")


def _audit_simulation_assumptions(path: Path, assumptions: object, issues: list[str]) -> None:
    if not isinstance(assumptions, dict):
        issues.append(f"{_display_path(path)} is missing simulation_assumptions")
        return
    if set(assumptions) != EXPECTED_SIMULATION_ASSUMPTION_FIELDS:
        issues.append(f"{_display_path(path)} simulation_assumptions has unexpected fields")
        return
    if assumptions.get("schema_version") != SIMULATION_ASSUMPTIONS_SCHEMA_VERSION:
        issues.append(f"{_display_path(path)} simulation_assumptions has unexpected schema_version")
    profile = assumptions.get("fill_assumption_profile")
    fill_assumption = assumptions.get("fill_assumption")
    if profile not in FILL_ASSUMPTION_PROFILES:
        issues.append(f"{_display_path(path)} simulation_assumptions has invalid fill_assumption_profile")
    if not isinstance(fill_assumption, dict):
        issues.append(f"{_display_path(path)} simulation_assumptions is missing fill_assumption")
    elif fill_assumption.get("profile") != profile:
        issues.append(f"{_display_path(path)} simulation_assumptions fill_assumption profile mismatch")
    if assumptions.get("data_scope") != "public_l2_order_book_and_agg_trade_records":
        issues.append(f"{_display_path(path)} simulation_assumptions has unexpected data_scope")
    if assumptions.get("private_exchange_execution_reports") is not False:
        issues.append(f"{_display_path(path)} simulation_assumptions must not claim private exchange execution reports")
    if assumptions.get("queue_priority_model") != "synthetic_queue_ahead_by_price_level":
        issues.append(f"{_display_path(path)} simulation_assumptions has unexpected queue priority model")

    overlap = assumptions.get("overlap_netting")
    if not isinstance(overlap, dict):
        issues.append(f"{_display_path(path)} simulation_assumptions.overlap_netting must be an object")
    else:
        expected_enabled = True
        expected_window = TRADE_DEPTH_OVERLAP_WINDOW_SECONDS
        if isinstance(fill_assumption, dict):
            raw_window = fill_assumption.get("overlap_window_seconds")
            if isinstance(raw_window, (int, float)):
                expected_window = float(raw_window)
            expected_enabled = bool(fill_assumption.get("overlap_netting_enabled") is True and expected_window > 0)
        if overlap.get("enabled") is not expected_enabled:
            issues.append(f"{_display_path(path)} simulation_assumptions overlap netting flag is inconsistent")
        if overlap.get("window_seconds") != expected_window:
            issues.append(f"{_display_path(path)} simulation_assumptions has unexpected overlap window")

    limitations = assumptions.get("limitations")
    if not isinstance(limitations, list):
        issues.append(f"{_display_path(path)} simulation_assumptions.limitations must be a list")
    elif not EXPECTED_SIMULATION_LIMITATIONS <= set(limitations):
        issues.append(f"{_display_path(path)} simulation_assumptions is missing required limitation token(s)")


def _audit_summary_csv(pack_dir: Path, summary: dict[str, Any], issues: list[str]) -> None:
    summary_csv_path = pack_dir / "summary.csv"
    rows = _read_csv_rows(summary_csv_path, (), issues)
    if not rows:
        return
    if len(rows) != 1:
        issues.append(f"{_display_path(summary_csv_path)} must contain exactly one summary row")
        return
    row = rows[0]
    if row.get("fill_rate") not in {None, ""} and not _has_explicit_deprecated_fill_rate(summary):
        issues.append(
            f"{_display_path(summary_csv_path)} uses ambiguous fill_rate; use explicit fill-frequency metrics"
        )

    for field in SUMMARY_CSV_EXACT_FIELDS:
        actual = row.get(field)
        expected = summary.get(field)
        if actual != str(expected):
            issues.append(
                f"{_display_path(summary_csv_path)} {field}={actual!r} does not match summary value {expected!r}"
            )

    for field in SUMMARY_CSV_INT_FIELDS:
        raw_value = row.get(field)
        try:
            actual_int = int(str(raw_value))
        except (TypeError, ValueError):
            issues.append(f"{_display_path(summary_csv_path)} {field}={raw_value!r} is not an integer")
            continue
        expected = summary.get(field)
        if actual_int != expected:
            issues.append(
                f"{_display_path(summary_csv_path)} {field}={raw_value!r} does not match summary value {expected!r}"
            )

    for field in SUMMARY_CSV_JSON_FIELDS:
        raw_value = row.get(field)
        if raw_value in {None, ""}:
            issues.append(f"{_display_path(summary_csv_path)} is missing {field}")
            continue
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            issues.append(f"{_display_path(summary_csv_path)} {field} is invalid JSON: {exc.msg}")
            continue
        expected = summary.get(field)
        if decoded != expected:
            issues.append(f"{_display_path(summary_csv_path)} {field} does not match summary.json")


def _audit_fill_frequency_metrics(
    pack_dir: Path,
    summary: dict[str, Any],
    trade_rows: list[dict[str, str]],
    issues: list[str],
) -> None:
    summary_path = pack_dir / "summary.json"
    if "fill_rate" in summary and not _has_explicit_deprecated_fill_rate(summary):
        issues.append(
            f"{_display_path(summary_path)} uses ambiguous fill_rate; use explicit fill-frequency metrics "
            "or mark deprecated_fields.fill_rate with replacement metrics"
        )

    lifecycle = summary.get("order_lifecycle_counts")
    if not isinstance(lifecycle, dict):
        return
    try:
        fill_count = int(summary.get("fill_count"))
        quote_count = int(summary.get("quote_count"))
        arrived = int(lifecycle.get("arrived"))
    except (TypeError, ValueError):
        return

    expected = {
        "fills_per_quote_request": float(Decimal(fill_count) / Decimal(quote_count)) if quote_count else 0.0,
        "fills_per_arrived_order": float(Decimal(fill_count) / Decimal(arrived)) if arrived else 0.0,
        "quote_fill_probability": (
            float(Decimal(len({row.get("order_id") for row in trade_rows if row.get("order_id")})) / Decimal(arrived))
            if arrived
            else 0.0
        ),
    }
    for field, expected_value in expected.items():
        actual = summary.get(field)
        if not isinstance(actual, (int, float)):
            issues.append(f"{_display_path(summary_path)} is missing numeric {field}")
            continue
        if field == "quote_fill_probability" and not 0.0 <= float(actual) <= 1.0:
            issues.append(f"{_display_path(summary_path)} quote_fill_probability must be bounded between 0 and 1")
        if not math.isclose(float(actual), expected_value, rel_tol=1e-12, abs_tol=1e-12):
            issues.append(
                f"{_display_path(summary_path)} {field}={actual!r} does not match expected {expected_value!r}"
            )


def _audit_fill_exports(
    summary: dict[str, Any],
    trade_rows: list[dict[str, str]],
    fill_records: list[tuple[int, dict[str, str], dict[str, Any]]],
    trades_path: Path,
    trace_path: Path,
    input_evidence_ids: set[str] | None,
    issues: list[str],
) -> None:
    summary_fills = summary.get("fills")
    if not isinstance(summary_fills, list):
        issues.append("summary.json is missing fills")
        return
    if len(summary_fills) != len(trade_rows):
        issues.append(f"summary.fills has {len(summary_fills)} row(s), trades.csv has {len(trade_rows)}")
    if len(summary_fills) != len(fill_records):
        issues.append(f"summary.fills has {len(summary_fills)} row(s), event_trace fill rows has {len(fill_records)}")

    provenance = summary.get("fill_provenance")
    if not isinstance(provenance, dict):
        issues.append("summary.json is missing fill_provenance")
    else:
        expected_provenance_fields = {
            "schema_version",
            "fill_count",
            "with_provenance_schema",
            "with_scenario",
            "with_evidence_ids",
            "with_validity",
            "execution_valid",
            "with_queue_trajectory",
            "with_latency_draws",
            "with_latency_model",
            "with_lifecycle_state",
            "with_fee_model",
            "complete",
        }
        if set(provenance) != expected_provenance_fields:
            issues.append("summary.fill_provenance has unexpected fields")
        if provenance.get("schema_version") != "lob_sim.fill_provenance_coverage.v1":
            issues.append("summary.fill_provenance has unexpected schema_version")
        if provenance.get("fill_count") != len(summary_fills):
            issues.append("summary.fill_provenance.fill_count does not match summary.fills")
        for field in (
            "with_provenance_schema",
            "with_scenario",
            "with_evidence_ids",
            "with_validity",
            "with_queue_trajectory",
            "with_latency_draws",
            "with_latency_model",
            "with_lifecycle_state",
            "with_fee_model",
        ):
            if provenance.get(field) != len(summary_fills):
                issues.append(f"summary.fill_provenance.{field} does not cover every fill")
        if provenance.get("complete") is not True:
            issues.append("summary.fill_provenance.complete must be true")

    execution_valid_count = 0
    for index, summary_fill in enumerate(summary_fills):
        if not isinstance(summary_fill, dict):
            issues.append(f"summary.fills[{index}] must be a JSON object")
            continue
        if summary_fill.get("provenance_schema_version") != "lob_sim.fill_provenance.v1":
            issues.append(f"summary.fills[{index}] has unexpected provenance_schema_version")
        expected_scenario_id = _expected_fill_scenario_id(summary, summary_fill.get("fill_source"))
        if expected_scenario_id is None or summary_fill.get("scenario_id") != expected_scenario_id:
            issues.append(
                f"summary.fills[{index}] scenario_id={summary_fill.get('scenario_id')!r} "
                f"does not match run assumptions {expected_scenario_id!r}"
            )
        evidence_ids = summary_fill.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not isinstance(value, str) or not value for value in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
        ):
            issues.append(f"summary.fills[{index}] has invalid evidence_ids")
        elif input_evidence_ids is not None:
            unresolved = sorted(set(evidence_ids) - input_evidence_ids)
            if unresolved:
                issues.append(f"summary.fills[{index}] has unresolved evidence_ids: {unresolved}")
        validity = summary_fill.get("validity")
        if not isinstance(validity, dict) or set(validity) != FILL_VALIDITY_FIELDS:
            issues.append(f"summary.fills[{index}] has invalid validity")
        else:
            boolean_fields = FILL_VALIDITY_FIELDS - {"reason"}
            if any(not isinstance(validity.get(field), bool) for field in boolean_fields):
                issues.append(f"summary.fills[{index}] validity fields must be boolean")
            else:
                expected_execution_valid = (
                    validity["book_valid"]
                    and validity["clock_valid"]
                    and validity["capture_valid"]
                    and (validity["trade_stream_valid"] or not validity["trade_stream_required"])
                )
                if validity["execution_valid"] is not expected_execution_valid:
                    issues.append(f"summary.fills[{index}] validity has inconsistent execution_valid")
            reason = validity.get("reason")
            if reason is not None and (not isinstance(reason, str) or not reason):
                issues.append(f"summary.fills[{index}] validity has invalid reason")
            if summary_fill.get("fill_source") == "agg_trade" and validity.get("trade_stream_required") is not True:
                issues.append(f"summary.fills[{index}] agg_trade validity must require the trade stream")
            if validity.get("execution_valid") is True:
                execution_valid_count += 1
        queue_trajectory = summary_fill.get("queue_trajectory")
        expected_queue_fields = (
            TAKER_QUEUE_TRAJECTORY_FIELDS
            if summary_fill.get("fill_source") == "taker_order"
            else PASSIVE_QUEUE_TRAJECTORY_FIELDS
        )
        if not isinstance(queue_trajectory, dict) or set(queue_trajectory) != expected_queue_fields:
            issues.append(f"summary.fills[{index}] has invalid queue_trajectory fields")
        elif any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in queue_trajectory.values()
        ):
            issues.append(f"summary.fills[{index}] has invalid queue_trajectory values")
        elif summary_fill.get("fill_source") == "taker_order":
            if (
                queue_trajectory["visible_level_after_lots"]
                != queue_trajectory["visible_level_before_lots"] - queue_trajectory["fill_lots"]
            ):
                issues.append(f"summary.fills[{index}] has inconsistent taker queue_trajectory")
        else:
            before = queue_trajectory["queue_ahead_before_trigger_lots"]
            at_fill = queue_trajectory["queue_ahead_at_fill_lots"]
            consumed = queue_trajectory["queue_consumed_before_fill_lots"]
            if at_fill > before or consumed != before - at_fill:
                issues.append(f"summary.fills[{index}] has inconsistent passive queue_trajectory")
        latency_draws = summary_fill.get("latency_draws_ms")
        if not isinstance(latency_draws, dict) or set(latency_draws) != {"new_order", "cancel"}:
            issues.append(f"summary.fills[{index}] has invalid latency_draws_ms")
        elif any(
            value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0)
            for value in latency_draws.values()
        ):
            issues.append(f"summary.fills[{index}] has non-finite latency draw")
        latency_model = summary_fill.get("latency_model")
        if not isinstance(latency_model, dict) or set(latency_model) != LATENCY_MODEL_FIELDS:
            issues.append(f"summary.fills[{index}] has invalid latency_model")
        else:
            if not isinstance(latency_model.get("mode"), str) or not latency_model.get("mode"):
                issues.append(f"summary.fills[{index}] latency_model has invalid mode")
            if not isinstance(latency_model.get("seed"), int) or isinstance(latency_model.get("seed"), bool):
                issues.append(f"summary.fills[{index}] latency_model has invalid seed")
            if latency_model.get("source") != "configured_scenario" or latency_model.get("measured") is not False:
                issues.append(f"summary.fills[{index}] latency_model must not claim measurement")
        if summary_fill.get("order_state_at_fill") not in {"live", "pending_cancel"}:
            issues.append(f"summary.fills[{index}] has invalid order_state_at_fill")
        if summary_fill.get("fee_model_id") != "static_config_bps":
            issues.append(f"summary.fills[{index}] has invalid fee_model_id")
        trade_row = trade_rows[index] if index < len(trade_rows) else None
        fill_record = fill_records[index] if index < len(fill_records) else None

        if trade_row is not None:
            trade_row_number = index + 2
            for field in TRADE_CSV_FIELDS:
                actual = trade_row.get(field)
                expected = summary_fill.get(field)
                if field in TRADE_JSON_FIELDS:
                    try:
                        decoded = json.loads(actual or "")
                    except json.JSONDecodeError:
                        decoded = object()
                    matches = decoded == expected
                else:
                    matches = _scalar_matches(actual, expected)
                if not matches:
                    issues.append(
                        f"{_display_path(trades_path)}:{trade_row_number} {field}={actual!r} "
                        f"does not match summary.fills[{index}].{field}={expected!r}"
                    )

        if fill_record is None:
            continue
        trace_row_number, trace_row, details = fill_record
        if isinstance(queue_trajectory, dict) and isinstance(queue_trajectory.get("fill_lots"), int):
            try:
                trace_fill_lots = int(trace_row.get("qty_lots", ""))
            except (TypeError, ValueError):
                trace_fill_lots = None
            if trace_fill_lots is not None and queue_trajectory["fill_lots"] != trace_fill_lots:
                issues.append(
                    f"{_display_path(trace_path)}:{trace_row_number} queue_trajectory fill_lots does not match qty_lots"
                )
        for field in FILL_TRACE_ROW_FIELDS:
            actual = trace_row.get(field)
            expected = summary_fill.get(field)
            if not _scalar_matches(actual, expected):
                issues.append(
                    f"{_display_path(trace_path)}:{trace_row_number} {field}={actual!r} "
                    f"does not match summary.fills[{index}].{field}={expected!r}"
                )
        for field in FILL_TRACE_DETAIL_FIELDS:
            actual = details.get(field)
            expected = summary_fill.get(field)
            matches = actual == expected if field in TRADE_JSON_FIELDS else _scalar_matches(actual, expected)
            if not matches:
                issues.append(
                    f"{_display_path(trace_path)}:{trace_row_number} details.{field}={actual!r} "
                    f"does not match summary.fills[{index}].{field}={expected!r}"
                )
    if isinstance(provenance, dict) and provenance.get("execution_valid") != execution_valid_count:
        issues.append("summary.fill_provenance.execution_valid does not match summary.fills")


def _audit_markout_exports(
    summary: dict[str, Any],
    markout_records: list[tuple[int, dict[str, str], dict[str, Any]]],
    trace_path: Path,
    issues: list[str],
) -> None:
    summary_markouts = summary.get("markout_events")
    if not isinstance(summary_markouts, list):
        issues.append("summary.json is missing markout_events")
        return
    if len(summary_markouts) != len(markout_records):
        issues.append(
            f"summary.markout_events has {len(summary_markouts)} row(s), "
            f"event_trace markout rows has {len(markout_records)}"
        )

    for index, summary_markout in enumerate(summary_markouts):
        if not isinstance(summary_markout, dict):
            issues.append(f"summary.markout_events[{index}] must be a JSON object")
            continue
        if index >= len(markout_records):
            continue
        trace_row_number, trace_row, details = markout_records[index]
        if not _scalar_matches(trace_row.get("ts_local"), summary_markout.get("markout_ts_local")):
            issues.append(
                f"{_display_path(trace_path)}:{trace_row_number} ts_local={trace_row.get('ts_local')!r} "
                f"does not match summary.markout_events[{index}].markout_ts_local="
                f"{summary_markout.get('markout_ts_local')!r}"
            )
        for field in MARKOUT_TRACE_ROW_FIELDS:
            actual = trace_row.get(field)
            expected = summary_markout.get(field)
            if not _scalar_matches(actual, expected):
                issues.append(
                    f"{_display_path(trace_path)}:{trace_row_number} {field}={actual!r} "
                    f"does not match summary.markout_events[{index}].{field}={expected!r}"
                )
        for detail_field, summary_field in MARKOUT_TRACE_DETAIL_FIELDS:
            actual = details.get(detail_field)
            expected = summary_markout.get(summary_field)
            if not _scalar_matches(actual, expected):
                issues.append(
                    f"{_display_path(trace_path)}:{trace_row_number} details.{detail_field}={actual!r} "
                    f"does not match summary.markout_events[{index}].{summary_field}={expected!r}"
                )


def _audit_replay_event_counts(
    pack_dir: Path,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    market_record_counts: dict[str, int],
    book_gap_counts_by_symbol: dict[str, int],
    issues: list[str],
) -> set[str] | None:
    event_counts = summary.get("event_counts")
    summary_path = pack_dir / "summary.json"
    if not isinstance(event_counts, dict):
        issues.append("summary.json is missing event_counts")
        return None
    if set(event_counts) != set(EVENT_COUNT_FIELDS):
        issues.append(f"{_display_path(summary_path)} has unexpected event_counts keys: {sorted(event_counts)}")
        return None
    for field in EVENT_COUNT_FIELDS:
        value = event_counts.get(field)
        if not isinstance(value, int) or value < 0:
            issues.append(f"{_display_path(summary_path)} event_counts.{field} must be a non-negative integer")
            return None

    market_record_total = sum(market_record_counts.values())
    if event_counts["records_processed"] != market_record_total:
        issues.append(
            f"event_counts.records_processed={event_counts['records_processed']!r} "
            f"does not match trace market_record count {market_record_total}"
        )
    for source, field in MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.items():
        observed = market_record_counts.get(source, 0)
        if event_counts[field] != observed:
            issues.append(
                f"event_counts.{field}={event_counts[field]!r} does not match trace source {source} count {observed}"
            )

    if event_counts["book_gap_count"] != sum(book_gap_counts_by_symbol.values()):
        issues.append(
            f"event_counts.book_gap_count={event_counts['book_gap_count']!r} "
            f"does not match trace book_gap count {sum(book_gap_counts_by_symbol.values())}"
        )
    if summary.get("book_gap_count_by_symbol") != book_gap_counts_by_symbol:
        issues.append(
            f"summary.book_gap_count_by_symbol={summary.get('book_gap_count_by_symbol')!r} "
            f"does not match trace value {book_gap_counts_by_symbol}"
        )

    manifest_input = manifest.get("input")
    if not isinstance(manifest_input, dict) or not isinstance(manifest_input.get("path"), str):
        issues.append(f"{_display_path(pack_dir / 'manifest.json')} is missing input.path")
        return None
    input_path = _resolve_artifact_path(manifest_input["path"], pack_dir)
    input_counts = {field: 0 for field in ("exchange_info", "snapshot", "depth_update", "agg_trade")}
    records_processed = 0
    input_evidence_ids: set[str] = set()
    try:
        for record in iter_records(input_path):
            records_processed += 1
            input_evidence_ids.add(_record_evidence_id(record, records_processed))
            if record.type == "exchangeInfo":
                input_counts["exchange_info"] += 1
            elif record.type == "snapshot":
                input_counts["snapshot"] += 1
            elif record.type == "depthUpdate":
                input_counts["depth_update"] += 1
            elif record.type == "aggTrade":
                input_counts["agg_trade"] += 1
    except FileNotFoundError:
        issues.append(f"Missing replay input file: {_display_path(input_path)}")
        return None
    except (OSError, ValueError, RecordValidationError) as exc:
        issues.append(f"{_display_path(input_path)} could not be read for event-count audit: {exc}")
        return None

    if event_counts["records_processed"] != records_processed:
        issues.append(
            f"event_counts.records_processed={event_counts['records_processed']!r} "
            f"does not match replay input record count {records_processed}"
        )
    for field, observed in input_counts.items():
        if event_counts[field] != observed:
            issues.append(f"event_counts.{field}={event_counts[field]!r} does not match replay input count {observed}")
    return input_evidence_ids


def audit_futures_pack(pack_dir: Path) -> dict[str, Any]:
    pack_dir = pack_dir.resolve()
    issues: list[str] = []
    summary_path = pack_dir / "summary.json"
    trades_path = pack_dir / "trades.csv"
    trace_path = pack_dir / "event_trace.csv"
    manifest_path = pack_dir / "manifest.json"
    summary = _load_json_object(summary_path, issues)
    manifest = _load_json_object(manifest_path, issues)
    trace_rows = _read_csv_rows(trace_path, EVENT_TRACE_FIELDS, issues)
    trade_rows = _read_csv_rows(trades_path, TRADE_CSV_FIELDS, issues)

    if summary:
        _audit_manifest(pack_dir, summary, manifest, issues)
        _audit_fixture_provenance(pack_dir, summary, manifest, issues)
        _audit_fill_assumption_metadata(pack_dir, summary, manifest, issues)
        _audit_simulation_assumptions(summary_path, summary.get("simulation_assumptions"), issues)
        _audit_summary_csv(pack_dir, summary, issues)
        _audit_fill_frequency_metrics(pack_dir, summary, trade_rows, issues)
        _audit_retention_contract(summary, issues)

    expected_event_trace_count = summary.get("event_trace_count")
    if isinstance(expected_event_trace_count, int) and len(trace_rows) != expected_event_trace_count:
        issues.append(f"event_trace.csv has {len(trace_rows)} row(s), summary expected {expected_event_trace_count}")
    expected_fill_count = summary.get("fill_count")
    if isinstance(expected_fill_count, int) and len(trade_rows) != expected_fill_count:
        issues.append(f"trades.csv has {len(trade_rows)} row(s), summary expected {expected_fill_count}")

    previous_ts: float | None = None
    fill_rows: list[dict[str, str]] = []
    fill_source_counts = {source: 0 for source in FILL_SOURCES}
    event_type_counts: Counter[str] = Counter()
    market_record_counts = {source: 0 for source in MARKET_RECORD_SOURCES}
    book_gap_counts_by_symbol: dict[str, int] = {}
    lifecycle_counts = {key: 0 for key in ORDER_LIFECYCLE_KEYS}
    consumption = {source: {field: 0 for field in PUBLIC_CONSUMPTION_FIELDS} for source in PUBLIC_CONSUMPTION_SOURCES}
    markouts = {
        source: {
            "samples": 0,
            "adverse_samples": 0,
            "qty": Decimal("0"),
            "markout_sum": Decimal("0"),
        }
        for source in FILL_SOURCES
    }
    markout_row_count = 0
    arrival_queue_samples = 0
    arrival_with_queue = 0
    arrival_queue_sum = 0
    max_arrival_queue = 0
    fill_records: list[tuple[int, dict[str, str], dict[str, Any]]] = []
    markout_records: list[tuple[int, dict[str, str], dict[str, Any]]] = []

    for row_number, row in enumerate(trace_rows, start=2):
        event_type = row.get("event_type", "")
        event_type_counts[event_type] += 1
        try:
            seq = int(row.get("seq", ""))
        except (TypeError, ValueError):
            issues.append(f"{_display_path(trace_path)}:{row_number} has invalid seq")
            continue
        expected_seq = row_number - 2
        if seq != expected_seq:
            issues.append(f"{_display_path(trace_path)}:{row_number} seq={seq} expected {expected_seq}")
        try:
            ts_local = float(row.get("ts_local", ""))
        except (TypeError, ValueError):
            issues.append(f"{_display_path(trace_path)}:{row_number} has invalid ts_local")
            continue
        if previous_ts is not None and ts_local < previous_ts:
            issues.append(f"{_display_path(trace_path)}:{row_number} is out of event-time order")
        previous_ts = ts_local

        details = _parse_details(trace_path, row_number, row.get("details", ""), issues)
        if event_type == "market_record":
            source = row.get("source", "")
            if source not in MARKET_RECORD_SOURCES:
                issues.append(f"{_display_path(trace_path)}:{row_number} has invalid market_record source {source!r}")
            else:
                market_record_counts[source] += 1
            if details.get("record_type") != source:
                issues.append(
                    f"{_display_path(trace_path)}:{row_number} market_record details.record_type does not match source"
                )
        elif event_type == "book_gap":
            symbol = row.get("symbol", "")
            if not symbol:
                issues.append(f"{_display_path(trace_path)}:{row_number} book_gap row is missing symbol")
            else:
                book_gap_counts_by_symbol[symbol] = book_gap_counts_by_symbol.get(symbol, 0) + 1
        elif event_type == "order_arrival_scheduled":
            lifecycle_counts["arrival_scheduled"] += 1
        elif event_type == "order_arrival":
            lifecycle_counts["arrived"] += 1
            if details.get("resting_after_arrival") is True:
                lifecycle_counts["rested_after_arrival"] += 1
                queue_ahead = details.get("queue_ahead_lots_after_arrival")
                if isinstance(queue_ahead, int) and queue_ahead >= 0:
                    arrival_queue_samples += 1
                    arrival_queue_sum += queue_ahead
                    max_arrival_queue = max(max_arrival_queue, queue_ahead)
                    if queue_ahead > 0:
                        arrival_with_queue += 1
                else:
                    issues.append(
                        f"{_display_path(trace_path)}:{row_number} has invalid queue_ahead_lots_after_arrival"
                    )
            if isinstance(details.get("immediate_fills"), int) and details["immediate_fills"] > 0:
                lifecycle_counts["immediate_fill_arrivals"] += 1
            if (
                details.get("resting_after_arrival") is False
                and isinstance(details.get("remaining_lots_after_arrival"), int)
                and details["remaining_lots_after_arrival"] > 0
            ):
                lifecycle_counts["expired_unfilled_arrivals"] += 1
            if details.get("self_trade_prevented") is True:
                lifecycle_counts["self_trade_prevented"] += 1
        elif event_type == "cancel_requested":
            lifecycle_counts["cancel_requested"] += 1
        elif event_type == "cancel_ack":
            lifecycle_counts["cancel_acknowledged"] += 1
        elif event_type == "fill":
            fill_source = row.get("fill_source", "")
            if fill_source not in FILL_SOURCES:
                issues.append(f"{_display_path(trace_path)}:{row_number} has invalid fill_source {fill_source!r}")
            else:
                fill_source_counts[fill_source] += 1
            for field in ("side", "price_tick", "qty_lots", "order_id"):
                if not row.get(field):
                    issues.append(f"{_display_path(trace_path)}:{row_number} fill row is missing {field}")
            fill_rows.append(row)
            fill_records.append((row_number, row, details))
        elif event_type == "queue_consumption":
            source = row.get("source", "")
            if source not in PUBLIC_CONSUMPTION_SOURCES:
                issues.append(
                    f"{_display_path(trace_path)}:{row_number} has invalid queue_consumption source {source!r}"
                )
                continue
            profile = details.get("fill_assumption_profile")
            if profile not in FILL_ASSUMPTION_PROFILES:
                issues.append(
                    f"{_display_path(trace_path)}:{row_number} queue_consumption is missing fill_assumption_profile"
                )
            parsed: dict[str, int] = {}
            for field in PUBLIC_CONSUMPTION_FIELDS:
                value = details.get(field)
                if not isinstance(value, int) or value < 0:
                    issues.append(f"{_display_path(trace_path)}:{row_number} has invalid queue_consumption {field}")
                else:
                    parsed[field] = value
                    consumption[source][field] += value
            if len(parsed) == len(PUBLIC_CONSUMPTION_FIELDS):
                if parsed["observed_lots"] < parsed["modeled_lots"]:
                    issues.append(
                        f"{_display_path(trace_path)}:{row_number} queue_consumption models more lots than observed"
                    )
                if parsed["overlap_netted_lots"] != parsed["observed_lots"] - parsed["modeled_lots"]:
                    issues.append(
                        f"{_display_path(trace_path)}:{row_number} queue_consumption netted lots are inconsistent"
                    )
                if parsed["queue_consumed_lots"] > parsed["modeled_lots"]:
                    issues.append(
                        f"{_display_path(trace_path)}:{row_number} queue_consumption consumes more queue than modeled"
                    )
                if parsed["unmatched_lots"] != parsed["modeled_lots"] - parsed["queue_consumed_lots"]:
                    issues.append(
                        f"{_display_path(trace_path)}:{row_number} queue_consumption unmatched lots are inconsistent"
                    )
        elif event_type == "markout":
            source = row.get("fill_source", "")
            qty = _decimal(details.get("qty"))
            markout = _decimal(details.get("markout"))
            if source not in FILL_SOURCES or qty is None or markout is None:
                issues.append(
                    f"{_display_path(trace_path)}:{row_number} markout row has invalid source, qty, or markout"
                )
                continue
            markouts[source]["samples"] = int(markouts[source]["samples"]) + 1
            markouts[source]["qty"] = markouts[source]["qty"] + qty
            markouts[source]["markout_sum"] = markouts[source]["markout_sum"] + markout * qty
            if details.get("adverse") is True:
                markouts[source]["adverse_samples"] = int(markouts[source]["adverse_samples"]) + 1
            markout_row_count += 1
            markout_records.append((row_number, row, details))

    if isinstance(expected_fill_count, int) and len(fill_rows) != expected_fill_count:
        issues.append(f"event_trace.csv has {len(fill_rows)} fill row(s), summary expected {expected_fill_count}")
    if summary.get("fill_source_counts") != fill_source_counts:
        issues.append(
            f"summary.fill_source_counts={summary.get('fill_source_counts')!r} does not match trace value {fill_source_counts}"
        )

    trade_order_ids = {row.get("order_id") for row in trade_rows if row.get("order_id")}
    trace_order_ids = {row.get("order_id") for row in fill_rows if row.get("order_id")}
    if trade_order_ids != trace_order_ids:
        issues.append("trades.csv order_ids do not match event_trace fill order_ids")

    arrival_queue = {
        "resting_arrival_queue_samples": arrival_queue_samples,
        "arrival_with_queue_ahead_count": arrival_with_queue,
        "avg_arrival_queue_ahead_lots": arrival_queue_sum / arrival_queue_samples if arrival_queue_samples else 0.0,
        "max_arrival_queue_ahead_lots": max_arrival_queue,
    }
    if summary:
        _audit_lifecycle(summary, lifecycle_counts, arrival_queue, issues)
        _audit_public_consumption(summary, consumption, issues)
        _audit_markouts(summary, markouts, markout_row_count, issues)
        input_evidence_ids = _audit_replay_event_counts(
            pack_dir,
            summary,
            manifest,
            market_record_counts,
            book_gap_counts_by_symbol,
            issues,
        )
        _audit_fill_exports(
            summary,
            trade_rows,
            fill_records,
            trades_path,
            trace_path,
            input_evidence_ids,
            issues,
        )
        _audit_markout_exports(summary, markout_records, trace_path, issues)

    return {
        "schema_version": PACK_AUDIT_SCHEMA_VERSION,
        "pack_dir": _display_path(pack_dir),
        "ok": not issues,
        "issues": issues,
        "counts": {
            "event_trace_rows": len(trace_rows),
            "trade_rows": len(trade_rows),
            "fill_rows": len(fill_rows),
            "queue_consumption_rows": event_type_counts.get("queue_consumption", 0),
            "markout_rows": markout_row_count,
            "event_type_counts": dict(sorted(event_type_counts.items())),
        },
        "summary": {
            "run_id": summary.get("run_id"),
            "input_sha256": summary.get("input_sha256"),
            "fill_assumption_profile": summary.get("fill_assumption_profile"),
            "fill_count": summary.get("fill_count"),
            "event_trace_count": summary.get("event_trace_count"),
            "feed_adapter": summary.get("feed_adapter"),
        },
    }


def audit_futures_packs(pack_dirs: list[Path]) -> dict[str, Any]:
    if not pack_dirs:
        return {
            "schema_version": PACK_AUDIT_SCHEMA_VERSION,
            "ok": False,
            "pack_count": 0,
            "packs": [],
            "issues": ["No futures packs supplied"],
        }
    pack_results = [audit_futures_pack(pack_dir) for pack_dir in pack_dirs]
    return {
        "schema_version": PACK_AUDIT_SCHEMA_VERSION,
        "ok": all(result["ok"] for result in pack_results),
        "pack_count": len(pack_results),
        "packs": pack_results,
        "issues": [f"{result['pack_dir']}: {issue}" for result in pack_results for issue in result["issues"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a generated futures simulation pack")
    parser.add_argument(
        "--pack",
        action="append",
        default=[],
        help=(
            "Directory containing summary.json, trades.csv, event_trace.csv, "
            "and manifest.json. May be passed more than once."
        ),
    )
    parser.add_argument(
        "--committed-futures",
        action="store_true",
        help="Audit the committed futures walkthrough and recorded-clip packs.",
    )
    parser.add_argument("--json-out", help="Optional path for the machine-readable audit report")
    args = parser.parse_args()

    pack_dirs = [Path(pack) for pack in args.pack]
    if args.committed_futures:
        pack_dirs = [_repo_root() / pack for pack in COMMITTED_FUTURES_PACKS] + pack_dirs
    if not pack_dirs:
        parser.error("at least one --pack or --committed-futures is required")

    if len(pack_dirs) == 1 and not args.committed_futures:
        result = audit_futures_pack(pack_dirs[0])
    else:
        result = audit_futures_packs(pack_dirs)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
