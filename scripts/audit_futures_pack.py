from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lob_sim.replay.inspection import file_sha256


PACK_AUDIT_SCHEMA_VERSION = "lob_sim.futures_pack_audit.v1"
RUN_MANIFEST_SCHEMA_VERSION = "lob_sim.simulation_run.v2"
FILL_SOURCES = ("depth_update", "agg_trade", "taker_order")
PUBLIC_CONSUMPTION_SOURCES = ("depth_update", "agg_trade")
PUBLIC_CONSUMPTION_FIELDS = (
    "observed_lots",
    "modeled_lots",
    "overlap_netted_lots",
    "queue_consumed_lots",
    "unmatched_lots",
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
COMMITTED_FUTURES_PACKS = (
    Path("docs/sample_outputs/futures_replay_walkthrough"),
    Path("docs/sample_outputs/futures_recorded_clip_case"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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
            issues.append(f"order_lifecycle_counts.{key}={expected.get(key)!r} does not match trace value {lifecycle_counts[key]}")
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
            issues.append(f"public_consumption_summary.{total_field}={public_summary.get(total_field)!r} does not match trace value {totals[field]}")


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
                    issues.append(f"markout_by_fill_source.{source}.{field}={actual!r} does not match trace value {value}")
            elif actual != value:
                issues.append(f"markout_by_fill_source.{source}.{field}={actual!r} does not match trace value {value}")


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
    trade_rows = _read_csv_rows(trades_path, (), issues)

    if summary:
        _audit_manifest(pack_dir, summary, manifest, issues)

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
    lifecycle_counts = {key: 0 for key in ORDER_LIFECYCLE_KEYS}
    consumption = {
        source: {field: 0 for field in PUBLIC_CONSUMPTION_FIELDS}
        for source in PUBLIC_CONSUMPTION_SOURCES
    }
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
        if event_type == "order_arrival_scheduled":
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
                    issues.append(f"{_display_path(trace_path)}:{row_number} has invalid queue_ahead_lots_after_arrival")
            if isinstance(details.get("immediate_fills"), int) and details["immediate_fills"] > 0:
                lifecycle_counts["immediate_fill_arrivals"] += 1
            if details.get("resting_after_arrival") is False and isinstance(details.get("remaining_lots_after_arrival"), int) and details["remaining_lots_after_arrival"] > 0:
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
        elif event_type == "queue_consumption":
            source = row.get("source", "")
            if source not in PUBLIC_CONSUMPTION_SOURCES:
                issues.append(f"{_display_path(trace_path)}:{row_number} has invalid queue_consumption source {source!r}")
                continue
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
                    issues.append(f"{_display_path(trace_path)}:{row_number} queue_consumption models more lots than observed")
                if parsed["overlap_netted_lots"] != parsed["observed_lots"] - parsed["modeled_lots"]:
                    issues.append(f"{_display_path(trace_path)}:{row_number} queue_consumption netted lots are inconsistent")
                if parsed["queue_consumed_lots"] > parsed["modeled_lots"]:
                    issues.append(f"{_display_path(trace_path)}:{row_number} queue_consumption consumes more queue than modeled")
                if parsed["unmatched_lots"] != parsed["modeled_lots"] - parsed["queue_consumed_lots"]:
                    issues.append(f"{_display_path(trace_path)}:{row_number} queue_consumption unmatched lots are inconsistent")
        elif event_type == "markout":
            source = row.get("fill_source", "")
            qty = _decimal(details.get("qty"))
            markout = _decimal(details.get("markout"))
            if source not in FILL_SOURCES or qty is None or markout is None:
                issues.append(f"{_display_path(trace_path)}:{row_number} markout row has invalid source, qty, or markout")
                continue
            markouts[source]["samples"] = int(markouts[source]["samples"]) + 1
            markouts[source]["qty"] = markouts[source]["qty"] + qty
            markouts[source]["markout_sum"] = markouts[source]["markout_sum"] + markout * qty
            if details.get("adverse") is True:
                markouts[source]["adverse_samples"] = int(markouts[source]["adverse_samples"]) + 1
            markout_row_count += 1

    if isinstance(expected_fill_count, int) and len(fill_rows) != expected_fill_count:
        issues.append(f"event_trace.csv has {len(fill_rows)} fill row(s), summary expected {expected_fill_count}")
    if summary.get("fill_source_counts") != fill_source_counts:
        issues.append(f"summary.fill_source_counts={summary.get('fill_source_counts')!r} does not match trace value {fill_source_counts}")

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
        "issues": [
            f"{result['pack_dir']}: {issue}"
            for result in pack_results
            for issue in result["issues"]
        ],
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
