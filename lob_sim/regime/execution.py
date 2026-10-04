"""Frozen quote-lifetime attribution using the simulator's existing fill/markout path.

This audit observes execution; it never infers a fill or values a position.
Rows are streamed and aggregates have fixed model-state/horizon cardinality.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
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
    "horizon_ms",
    "status",
    "markout",
    "deadline_ts",
    "observed_ts",
    "actual_lag_seconds",
    "invalid_reason",
    "quote",
)
CHAIN_DOMAIN = b"lob_sim.hmm_execution.v2"


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
        "markouts": {str(horizon): _blank_horizon() for horizon in horizons},
    }


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
    ) -> None:
        if len(model_sha256) != 64 or any(char not in "0123456789abcdef" for char in model_sha256):
            raise ValueError("execution model SHA-256 invalid")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("execution symbol invalid")
        integer(state_count, "state count", minimum=2)
        if state_count > 5:
            raise ValueError("execution audit supports K=2..5")
        integer(max_orders, "order context cap", minimum=1)
        if len(set(horizons)) != len(horizons):
            raise ValueError("duplicate execution horizons")
        for horizon in horizons:
            integer(horizon, "execution horizon", minimum=1)
        self.model_sha256, self.symbol, self.state_count = model_sha256, symbol, state_count
        self.horizons, self.max_orders = tuple(sorted(horizons)), max_orders
        self.sink = sink if sink is not None else NullSink()
        self.labels = tuple(f"STATE_{index}" for index in range(state_count)) + ("UNCONFIRMED", "UNAVAILABLE")
        self.quotes = RegimeQuoteAudit(symbol, self.labels, max_orders, quote_sink)
        self._orders: dict[str, dict[str, Any]] = {}
        self._conditioned = {phase: {label: _blank_cell(self.horizons) for label in self.labels} for phase in PHASES}
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
        self.sink.write(deepcopy(normalized))
        self._trace_sha256, self._trace_count = digest, self._trace_count + 1

    def _row(self, data: Mapping[str, Any], attribution: Mapping[str, Any]) -> dict[str, Any]:
        row = {key: data.get(key) for key in EXECUTION_FIELDS}
        row.update({"schema_version": "lob_sim.hmm_execution.v2", **deepcopy(dict(attribution))})
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
            cell = self._conditioned[phase][self._label(frozen[phase])]
            cell["fill_count"] += 1
            cell["qty_lots"] += lots
            cell["qty"] = str(Decimal(cell["qty"]) + quantity)
            cell["fee"] = str(Decimal(cell["fee"]) + fee)
            cell["wait_ms_sum"] = str(Decimal(cell["wait_ms_sum"]) + wait)
            cell["pending_cancel_fill_count"] += int(fill.get("order_state_at_fill") == "pending_cancel")
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
            stats = self._conditioned[phase][self._label(attribution[phase])]["markouts"][str(horizon_ms)]
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
        conditioned = deepcopy(self._conditioned)
        for phase in PHASES:
            for cell in conditioned[phase].values():
                fills = cell["fill_count"]
                cell["mean_time_in_book_ms"] = float(Decimal(cell["wait_ms_sum"]) / fills) if fills else None
                cell["spread_capture_coverage"] = cell["marked_fill_count"] / fills if fills else None
                marked_qty = Decimal(cell["marked_qty"])
                cell["mean_marked_spread_capture"] = (
                    float(Decimal(cell["spread_capture_value"]) / marked_qty) if marked_qty else None
                )
                for stats in cell["markouts"].values():
                    resolved = stats["resolved_samples"]
                    stats["unresolved_samples"] = fills - resolved - stats["invalidated_samples"]
                    stats["coverage"] = resolved / fills if fills else None
                    stats["mean_signed_markout"] = (
                        float(Decimal(stats["weighted_markout_sum"]) / Decimal(stats["qty"]))
                        if Decimal(stats["qty"])
                        else None
                    )
                    stats["adverse_rate"] = stats["adverse_samples"] / resolved if resolved else None
                    stats["mean_actual_lag_seconds"] = (
                        float(Decimal(stats["actual_lag_sum"]) / resolved) if resolved else None
                    )
        return {
            "schema_version": "lob_sim.hmm_execution_summary.v2",
            "model_sha256": self.model_sha256,
            "symbol": self.symbol,
            "fill_count": self._fill_count,
            "last_fill_id": self._last_fill_id,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "conditioned": conditioned,
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
                "schema_version": "lob_sim.hmm_execution_checkpoint.v2",
                "model_sha256": self.model_sha256,
                "symbol": self.symbol,
                "state_count": self.state_count,
                "horizons": list(self.horizons),
                "max_orders": self.max_orders,
                "orders": self._orders,
                "conditioned": self._conditioned,
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
        for key in ("schema_version", "model_sha256", "symbol", "state_count", "horizons", "max_orders"):
            if data[key] != self.checkpoint()[key]:
                raise ValueError("execution checkpoint configuration mismatch")
        integer(data["state_count"], "execution state count", minimum=2)
        integer(data["max_orders"], "execution order bound", minimum=1)
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
                require_keys(cell, set(_blank_cell(self.horizons)), "execution cell")
                count = integer(cell["fill_count"], "cell fill count")
                integer(cell["qty_lots"], "cell lots")
                for key in ("marked_fill_count", "pending_cancel_fill_count"):
                    if integer(cell[key], key) > count:
                        raise ValueError("execution cell count exceeds fills")
                for key in ("qty", "fee", "spread_capture_value", "wait_ms_sum", "marked_qty"):
                    _decimal(cell[key], key, nonnegative=key in {"qty", "wait_ms_sum", "marked_qty"})
                if Decimal(cell["marked_qty"]) > Decimal(cell["qty"]):
                    raise ValueError("marked quantity exceeds fill quantity")
                horizons = require_keys(cell["markouts"], {str(h) for h in self.horizons}, "execution horizons")
                for stats in horizons.values():
                    require_keys(stats, set(_blank_horizon()), "execution horizon stats")
                    resolved = integer(stats["resolved_samples"], "resolved samples")
                    invalidated = integer(stats["invalidated_samples"], "invalidated samples")
                    if (
                        resolved + invalidated > count
                        or integer(stats["adverse_samples"], "adverse samples") > resolved
                    ):
                        raise ValueError("execution horizon samples inconsistent")
                    for key in ("qty", "weighted_markout_sum", "actual_lag_sum", "actual_lag_max"):
                        _decimal(stats[key], key, nonnegative=key != "weighted_markout_sum")
                    resolved_total += resolved + invalidated
            if sum(cell["fill_count"] for cell in groups.values()) != fills:
                raise ValueError("execution group total inconsistent")
            totals.append(resolved_total)
        if len(set(totals)) != 1:
            raise ValueError("execution horizon totals disagree")
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
        pending = {phase: {label: {str(h): 0 for h in self.horizons} for label in self.labels} for phase in PHASES}
        seen: set[tuple[int, int]] = set()
        for entry in [*metrics["_pending_markouts"], *metrics["_pending_markout_horizons"]]:
            if entry["symbol"] != self.symbol:
                if "hmm_attribution" in entry:
                    raise ValueError("foreign symbol carries HMM attribution")
                continue
            attribution = require_keys(
                entry.get("hmm_attribution"), {*PHASES, "logical_ns", "fill_id", "quote"}, "pending HMM attribution"
            )
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
        for phase in PHASES:
            for label, cell in self._conditioned[phase].items():
                for horizon, stats in cell["markouts"].items():
                    expected = cell["fill_count"] - stats["resolved_samples"] - stats["invalidated_samples"]
                    if pending[phase][label][horizon] != expected:
                        raise ValueError("pending HMM horizon count differs from execution statistics")
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
    """Reconstruct serialized canonical rows; never trust sink counters alone."""
    json_fields = {"decision", "arrival", "pre_fill", "evidence_ids", "validity", "latency_draws_ms", "quote"}
    int_fields = {"logical_ns", "fill_id", "qty_lots", "horizon_ms"}
    float_fields = {"time_in_book_ms", "deadline_ts", "observed_ts", "actual_lag_seconds"}
    digest, count = sha256(CHAIN_DOMAIN).hexdigest(), 0
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(EXECUTION_FIELDS):
            raise ValueError("execution trace field contract mismatch")
        for row in reader:
            if set(row) != set(EXECUTION_FIELDS) or any(value is None for value in row.values()):
                raise ValueError("malformed execution trace row")
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
            digest, count = _advance(digest, parsed), count + 1
    if count != summary["trace_count"] or digest != summary["trace_sha256"]:
        raise ValueError("serialized execution audit count/hash mismatch")
