"""Independent bounded-memory audit for streamed simulation bundles.

The simulator verifies serialized audit chains before publishing a manifest.
This module deliberately performs a second implementation of that check from
the files on disk.  It never imports the exporter's row decoders and never
retains trace, fill, markout, order-id, or evidence-id collections in Python
memory.  Exact set membership is delegated to a temporary on-disk SQLite
index, and diagnostics are capped for hostile/corrupt inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..oracle import canonical_bytes
from ..record.schema import RecordValidationError
from ..replay.inspection import file_sha256
from ..replay.reader import iter_records
from ..sim.metrics import FILL_AUDIT_CHAIN_DOMAIN, MARKOUT_AUDIT_CHAIN_DOMAIN
from ..sim.run_manifest import RUN_MANIFEST_SCHEMA_VERSION


STREAMING_BUNDLE_AUDIT_SCHEMA_VERSION = "lob_sim.streaming_bundle_audit.v1"
SIMULATION_EXPORT_SCHEMA_VERSION = "lob_sim.simulation_export.v1"
ARTIFACT_BUNDLE_SCHEMA_VERSION = "lob_sim.artifact_bundle.v1"
AUDIT_RETENTION_SCHEMA_VERSION = "lob_sim.audit_retention.v1"
EVENT_TRACE_RETENTION_SCHEMA_VERSION = "lob_sim.event_trace_retention.v1"
FILL_PROVENANCE_SCHEMA_VERSION = "lob_sim.fill_provenance.v1"
FILL_PROVENANCE_COVERAGE_SCHEMA_VERSION = "lob_sim.fill_provenance_coverage.v1"
FILL_SOURCES = ("depth_update", "agg_trade", "taker_order")
MARKOUT_STATUSES = ("resolved", "invalidated")
MARKET_RECORD_SOURCES = ("exchangeInfo", "snapshot", "depthUpdate", "aggTrade", "captureMeta", "captureEvent")
MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD = {
    "exchangeInfo": "exchange_info",
    "snapshot": "snapshot",
    "depthUpdate": "depth_update",
    "aggTrade": "agg_trade",
}
EVENT_COUNT_FIELDS = (
    "records_processed",
    "exchange_info",
    "snapshot",
    "depth_update",
    "agg_trade",
    "depth_changes_applied",
    "book_gap_count",
)
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
PUBLIC_CONSUMPTION_SOURCES = ("depth_update", "agg_trade")
PUBLIC_CONSUMPTION_FIELDS = (
    "observed_lots",
    "modeled_lots",
    "overlap_netted_lots",
    "queue_consumed_lots",
    "unmatched_lots",
)
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
FILL_JSON_FIELDS = {"evidence_ids", "validity", "queue_trajectory", "latency_draws_ms", "latency_model"}
FILL_FLOAT_FIELDS = {"ts_local", "created_ts", "time_in_book_ms", "markout_horizon"}
FILL_INT_FIELDS = {"queue_ahead_lots", "book_bid_tick", "book_ask_tick"}
FILL_OPTIONAL_FIELDS = {
    "created_ts",
    "mid_at_fill",
    "spread_capture",
    "spread_capture_value",
    "book_bid_tick",
    "book_ask_tick",
}
MARKOUT_FLOAT_FIELDS = {"horizon", "ts_local", "deadline_ts", "markout_ts_local", "resolution_lag_seconds"}
MARKOUT_INT_FIELDS = {"price_tick", "qty_lots"}
MARKOUT_OPTIONAL_FIELDS = {
    "price_tick",
    "qty_lots",
    "order_id",
    "fill_mid",
    "mid_after",
    "markout",
    "adverse",
    "resolution_lag_seconds",
    "invalid_reason",
}
EXPECTED_FILES = {
    "event_trace": "event_trace.csv",
    "markouts": "markouts.csv",
    "summary": "summary.json",
    "summary_csv": "summary.csv",
    "trades": "trades.csv",
    "manifest": "manifest.json",
}
MAX_DISTINCT_EVENT_TYPES = 128

# Deliberately duplicated here rather than imported from the exporter.  The
# bounded audit is an independent oracle: a schema change must update both
# sides and the resulting mismatch must fail closed.
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
TRADE_AUDIT_FIELDS = (
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
MARKOUT_AUDIT_FIELDS = (
    "symbol",
    "side",
    "fill_source",
    "regime",
    "fill_price",
    "price_tick",
    "qty",
    "qty_lots",
    "order_id",
    "fill_mid",
    "mid_after",
    "markout",
    "contract_multiplier",
    "adverse",
    "horizon",
    "ts_local",
    "deadline_ts",
    "markout_ts_local",
    "resolution_lag_seconds",
    "status",
    "invalid_reason",
)
FILL_TRACE_ROW_FIELDS = ("ts_local", "symbol", "side", "order_id", "fill_source")
FILL_TRACE_DETAIL_FIELDS = tuple(
    field for field in TRADE_AUDIT_FIELDS if field not in {"ts_local", "symbol", "side", "fill_source", "order_id"}
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
    ("status", "status"),
    ("invalid_reason", "invalid_reason"),
)


@dataclass
class _Issues:
    limit: int = 250
    messages: list[str] = field(default_factory=list)
    total: int = 0

    def add(self, message: str) -> None:
        self.total += 1
        if len(self.messages) < self.limit:
            self.messages.append(message)

    @property
    def omitted(self) -> int:
        return max(0, self.total - len(self.messages))


@dataclass(frozen=True)
class _ParsedRow:
    row_number: int
    raw: dict[str, str]
    event: dict[str, Any] | None


@dataclass
class _AuditChain:
    domain: str
    count: int = 0
    digest: bytes = b""

    def __post_init__(self) -> None:
        self.digest = hashlib.sha256(self.domain.encode("utf-8")).digest()

    def add(self, event: Mapping[str, Any]) -> None:
        self.digest = hashlib.sha256(self.digest + b"\0" + canonical_bytes(event)).digest()
        self.count += 1

    @property
    def hexdigest(self) -> str:
        return self.digest.hex()


def _display(path: Path) -> str:
    return path.resolve().as_posix()


def _load_json_object(path: Path, issues: _Issues) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.add(f"Missing required file: {_display(path)}")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        issues.add(f"{_display(path)} is not readable JSON: {exc}")
        return {}
    if not isinstance(decoded, dict):
        issues.add(f"{_display(path)} must contain a JSON object")
        return {}
    return decoded


def _decimal(value: Any) -> Decimal | None:
    try:
        decoded = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return decoded if decoded.is_finite() else None


def _parse_bool(value: str) -> bool | None:
    if value == "True":
        return True
    if value == "False":
        return False
    return None


def _parse_finite_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _scalar_matches(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        left_bool = left if isinstance(left, bool) else _parse_bool(str(left))
        right_bool = right if isinstance(right, bool) else _parse_bool(str(right))
        return left_bool is not None and right_bool is not None and left_bool == right_bool
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    if left is None or right is None:
        left_empty = left is None or left == ""
        right_empty = right is None or right == ""
        return left_empty and right_empty
    return str(left) == str(right)


def _iter_csv_rows(
    path: Path, expected_fields: tuple[str, ...], issues: _Issues
) -> Iterator[tuple[int, dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != expected_fields:
                issues.add(f"{_display(path)} has unexpected CSV schema {reader.fieldnames!r}")
                return
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    issues.add(f"{_display(path)}:{row_number} has more cells than its fixed schema")
                    continue
                yield row_number, {str(key): str(value) for key, value in row.items()}
    except (OSError, csv.Error) as exc:
        issues.add(f"{_display(path)} could not be streamed: {exc}")


def _decode_fill_row(path: Path, row_number: int, row: dict[str, str], issues: _Issues) -> dict[str, Any] | None:
    event: dict[str, Any] = {}
    valid = True
    for field_name in TRADE_AUDIT_FIELDS:
        value = row[field_name]
        if value == "" and field_name in FILL_OPTIONAL_FIELDS:
            event[field_name] = None
        elif field_name in FILL_JSON_FIELDS:
            try:
                event[field_name] = json.loads(value)
            except json.JSONDecodeError as exc:
                issues.add(f"{_display(path)}:{row_number} {field_name} is invalid JSON: {exc.msg}")
                valid = False
        elif field_name in FILL_FLOAT_FIELDS:
            parsed = _parse_finite_float(value)
            if parsed is None:
                issues.add(f"{_display(path)}:{row_number} {field_name} must be finite")
                valid = False
            else:
                event[field_name] = parsed
        elif field_name in FILL_INT_FIELDS:
            try:
                event[field_name] = int(value)
            except ValueError:
                issues.add(f"{_display(path)}:{row_number} {field_name} must be an integer")
                valid = False
        elif field_name == "maker":
            parsed_bool = _parse_bool(value)
            if parsed_bool is None:
                issues.add(f"{_display(path)}:{row_number} maker must be True or False")
                valid = False
            else:
                event[field_name] = parsed_bool
        else:
            event[field_name] = value
    return event if valid else None


def _decode_markout_row(path: Path, row_number: int, row: dict[str, str], issues: _Issues) -> dict[str, Any] | None:
    event: dict[str, Any] = {}
    valid = True
    for field_name in MARKOUT_AUDIT_FIELDS:
        value = row[field_name]
        if field_name == "invalid_reason" and value == "":
            continue
        if value == "" and field_name in MARKOUT_OPTIONAL_FIELDS:
            event[field_name] = None
        elif field_name in MARKOUT_FLOAT_FIELDS:
            parsed = _parse_finite_float(value)
            if parsed is None:
                issues.add(f"{_display(path)}:{row_number} {field_name} must be finite")
                valid = False
            else:
                event[field_name] = parsed
        elif field_name in MARKOUT_INT_FIELDS:
            try:
                event[field_name] = int(value)
            except ValueError:
                issues.add(f"{_display(path)}:{row_number} {field_name} must be an integer")
                valid = False
        elif field_name == "adverse":
            parsed_bool = _parse_bool(value)
            if parsed_bool is None:
                issues.add(f"{_display(path)}:{row_number} adverse must be True, False, or empty")
                valid = False
            else:
                event[field_name] = parsed_bool
        else:
            event[field_name] = value
    return event if valid else None


def _iter_fill_rows(path: Path, issues: _Issues) -> Iterator[_ParsedRow]:
    for row_number, row in _iter_csv_rows(path, TRADE_AUDIT_FIELDS, issues):
        yield _ParsedRow(row_number, row, _decode_fill_row(path, row_number, row, issues))


def _iter_markout_rows(path: Path, issues: _Issues) -> Iterator[_ParsedRow]:
    for row_number, row in _iter_csv_rows(path, MARKOUT_AUDIT_FIELDS, issues):
        yield _ParsedRow(row_number, row, _decode_markout_row(path, row_number, row, issues))


class _AuditIndex:
    """Disk-backed exact sets whose Python memory does not grow with the tape."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY, resolved INTEGER NOT NULL DEFAULT 0)")
        self.connection.execute("CREATE TABLE filled_orders (id TEXT PRIMARY KEY)")

    def add_evidence(self, evidence_id: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO evidence(id) VALUES (?)", (evidence_id,))

    def resolve_evidence(self, evidence_id: str) -> None:
        self.connection.execute("UPDATE evidence SET resolved=1 WHERE id=?", (evidence_id,))

    def add_filled_order(self, order_id: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO filled_orders(id) VALUES (?)", (order_id,))

    def commit(self) -> None:
        self.connection.commit()

    def count_filled_orders(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM filled_orders").fetchone()
        return int(row[0]) if row is not None else 0

    def unresolved_evidence(self) -> tuple[int, list[str]]:
        row = self.connection.execute("SELECT COUNT(*) FROM evidence WHERE resolved=0").fetchone()
        total = int(row[0]) if row is not None else 0
        examples = [
            str(value[0]) for value in self.connection.execute("SELECT id FROM evidence WHERE resolved=0 LIMIT 10")
        ]
        return total, examples

    def close(self) -> None:
        self.connection.close()


@dataclass
class _FillAuditState:
    row_count: int = 0
    source_counts: dict[str, int] = field(default_factory=lambda: {source: 0 for source in FILL_SOURCES})
    provenance_counts: dict[str, int] = field(
        default_factory=lambda: {
            "with_provenance_schema": 0,
            "with_scenario": 0,
            "with_evidence_ids": 0,
            "with_validity": 0,
            "execution_valid": 0,
            "with_queue_trajectory": 0,
            "with_latency_draws": 0,
            "with_latency_model": 0,
            "with_lifecycle_state": 0,
            "with_fee_model": 0,
        }
    )
    chain: _AuditChain = field(default_factory=lambda: _AuditChain(FILL_AUDIT_CHAIN_DOMAIN))


@dataclass
class _MarkoutAuditState:
    row_count: int = 0
    resolved_count: int = 0
    invalidated_count: int = 0
    by_source: dict[str, dict[str, Decimal | int]] = field(
        default_factory=lambda: {
            source: {
                "samples": 0,
                "adverse_samples": 0,
                "qty": Decimal("0"),
                "markout_sum": Decimal("0"),
            }
            for source in FILL_SOURCES
        }
    )
    chain: _AuditChain = field(default_factory=lambda: _AuditChain(MARKOUT_AUDIT_CHAIN_DOMAIN))


@dataclass
class _TraceState:
    row_count: int = 0
    fill_count: int = 0
    markout_count: int = 0
    previous_ts: float | None = None
    event_type_counts: Counter[str] = field(default_factory=Counter)
    market_record_counts: dict[str, int] = field(
        default_factory=lambda: {source: 0 for source in MARKET_RECORD_SOURCES}
    )
    book_gap_counts_by_symbol: dict[str, int] = field(default_factory=dict)
    lifecycle_counts: dict[str, int] = field(default_factory=lambda: {key: 0 for key in ORDER_LIFECYCLE_KEYS})
    consumption: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            source: {field_name: 0 for field_name in PUBLIC_CONSUMPTION_FIELDS} for source in PUBLIC_CONSUMPTION_SOURCES
        }
    )
    arrival_queue_samples: int = 0
    arrival_with_queue: int = 0
    arrival_queue_sum: int = 0
    max_arrival_queue: int = 0


def _expected_fill_scenario_id(summary: Mapping[str, Any], fill_source: object) -> str | None:
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
    signal = "trade_and_depth" if trade and depth else "trade" if trade else "depth" if depth else "none"
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


def _validate_fill_event(
    event: dict[str, Any],
    *,
    path: Path,
    row_number: int,
    summary: Mapping[str, Any],
    index: _AuditIndex,
    state: _FillAuditState,
    issues: _Issues,
) -> None:
    state.row_count += 1
    state.chain.add(event)
    source = event.get("fill_source")
    if source not in FILL_SOURCES:
        issues.add(f"{_display(path)}:{row_number} has invalid fill_source {source!r}")
    else:
        state.source_counts[str(source)] += 1
    if event.get("side") not in {"bid", "ask"}:
        issues.add(f"{_display(path)}:{row_number} has invalid side")
    order_id = event.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        issues.add(f"{_display(path)}:{row_number} is missing order_id")
    else:
        index.add_filled_order(order_id)
    for field_name in ("price", "qty", "notional", "contract_multiplier", "fee_bps", "fee"):
        value = _decimal(event.get(field_name))
        if value is None:
            issues.add(f"{_display(path)}:{row_number} {field_name} must be a finite decimal")
        elif field_name in {"price", "qty", "notional", "contract_multiplier"} and value <= 0:
            issues.add(f"{_display(path)}:{row_number} {field_name} must be positive")

    if event.get("provenance_schema_version") == FILL_PROVENANCE_SCHEMA_VERSION:
        state.provenance_counts["with_provenance_schema"] += 1
    else:
        issues.add(f"{_display(path)}:{row_number} has unexpected provenance_schema_version")
    expected_scenario = _expected_fill_scenario_id(summary, source)
    if isinstance(event.get("scenario_id"), str) and event.get("scenario_id"):
        state.provenance_counts["with_scenario"] += 1
    if expected_scenario is None or event.get("scenario_id") != expected_scenario:
        issues.add(
            f"{_display(path)}:{row_number} scenario_id={event.get('scenario_id')!r} "
            f"does not match run assumptions {expected_scenario!r}"
        )

    evidence_ids = event.get("evidence_ids")
    evidence_valid = (
        isinstance(evidence_ids, list)
        and bool(evidence_ids)
        and all(isinstance(value, str) and bool(value) for value in evidence_ids)
        and len(set(evidence_ids)) == len(evidence_ids)
    )
    if evidence_valid and isinstance(evidence_ids, list):
        state.provenance_counts["with_evidence_ids"] += 1
        for evidence_id in evidence_ids:
            index.add_evidence(str(evidence_id))
    else:
        issues.add(f"{_display(path)}:{row_number} has invalid evidence_ids")

    validity = event.get("validity")
    if isinstance(validity, dict):
        state.provenance_counts["with_validity"] += 1
    if not isinstance(validity, dict) or set(validity) != FILL_VALIDITY_FIELDS:
        issues.add(f"{_display(path)}:{row_number} has invalid validity")
    else:
        boolean_fields = FILL_VALIDITY_FIELDS - {"reason"}
        if any(not isinstance(validity.get(field_name), bool) for field_name in boolean_fields):
            issues.add(f"{_display(path)}:{row_number} validity fields must be boolean")
        else:
            expected_execution_valid = (
                validity["book_valid"]
                and validity["clock_valid"]
                and validity["capture_valid"]
                and (validity["trade_stream_valid"] or not validity["trade_stream_required"])
            )
            if validity["execution_valid"] is not expected_execution_valid:
                issues.add(f"{_display(path)}:{row_number} validity has inconsistent execution_valid")
            if validity["execution_valid"]:
                state.provenance_counts["execution_valid"] += 1
        reason = validity.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason):
            issues.add(f"{_display(path)}:{row_number} validity has invalid reason")
        if source == "agg_trade" and validity.get("trade_stream_required") is not True:
            issues.add(f"{_display(path)}:{row_number} agg_trade validity must require the trade stream")

    trajectory = event.get("queue_trajectory")
    if isinstance(trajectory, dict) and trajectory:
        state.provenance_counts["with_queue_trajectory"] += 1
    expected_fields = TAKER_QUEUE_TRAJECTORY_FIELDS if source == "taker_order" else PASSIVE_QUEUE_TRAJECTORY_FIELDS
    if not isinstance(trajectory, dict) or set(trajectory) != expected_fields:
        issues.add(f"{_display(path)}:{row_number} has invalid queue_trajectory fields")
    elif any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in trajectory.values()):
        issues.add(f"{_display(path)}:{row_number} has invalid queue_trajectory values")
    elif source == "taker_order":
        if trajectory["visible_level_after_lots"] != trajectory["visible_level_before_lots"] - trajectory["fill_lots"]:
            issues.add(f"{_display(path)}:{row_number} has inconsistent taker queue_trajectory")
    else:
        before = trajectory["queue_ahead_before_trigger_lots"]
        at_fill = trajectory["queue_ahead_at_fill_lots"]
        if at_fill > before or trajectory["queue_consumed_before_fill_lots"] != before - at_fill:
            issues.add(f"{_display(path)}:{row_number} has inconsistent passive queue_trajectory")

    latency_draws = event.get("latency_draws_ms")
    if isinstance(latency_draws, dict) and latency_draws:
        state.provenance_counts["with_latency_draws"] += 1
    if not isinstance(latency_draws, dict) or set(latency_draws) != {"new_order", "cancel"}:
        issues.add(f"{_display(path)}:{row_number} has invalid latency_draws_ms")
    elif any(
        value is not None
        and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0)
        for value in latency_draws.values()
    ):
        issues.add(f"{_display(path)}:{row_number} has non-finite latency draw")

    latency_model = event.get("latency_model")
    if isinstance(latency_model, dict) and latency_model:
        state.provenance_counts["with_latency_model"] += 1
    if not isinstance(latency_model, dict) or set(latency_model) != LATENCY_MODEL_FIELDS:
        issues.add(f"{_display(path)}:{row_number} has invalid latency_model")
    else:
        if not isinstance(latency_model.get("mode"), str) or not latency_model.get("mode"):
            issues.add(f"{_display(path)}:{row_number} latency_model has invalid mode")
        if not isinstance(latency_model.get("seed"), int) or isinstance(latency_model.get("seed"), bool):
            issues.add(f"{_display(path)}:{row_number} latency_model has invalid seed")
        if latency_model.get("source") != "configured_scenario" or latency_model.get("measured") is not False:
            issues.add(f"{_display(path)}:{row_number} latency_model must not claim measurement")

    if event.get("order_state_at_fill") in {"live", "pending_cancel"}:
        state.provenance_counts["with_lifecycle_state"] += 1
    else:
        issues.add(f"{_display(path)}:{row_number} has invalid order_state_at_fill")
    if event.get("fee_model_id"):
        state.provenance_counts["with_fee_model"] += 1
    if event.get("fee_model_id") != "static_config_bps":
        issues.add(f"{_display(path)}:{row_number} has invalid fee_model_id")


def _validate_markout_event(
    event: dict[str, Any],
    *,
    path: Path,
    row_number: int,
    state: _MarkoutAuditState,
    issues: _Issues,
) -> None:
    state.row_count += 1
    state.chain.add(event)
    source = event.get("fill_source")
    if source not in FILL_SOURCES:
        issues.add(f"{_display(path)}:{row_number} has invalid fill_source")
    if event.get("side") not in {"bid", "ask"}:
        issues.add(f"{_display(path)}:{row_number} has invalid side")
    if _decimal(event.get("fill_price")) is None or _decimal(event.get("qty")) is None:
        issues.add(f"{_display(path)}:{row_number} has invalid fill price or quantity")
    status = event.get("status")
    if status not in MARKOUT_STATUSES:
        issues.add(f"{_display(path)}:{row_number} has invalid status {status!r}")
        return
    if status == "invalidated":
        state.invalidated_count += 1
        if any(
            event.get(field_name) is not None
            for field_name in ("mid_after", "markout", "adverse", "resolution_lag_seconds")
        ):
            issues.add(f"{_display(path)}:{row_number} invalidated markout must keep outcome fields null")
        if not isinstance(event.get("invalid_reason"), str) or not event.get("invalid_reason"):
            issues.add(f"{_display(path)}:{row_number} invalidated markout is missing invalid_reason")
        return

    state.resolved_count += 1
    if event.get("invalid_reason") not in {None, ""}:
        issues.add(f"{_display(path)}:{row_number} resolved markout must not have invalid_reason")
    markout = _decimal(event.get("markout"))
    qty = _decimal(event.get("qty"))
    if markout is None or qty is None or qty <= 0 or not isinstance(event.get("adverse"), bool):
        issues.add(f"{_display(path)}:{row_number} resolved markout has invalid outcome fields")
        return
    if event["adverse"] is not (markout < 0):
        issues.add(f"{_display(path)}:{row_number} adverse flag is inconsistent with markout")
    if source in FILL_SOURCES:
        source_state = state.by_source[str(source)]
        source_state["samples"] = int(source_state["samples"]) + 1
        source_state["qty"] = Decimal(source_state["qty"]) + qty
        source_state["markout_sum"] = Decimal(source_state["markout_sum"]) + markout * qty
        if event["adverse"]:
            source_state["adverse_samples"] = int(source_state["adverse_samples"]) + 1


def _artifact_path(pack_dir: Path, label: str) -> Path:
    return pack_dir / EXPECTED_FILES[label]


def _artifact_bundle_snapshot(output_artifacts: Mapping[str, Any]) -> dict[str, Any]:
    """Independently recompute the content identity of non-manifest outputs."""

    entries: list[dict[str, Any]] = []
    complete = True
    for label, metadata in sorted(output_artifacts.items()):
        if label == "manifest":
            continue
        if not isinstance(metadata, Mapping):
            complete = False
            continue
        size_bytes = metadata.get("size_bytes")
        digest = metadata.get("sha256")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            complete = False
            continue
        entries.append({"label": str(label), "size_bytes": size_bytes, "sha256": digest})
    payload = {"schema_version": ARTIFACT_BUNDLE_SCHEMA_VERSION, "artifacts": entries}
    bundle: dict[str, Any] = {
        "schema_version": ARTIFACT_BUNDLE_SCHEMA_VERSION,
        "algorithm": "sha256",
        "artifact_count": len(entries),
        "complete": complete,
        "sha256": None,
    }
    if complete:
        bundle["sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return bundle


def _audit_bundle_boundaries(pack_dir: Path, issues: _Issues) -> None:
    incomplete = pack_dir / "_INCOMPLETE.json"
    if incomplete.exists():
        issues.add(f"{_display(pack_dir)} is incomplete because {incomplete.name} is present")
    for partial in pack_dir.glob("*.partial"):
        issues.add(f"{_display(pack_dir)} contains unfinished artifact {partial.name}")
    for filename in EXPECTED_FILES.values():
        path = pack_dir / filename
        if not path.is_file():
            issues.add(f"Missing required file: {_display(path)}")


def _audit_manifest_and_artifacts(
    pack_dir: Path,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    issues: _Issues,
) -> None:
    manifest_path = _artifact_path(pack_dir, "manifest")
    if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        issues.add(f"{_display(manifest_path)} has unexpected schema_version")
    for field_name in ("run_id", "feed_adapter", "instrument_specs", "simulation_assumptions"):
        if manifest.get(field_name) != summary.get(field_name):
            issues.add(f"{_display(manifest_path)} {field_name} does not match summary.json")
    manifest_input = manifest.get("input")
    if not isinstance(manifest_input, dict):
        issues.add(f"{_display(manifest_path)} is missing input metadata")
    elif manifest_input.get("sha256") != summary.get("input_sha256"):
        issues.add(f"{_display(manifest_path)} input.sha256 does not match summary input_sha256")
    manifest_config = manifest.get("config")
    if not isinstance(manifest_config, dict):
        issues.add(f"{_display(manifest_path)} is missing config")
    else:
        for field_name in ("fill_assumption_profile", "fill_assumption"):
            if manifest_config.get(field_name) != summary.get(field_name):
                issues.add(f"{_display(manifest_path)} {field_name} does not match summary.json")

    output_files = summary.get("output_files")
    if not isinstance(output_files, dict) or set(output_files) != set(EXPECTED_FILES):
        issues.add("summary.output_files does not match the bounded bundle contract")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(EXPECTED_FILES):
        issues.add("manifest.outputs does not match the bounded bundle contract")

    artifacts = manifest.get("output_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(EXPECTED_FILES):
        issues.add(f"{_display(manifest_path)} output_artifacts does not match the bounded bundle contract")
        return
    actual_artifacts: dict[str, Any] = {}
    for label, filename in EXPECTED_FILES.items():
        artifact = artifacts.get(label)
        target = pack_dir / filename
        if not isinstance(artifact, dict):
            issues.add(f"{_display(manifest_path)} output_artifacts[{label}] is missing")
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or Path(raw_path).name != filename:
            issues.add(f"{_display(manifest_path)} output_artifacts[{label}].path is inconsistent")
        if label == "manifest":
            if set(artifact) != {"path"}:
                issues.add(f"{_display(manifest_path)} must not self-hash its manifest artifact")
            continue
        if not target.is_file():
            actual_artifacts[label] = {}
            continue
        actual_size = target.stat().st_size
        actual_sha = file_sha256(target)
        actual_artifacts[label] = {"size_bytes": actual_size, "sha256": actual_sha}
        if artifact.get("size_bytes") != actual_size:
            issues.add(f"{_display(manifest_path)} output_artifacts[{label}].size_bytes is stale")
        if artifact.get("sha256") != actual_sha:
            issues.add(f"{_display(manifest_path)} output_artifacts[{label}].sha256 is stale")

    export = summary.get("simulation_export")
    if isinstance(export, dict) and export.get("mode") == "bounded_streaming":
        expected_bundle = _artifact_bundle_snapshot(actual_artifacts)
        if manifest.get("artifact_bundle") != expected_bundle:
            issues.add(f"{_display(manifest_path)} artifact_bundle does not match output_artifacts")


def _audit_streaming_contract(summary: Mapping[str, Any], manifest: Mapping[str, Any], issues: _Issues) -> None:
    export = summary.get("simulation_export")
    expected_export = {
        "schema_version": SIMULATION_EXPORT_SCHEMA_VERSION,
        "mode": "bounded_streaming",
        "memory_bounded_by_tape_duration": True,
        "detail_rows_complete_in_summary": False,
        "detail_rows_streamed": True,
        "markout_audit_file": "markouts",
        "completion_record": "manifest_with_absent_incomplete_sentinel",
        "intended_use": "ordinary_and_large_tape_simulation",
    }
    if export != expected_export:
        issues.add("summary.simulation_export does not match the bounded streaming contract")
    if summary.get("fills") is not None or summary.get("markout_events") is not None:
        issues.add("bounded summary must not retain fill or markout detail rows")

    retention = summary.get("audit_retention")
    if not isinstance(retention, dict):
        issues.add("summary.json is missing audit_retention")
    else:
        expected_retention = {
            "schema_version": AUDIT_RETENTION_SCHEMA_VERSION,
            "mode": "streaming",
            "memory_bounded_by_tape_duration": True,
            "built_in_sinks_memory_bounded": True,
            "detail_rows_complete_in_summary": False,
            "fill_rows_emitted": summary.get("fill_count"),
            "fill_rows_retained": 0,
            "fill_sink": "StreamingCsvSink",
            "markout_rows_retained": 0,
            "markout_sink": "StreamingCsvSink",
            "markout_trace_buffering": True,
            "pending_markouts": summary.get("markout_samples_remaining"),
        }
        for field_name, expected in expected_retention.items():
            if retention.get(field_name) != expected:
                issues.add(
                    f"summary.audit_retention.{field_name}={retention.get(field_name)!r} "
                    f"does not match expected {expected!r}"
                )
        max_pending = retention.get("max_pending_markouts")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending <= 0:
            issues.add("summary.audit_retention.max_pending_markouts must be a positive integer")
        manifest_config = manifest.get("config")
        if isinstance(manifest_config, dict) and manifest_config.get("sim_max_pending_markouts") != max_pending:
            issues.add("manifest.config.sim_max_pending_markouts does not match summary.audit_retention")

    trace_retention = summary.get("event_trace_retention")
    expected_trace = {
        "schema_version": EVENT_TRACE_RETENTION_SCHEMA_VERSION,
        "retained_in_memory": False,
        "rows_emitted": summary.get("event_trace_count"),
        "rows_retained": 0,
        "sink": "StreamingCsvSink",
        "sink_memory_bounded": True,
        "memory_bounded_by_tape_duration": True,
    }
    if trace_retention != expected_trace:
        issues.add("summary.event_trace_retention does not match the bounded streaming contract")


def _audit_summary_csv(pack_dir: Path, summary: Mapping[str, Any], issues: _Issues) -> None:
    path = _artifact_path(pack_dir, "summary_csv")
    # Summary CSV intentionally has a dynamic aggregate schema. Read it
    # independently here, but retain at most its single contract row.
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
            second = next(reader, None)
    except (OSError, csv.Error) as exc:
        issues.add(f"{_display(path)} could not be read: {exc}")
        return
    if first is None or second is not None:
        issues.add(f"{_display(path)} must contain exactly one summary row")
        return
    for field_name in ("strategy_profile", "fill_assumption_profile", "run_id", "input_sha256"):
        if first.get(field_name) != str(summary.get(field_name)):
            issues.add(f"{_display(path)} {field_name} does not match summary.json")
    for field_name in ("fill_count", "quote_count", "cancel_count", "self_trade_prevention_count", "event_trace_count"):
        try:
            observed = int(str(first.get(field_name)))
        except (TypeError, ValueError):
            issues.add(f"{_display(path)} {field_name} is not an integer")
            continue
        if observed != summary.get(field_name):
            issues.add(f"{_display(path)} {field_name} does not match summary.json")
    for field_name in (
        "event_counts",
        "book_gap_count_by_symbol",
        "fill_source_counts",
        "fill_provenance",
        "audit_retention",
        "event_trace_retention",
        "order_lifecycle_counts",
        "markout_by_fill_source",
        "public_consumption_summary",
        "fill_assumption",
        "fill_assumption_diagnostics",
        "feed_adapter",
        "instrument_specs",
        "simulation_assumptions",
        "output_files",
        "simulation_export",
    ):
        raw = first.get(field_name)
        try:
            decoded = json.loads(raw or "")
        except json.JSONDecodeError as exc:
            issues.add(f"{_display(path)} {field_name} is invalid JSON: {exc.msg}")
            continue
        if decoded != summary.get(field_name):
            issues.add(f"{_display(path)} {field_name} does not match summary.json")


def _scan_fill_audit(
    path: Path,
    summary: Mapping[str, Any],
    index: _AuditIndex,
    issues: _Issues,
) -> _FillAuditState:
    state = _FillAuditState()
    raw_count = 0
    for parsed in _iter_fill_rows(path, issues):
        raw_count += 1
        if parsed.event is None:
            continue
        _validate_fill_event(
            parsed.event,
            path=path,
            row_number=parsed.row_number,
            summary=summary,
            index=index,
            state=state,
            issues=issues,
        )
    if raw_count != state.row_count:
        issues.add(f"{_display(path)} has {raw_count - state.row_count} undecodable fill row(s)")
    return state


def _scan_markout_audit(path: Path, issues: _Issues) -> _MarkoutAuditState:
    state = _MarkoutAuditState()
    raw_count = 0
    for parsed in _iter_markout_rows(path, issues):
        raw_count += 1
        if parsed.event is None:
            continue
        _validate_markout_event(
            parsed.event,
            path=path,
            row_number=parsed.row_number,
            state=state,
            issues=issues,
        )
    if raw_count != state.row_count:
        issues.add(f"{_display(path)} has {raw_count - state.row_count} undecodable markout row(s)")
    return state


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


def _resolve_input_path(pack_dir: Path, manifest: Mapping[str, Any]) -> Path | None:
    manifest_input = manifest.get("input")
    if not isinstance(manifest_input, dict) or not isinstance(manifest_input.get("path"), str):
        return None
    raw = Path(manifest_input["path"])
    if raw.is_absolute():
        return raw
    candidates = (pack_dir / raw.name, pack_dir / raw, Path.cwd() / raw)
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _scan_input(
    pack_dir: Path,
    manifest: Mapping[str, Any],
    index: _AuditIndex,
    issues: _Issues,
) -> tuple[Path | None, dict[str, int], int]:
    path = _resolve_input_path(pack_dir, manifest)
    if path is None:
        issues.add(f"{_display(_artifact_path(pack_dir, 'manifest'))} is missing input.path")
        return None, {field_name: 0 for field_name in MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.values()}, 0
    if not path.is_file():
        issues.add(f"Missing replay input file: {_display(path)}")
        return path, {field_name: 0 for field_name in MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.values()}, 0

    manifest_input = manifest.get("input")
    assert isinstance(manifest_input, dict)
    if manifest_input.get("size_bytes") != path.stat().st_size:
        issues.add(f"{_display(path)} size does not match manifest input metadata")
    observed_sha = file_sha256(path)
    if manifest_input.get("sha256") != observed_sha:
        issues.add(f"{_display(path)} SHA-256 does not match manifest input metadata")

    counts = {field_name: 0 for field_name in MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.values()}
    records_processed = 0
    try:
        for record in iter_records(path):
            records_processed += 1
            index.resolve_evidence(_record_evidence_id(record, records_processed))
            summary_field = MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.get(record.type)
            if summary_field is not None:
                counts[summary_field] += 1
    except (OSError, ValueError, RecordValidationError) as exc:
        issues.add(f"{_display(path)} could not be read for event-count audit: {exc}")
    index.commit()
    unresolved_count, examples = index.unresolved_evidence()
    if unresolved_count:
        issues.add(f"fill audit has {unresolved_count} unresolved evidence id(s); examples={examples}")
    return path, counts, records_processed


def _parse_details(path: Path, row_number: int, raw: str, issues: _Issues) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        issues.add(f"{_display(path)}:{row_number} details is invalid JSON: {exc.msg}")
        return {}
    if not isinstance(decoded, dict):
        issues.add(f"{_display(path)}:{row_number} details must be a JSON object")
        return {}
    return decoded


def _next_row(iterator: Iterator[_ParsedRow]) -> _ParsedRow | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


def _compare_fill_trace(
    trace_path: Path,
    row_number: int,
    row: Mapping[str, str],
    details: Mapping[str, Any],
    parsed_fill: _ParsedRow | None,
    issues: _Issues,
) -> None:
    if parsed_fill is None or parsed_fill.event is None:
        issues.add(f"{_display(trace_path)}:{row_number} has no corresponding trades.csv row")
        return
    fill = parsed_fill.event
    for field_name in FILL_TRACE_ROW_FIELDS:
        if not _scalar_matches(row.get(field_name), fill.get(field_name)):
            issues.add(f"{_display(trace_path)}:{row_number} {field_name} does not match trades.csv")
    for field_name in FILL_TRACE_DETAIL_FIELDS:
        actual = details.get(field_name)
        expected = fill.get(field_name)
        matches = actual == expected if field_name in FILL_JSON_FIELDS else _scalar_matches(actual, expected)
        if not matches:
            issues.add(f"{_display(trace_path)}:{row_number} details.{field_name} does not match trades.csv")
    trajectory = fill.get("queue_trajectory")
    if isinstance(trajectory, dict) and isinstance(trajectory.get("fill_lots"), int):
        try:
            trace_lots = int(str(row.get("qty_lots", "")))
        except ValueError:
            trace_lots = None
        if trace_lots is not None and trajectory["fill_lots"] != trace_lots:
            issues.add(f"{_display(trace_path)}:{row_number} queue_trajectory fill_lots does not match qty_lots")


def _compare_markout_trace(
    trace_path: Path,
    row_number: int,
    row: Mapping[str, str],
    details: Mapping[str, Any],
    parsed_markout: _ParsedRow | None,
    issues: _Issues,
) -> None:
    if parsed_markout is None or parsed_markout.event is None:
        issues.add(f"{_display(trace_path)}:{row_number} has no corresponding markouts.csv row")
        return
    markout = parsed_markout.event
    if not _scalar_matches(row.get("ts_local"), markout.get("markout_ts_local")):
        issues.add(f"{_display(trace_path)}:{row_number} ts_local does not match markouts.csv")
    for field_name in MARKOUT_TRACE_ROW_FIELDS:
        if not _scalar_matches(row.get(field_name), markout.get(field_name)):
            issues.add(f"{_display(trace_path)}:{row_number} {field_name} does not match markouts.csv")
    for detail_field, markout_field in MARKOUT_TRACE_DETAIL_FIELDS:
        if not _scalar_matches(details.get(detail_field), markout.get(markout_field)):
            issues.add(f"{_display(trace_path)}:{row_number} details.{detail_field} does not match markouts.csv")


def _count_event_type(state: _TraceState, event_type: str, issues: _Issues, path: Path, row_number: int) -> None:
    if event_type in state.event_type_counts or len(state.event_type_counts) < MAX_DISTINCT_EVENT_TYPES:
        state.event_type_counts[event_type] += 1
        return
    state.event_type_counts["<additional-unrecognized-types>"] += 1
    issues.add(f"{_display(path)}:{row_number} exceeds the distinct event_type limit")


def _audit_queue_consumption(
    trace_path: Path,
    row_number: int,
    source: str,
    details: Mapping[str, Any],
    state: _TraceState,
    issues: _Issues,
) -> None:
    if source not in PUBLIC_CONSUMPTION_SOURCES:
        issues.add(f"{_display(trace_path)}:{row_number} has invalid queue_consumption source {source!r}")
        return
    parsed: dict[str, int] = {}
    for field_name in PUBLIC_CONSUMPTION_FIELDS:
        value = details.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.add(f"{_display(trace_path)}:{row_number} has invalid queue_consumption {field_name}")
        else:
            parsed[field_name] = value
            state.consumption[source][field_name] += value
    if len(parsed) != len(PUBLIC_CONSUMPTION_FIELDS):
        return
    if parsed["observed_lots"] < parsed["modeled_lots"]:
        issues.add(f"{_display(trace_path)}:{row_number} queue_consumption models more lots than observed")
    if parsed["overlap_netted_lots"] != parsed["observed_lots"] - parsed["modeled_lots"]:
        issues.add(f"{_display(trace_path)}:{row_number} queue_consumption netted lots are inconsistent")
    if parsed["queue_consumed_lots"] > parsed["modeled_lots"]:
        issues.add(f"{_display(trace_path)}:{row_number} queue_consumption consumes more queue than modeled")
    if parsed["unmatched_lots"] != parsed["modeled_lots"] - parsed["queue_consumed_lots"]:
        issues.add(f"{_display(trace_path)}:{row_number} queue_consumption unmatched lots are inconsistent")


def _scan_trace(
    pack_dir: Path,
    fill_issues: _Issues,
    markout_issues: _Issues,
    issues: _Issues,
) -> _TraceState:
    trace_path = _artifact_path(pack_dir, "event_trace")
    fill_iter = _iter_fill_rows(_artifact_path(pack_dir, "trades"), fill_issues)
    markout_iter = _iter_markout_rows(_artifact_path(pack_dir, "markouts"), markout_issues)
    state = _TraceState()
    for row_number, row in _iter_csv_rows(trace_path, EVENT_TRACE_FIELDS, issues):
        state.row_count += 1
        event_type = row.get("event_type", "")
        _count_event_type(state, event_type, issues, trace_path, row_number)
        try:
            seq = int(row.get("seq", ""))
        except (TypeError, ValueError):
            issues.add(f"{_display(trace_path)}:{row_number} has invalid seq")
            seq = None
        expected_seq = state.row_count - 1
        if seq is not None and seq != expected_seq:
            issues.add(f"{_display(trace_path)}:{row_number} seq={seq} expected {expected_seq}")
        ts_local = _parse_finite_float(row.get("ts_local", ""))
        if ts_local is None or ts_local < 0:
            issues.add(f"{_display(trace_path)}:{row_number} has invalid ts_local")
        else:
            if state.previous_ts is not None and ts_local < state.previous_ts:
                issues.add(f"{_display(trace_path)}:{row_number} is out of event-time order")
            state.previous_ts = ts_local

        details = _parse_details(trace_path, row_number, row.get("details", ""), issues)
        if event_type == "market_record":
            source = row.get("source", "")
            if source not in MARKET_RECORD_SOURCES:
                issues.add(f"{_display(trace_path)}:{row_number} has invalid market_record source {source!r}")
            else:
                state.market_record_counts[source] += 1
            if details.get("record_type") != source:
                issues.add(
                    f"{_display(trace_path)}:{row_number} market_record details.record_type does not match source"
                )
        elif event_type == "book_gap":
            symbol = row.get("symbol", "")
            if not symbol:
                issues.add(f"{_display(trace_path)}:{row_number} book_gap row is missing symbol")
            else:
                state.book_gap_counts_by_symbol[symbol] = state.book_gap_counts_by_symbol.get(symbol, 0) + 1
        elif event_type == "order_arrival_scheduled":
            state.lifecycle_counts["arrival_scheduled"] += 1
        elif event_type == "order_arrival":
            state.lifecycle_counts["arrived"] += 1
            if details.get("resting_after_arrival") is True:
                state.lifecycle_counts["rested_after_arrival"] += 1
                queue_ahead = details.get("queue_ahead_lots_after_arrival")
                if isinstance(queue_ahead, int) and not isinstance(queue_ahead, bool) and queue_ahead >= 0:
                    state.arrival_queue_samples += 1
                    state.arrival_queue_sum += queue_ahead
                    state.max_arrival_queue = max(state.max_arrival_queue, queue_ahead)
                    if queue_ahead > 0:
                        state.arrival_with_queue += 1
                else:
                    issues.add(f"{_display(trace_path)}:{row_number} has invalid queue_ahead_lots_after_arrival")
            immediate_fills = details.get("immediate_fills")
            if isinstance(immediate_fills, int) and not isinstance(immediate_fills, bool) and immediate_fills > 0:
                state.lifecycle_counts["immediate_fill_arrivals"] += 1
            remaining = details.get("remaining_lots_after_arrival")
            if details.get("resting_after_arrival") is False and isinstance(remaining, int) and remaining > 0:
                state.lifecycle_counts["expired_unfilled_arrivals"] += 1
            if details.get("self_trade_prevented") is True:
                state.lifecycle_counts["self_trade_prevented"] += 1
        elif event_type == "cancel_requested":
            state.lifecycle_counts["cancel_requested"] += 1
        elif event_type == "cancel_ack":
            state.lifecycle_counts["cancel_acknowledged"] += 1
        elif event_type == "queue_consumption":
            _audit_queue_consumption(trace_path, row_number, row.get("source", ""), details, state, issues)
        elif event_type == "fill":
            state.fill_count += 1
            for field_name in ("side", "price_tick", "qty_lots", "order_id", "fill_source"):
                if not row.get(field_name):
                    issues.add(f"{_display(trace_path)}:{row_number} fill row is missing {field_name}")
            _compare_fill_trace(trace_path, row_number, row, details, _next_row(fill_iter), issues)
        elif event_type == "markout":
            state.markout_count += 1
            _compare_markout_trace(trace_path, row_number, row, details, _next_row(markout_iter), issues)

    extra_fill = _next_row(fill_iter)
    if extra_fill is not None:
        issues.add(f"{_display(_artifact_path(pack_dir, 'trades'))} has rows without corresponding trace fills")
    extra_markout = _next_row(markout_iter)
    if extra_markout is not None:
        issues.add(f"{_display(_artifact_path(pack_dir, 'markouts'))} has rows without corresponding trace markouts")
    return state


def _audit_fill_summary(
    pack_dir: Path,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    state: _FillAuditState,
    unique_order_count: int,
    issues: _Issues,
) -> None:
    if summary.get("fill_count") != state.row_count:
        issues.add(f"trades.csv has {state.row_count} row(s), summary expected {summary.get('fill_count')!r}")
    if summary.get("fill_source_counts") != state.source_counts:
        issues.add(f"summary.fill_source_counts does not match trades.csv value {state.source_counts}")
    retention = summary.get("audit_retention")
    if isinstance(retention, dict):
        if retention.get("fill_rows_emitted") != state.row_count:
            issues.add("summary.audit_retention.fill_rows_emitted does not match trades.csv")
        if retention.get("fill_audit_sha256") != state.chain.hexdigest:
            issues.add("summary.audit_retention.fill_audit_sha256 does not match serialized trades.csv")

    provenance = summary.get("fill_provenance")
    expected_provenance = {
        "schema_version": FILL_PROVENANCE_COVERAGE_SCHEMA_VERSION,
        "fill_count": state.row_count,
        **state.provenance_counts,
        "complete": all(
            state.provenance_counts[field_name] == state.row_count
            for field_name in (
                "with_provenance_schema",
                "with_scenario",
                "with_evidence_ids",
                "with_validity",
                "with_queue_trajectory",
                "with_latency_draws",
                "with_latency_model",
                "with_lifecycle_state",
                "with_fee_model",
            )
        ),
    }
    if provenance != expected_provenance:
        issues.add("summary.fill_provenance does not match serialized fill coverage")

    lifecycle = summary.get("order_lifecycle_counts")
    arrived = lifecycle.get("arrived") if isinstance(lifecycle, dict) else None
    fill_count = summary.get("fill_count")
    quote_count = summary.get("quote_count")
    if isinstance(fill_count, int) and isinstance(quote_count, int):
        expected = float(Decimal(fill_count) / Decimal(quote_count)) if quote_count else 0.0
        if not _scalar_matches(summary.get("fills_per_quote_request"), expected):
            issues.add("summary.fills_per_quote_request is inconsistent")
    if isinstance(fill_count, int) and isinstance(arrived, int):
        fills_per_arrived = float(Decimal(fill_count) / Decimal(arrived)) if arrived else 0.0
        fill_probability = float(Decimal(unique_order_count) / Decimal(arrived)) if arrived else 0.0
        if not _scalar_matches(summary.get("fills_per_arrived_order"), fills_per_arrived):
            issues.add("summary.fills_per_arrived_order is inconsistent")
        if not _scalar_matches(summary.get("quote_fill_probability"), fill_probability):
            issues.add("summary.quote_fill_probability is inconsistent with distinct filled orders")
        observed_probability = summary.get("quote_fill_probability")
        if not isinstance(observed_probability, (int, float)) or not 0 <= float(observed_probability) <= 1:
            issues.add("summary.quote_fill_probability must be between zero and one")

    manifest_config = manifest.get("config")
    if isinstance(manifest_config, dict) and manifest_config.get("fill_assumption") != summary.get("fill_assumption"):
        issues.add(f"{_display(_artifact_path(pack_dir, 'manifest'))} fill assumptions do not match summary")


def _audit_markout_summary(summary: Mapping[str, Any], state: _MarkoutAuditState, issues: _Issues) -> None:
    retention = summary.get("audit_retention")
    if isinstance(retention, dict):
        if retention.get("markout_rows_emitted") != state.row_count:
            issues.add("summary.audit_retention.markout_rows_emitted does not match markouts.csv")
        if retention.get("markout_audit_sha256") != state.chain.hexdigest:
            issues.add("summary.audit_retention.markout_audit_sha256 does not match serialized markouts.csv")
    if summary.get("markout_resolved_count") != state.resolved_count:
        issues.add("summary.markout_resolved_count does not match markouts.csv")
    if summary.get("markout_invalidated_count") != state.invalidated_count:
        issues.add("summary.markout_invalidated_count does not match markouts.csv")
    pending = summary.get("markout_samples_remaining")
    if isinstance(pending, int) and summary.get("markout_unresolved_count") != state.invalidated_count + pending:
        issues.add("summary.markout_unresolved_count is inconsistent")

    by_source = summary.get("markout_by_fill_source")
    if not isinstance(by_source, dict):
        issues.add("summary.json is missing markout_by_fill_source")
        return
    for source in FILL_SOURCES:
        observed = by_source.get(source)
        if not isinstance(observed, dict):
            issues.add(f"summary.markout_by_fill_source is missing {source}")
            continue
        expected = state.by_source[source]
        samples = int(expected["samples"])
        adverse = int(expected["adverse_samples"])
        qty = Decimal(expected["qty"])
        weighted_sum = Decimal(expected["markout_sum"])
        values = {
            "samples": samples,
            "adverse_samples": adverse,
            "qty": float(qty),
            "avg_markout_1s": float(weighted_sum / qty) if qty > 0 else 0.0,
            "adverse_fill_rate_1s": float(Decimal(adverse) / Decimal(samples)) if samples else 0.0,
        }
        for field_name, expected_value in values.items():
            if not _scalar_matches(observed.get(field_name), expected_value):
                issues.add(f"summary.markout_by_fill_source.{source}.{field_name} is inconsistent")


def _audit_trace_summary(summary: Mapping[str, Any], state: _TraceState, issues: _Issues) -> None:
    if summary.get("event_trace_count") != state.row_count:
        issues.add(
            f"event_trace.csv has {state.row_count} row(s), summary expected {summary.get('event_trace_count')!r}"
        )
    if summary.get("fill_count") != state.fill_count:
        issues.add(
            f"event_trace.csv has {state.fill_count} fill row(s), summary expected {summary.get('fill_count')!r}"
        )
    retention = summary.get("audit_retention")
    if isinstance(retention, dict) and retention.get("markout_rows_emitted") != state.markout_count:
        issues.add("event_trace markout count does not match summary.audit_retention")

    lifecycle = summary.get("order_lifecycle_counts")
    if lifecycle != state.lifecycle_counts:
        issues.add(f"summary.order_lifecycle_counts does not match trace value {state.lifecycle_counts}")
    if isinstance(lifecycle, dict):
        if lifecycle.get("arrived") != summary.get("quote_count"):
            issues.add("order_lifecycle_counts.arrived does not match quote_count")
        if lifecycle.get("cancel_requested") != summary.get("cancel_count"):
            issues.add("order_lifecycle_counts.cancel_requested does not match cancel_count")
        if lifecycle.get("self_trade_prevented") != summary.get("self_trade_prevention_count"):
            issues.add("order_lifecycle_counts.self_trade_prevented does not match self_trade_prevention_count")
    arrival_queue = {
        "resting_arrival_queue_samples": state.arrival_queue_samples,
        "arrival_with_queue_ahead_count": state.arrival_with_queue,
        "avg_arrival_queue_ahead_lots": (
            state.arrival_queue_sum / state.arrival_queue_samples if state.arrival_queue_samples else 0.0
        ),
        "max_arrival_queue_ahead_lots": state.max_arrival_queue,
    }
    for field_name, expected in arrival_queue.items():
        if not _scalar_matches(summary.get(field_name), expected):
            issues.add(f"summary.{field_name} does not match event_trace.csv")

    public_summary = summary.get("public_consumption_summary")
    if not isinstance(public_summary, dict) or not isinstance(public_summary.get("sources"), dict):
        issues.add("summary.public_consumption_summary is missing sources")
    else:
        totals = {field_name: 0 for field_name in PUBLIC_CONSUMPTION_FIELDS}
        for source in PUBLIC_CONSUMPTION_SOURCES:
            observed = public_summary["sources"].get(source)
            if observed != state.consumption[source]:
                issues.add(f"summary.public_consumption_summary.{source} does not match trace")
            for field_name in PUBLIC_CONSUMPTION_FIELDS:
                totals[field_name] += state.consumption[source][field_name]
        total_names = {
            "observed_lots": "total_observed_lots",
            "modeled_lots": "total_modeled_lots",
            "overlap_netted_lots": "total_overlap_netted_lots",
            "queue_consumed_lots": "total_queue_consumed_lots",
            "unmatched_lots": "total_unmatched_lots",
        }
        for field_name, summary_field in total_names.items():
            if public_summary.get(summary_field) != totals[field_name]:
                issues.add(f"summary.public_consumption_summary.{summary_field} does not match trace")

    event_counts = summary.get("event_counts")
    if not isinstance(event_counts, dict) or set(event_counts) != set(EVENT_COUNT_FIELDS):
        issues.add("summary.event_counts has an unexpected contract")
        return
    market_total = sum(state.market_record_counts.values())
    if event_counts.get("records_processed") != market_total:
        issues.add("summary.event_counts.records_processed does not match trace market records")
    for source, field_name in MARKET_RECORD_SOURCE_TO_SUMMARY_FIELD.items():
        if event_counts.get(field_name) != state.market_record_counts[source]:
            issues.add(f"summary.event_counts.{field_name} does not match trace {source} records")
    if event_counts.get("book_gap_count") != sum(state.book_gap_counts_by_symbol.values()):
        issues.add("summary.event_counts.book_gap_count does not match trace")
    if summary.get("book_gap_count_by_symbol") != state.book_gap_counts_by_symbol:
        issues.add("summary.book_gap_count_by_symbol does not match trace")


def _audit_input_summary(
    summary: Mapping[str, Any],
    input_counts: Mapping[str, int],
    records_processed: int,
    issues: _Issues,
) -> None:
    event_counts = summary.get("event_counts")
    if not isinstance(event_counts, dict):
        return
    if event_counts.get("records_processed") != records_processed:
        issues.add("summary.event_counts.records_processed does not match replay input")
    for field_name, observed in input_counts.items():
        if event_counts.get(field_name) != observed:
            issues.add(f"summary.event_counts.{field_name} does not match replay input")


def _audit_chain_contract(
    summary: Mapping[str, Any],
    fill_state: _FillAuditState,
    markout_state: _MarkoutAuditState,
    issues: _Issues,
) -> None:
    retention = summary.get("audit_retention")
    if not isinstance(retention, dict):
        return
    expected_fields = {
        "fill_audit_sha256": fill_state.chain.hexdigest,
        "markout_audit_sha256": markout_state.chain.hexdigest,
    }
    for field_name, expected in expected_fields.items():
        observed = retention.get(field_name)
        if not isinstance(observed, str) or len(observed) != 64:
            issues.add(f"summary.audit_retention.{field_name} must be a SHA-256 hex digest")
        elif observed != expected:
            issues.add(f"summary.audit_retention.{field_name} does not match the serialized audit chain")


def audit_streaming_bundle(pack_dir: Path, *, max_issues: int = 250) -> dict[str, Any]:
    """Audit one completed bounded-streaming run with duration-independent memory.

    The returned ``memory_contract`` describes the implementation mechanism;
    it is not a measured peak-memory claim.  Large files are streamed and the
    only exact growing sets live in a temporary SQLite database on disk.
    """

    pack_dir = pack_dir.resolve()
    issues = _Issues(limit=max(1, max_issues))
    _audit_bundle_boundaries(pack_dir, issues)
    summary = _load_json_object(_artifact_path(pack_dir, "summary"), issues)
    manifest = _load_json_object(_artifact_path(pack_dir, "manifest"), issues)
    if summary:
        _audit_streaming_contract(summary, manifest, issues)
        _audit_summary_csv(pack_dir, summary, issues)
    if summary and manifest:
        _audit_manifest_and_artifacts(pack_dir, summary, manifest, issues)

    with TemporaryDirectory(prefix="lob_sim_bundle_audit_") as temporary:
        index = _AuditIndex(Path(temporary) / "audit_index.sqlite3")
        try:
            fill_state = _scan_fill_audit(_artifact_path(pack_dir, "trades"), summary, index, issues)
            markout_state = _scan_markout_audit(_artifact_path(pack_dir, "markouts"), issues)
            index.commit()
            _, input_counts, records_processed = _scan_input(pack_dir, manifest, index, issues)
            trace_decode_issues = _Issues(limit=issues.limit)
            markout_decode_issues = _Issues(limit=issues.limit)
            trace_state = _scan_trace(pack_dir, trace_decode_issues, markout_decode_issues, issues)
            for message in trace_decode_issues.messages:
                issues.add(message)
            for message in markout_decode_issues.messages:
                issues.add(message)
            if trace_decode_issues.omitted:
                issues.add(f"trades.csv trace-correlation decoding omitted {trace_decode_issues.omitted} issue(s)")
            if markout_decode_issues.omitted:
                issues.add(f"markouts.csv trace-correlation decoding omitted {markout_decode_issues.omitted} issue(s)")
            unique_order_count = index.count_filled_orders()
        finally:
            index.close()

    if summary:
        _audit_fill_summary(pack_dir, summary, manifest, fill_state, unique_order_count, issues)
        _audit_markout_summary(summary, markout_state, issues)
        _audit_trace_summary(summary, trace_state, issues)
        _audit_input_summary(summary, input_counts, records_processed, issues)
        _audit_chain_contract(summary, fill_state, markout_state, issues)

    return {
        "schema_version": STREAMING_BUNDLE_AUDIT_SCHEMA_VERSION,
        "audit_mode": "bounded_streaming",
        "pack_dir": _display(pack_dir),
        "ok": issues.total == 0,
        "issues": issues.messages,
        "issue_count": issues.total,
        "issues_omitted": issues.omitted,
        "memory_contract": {
            "schema_version": "lob_sim.streaming_audit_memory.v1",
            "detail_rows_retained": 0,
            "csv_processing": "sequential_rows",
            "exact_set_storage": "temporary_on_disk_sqlite",
            "diagnostic_limit": issues.limit,
            "memory_bounded_by_tape_duration": True,
        },
        "counts": {
            "event_trace_rows": trace_state.row_count,
            "trade_rows": fill_state.row_count,
            "fill_rows": trace_state.fill_count,
            "markout_rows": markout_state.row_count,
            "queue_consumption_rows": trace_state.event_type_counts.get("queue_consumption", 0),
            "event_type_counts": dict(sorted(trace_state.event_type_counts.items())),
            "distinct_filled_orders": unique_order_count,
            "resolved_markouts": markout_state.resolved_count,
            "invalidated_markouts": markout_state.invalidated_count,
        },
        "hashes": {
            "fill_audit_sha256": fill_state.chain.hexdigest,
            "markout_audit_sha256": markout_state.chain.hexdigest,
            "artifact_bundle_sha256": (
                manifest.get("artifact_bundle", {}).get("sha256")
                if isinstance(manifest.get("artifact_bundle"), dict)
                else None
            ),
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
