"""Frozen quote-lifetime attribution using the simulator's existing fill/markout path.

This audit observes execution; it never infers a fill or values a position.
Rows are streamed and aggregates have fixed model-state/horizon cardinality.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterator, Mapping
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..sim.sinks import EventSink, NullSink
from ..record.envelope import ValidityState
from .validation import canonical_json, finite, integer, probabilities, require_keys, strict_json
from .quotes import RegimeQuoteAudit

PHASES = ("decision", "arrival", "pre_fill")
STAGE_FIELDS = (
    "logical_ns",
    "model_sha256",
    "status",
    "sample_ns",
    "available_at_ns",
    "receive_seq",
    "epochs",
    "raw_map_state",
    "active_state",
    "posterior",
    "confidence",
    "normalized_entropy",
    "filter_reset_reason",
    "validity",
)
EXECUTION_FIELDS = (
    "schema_version",
    "event_type",
    "fill_id",
    "symbol",
    "logical_ns",
    "order_id",
    "side",
    "qty",
    "qty_lots",
    "maker",
    "fill_source",
    "queue_ahead_lots",
    "queue_trajectory",
    "scenario_id",
    "evidence_ids",
    "validity",
    "latency_draws_ms",
    "order_state_at_fill",
    "decision",
    "arrival",
    "pre_fill",
    "decision_label",
    "arrival_label",
    "pre_fill_label",
    "spread_capture_value",
    "fee",
    "time_in_book_ms",
    "fill_ts_local",
    "horizon_ms",
    "status",
    "markout",
    "deadline_ts",
    "observed_ts",
    "actual_lag_seconds",
    "invalid_reason",
    "quote",
)
CHAIN_DOMAIN = b"lob_sim.hmm_execution.v3"
FILL_SOURCE_GROUPS = ("depth_update", "agg_trade", "taker_order", "OTHER")
QUEUE_FIELDS = (
    "queue_ahead_before_trigger_lots",
    "queue_ahead_at_fill_lots",
    "queue_consumed_before_fill_lots",
    "public_consumption_trigger_lots",
    "fill_lots",
    "remaining_order_lots_after_fill",
    "visible_level_before_lots",
    "visible_level_after_lots",
)


def _source(value: object) -> str:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("execution fill source must be a nonempty string or unavailable")
    return value if value in FILL_SOURCE_GROUPS else "OTHER"


def _queue(data: Mapping[str, Any], lots: int) -> tuple[int | None, dict[str, int] | None]:
    ahead = data.get("queue_ahead_lots")
    if ahead is not None:
        integer(ahead, "modeled queue ahead")
    trajectory = data.get("queue_trajectory")
    if trajectory is None:
        return ahead, None
    if not isinstance(trajectory, Mapping) or not set(trajectory) <= set(QUEUE_FIELDS):
        raise ValueError("execution queue trajectory has unknown fields")
    parsed = {key: integer(value, key) for key, value in trajectory.items()}
    if "fill_lots" in parsed and parsed["fill_lots"] != lots:
        raise ValueError("queue trajectory fill quantity mismatch")
    if "queue_ahead_at_fill_lots" in parsed and parsed["queue_ahead_at_fill_lots"] != ahead:
        raise ValueError("queue trajectory ahead quantity mismatch")
    before, at_fill, consumed = (parsed.get(key) for key in QUEUE_FIELDS[:3])
    if before is not None and at_fill is not None and consumed is not None and before - at_fill != consumed:
        raise ValueError("queue trajectory depletion inconsistent")
    visible_before, visible_after = parsed.get("visible_level_before_lots"), parsed.get("visible_level_after_lots")
    if visible_before is not None and visible_after is not None and visible_before - visible_after != lots:
        raise ValueError("taker visible-level depletion inconsistent")
    return ahead, parsed


def capture_stage(signal: Mapping[str, Any], logical_ns: int) -> dict[str, Any]:
    """Compact independent information set, rejecting future availability."""
    integer(logical_ns, "attribution logical time")
    available = signal.get("available_at_ns")
    if available is not None and integer(available, "signal availability") > logical_ns:
        raise ValueError("future regime unavailable at attribution time")
    stage = {name: deepcopy(signal.get(name)) for name in STAGE_FIELDS}
    stage["logical_ns"] = logical_ns
    if stage["status"] != "VALID":
        for name in ("posterior", "raw_map_state", "active_state", "confidence", "normalized_entropy"):
            stage[name] = None
    return stage


def _decimal(value: object, name: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} is not decimal") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise ValueError(f"{name} must be finite{' and nonnegative' if nonnegative else ''}")
    return parsed


def _advance(digest: str, row: Mapping[str, Any]) -> str:
    return sha256(bytes.fromhex(digest) + canonical_json(dict(row)).encode("utf-8")).hexdigest()


def _parse_execution_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One serialization contract shared by verification and offline consumers."""
    if set(row) != set(EXECUTION_FIELDS) or any(value is None for value in row.values()):
        raise ValueError("malformed execution trace row")
    json_fields = {
        "decision",
        "arrival",
        "pre_fill",
        "evidence_ids",
        "validity",
        "latency_draws_ms",
        "quote",
        "queue_trajectory",
    }
    int_fields = {"logical_ns", "fill_id", "qty_lots", "horizon_ms", "queue_ahead_lots"}
    float_fields = {"time_in_book_ms", "fill_ts_local", "deadline_ts", "observed_ts", "actual_lag_seconds"}
    parsed: dict[str, Any] = {}
    for key, value in row.items():
        if value == "":
            parsed[key] = None
        elif key in json_fields:
            parsed[key] = strict_json(value)
        elif key in int_fields:
            parsed[key] = integer(int(value), key)
        elif key in float_fields:
            parsed[key] = finite(float(value), key)
        elif key == "maker":
            if value not in {"True", "False"}:
                raise ValueError("invalid execution maker boolean")
            parsed[key] = value == "True"
        else:
            parsed[key] = value
    return parsed


def iter_execution_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Parse without retaining history; callers must verify semantics and chain."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(EXECUTION_FIELDS):
            raise ValueError("execution trace field contract mismatch")
        for row in reader:
            yield _parse_execution_row(row)


def _blank_horizon() -> dict[str, Any]:
    return {
        "resolved_samples": 0,
        "invalidated_samples": 0,
        "adverse_samples": 0,
        "qty": "0",
        "weighted_markout_sum": "0",
        "actual_lag_sum": "0",
        "actual_lag_max": "0",
    }


def _blank_cell(horizons: tuple[int, ...]) -> dict[str, Any]:
    return {
        "fill_count": 0,
        "qty_lots": 0,
        "qty": "0",
        "fee": "0",
        "spread_capture_value": "0",
        "marked_qty": "0",
        "marked_fill_count": 0,
        "pending_cancel_fill_count": 0,
        "wait_ms_sum": "0",
        "queue_ahead_samples": 0,
        "queue_ahead_lots_sum": 0,
        "queue_trajectory_samples": 0,
        "queue_trajectory_lots": {key: 0 for key in QUEUE_FIELDS},
        "queue_trajectory_field_samples": {key: 0 for key in QUEUE_FIELDS},
        "markouts": {str(horizon): _blank_horizon() for horizon in horizons},
    }


def _summarize_cells(groups: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(groups))
    for cell in result.values():
        fills = cell["fill_count"]
        cell["mean_time_in_book_ms"] = float(Decimal(cell["wait_ms_sum"]) / fills) if fills else None
        cell["spread_capture_coverage"] = cell["marked_fill_count"] / fills if fills else None
        marked_qty = Decimal(cell["marked_qty"])
        cell["mean_marked_spread_capture"] = (
            float(Decimal(cell["spread_capture_value"]) / marked_qty) if marked_qty else None
        )
        queue_samples = cell["queue_ahead_samples"]
        cell["queue_ahead_coverage"] = queue_samples / fills if fills else None
        cell["mean_modeled_queue_ahead_lots"] = cell["queue_ahead_lots_sum"] / queue_samples if queue_samples else None
        cell["queue_trajectory_coverage"] = cell["queue_trajectory_samples"] / fills if fills else None
        for stats in cell["markouts"].values():
            resolved = stats["resolved_samples"]
            stats["unresolved_samples"] = fills - resolved - stats["invalidated_samples"]
            stats["coverage"] = resolved / fills if fills else None
            stats["mean_signed_markout"] = (
                float(Decimal(stats["weighted_markout_sum"]) / Decimal(stats["qty"])) if Decimal(stats["qty"]) else None
            )
            stats["adverse_rate"] = stats["adverse_samples"] / resolved if resolved else None
            stats["mean_actual_lag_seconds"] = float(Decimal(stats["actual_lag_sum"]) / resolved) if resolved else None
    return result


def _validate_cell(cell: object, horizons: tuple[int, ...]) -> Mapping[str, Any]:
    data = require_keys(cell, set(_blank_cell(horizons)), "execution cell")
    count = integer(data["fill_count"], "cell fill count")
    lots = integer(data["qty_lots"], "cell lots")
    for key in ("marked_fill_count", "pending_cancel_fill_count", "queue_ahead_samples", "queue_trajectory_samples"):
        if integer(data[key], key) > count:
            raise ValueError("execution cell count exceeds fills")
    integer(data["queue_ahead_lots_sum"], "queue ahead sum")
    trajectory = require_keys(data["queue_trajectory_lots"], set(QUEUE_FIELDS), "queue trajectory totals")
    field_samples = require_keys(data["queue_trajectory_field_samples"], set(QUEUE_FIELDS), "queue field coverage")
    for key, value in field_samples.items():
        if integer(value, "queue field samples") > data["queue_trajectory_samples"] or (value == 0 and trajectory[key]):
            raise ValueError("execution queue field coverage inconsistent")
    for value in trajectory.values():
        integer(value, "queue trajectory lots")
    if (data["queue_ahead_samples"] == 0 and data["queue_ahead_lots_sum"]) or (
        data["queue_trajectory_samples"] == 0 and any(trajectory.values())
    ):
        raise ValueError("execution queue totals lack observations")
    for key in ("qty", "fee", "spread_capture_value", "wait_ms_sum", "marked_qty"):
        _decimal(data[key], key, nonnegative=key in {"qty", "wait_ms_sum", "marked_qty"})
    if (count == 0) != (lots == 0) or (count == 0) != (Decimal(data["qty"]) == 0) or lots < count:
        raise ValueError("execution fill count/quantity inconsistent")
    if Decimal(data["marked_qty"]) > Decimal(data["qty"]) or (
        data["marked_fill_count"] == 0 and (Decimal(data["marked_qty"]) or Decimal(data["spread_capture_value"]))
    ):
        raise ValueError("marked quantity exceeds fill quantity or lacks observations")
    groups = require_keys(data["markouts"], {str(h) for h in horizons}, "execution horizons")
    for stats in groups.values():
        require_keys(stats, set(_blank_horizon()), "execution horizon stats")
        resolved = integer(stats["resolved_samples"], "resolved samples")
        invalidated = integer(stats["invalidated_samples"], "invalidated samples")
        if resolved + invalidated > count or integer(stats["adverse_samples"], "adverse samples") > resolved:
            raise ValueError("execution horizon samples inconsistent")
        for key in ("qty", "weighted_markout_sum", "actual_lag_sum", "actual_lag_max"):
            _decimal(stats[key], key, nonnegative=key != "weighted_markout_sum")
        if (
            Decimal(stats["qty"]) > Decimal(data["qty"])
            or (resolved == 0) != (Decimal(stats["qty"]) == 0)
            or Decimal(stats["actual_lag_max"]) > Decimal(stats["actual_lag_sum"])
            or (resolved == 0 and any(Decimal(stats[key]) for key in ("weighted_markout_sum", "actual_lag_sum")))
        ):
            raise ValueError("execution horizon quantities/lags inconsistent")
    return data


def _merged_cells(cells: list[Mapping[str, Any]], horizons: tuple[int, ...]) -> dict[str, Any]:
    """Numeric marginal identity; Decimal representation alone is not a mismatch."""
    result = _blank_cell(horizons)
    decimal_keys = {"qty", "fee", "spread_capture_value", "wait_ms_sum", "marked_qty"}
    for key in result:
        if key in decimal_keys:
            result[key] = sum((Decimal(cell[key]) for cell in cells), Decimal(0))
        elif key in {"queue_trajectory_lots", "queue_trajectory_field_samples"}:
            result[key] = {field: sum(cell[key][field] for cell in cells) for field in QUEUE_FIELDS}
        elif key == "markouts":
            for horizon, stats in result[key].items():
                for field in stats:
                    values = [cell[key][horizon][field] for cell in cells]
                    if field in {"qty", "weighted_markout_sum", "actual_lag_sum", "actual_lag_max"}:
                        decimals = [Decimal(value) for value in values]
                        stats[field] = (
                            max(decimals, default=Decimal(0))
                            if field == "actual_lag_max"
                            else sum(decimals, Decimal(0))
                        )
                    else:
                        stats[field] = sum(values)
        else:
            result[key] = sum(cell[key] for cell in cells)
    return result


def _validate_summary_cell(cell: object, horizons: tuple[int, ...]) -> None:
    """Reject boolean counters/rates even where Python equality says True == 1."""
    blank = _blank_cell(horizons)
    template = _summarize_cells({"cell": blank})["cell"]
    data = require_keys(cell, set(template), "execution summary cell")
    raw = {key: data[key] for key in blank}
    groups = require_keys(data["markouts"], {str(h) for h in horizons}, "summary horizons")
    raw["markouts"] = {}
    for horizon, stats in groups.items():
        require_keys(stats, set(template["markouts"][horizon]), "summary horizon statistics")
        raw["markouts"][horizon] = {key: stats[key] for key in _blank_horizon()}
        integer(stats["unresolved_samples"], "unresolved samples")
        for key in ("coverage", "mean_signed_markout", "adverse_rate", "mean_actual_lag_seconds"):
            if stats[key] is not None:
                finite(stats[key], key)
    for key in set(template) - set(blank):
        if data[key] is not None:
            finite(data[key], key)
    _validate_cell(raw, horizons)


class RegimeExecutionAudit:
    """One symbol/model, bounded live-order snapshots and constant-size aggregates."""

    def __init__(
        self,
        model_sha256: str,
        symbol: str,
        state_count: int,
        horizons: tuple[int, ...],
        max_orders: int,
        sink: EventSink | None = None,
        quote_sink: EventSink | None = None,
        max_pending_markouts: int = 100_000,
    ) -> None:
        if len(model_sha256) != 64 or any(char not in "0123456789abcdef" for char in model_sha256):
            raise ValueError("execution model SHA-256 invalid")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("execution symbol invalid")
        integer(state_count, "state count", minimum=2)
        if state_count > 5:
            raise ValueError("execution audit supports K=2..5")
        integer(max_orders, "order context cap", minimum=1)
        integer(max_pending_markouts, "pending markout bound", minimum=1)
        if len(set(horizons)) != len(horizons):
            raise ValueError("duplicate execution horizons")
        for horizon in horizons:
            integer(horizon, "execution horizon", minimum=1)
        self.model_sha256, self.symbol, self.state_count = model_sha256, symbol, state_count
        self.horizons, self.max_orders = tuple(sorted(horizons)), max_orders
        self.max_pending_markouts = max_pending_markouts
        self.sink = sink if sink is not None else NullSink()
        self.labels = tuple(f"STATE_{index}" for index in range(state_count)) + ("UNCONFIRMED", "UNAVAILABLE")
        self.quotes = RegimeQuoteAudit(symbol, self.labels, max_orders, quote_sink)
        self._orders: dict[str, dict[str, Any]] = {}
        self._conditioned = {phase: {label: _blank_cell(self.horizons) for label in self.labels} for phase in PHASES}
        self._by_source = {
            phase: {
                label: {source: _blank_cell(self.horizons) for source in FILL_SOURCE_GROUPS} for label in self.labels
            }
            for phase in PHASES
        }
        self._transitions = {label: {other: 0 for other in self.labels} for label in self.labels}
        self._fill_count = self._last_fill_id = self._trace_count = 0
        self._trace_sha256 = sha256(CHAIN_DOMAIN).hexdigest()

    def _stage(self, value: object) -> dict[str, Any] | None:
        if value is None:
            return None  # Missing creation/arrival provenance is explicit, never backfilled.
        stage = dict(require_keys(value, set(STAGE_FIELDS), "regime attribution stage"))
        if stage["model_sha256"] != self.model_sha256:
            raise ValueError("attribution model identity mismatch")
        logical = integer(stage["logical_ns"], "stage time")
        if not isinstance(stage["status"], str) or not stage["status"]:
            raise ValueError("attribution status missing")
        for key in ("sample_ns", "available_at_ns", "receive_seq"):
            if stage[key] is not None:
                integer(stage[key], key)
        if stage["available_at_ns"] is not None and stage["available_at_ns"] > logical:
            raise ValueError("future attribution availability")
        if stage["sample_ns"] is not None and (
            stage["available_at_ns"] is None or stage["sample_ns"] > stage["available_at_ns"]
        ):
            raise ValueError("attribution sample/availability inconsistent")
        if stage["epochs"] is not None:
            if not isinstance(stage["epochs"], list) or len(stage["epochs"]) != 3:
                raise ValueError("attribution epoch dimension mismatch")
            for epoch in stage["epochs"]:
                integer(epoch, "attribution epoch")
        validity = stage["validity"]
        if validity is not None:
            require_keys(validity, set(ValidityState().as_dict()), "attribution validity")
            if any(type(validity[key]) is not bool for key in validity if key != "reason"):
                raise ValueError("attribution validity must contain booleans")
            if validity["reason"] is not None and not isinstance(validity["reason"], str):
                raise ValueError("attribution validity reason invalid")
            dimensions = ValidityState(**{key: value for key, value in validity.items() if key != "execution_valid"})
            if dimensions.execution_valid != validity["execution_valid"]:
                raise ValueError("attribution validity intersection inconsistent")
            if stage["status"] == "VALID" and not dimensions.execution_valid:
                raise ValueError("valid attribution has invalid information set")
        if stage["filter_reset_reason"] is not None and not isinstance(stage["filter_reset_reason"], str):
            raise ValueError("attribution reset reason invalid")
        for name in ("raw_map_state", "active_state"):
            state = stage[name]
            if state is not None and integer(state, name) >= self.state_count:
                raise ValueError("attribution state out of range")
        if stage["status"] == "VALID":
            probabilities(stage["posterior"], "attribution posterior", self.state_count)
            confidence = finite(stage["confidence"], "attribution confidence")
            entropy = finite(stage["normalized_entropy"], "attribution entropy")
            if not 0 <= entropy <= 1 or abs(confidence - max(stage["posterior"])) > 1e-10:
                raise ValueError("attribution confidence/entropy inconsistent")
            expected_entropy = -math.fsum(p * math.log(p) for p in stage["posterior"] if p) / math.log(self.state_count)
            if not math.isclose(entropy, expected_entropy, abs_tol=1e-10):
                raise ValueError("attribution normalized entropy inconsistent")
            if stage["raw_map_state"] != max(range(self.state_count), key=lambda index: stage["posterior"][index]):
                raise ValueError("attribution MAP state inconsistent")
            if stage["sample_ns"] is None or stage["epochs"] is None:
                raise ValueError("valid attribution lacks causal anchors")
        elif any(
            stage[key] is not None
            for key in ("posterior", "raw_map_state", "active_state", "confidence", "normalized_entropy")
        ):
            raise ValueError("invalid attribution retains inference")
        return deepcopy(stage)

    def _label(self, stage: Mapping[str, Any] | None) -> str:
        if stage is None or stage["status"] != "VALID":
            return "UNAVAILABLE"
        state = stage["active_state"]
        return f"STATE_{state}" if state is not None else "UNCONFIRMED"

    def schedule_quote(self, decision: object, side: str, quote_slot: str, qty_lots: int) -> int:
        stage = self._stage(decision)
        if stage is None:
            raise ValueError("scheduled quote requires a decision information set")
        return self.quotes.schedule(self._label(stage), stage["logical_ns"], side, quote_slot, qty_lots)

    def reject_quote(self, request_id: int, decision: object, arrival: object, reason: str) -> None:
        self._validate_request(request_id, decision)
        stage = self._stage(arrival)
        if stage is None:
            raise ValueError("quote rejection requires arrival information set")
        self.quotes.arrive(request_id, self._label(stage), stage["logical_ns"], reason=reason)

    def _validate_request(self, request_id: int, decision: object) -> None:
        stage = self._stage(decision)
        context = self.quotes._pending.get(integer(request_id, "quote request", minimum=1))
        if (
            stage is None
            or context is None
            or context["decision_label"] != self._label(stage)
            or context["decision_ns"] != stage["logical_ns"]
        ):
            raise ValueError("quote request decision identity mismatch")

    def accept_order(self, order_id: str, decision: object, arrival: object, request_id: int | None = None) -> None:
        if order_id in self._orders or len(self._orders) >= self.max_orders:
            raise ValueError("regime order context capacity or duplicate identity")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("regime order identity invalid")
        creation, accepted = self._stage(decision), self._stage(arrival)
        if accepted is None or (creation is not None and creation["logical_ns"] > accepted["logical_ns"]):
            raise ValueError("regime order arrival precedes decision")
        if request_id is not None:
            self._validate_request(request_id, creation)
            self.quotes.arrive(request_id, self._label(accepted), accepted["logical_ns"], order_id=order_id)
        self._orders[order_id] = {"decision": creation, "arrival": accepted}

    def release_order(self, order_id: str, logical_ns: int | None = None, reason: str = "cancelled") -> None:
        context = self._orders.get(order_id)
        if logical_ns is None and context is not None:
            logical_ns = context["arrival"]["logical_ns"]
        if logical_ns is not None:
            self.quotes.terminate(order_id, logical_ns, reason)
        self._orders.pop(order_id, None)

    def clear_orders(
        self, logical_ns: int = 0, reason: str = "epoch_invalidated", *, discard_pending: bool = True
    ) -> None:
        self.quotes.clear(logical_ns, reason, discard_pending=discard_pending)
        self._orders.clear()

    def order_age_ns(self, order_id: str, logical_ns: int) -> int:
        """Age from immutable integer acceptance time, not projected float seconds."""
        integer(logical_ns, "quote age observation time")
        context = self._orders.get(order_id)
        if context is None or context["arrival"] is None:
            raise ValueError("quote age requires an attributed accepted order")
        age = logical_ns - context["arrival"]["logical_ns"]
        if age < 0:
            raise ValueError("quote age cannot precede acceptance")
        return age

    def at_fill(self, order_id: str | None, pre_fill: object, qty_lots: int | None = None) -> dict[str, Any]:
        context = self._orders.get(order_id or "", {"decision": None, "arrival": None})
        before = self._stage(pre_fill)
        if before is None:
            raise ValueError("pre-fill information set required")
        if context["arrival"] is not None and context["arrival"]["logical_ns"] > before["logical_ns"]:
            raise ValueError("fill precedes attributed acceptance")
        quote = self.quotes.freeze_fill(order_id, qty_lots) if qty_lots is not None else None
        return {**deepcopy(context), "pre_fill": before, "logical_ns": before["logical_ns"], "quote": quote}

    def _write(self, row: Mapping[str, Any]) -> None:
        normalized = {key: row.get(key) for key in EXECUTION_FIELDS}
        digest = _advance(self._trace_sha256, normalized)
        # No canonical hash/validation work is omitted on the null path.
        if type(self.sink) is not NullSink:
            self.sink.write(deepcopy(normalized))
        self._trace_sha256, self._trace_count = digest, self._trace_count + 1

    def _row(self, data: Mapping[str, Any], attribution: Mapping[str, Any]) -> dict[str, Any]:
        row = {key: data.get(key) for key in EXECUTION_FIELDS}
        row.update({"schema_version": "lob_sim.hmm_execution.v3", **deepcopy(dict(attribution))})
        row["fill_ts_local"] = data.get("ts_local")
        row.update({f"{phase}_label": self._label(attribution[phase]) for phase in PHASES})
        return row

    def on_fill(self, fill_id: int, fill: Mapping[str, Any], attribution: Mapping[str, Any]) -> dict[str, Any]:
        integer(fill_id, "fill ordinal", minimum=1)
        if fill_id <= self._last_fill_id:
            raise ValueError("fill ordinal must increase")
        if fill["symbol"] != self.symbol:
            raise ValueError("attribution fill symbol mismatch")
        frozen: dict[str, Any] = {phase: self._stage(attribution[phase]) for phase in PHASES}
        frozen["quote"] = self.quotes.validate_fill(attribution.get("quote"))
        _source(fill.get("fill_source"))
        frozen["fill_source"] = fill.get("fill_source")
        frozen.update({"logical_ns": integer(attribution["logical_ns"], "fill logical time"), "fill_id": fill_id})
        for phase in PHASES:
            if frozen[phase] is not None and frozen[phase]["logical_ns"] > frozen["logical_ns"]:
                raise ValueError("future information set at fill")
        quantity = _decimal(fill["qty"], "fill quantity", nonnegative=True)
        fee = _decimal(fill["fee"], "fill fee")
        spread = (
            None if fill["spread_capture_value"] is None else _decimal(fill["spread_capture_value"], "spread capture")
        )
        lots = integer(fill["qty_lots"], "fill lots", minimum=1)
        ahead, trajectory = _queue(fill, lots)
        wait = Decimal(str(finite(fill["time_in_book_ms"], "time in book")))
        if quantity <= 0 or wait < 0:
            raise ValueError("nonpositive quantity or negative quote age")
        self._write({**self._row(fill, frozen), "event_type": "fill", "status": "filled"})
        self.quotes.on_fill(
            fill_id,
            frozen["quote"],
            frozen["logical_ns"],
            fill.get("order_id"),
            fill["side"],
            lots,
            self._label(frozen["decision"]),
            self._label(frozen["arrival"]),
        )
        for phase in PHASES:
            label = self._label(frozen[phase])
            for cell in (
                self._conditioned[phase][label],
                self._by_source[phase][label][_source(frozen["fill_source"])],
            ):
                cell["fill_count"] += 1
                cell["qty_lots"] += lots
                cell["qty"] = str(Decimal(cell["qty"]) + quantity)
                cell["fee"] = str(Decimal(cell["fee"]) + fee)
                cell["wait_ms_sum"] = str(Decimal(cell["wait_ms_sum"]) + wait)
                cell["pending_cancel_fill_count"] += int(fill.get("order_state_at_fill") == "pending_cancel")
                if ahead is not None:
                    cell["queue_ahead_samples"] += 1
                    cell["queue_ahead_lots_sum"] += ahead
                if trajectory:
                    cell["queue_trajectory_samples"] += 1
                    for key, value in trajectory.items():
                        cell["queue_trajectory_lots"][key] += value
                        cell["queue_trajectory_field_samples"][key] += 1
                if spread is not None:
                    cell["marked_fill_count"] += 1
                    cell["spread_capture_value"] = str(Decimal(cell["spread_capture_value"]) + spread)
                    cell["marked_qty"] = str(Decimal(cell["marked_qty"]) + quantity)
        self._transitions[self._label(frozen["decision"])][self._label(frozen["pre_fill"])] += 1
        self._fill_count += 1
        self._last_fill_id = fill_id
        return deepcopy(frozen)

    def on_markout(
        self,
        entry: Mapping[str, Any],
        horizon_ms: int,
        *,
        markout: Decimal | None,
        observed_ts: float,
        invalid_reason: str | None = None,
    ) -> None:
        attribution = entry.get("hmm_attribution")
        if attribution is None:
            return
        _source(attribution.get("fill_source"))
        if entry.get("fill_source") != attribution.get("fill_source"):
            raise ValueError("markout source differs from frozen fill source")
        if horizon_ms not in self.horizons:
            raise ValueError("unexpected attribution horizon")
        resolved = markout is not None
        if resolved == (invalid_reason is not None) or (markout is not None and not markout.is_finite()):
            raise ValueError("markout resolution/invalidation inconsistent")
        lag = Decimal(str(finite(observed_ts, "markout observation"))) - Decimal(str(entry["ts_local"]))
        quantity = _decimal(entry["qty"], "markout quantity", nonnegative=True)
        if lag < 0 or (resolved and observed_ts < entry["deadline_ts"]):
            raise ValueError("markout resolution precedes deadline")
        self._write(
            {
                **self._row(entry, attribution),
                "event_type": "markout",
                "horizon_ms": horizon_ms,
                "markout": str(markout) if resolved else None,
                "observed_ts": observed_ts,
                "actual_lag_seconds": float(lag) if resolved else None,
                "status": "resolved" if resolved else "invalidated",
                "invalid_reason": invalid_reason,
            }
        )
        for phase in PHASES:
            label = self._label(attribution[phase])
            for cell in (
                self._conditioned[phase][label],
                self._by_source[phase][label][_source(attribution.get("fill_source"))],
            ):
                stats = cell["markouts"][str(horizon_ms)]
                if markout is not None:
                    stats["resolved_samples"] += 1
                    stats["adverse_samples"] += int(markout < 0)
                    stats["qty"] = str(Decimal(stats["qty"]) + quantity)
                    stats["weighted_markout_sum"] = str(Decimal(stats["weighted_markout_sum"]) + markout * quantity)
                    stats["actual_lag_sum"] = str(Decimal(stats["actual_lag_sum"]) + lag)
                    stats["actual_lag_max"] = str(max(Decimal(stats["actual_lag_max"]), lag))
                else:
                    stats["invalidated_samples"] += 1

    def summary(self) -> dict[str, Any]:
        conditioned = {phase: _summarize_cells(groups) for phase, groups in self._conditioned.items()}
        return {
            "schema_version": "lob_sim.hmm_execution_summary.v3",
            "model_sha256": self.model_sha256,
            "symbol": self.symbol,
            "fill_count": self._fill_count,
            "last_fill_id": self._last_fill_id,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "conditioned": conditioned,
            "conditioned_by_source": {
                phase: {label: _summarize_cells(sources) for label, sources in groups.items()}
                for phase, groups in self._by_source.items()
            },
            "horizons_ms": list(self.horizons),
            "max_pending_markouts": self.max_pending_markouts,
            "source_groups": list(FILL_SOURCE_GROUPS),
            "source_denominator_note": "fill-event populations, not quote acceptance or lifetime fill probabilities",
            "queue_note": "modeled visible/synthetic queue trajectory, never private participant FIFO",
            "quote_lifecycles": self.quotes.summary(),
            "fill_transition_counts": deepcopy(self._transitions),
            "retained_order_contexts": len(self._orders),
            "max_order_contexts": self.max_orders,
            "memory_bounded_by_tape_duration": self.sink.memory_bounded and self.quotes.sink.memory_bounded,
            "claim_ready": False,
            "claim_reason": "descriptive execution scenarios, not predictive or economic evidence",
        }

    def checkpoint(self) -> dict[str, Any]:
        return deepcopy(
            {
                "schema_version": "lob_sim.hmm_execution_checkpoint.v3",
                "model_sha256": self.model_sha256,
                "symbol": self.symbol,
                "state_count": self.state_count,
                "horizons": list(self.horizons),
                "max_orders": self.max_orders,
                "max_pending_markouts": self.max_pending_markouts,
                "orders": self._orders,
                "conditioned": self._conditioned,
                "by_source": self._by_source,
                "transitions": self._transitions,
                "fill_count": self._fill_count,
                "last_fill_id": self._last_fill_id,
                "trace_count": self._trace_count,
                "trace_sha256": self._trace_sha256,
                "quotes": self.quotes.checkpoint(),
            }
        )

    def validated_copy(self, checkpoint: object) -> RegimeExecutionAudit:
        data = require_keys(checkpoint, set(self.checkpoint()), "execution checkpoint")
        for key in (
            "schema_version",
            "model_sha256",
            "symbol",
            "state_count",
            "horizons",
            "max_orders",
            "max_pending_markouts",
        ):
            if data[key] != self.checkpoint()[key]:
                raise ValueError("execution checkpoint configuration mismatch")
        integer(data["state_count"], "execution state count", minimum=2)
        integer(data["max_orders"], "execution order bound", minimum=1)
        integer(data["max_pending_markouts"], "pending markout bound", minimum=1)
        for horizon in data["horizons"]:
            integer(horizon, "execution horizon", minimum=1)
        orders = data["orders"]
        if not isinstance(orders, Mapping) or len(orders) > self.max_orders:
            raise ValueError("execution checkpoint order capacity")
        candidate = RegimeExecutionAudit(
            self.model_sha256,
            self.symbol,
            self.state_count,
            self.horizons,
            self.max_orders,
            self.sink,
            self.quotes.sink,
            self.max_pending_markouts,
        )
        candidate.quotes = self.quotes.validated_copy(data["quotes"])
        for order_id, context in orders.items():
            require_keys(context, {"decision", "arrival"}, "order attribution")
            candidate.accept_order(order_id, context["decision"], context["arrival"])
        fills = integer(data["fill_count"], "attributed fills")
        last = integer(data["last_fill_id"], "last fill ordinal")
        if (fills == 0) != (last == 0) or last < fills:
            raise ValueError("execution checkpoint fill ordinal inconsistent")
        conditioned = require_keys(data["conditioned"], set(PHASES), "conditioned execution")
        expected_trace = fills
        totals = []
        for phase in PHASES:
            groups = require_keys(conditioned[phase], set(self.labels), "execution state groups")
            resolved_total = 0
            for label, cell in groups.items():
                _validate_cell(cell, self.horizons)
                for stats in cell["markouts"].values():
                    resolved_total += stats["resolved_samples"] + stats["invalidated_samples"]
            if sum(cell["fill_count"] for cell in groups.values()) != fills:
                raise ValueError("execution group total inconsistent")
            totals.append(resolved_total)
        if len(set(totals)) != 1:
            raise ValueError("execution horizon totals disagree")
        source_groups = require_keys(data["by_source"], set(PHASES), "execution source phases")
        for phase in PHASES:
            require_keys(source_groups[phase], set(self.labels), "execution source state labels")
            for label in self.labels:
                sources = require_keys(source_groups[phase][label], set(FILL_SOURCE_GROUPS), "execution fill sources")
                for cell in sources.values():
                    _validate_cell(cell, self.horizons)
                if _merged_cells(list(sources.values()), self.horizons) != _merged_cells(
                    [conditioned[phase][label]], self.horizons
                ):
                    raise ValueError("execution source marginal differs from state totals")
        for phase in PHASES[1:]:
            if _merged_cells(list(conditioned[phase].values()), self.horizons) != _merged_cells(
                list(conditioned[PHASES[0]].values()), self.horizons
            ):
                raise ValueError("execution phase marginals disagree")
            for source in FILL_SOURCE_GROUPS:
                if _merged_cells(
                    [source_groups[phase][label][source] for label in self.labels], self.horizons
                ) != _merged_cells([source_groups[PHASES[0]][label][source] for label in self.labels], self.horizons):
                    raise ValueError("execution source phase marginals disagree")
        expected_trace += totals[0]
        transitions = require_keys(data["transitions"], set(self.labels), "fill transitions")
        transition_total = 0
        for origin, row in transitions.items():
            require_keys(row, set(self.labels), "transition destinations")
            transition_total += sum(integer(value, "transition count") for value in row.values())
            if sum(row.values()) != conditioned["decision"][origin]["fill_count"]:
                raise ValueError("execution transition row mismatch")
        for destination in self.labels:
            if (
                sum(row[destination] for row in transitions.values())
                != conditioned["pre_fill"][destination]["fill_count"]
            ):
                raise ValueError("execution transition column mismatch")
        if transition_total != fills or integer(data["trace_count"], "execution trace count") != expected_trace:
            raise ValueError("execution trace total mismatch")
        digest = data["trace_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("execution trace digest invalid")
        if expected_trace == 0 and digest != sha256(CHAIN_DOMAIN).hexdigest():
            raise ValueError("empty execution trace digest mismatch")
        candidate._conditioned, candidate._transitions = deepcopy(dict(conditioned)), deepcopy(dict(transitions))
        candidate._by_source = deepcopy(dict(source_groups))
        candidate._fill_count, candidate._last_fill_id = fills, last
        candidate._trace_count, candidate._trace_sha256 = expected_trace, digest
        return candidate

    def validate_continuation(self, engine: Mapping[str, Any], primary_horizon_ms: int | None) -> None:
        """Cross-check attribution against authoritative live orders and pending horizons.

        This runs on decoded candidate state, before the running engine is mutated.
        A self-consistent sidecar alone is insufficient evidence of a valid resume.
        """
        live_ids = {order.order_id for order in engine["fill_model"]["_orders"].values() if order.symbol == self.symbol}
        if live_ids != set(self._orders):
            raise ValueError("execution checkpoint contexts do not match live orders")
        if set(self.quotes._live) != live_ids:
            raise ValueError("quote checkpoint bindings do not match live orders")
        for order in engine["fill_model"]["_orders"].values():
            if order.symbol != self.symbol:
                continue
            binding, context = self.quotes._live[order.order_id], self._orders[order.order_id]
            if (
                any(binding[key] != getattr(order, key) for key in ("side", "quote_slot", "qty_lots"))
                or binding["matched_lots"] != order.qty_lots - order.remaining_lots
            ):
                raise ValueError("quote checkpoint quantity/slot differs from live order")
            for phase in ("decision", "arrival"):
                if (
                    binding[f"{phase}_label"] != self._label(context[phase])
                    or context[phase] is None
                    or binding[f"{phase}_ns"] != context[phase]["logical_ns"]
                ):
                    raise ValueError("quote checkpoint causal stage differs from order attribution")
        watermark = integer(engine["last_logical_ns"], "checkpoint watermark")
        for context in self._orders.values():
            if context["arrival"]["logical_ns"] > watermark:
                raise ValueError("execution checkpoint contains future acceptance")
        metrics = engine["metrics"]
        quote_fills = sum(cell["fill_events"] for cell in self.quotes._cohorts["decision"].values())
        if quote_fills != self._fill_count or self.quotes._last_fill != self._last_fill_id:
            raise ValueError("quote checkpoint fill census differs from execution audit")
        if self._last_fill_id > integer(metrics["fill_count"], "engine fill count"):
            raise ValueError("execution checkpoint fill ordinal exceeds engine count")
        if self._fill_count == metrics["fill_count"]:
            for name, core_name in (
                ("qty", "fill_qty"),
                ("fee", "total_fees"),
                ("spread_capture_value", "spread_capture_sum"),
            ):
                attributed = sum((Decimal(cell[name]) for cell in self._conditioned["pre_fill"].values()), Decimal(0))
                if attributed != metrics[core_name]:
                    raise ValueError("execution checkpoint aggregates differ from core accounting")
            for source in FILL_SOURCE_GROUPS:
                expected = sum(
                    count for name, count in metrics["fill_source_counts"].items() if _source(name) == source
                )
                actual = sum(groups[source]["fill_count"] for groups in self._by_source["pre_fill"].values())
                if expected != actual:
                    raise ValueError("execution source census differs from core fills")
        pending = {phase: {label: {str(h): 0 for h in self.horizons} for label in self.labels} for phase in PHASES}
        source_pending = {
            phase: {
                label: {source: {str(h): 0 for h in self.horizons} for source in FILL_SOURCE_GROUPS}
                for label in self.labels
            }
            for phase in PHASES
        }
        seen: set[tuple[int, int]] = set()
        for entry in [*metrics["_pending_markouts"], *metrics["_pending_markout_horizons"]]:
            if entry["symbol"] != self.symbol:
                if "hmm_attribution" in entry:
                    raise ValueError("foreign symbol carries HMM attribution")
                continue
            attribution = require_keys(
                entry.get("hmm_attribution"),
                {*PHASES, "logical_ns", "fill_id", "quote", "fill_source"},
                "pending HMM attribution",
            )
            source = _source(attribution["fill_source"])
            if entry.get("fill_source") != attribution["fill_source"]:
                raise ValueError("pending markout differs from frozen fill source")
            if self.quotes.validate_fill(attribution["quote"]) is None:
                raise ValueError("native pending markout lacks quote request attribution")
            ordinal = integer(attribution["fill_id"], "pending fill ordinal", minimum=1)
            logical = integer(attribution["logical_ns"], "pending fill time")
            if ordinal > self._last_fill_id or logical > watermark:
                raise ValueError("pending HMM attribution exceeds checkpoint prefix")
            horizon = entry.get("horizon_ms", primary_horizon_ms)
            if horizon not in self.horizons or (ordinal, horizon) in seen:
                raise ValueError("duplicate or unexpected pending HMM horizon")
            seen.add((ordinal, horizon))
            for phase in PHASES:
                stage = self._stage(attribution[phase])
                if stage is not None and stage["logical_ns"] > logical:
                    raise ValueError("pending attribution contains future stage")
                if phase == "pre_fill" and (stage is None or stage["logical_ns"] != logical):
                    raise ValueError("pending pre-fill anchor mismatch")
                pending[phase][self._label(stage)][str(horizon)] += 1
                source_pending[phase][self._label(stage)][source][str(horizon)] += 1
        for phase in PHASES:
            for label, cell in self._conditioned[phase].items():
                for horizon, stats in cell["markouts"].items():
                    expected = cell["fill_count"] - stats["resolved_samples"] - stats["invalidated_samples"]
                    if pending[phase][label][horizon] != expected:
                        raise ValueError("pending HMM horizon count differs from execution statistics")
                    for source, source_cell in self._by_source[phase][label].items():
                        source_stats = source_cell["markouts"][horizon]
                        expected_source = (
                            source_cell["fill_count"]
                            - source_stats["resolved_samples"]
                            - source_stats["invalidated_samples"]
                        )
                        if source_pending[phase][label][source][horizon] != expected_source:
                            raise ValueError("pending HMM source horizon count differs from execution statistics")
        pending_requests: set[int] = set()
        for action in engine["actions"]:
            if action.symbol != self.symbol:
                continue
            if action.kind == "order_arrival":
                decision = self._stage(action.payload.get("hmm_decision"))
                if decision is None or decision["logical_ns"] > min(watermark, action.logical_ns):
                    raise ValueError("pending HMM order decision missing or future")
                request = integer(action.payload.get("hmm_request_id"), "pending quote request", minimum=1)
                if request in pending_requests:
                    raise ValueError("duplicate pending quote request")
                self._validate_request(request, decision)
                context = self.quotes._pending[request]
                if any(context[key] != action.payload.get(key) for key in ("side", "quote_slot", "qty_lots")):
                    raise ValueError("pending quote binding differs from outbound intent")
                pending_requests.add(request)
            elif action.kind == "trade_execution":
                fills, attributions = action.payload["fills"], action.payload.get("hmm_attributions")
                if not isinstance(attributions, list) or len(attributions) != len(fills):
                    raise ValueError("pending HMM fill batch mismatch")
                for attribution in attributions:
                    require_keys(attribution, {*PHASES, "logical_ns", "quote"}, "scheduled HMM fill attribution")
                    if self.quotes.validate_fill(attribution["quote"]) is None:
                        raise ValueError("native scheduled fill lacks quote request attribution")
                    logical = integer(attribution["logical_ns"], "scheduled fill time")
                    if logical > action.logical_ns:
                        raise ValueError("scheduled HMM fill attribution is future")
                    for phase in PHASES:
                        stage = self._stage(attribution[phase])
                        if stage is not None and stage["logical_ns"] > logical:
                            raise ValueError("scheduled HMM fill stage is future")
        if pending_requests != set(self.quotes._pending):
            raise ValueError("quote checkpoint pending requests do not match scheduler")


def verify_execution_trace(path: Path, summary: Mapping[str, Any]) -> None:
    """Stream-check hashes, frozen horizon identities and every sufficient statistic.

    Pending identities use the core's explicit markout cap. No historical fill
    set or trace list is retained. This verifies internal scenario evidence,
    not private executions or the correctness of market-data inputs.
    """
    horizons = summary.get("horizons_ms")
    if not isinstance(horizons, list):
        raise ValueError("execution summary horizon contract missing")
    state_count = len(summary["conditioned"]["pre_fill"]) - 2
    schema = RegimeExecutionAudit(
        summary["model_sha256"],
        summary["symbol"],
        state_count,
        tuple(horizons),
        summary["max_order_contexts"],
        max_pending_markouts=summary["max_pending_markouts"],
    )
    template = schema.summary()
    require_keys(summary, set(template), "execution summary")
    if summary["schema_version"] != "lob_sim.hmm_execution_summary.v3" or summary["source_groups"] != list(
        FILL_SOURCE_GROUPS
    ):
        raise ValueError("execution summary version/source contract mismatch")
    for key in ("source_denominator_note", "queue_note", "claim_reason"):
        if summary[key] != template[key]:
            raise ValueError("execution summary claim boundary mismatch")
    matrix = require_keys(summary["fill_transition_counts"], set(schema.labels), "summary fill transitions")
    for row in matrix.values():
        require_keys(row, set(schema.labels), "summary transition destinations")
        for value in row.values():
            integer(value, "summary transition count")
    for key in ("fill_count", "last_fill_id", "trace_count", "retained_order_contexts"):
        integer(summary[key], key)
    if (
        summary["retained_order_contexts"] > schema.max_orders
        or type(summary["memory_bounded_by_tape_duration"]) is not bool
        or summary["claim_ready"] is not False
    ):
        raise ValueError("execution summary context/claim contract invalid")
    require_keys(summary["conditioned"], set(PHASES), "summary phases")
    require_keys(summary["conditioned_by_source"], set(PHASES), "summary source phases")
    for phase in PHASES:
        require_keys(summary["conditioned"][phase], set(schema.labels), "summary labels")
        require_keys(summary["conditioned_by_source"][phase], set(schema.labels), "summary source labels")
        for label in schema.labels:
            _validate_summary_cell(summary["conditioned"][phase][label], schema.horizons)
            sources = require_keys(
                summary["conditioned_by_source"][phase][label], set(FILL_SOURCE_GROUPS), "summary fill sources"
            )
            for cell in sources.values():
                _validate_summary_cell(cell, schema.horizons)
    digest, count = sha256(CHAIN_DOMAIN).hexdigest(), 0
    last_fill, fill_count = 0, 0
    conditioned = {phase: {label: _blank_cell(schema.horizons) for label in schema.labels} for phase in PHASES}
    by_source = {
        phase: {
            label: {source: _blank_cell(schema.horizons) for source in FILL_SOURCE_GROUPS} for label in schema.labels
        }
        for phase in PHASES
    }
    transitions = {label: {other: 0 for other in schema.labels} for label in schema.labels}
    pending: dict[int, dict[str, Any]] = {}
    identity_fields = (
        "symbol",
        "order_id",
        "side",
        "qty",
        "qty_lots",
        "logical_ns",
        "fill_ts_local",
        "fill_source",
        "quote",
        *PHASES,
        *(f"{phase}_label" for phase in PHASES),
    )
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(EXECUTION_FIELDS):
            raise ValueError("execution trace field contract mismatch")
        for row in reader:
            parsed = _parse_execution_row(row)
            if parsed["schema_version"] != "lob_sim.hmm_execution.v3" or parsed["symbol"] != schema.symbol:
                raise ValueError("serialized execution version/symbol mismatch")
            logical = integer(parsed["logical_ns"], "serialized fill time")
            ordinal = integer(parsed["fill_id"], "serialized fill ordinal", minimum=1)
            for phase in PHASES:
                stage = schema._stage(parsed[phase])
                if (stage is not None and stage["logical_ns"] > logical) or parsed[f"{phase}_label"] != schema._label(
                    stage
                ):
                    raise ValueError("serialized execution causal label mismatch")
            if parsed["pre_fill"] is None or parsed["pre_fill"]["logical_ns"] != logical:
                raise ValueError("serialized execution pre-fill anchor mismatch")
            if (
                parsed["decision"] is not None
                and parsed["arrival"] is not None
                and parsed["decision"]["logical_ns"] > parsed["arrival"]["logical_ns"]
            ):
                raise ValueError("serialized decision occurs after acceptance")
            quote = parsed["quote"]
            if quote is not None:
                require_keys(quote, {"request_id", "first_fill"}, "serialized quote fill attribution")
                request_count = sum(
                    integer(cell["scheduled_requests"], "scheduled quote census")
                    for cell in summary["quote_lifecycles"]["cohorts"]["decision"].values()
                )
                if (
                    integer(quote["request_id"], "serialized quote request", minimum=1) > request_count
                    or type(quote["first_fill"]) is not bool
                ):
                    raise ValueError("serialized quote request/flag invalid")
            source = _source(parsed["fill_source"])
            quantity = _decimal(parsed["qty"], "serialized quantity", nonnegative=True)
            lots = integer(parsed["qty_lots"], "serialized fill lots", minimum=1)
            if quantity <= 0 or parsed["side"] not in {"bid", "ask"}:
                raise ValueError("serialized execution quantity/side invalid")
            fill_ts = finite(parsed["fill_ts_local"], "serialized fill clock")
            kind = parsed["event_type"]
            if kind == "fill":
                if ordinal <= last_fill or parsed["status"] != "filled" or type(parsed["maker"]) is not bool:
                    raise ValueError("serialized execution fill order/status invalid")
                if any(
                    parsed[key] is not None
                    for key in (
                        "horizon_ms",
                        "markout",
                        "observed_ts",
                        "actual_lag_seconds",
                        "invalid_reason",
                        "deadline_ts",
                    )
                ):
                    raise ValueError("serialized fill carries future markout fields")
                ahead, trajectory = _queue(parsed, lots)
                fee = _decimal(parsed["fee"], "serialized fee")
                spread = (
                    None
                    if parsed["spread_capture_value"] is None
                    else _decimal(parsed["spread_capture_value"], "serialized spread")
                )
                wait = Decimal(str(finite(parsed["time_in_book_ms"], "serialized quote age")))
                if wait < 0:
                    raise ValueError("serialized negative quote age")
                for phase in PHASES:
                    label = parsed[f"{phase}_label"]
                    for cell in (conditioned[phase][label], by_source[phase][label][source]):
                        cell["fill_count"] += 1
                        cell["qty_lots"] += lots
                        for field, amount in (("qty", quantity), ("fee", fee), ("wait_ms_sum", wait)):
                            cell[field] = str(Decimal(cell[field]) + amount)
                        cell["pending_cancel_fill_count"] += int(parsed["order_state_at_fill"] == "pending_cancel")
                        if spread is not None:
                            cell["marked_fill_count"] += 1
                            cell["marked_qty"] = str(Decimal(cell["marked_qty"]) + quantity)
                            cell["spread_capture_value"] = str(Decimal(cell["spread_capture_value"]) + spread)
                        if ahead is not None:
                            cell["queue_ahead_samples"] += 1
                            cell["queue_ahead_lots_sum"] += ahead
                        if trajectory:
                            cell["queue_trajectory_samples"] += 1
                            for field, queue_lots in trajectory.items():
                                cell["queue_trajectory_lots"][field] += queue_lots
                                cell["queue_trajectory_field_samples"][field] += 1
                transitions[parsed["decision_label"]][parsed["pre_fill_label"]] += 1
                if schema.horizons:
                    # Primary and additional horizons have separate core caps;
                    # their union is bounded by the sum, not the primary cap.
                    if len(pending) >= schema.max_pending_markouts * len(schema.horizons):
                        raise ValueError("serialized execution exceeds pending markout bound")
                    pending[ordinal] = {
                        **{key: parsed[key] for key in identity_fields},
                        "remaining": set(schema.horizons),
                    }
                last_fill, fill_count = ordinal, fill_count + 1
            elif kind == "markout":
                original = pending.get(ordinal)
                horizon = parsed["horizon_ms"]
                if original is None or horizon not in original["remaining"]:
                    raise ValueError("serialized markout lacks a pending unique fill/horizon")
                if any(original[key] != parsed[key] for key in identity_fields):
                    raise ValueError("serialized markout differs from frozen fill identity/source")
                observed = finite(parsed["observed_ts"], "serialized markout observation")
                deadline = finite(parsed["deadline_ts"], "serialized markout deadline")
                # The legacy primary horizon label is rounded to milliseconds,
                # but its stored deadline retains the configured float seconds.
                # Check that actual deadline, rather than inventing a new one.
                if observed < fill_ts or deadline < fill_ts:
                    raise ValueError("serialized markout clock/deadline inconsistent")
                resolved = parsed["status"] == "resolved"
                if not resolved and parsed["status"] != "invalidated":
                    raise ValueError("serialized markout status invalid")
                if resolved:
                    markout_value = _decimal(parsed["markout"], "serialized signed markout")
                    lag = Decimal(str(observed)) - Decimal(str(fill_ts))
                    if (
                        observed < deadline
                        or parsed["invalid_reason"] is not None
                        or parsed["actual_lag_seconds"] != float(lag)
                    ):
                        raise ValueError("serialized resolved markout lag/reason inconsistent")
                elif (
                    parsed["markout"] is not None
                    or parsed["actual_lag_seconds"] is not None
                    or not isinstance(parsed["invalid_reason"], str)
                    or not parsed["invalid_reason"]
                ):
                    raise ValueError("serialized invalidated markout carries a value or lacks reason")
                for phase in PHASES:
                    label = parsed[f"{phase}_label"]
                    for cell in (conditioned[phase][label], by_source[phase][label][source]):
                        stats = cell["markouts"][str(horizon)]
                        if resolved:
                            stats["resolved_samples"] += 1
                            stats["adverse_samples"] += int(markout_value < 0)
                            for field, amount in (
                                ("qty", quantity),
                                ("weighted_markout_sum", quantity * markout_value),
                                ("actual_lag_sum", lag),
                            ):
                                stats[field] = str(Decimal(stats[field]) + amount)
                            stats["actual_lag_max"] = str(max(Decimal(stats["actual_lag_max"]), lag))
                        else:
                            stats["invalidated_samples"] += 1
                original["remaining"].remove(horizon)
                if not original["remaining"]:
                    del pending[ordinal]
            else:
                raise ValueError("serialized execution event type invalid")
            digest, count = _advance(digest, parsed), count + 1
    if count != summary["trace_count"] or digest != summary["trace_sha256"]:
        raise ValueError("serialized execution audit count/hash mismatch")
    if (
        fill_count != summary["fill_count"]
        or last_fill != summary["last_fill_id"]
        or transitions != summary["fill_transition_counts"]
    ):
        raise ValueError("serialized execution fill/transition census mismatch")
    expected_conditioned = {phase: _summarize_cells(groups) for phase, groups in conditioned.items()}
    expected_sources = {
        phase: {label: _summarize_cells(sources) for label, sources in groups.items()}
        for phase, groups in by_source.items()
    }
    if expected_conditioned != summary["conditioned"] or expected_sources != summary["conditioned_by_source"]:
        raise ValueError("serialized execution sufficient statistics mismatch")


def format_execution_report(summary: Mapping[str, Any]) -> str:
    """Descriptive fill populations; do not manufacture quote denominators."""
    lines = [
        "Execution outcomes by frozen regime and fill source.",
        summary["source_denominator_note"],
        summary["queue_note"],
    ]
    for phase in PHASES:
        lines.append(f"{phase} cohorts:")
        for label, sources in summary["conditioned_by_source"][phase].items():
            for source, cell in sources.items():
                if not cell["fill_count"]:
                    continue
                lines.append(
                    f"  {label}/{source}: fills={cell['fill_count']}; lots={cell['qty_lots']}; fee={cell['fee']}; mean quote age ms={cell['mean_time_in_book_ms']}; pending-cancel fills={cell['pending_cancel_fill_count']}"
                )
                lines.append(
                    f"    marked spread capture/quantity={cell['mean_marked_spread_capture']}; coverage={cell['spread_capture_coverage']}; mean modeled queue ahead={cell['mean_modeled_queue_ahead_lots']}; queue coverage={cell['queue_ahead_coverage']}"
                )
                for horizon, stats in cell["markouts"].items():
                    lines.append(
                        f"    {horizon}ms: resolved={stats['resolved_samples']}; invalid={stats['invalidated_samples']}; pending={stats['unresolved_samples']}; coverage={stats['coverage']}; signed mean={stats['mean_signed_markout']}; adverse fraction={stats['adverse_rate']}; actual lag mean={stats['mean_actual_lag_seconds']}; max={stats['actual_lag_max']}"
                    )
    return "\n".join(lines)
