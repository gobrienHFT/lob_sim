"""Bounded, observational quote cohorts; no matching or accounting authority.

Requests mean outbound quotes actually scheduled, not strategy targets. A cohort
keeps its original decision/acceptance label. Fractions are descriptive at the
tape cutoff: outstanding quotes are censored, not treated as failed executions.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..sim.sinks import EventSink, NullSink
from .validation import canonical_json, integer, require_keys

QUOTE_FIELDS = (
    "schema_version",
    "event_type",
    "symbol",
    "logical_ns",
    "request_id",
    "order_id",
    "fill_id",
    "side",
    "quote_slot",
    "qty_lots",
    "decision_label",
    "arrival_label",
    "first_fill",
    "reason",
)
DOMAIN = b"lob_sim.hmm_quotes.v1"
TERMINALS = ("filled", "cancelled", "epoch_invalidated", "halted")
REJECTIONS = (
    "unsynced_book",
    "invalid_quantity",
    "quote_slot_occupied",
    "empty_book",
    "self_trade_prevented",
    "post_only_would_cross",
    "risk_limit",
    "hmm_soft_position_limit",
    "portfolio_mark_unavailable",
    "portfolio_notional_limit",
    "OTHER",
)
COUNTS = (
    "scheduled_requests",
    "scheduled_qty_lots",
    "arrived_requests",
    "accepted_orders",
    "accepted_qty_lots",
    "rejected_requests",
    "discarded_before_arrival",
    "fill_events",
    "filled_qty_lots",
    "unique_filled_orders",
)


def _cell() -> dict[str, Any]:
    return {**dict.fromkeys(COUNTS, 0), "terminal_orders": dict.fromkeys(TERMINALS, 0)}


def _digest(previous: str, row: Mapping[str, Any]) -> str:
    return sha256(bytes.fromhex(previous) + canonical_json(dict(row)).encode()).hexdigest()


class RegimeQuoteAudit:
    """Fixed K+2 cohorts, bounded pending/live bindings, no historical ID set."""

    def __init__(self, symbol: str, labels: tuple[str, ...], max_orders: int, sink: EventSink | None = None):
        if (
            not isinstance(symbol, str)
            or not symbol
            or not 4 <= len(labels) <= 7
            or labels != tuple(f"STATE_{i}" for i in range(len(labels) - 2)) + ("UNCONFIRMED", "UNAVAILABLE")
        ):
            raise ValueError("quote symbol/state dimension invalid")
        self.symbol, self.labels = symbol, labels
        self.max_orders = integer(max_orders, "quote capacity", minimum=1)
        self.sink = sink if sink is not None else NullSink()
        self._pending: dict[int, dict[str, Any]] = {}
        self._live: dict[str, dict[str, Any]] = {}
        self._cohorts = {basis: {label: _cell() for label in labels} for basis in ("decision", "arrival")}
        self._rejections = dict.fromkeys(REJECTIONS, 0)
        self._next_request = self._last_fill = self._trace_count = 0
        self._trace_sha256 = sha256(DOMAIN).hexdigest()

    def _label(self, label: object) -> str:
        if not isinstance(label, str) or label not in self.labels:
            raise ValueError("unknown quote cohort label")
        return label

    def _row(self, kind: str, logical_ns: int, context: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            **dict.fromkeys(QUOTE_FIELDS),
            "schema_version": "lob_sim.hmm_quotes.v1",
            "event_type": kind,
            "symbol": self.symbol,
            "logical_ns": integer(logical_ns, "quote event time"),
            **{key: context[key] for key in QUOTE_FIELDS if key in context},
            **extra,
        }

    def _reduce(self, row: Mapping[str, Any], *, apply: bool = True) -> None:
        """Fixed-cardinality sufficient statistics, also used for tape verification."""
        if row["schema_version"] != "lob_sim.hmm_quotes.v1" or row["symbol"] != self.symbol:
            raise ValueError("quote audit schema/symbol mismatch")
        integer(row["logical_ns"], "quote row time")
        integer(row["request_id"], "quote request identity", minimum=1)
        kind = row["event_type"]
        if kind not in {"scheduled", "accepted", "rejected", "discarded", "terminal", "fill"}:
            raise ValueError("unknown quote lifecycle event")
        lots = integer(row["qty_lots"], "quote row lots", minimum=1)
        decision = self._cohorts["decision"][self._label(row["decision_label"])]
        cells = [decision]
        if kind in {"accepted", "rejected", "terminal", "fill"}:
            cells.append(self._cohorts["arrival"][self._label(row["arrival_label"])])
        elif row["arrival_label"] is not None:
            raise ValueError("outbound quote cannot have an arrival label")
        if kind == "fill":
            ordinal = integer(row["fill_id"], "quote fill ordinal", minimum=1)
            if ordinal <= self._last_fill or type(row["first_fill"]) is not bool:
                raise ValueError("quote fill ordinal/first-fill flag inconsistent")
            if any(
                cell["filled_qty_lots"] + lots > cell["accepted_qty_lots"]
                or cell["unique_filled_orders"] + int(row["first_fill"] is True) > cell["accepted_orders"]
                for cell in cells
            ):
                raise ValueError("quote filled quantity/order count exceeds accepted denominator")
        elif row["first_fill"] is not None or row["fill_id"] is not None:
            raise ValueError("non-fill quote row contains fill metadata")
        if kind == "terminal" and row["reason"] not in TERMINALS:
            raise ValueError("unknown quote terminal reason")
        if kind == "discarded" and row["reason"] not in {"epoch_invalidated", "halted"}:
            raise ValueError("unknown outbound discard reason")
        if kind == "rejected" and (not isinstance(row["reason"], str) or not row["reason"]):
            raise ValueError("quote rejection reason missing")
        if row["side"] not in {"bid", "ask"}:
            raise ValueError("quote row side invalid")
        if not apply:
            return
        if kind == "fill":
            self._last_fill = ordinal
        if kind == "rejected":
            reason = row["reason"] if row["reason"] in REJECTIONS else "OTHER"
            self._rejections[reason] += 1
        for cell in cells:
            if kind == "scheduled":
                cell["scheduled_requests"] += 1
                cell["scheduled_qty_lots"] += lots
            elif kind in {"accepted", "rejected"}:
                cell["arrived_requests"] += 1
                cell["accepted_orders" if kind == "accepted" else "rejected_requests"] += 1
                if kind == "accepted":
                    cell["accepted_qty_lots"] += lots
            elif kind == "discarded":
                cell["discarded_before_arrival"] += 1
            elif kind == "terminal":
                cell["terminal_orders"][row["reason"]] += 1
            else:
                cell["fill_events"] += 1
                cell["filled_qty_lots"] += lots
                cell["unique_filled_orders"] += int(row["first_fill"] is True)

    def _write(self, row: Mapping[str, Any]) -> None:
        self._reduce(row, apply=False)
        digest = _digest(self._trace_sha256, row)
        # A subclass is a real consumer, even if it inherits the NullSink name.
        if type(self.sink) is not NullSink:
            self.sink.write(deepcopy(dict(row)))
        self._reduce(row)
        self._trace_count += 1
        self._trace_sha256 = digest

    def schedule(self, decision_label: str, logical_ns: int, side: str, quote_slot: str, qty_lots: int) -> int:
        if (
            len(self._pending) >= self.max_orders
            or side not in {"bid", "ask"}
            or not isinstance(quote_slot, str)
            or not quote_slot
        ):
            raise ValueError("quote request capacity/side/slot invalid")
        request = self._next_request + 1
        context = {
            "request_id": request,
            "decision_label": self._label(decision_label),
            "decision_ns": integer(logical_ns, "quote decision time"),
            "side": side,
            "quote_slot": quote_slot,
            "qty_lots": integer(qty_lots, "requested lots", minimum=1),
        }
        self._write(self._row("scheduled", logical_ns, context))
        self._pending[request], self._next_request = context, request
        return request

    def arrive(
        self,
        request_id: int,
        arrival_label: str,
        logical_ns: int,
        *,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        request_id = integer(request_id, "arrival request", minimum=1)
        context = self._pending.get(request_id)
        if context is None or logical_ns < context["decision_ns"]:
            raise ValueError("quote arrival missing request or precedes decision")
        if (order_id is None) == (reason is None):
            raise ValueError("quote arrival requires exactly acceptance or rejection")
        if order_id is not None and (not order_id or order_id in self._live or len(self._live) >= self.max_orders):
            raise ValueError("quote acceptance capacity/identity invalid")
        accepted = {
            **context,
            "arrival_label": self._label(arrival_label),
            "arrival_ns": logical_ns,
            "order_id": order_id,
            "matched_lots": 0,
            "first_fill_stamped": False,
        }
        self._write(self._row("accepted" if order_id is not None else "rejected", logical_ns, accepted, reason=reason))
        del self._pending[request_id]
        if order_id is not None:
            self._live[order_id] = accepted

    def discard(self, request_id: int, logical_ns: int, reason: str) -> None:
        context = self._pending.get(integer(request_id, "discard request", minimum=1))
        if context is None or logical_ns < context["decision_ns"]:
            raise ValueError("quote discard missing request or precedes decision")
        self._write(self._row("discarded", logical_ns, context, reason=reason))
        del self._pending[request_id]

    def terminate(self, order_id: str, logical_ns: int, reason: str) -> None:
        context = self._live.get(order_id)
        if context is None:
            return  # A cancel acknowledgement may follow an already filled order.
        if logical_ns < context["arrival_ns"] or (
            reason == "filled" and context["matched_lots"] != context["qty_lots"]
        ):
            raise ValueError("quote termination precedes acceptance or lacks full consumption")
        self._write(self._row("terminal", logical_ns, context, reason=reason))
        del self._live[order_id]

    def clear(self, logical_ns: int, reason: str, *, discard_pending: bool = True) -> None:
        for order_id in sorted(self._live):
            self.terminate(order_id, logical_ns, reason)
        if discard_pending:
            for request in sorted(self._pending):
                self.discard(request, logical_ns, reason)

    def freeze_fill(self, order_id: str | None, qty_lots: int) -> dict[str, Any] | None:
        context = self._live.get(order_id or "")
        if context is None:
            return None  # Standalone attribution without a registered outbound request.
        lots = integer(qty_lots, "matched quote lots", minimum=1)
        if context["matched_lots"] + lots > context["qty_lots"]:
            raise ValueError("quote fills exceed accepted quantity")
        frozen = {"request_id": context["request_id"], "first_fill": not context["first_fill_stamped"]}
        context["first_fill_stamped"] = True
        context["matched_lots"] += lots
        return frozen

    def validate_fill(self, value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        data = require_keys(value, {"request_id", "first_fill"}, "quote fill attribution")
        if (
            integer(data["request_id"], "fill request", minimum=1) > self._next_request
            or type(data["first_fill"]) is not bool
        ):
            raise ValueError("quote fill request/flag invalid")
        return deepcopy(dict(data))

    def on_fill(
        self,
        fill_id: int,
        quote: object,
        logical_ns: int,
        order_id: str | None,
        side: str,
        qty_lots: int,
        decision_label: str,
        arrival_label: str,
    ) -> None:
        frozen = self.validate_fill(quote)
        if frozen is None:
            return
        row = self._row(
            "fill",
            logical_ns,
            frozen,
            fill_id=fill_id,
            order_id=order_id,
            side=side,
            qty_lots=qty_lots,
            decision_label=decision_label,
            arrival_label=arrival_label,
        )
        self._write(row)

    def summary(self) -> dict[str, Any]:
        cohorts = deepcopy(self._cohorts)
        for basis, groups in cohorts.items():
            for cell in groups.values():
                accepted, arrived = cell["accepted_orders"], cell["arrived_requests"]
                cell["unique_filled_fraction_of_accepted"] = (
                    cell["unique_filled_orders"] / accepted if accepted else None
                )
                cell["acceptance_fraction_of_arrived"] = accepted / arrived if arrived else None
                cell["filled_fraction_of_accepted_lots"] = (
                    cell["filled_qty_lots"] / cell["accepted_qty_lots"] if cell["accepted_qty_lots"] else None
                )
                cell["unique_filled_fraction_of_scheduled"] = (
                    (cell["unique_filled_orders"] / cell["scheduled_requests"] if cell["scheduled_requests"] else None)
                    if basis == "decision"
                    else None
                )
            for label, cell in groups.items():
                cell["pending_requests"] = (
                    sum(c["decision_label"] == label for c in self._pending.values()) if basis == "decision" else None
                )
                cell["live_orders"] = sum(c[f"{basis}_label"] == label for c in self._live.values())
        return {
            "schema_version": "lob_sim.hmm_quote_summary.v1",
            "symbol": self.symbol,
            "cohorts": cohorts,
            "rejections_by_reason": dict(self._rejections),
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "retained_pending_requests": len(self._pending),
            "retained_live_bindings": len(self._live),
            "max_pending_requests": self.max_orders,
            "max_live_bindings": self.max_orders,
            "memory_bounded_by_tape_duration": self.sink.memory_bounded,
            "claim_ready": False,
            "denominator_note": "scheduled outbound intents; arrived includes rejects; accepted excludes rejects; unique orders, not partial-fill events; outstanding quotes right-censored at cutoff; scenario-specific descriptive fractions, not live fill probabilities",
        }

    def checkpoint(self) -> dict[str, Any]:
        return deepcopy(
            {
                "schema_version": "lob_sim.hmm_quote_checkpoint.v1",
                "symbol": self.symbol,
                "labels": list(self.labels),
                "max_orders": self.max_orders,
                "pending": {str(key): value for key, value in self._pending.items()},
                "live": self._live,
                "cohorts": self._cohorts,
                "rejections": self._rejections,
                "next_request": self._next_request,
                "last_fill": self._last_fill,
                "trace_count": self._trace_count,
                "trace_sha256": self._trace_sha256,
            }
        )

    def validated_copy(self, checkpoint: object) -> RegimeQuoteAudit:
        data = require_keys(checkpoint, set(self.checkpoint()), "quote checkpoint")
        for name in ("schema_version", "symbol", "labels", "max_orders"):
            if data[name] != self.checkpoint()[name]:
                raise ValueError("quote checkpoint configuration mismatch")
        candidate = RegimeQuoteAudit(self.symbol, self.labels, self.max_orders, self.sink)
        next_request = integer(data["next_request"], "quote request count")
        pending, live = data["pending"], data["live"]
        if (
            not isinstance(pending, Mapping)
            or not isinstance(live, Mapping)
            or max(len(pending), len(live)) > self.max_orders
        ):
            raise ValueError("quote checkpoint capacity invalid")
        ids: set[int] = set()
        base_keys = {"request_id", "decision_label", "decision_ns", "side", "quote_slot", "qty_lots"}
        for key, context in [*pending.items(), *live.items()]:
            is_pending = key in pending
            require_keys(
                context,
                base_keys
                if is_pending
                else base_keys | {"arrival_label", "arrival_ns", "order_id", "matched_lots", "first_fill_stamped"},
                "quote binding",
            )
            request = integer(context["request_id"], "binding request", minimum=1)
            if request > next_request or request in ids or (is_pending and key != str(request)):
                raise ValueError("quote binding identity mismatch")
            ids.add(request)
            candidate._label(context["decision_label"])
            integer(context["decision_ns"], "binding decision time")
            lots = integer(context["qty_lots"], "binding lots", minimum=1)
            if (
                context["side"] not in {"bid", "ask"}
                or not isinstance(context["quote_slot"], str)
                or not context["quote_slot"]
            ):
                raise ValueError("quote binding side/slot invalid")
            if not is_pending:
                candidate._label(context["arrival_label"])
                if (
                    not isinstance(key, str)
                    or not key
                    or context["order_id"] != key
                    or integer(context["arrival_ns"], "binding arrival") < context["decision_ns"]
                ):
                    raise ValueError("quote live binding identity/time invalid")
                matched = integer(context["matched_lots"], "matched lots")
                if (
                    matched > lots
                    or type(context["first_fill_stamped"]) is not bool
                    or context["first_fill_stamped"] != (matched > 0)
                ):
                    raise ValueError("quote first-fill flag/quantity mismatch")
        groups = require_keys(data["cohorts"], {"decision", "arrival"}, "quote cohorts")
        totals: dict[str, dict[str, int]] = {}
        for basis, cells in groups.items():
            require_keys(cells, set(self.labels), "quote cohort labels")
            for cell in cells.values():
                require_keys(cell, set(_cell()), "quote cohort statistics")
                for name in COUNTS:
                    integer(cell[name], name)
                terminals = require_keys(cell["terminal_orders"], set(TERMINALS), "quote terminal statistics")
                if sum(integer(value, "terminal count") for value in terminals.values()) > cell["accepted_orders"]:
                    raise ValueError("quote terminal counts exceed acceptances")
                if (
                    cell["arrived_requests"] != cell["accepted_orders"] + cell["rejected_requests"]
                    or cell["unique_filled_orders"] > min(cell["accepted_orders"], cell["fill_events"])
                    or cell["filled_qty_lots"] > cell["accepted_qty_lots"]
                ):
                    raise ValueError("quote cohort conservation invalid")
            totals[basis] = {name: sum(cell[name] for cell in cells.values()) for name in COUNTS}
            for label, cell in cells.items():
                live_count = sum(context[f"{basis}_label"] == label for context in live.values())
                if cell["accepted_orders"] != live_count + sum(cell["terminal_orders"].values()):
                    raise ValueError("quote acceptance/live/terminal conservation invalid")
                if basis == "decision":
                    pending_count = sum(context["decision_label"] == label for context in pending.values())
                    if (
                        cell["scheduled_requests"]
                        != cell["arrived_requests"] + cell["discarded_before_arrival"] + pending_count
                    ):
                        raise ValueError("quote request/arrival/discard conservation invalid")
                elif any(
                    cell[name] for name in ("scheduled_requests", "scheduled_qty_lots", "discarded_before_arrival")
                ):
                    raise ValueError("arrival cohort has outbound denominators")
        if totals["decision"]["scheduled_requests"] != next_request or any(
            totals["decision"][name] != totals["arrival"][name]
            for name in COUNTS
            if name not in {"scheduled_requests", "scheduled_qty_lots", "discarded_before_arrival"}
        ):
            raise ValueError("quote cohort totals disagree")
        rejections = require_keys(data["rejections"], set(REJECTIONS), "quote rejection reasons")
        if (
            sum(integer(value, "rejection count") for value in rejections.values())
            != totals["decision"]["rejected_requests"]
        ):
            raise ValueError("quote rejection totals disagree")
        expected_rows = (
            next_request
            + totals["decision"]["arrived_requests"]
            + totals["decision"]["discarded_before_arrival"]
            + totals["decision"]["accepted_orders"]
            - len(live)
            + totals["decision"]["fill_events"]
        )
        digest = data["trace_sha256"]
        if (
            integer(data["trace_count"], "quote trace count") != expected_rows
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("quote trace count/hash invalid")
        last_fill = integer(data["last_fill"], "quote last fill")
        if last_fill < totals["decision"]["fill_events"] or (last_fill == 0) != (
            totals["decision"]["fill_events"] == 0
        ):
            raise ValueError("quote fill ordinals inconsistent")
        if expected_rows == 0 and digest != sha256(DOMAIN).hexdigest():
            raise ValueError("empty quote audit digest invalid")
        candidate._pending = {int(key): deepcopy(value) for key, value in pending.items()}
        candidate._live, candidate._cohorts, candidate._rejections = (
            deepcopy(dict(live)),
            deepcopy(dict(groups)),
            dict(rejections),
        )
        candidate._next_request, candidate._last_fill = next_request, last_fill
        candidate._trace_count, candidate._trace_sha256 = expected_rows, digest
        return candidate


def verify_quote_trace(
    path: Path, summary: Mapping[str, Any], labels: tuple[str, ...], *, execution_path: Path | None = None
) -> None:
    """Stream a finalized tape and independently check its count/hash/statistics."""
    reducer = RegimeQuoteAudit(summary["symbol"], labels, summary["max_pending_requests"])
    digest, count = sha256(DOMAIN).hexdigest(), 0
    pending: dict[int, dict[str, Any]] = {}
    live: dict[str, dict[str, Any]] = {}
    next_request = 0
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(QUOTE_FIELDS):
            raise ValueError("quote trace fields mismatch")
        for raw in reader:
            if set(raw) != set(QUOTE_FIELDS) or any(value is None for value in raw.values()):
                raise ValueError("malformed quote trace row")
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if value == "":
                    row[key] = None
                elif key in {"logical_ns", "request_id", "fill_id", "qty_lots"}:
                    row[key] = integer(int(value), key)
                elif key == "first_fill":
                    if value not in {"True", "False"}:
                        raise ValueError("invalid quote first-fill boolean")
                    row[key] = value == "True"
                else:
                    row[key] = value
            reducer._reduce(row)
            request, kind = row["request_id"], row["event_type"]
            if kind == "scheduled":
                if request != next_request + 1 or len(pending) >= reducer.max_orders:
                    raise ValueError("serialized quote requests duplicated/reordered or exceed capacity")
                pending[request], next_request = row, request
            elif kind in {"accepted", "rejected", "discarded"}:
                original = pending.pop(request, None)
                if (
                    original is None
                    or original["logical_ns"] > row["logical_ns"]
                    or any(original[key] != row[key] for key in ("decision_label", "side", "quote_slot", "qty_lots"))
                ):
                    raise ValueError("serialized quote arrival/discard lacks its original intent")
                if kind == "accepted":
                    order_id = row["order_id"]
                    if (
                        not isinstance(order_id, str)
                        or not order_id
                        or order_id in live
                        or len(live) >= reducer.max_orders
                    ):
                        raise ValueError("serialized quote acceptance identity/capacity invalid")
                    live[order_id] = row
            elif kind == "terminal":
                original = live.pop(row["order_id"], None)
                if (
                    original is None
                    or original["logical_ns"] > row["logical_ns"]
                    or any(
                        original[key] != row[key]
                        for key in ("request_id", "decision_label", "arrival_label", "side", "quote_slot", "qty_lots")
                    )
                ):
                    raise ValueError("serialized quote terminal lacks its original acceptance")
            elif request > next_request:
                raise ValueError("serialized quote fill lacks an outbound request")
            digest, count = _digest(digest, row), count + 1
    reduced = reducer.summary()
    for basis, groups in reduced["cohorts"].items():
        for label, cell in groups.items():
            for name in (
                *COUNTS,
                "terminal_orders",
                "unique_filled_fraction_of_accepted",
                "acceptance_fraction_of_arrived",
                "filled_fraction_of_accepted_lots",
                "unique_filled_fraction_of_scheduled",
            ):
                if cell[name] != summary["cohorts"][basis][label][name]:
                    raise ValueError("serialized quote cohort statistics mismatch")
            expected_pending = (
                sum(row["decision_label"] == label for row in pending.values()) if basis == "decision" else None
            )
            expected_live = sum(row[f"{basis}_label"] == label for row in live.values())
            if (
                expected_pending != summary["cohorts"][basis][label]["pending_requests"]
                or expected_live != summary["cohorts"][basis][label]["live_orders"]
            ):
                raise ValueError("serialized quote cutoff census mismatch")
    if (
        reduced["rejections_by_reason"] != summary["rejections_by_reason"]
        or count != summary["trace_count"]
        or digest != summary["trace_sha256"]
    ):
        raise ValueError("serialized quote audit count/hash mismatch")
    if len(pending) != summary["retained_pending_requests"] or len(live) != summary["retained_live_bindings"]:
        raise ValueError("serialized quote retained contexts mismatch")
    if execution_path is not None:
        _verify_accounted_fills(path, execution_path)


def _verify_accounted_fills(path: Path, execution_path: Path) -> None:
    """Pair streamed quote fills with the authoritative execution audit, one row at a time."""
    from .validation import strict_json

    with (
        path.open(encoding="utf-8", newline="") as quote_handle,
        execution_path.open(encoding="utf-8", newline="") as execution_handle,
    ):
        quotes = (row for row in csv.DictReader(quote_handle) if row["event_type"] == "fill")
        executions = (row for row in csv.DictReader(execution_handle) if row["event_type"] == "fill")
        for execution in executions:
            attribution = strict_json(execution["quote"]) if execution["quote"] else None
            if attribution is None:
                raise ValueError("native execution fill lacks quote request attribution")
            quote = next(quotes, None)
            if (
                quote is None
                or any(
                    quote[key] != execution[key]
                    for key in (
                        "symbol",
                        "logical_ns",
                        "fill_id",
                        "order_id",
                        "side",
                        "qty_lots",
                        "decision_label",
                        "arrival_label",
                    )
                )
                or quote["request_id"] != str(attribution["request_id"])
                or quote["first_fill"] != str(attribution["first_fill"])
            ):
                raise ValueError("quote fill differs from accounted execution audit")
        if next(quotes, None) is not None:
            raise ValueError("quote audit contains unaccounted fills")


def format_quote_report(summary: Mapping[str, Any]) -> str:
    lines = ["Quote lifecycle cohorts (descriptive only)", summary["denominator_note"]]
    for basis, groups in summary["cohorts"].items():
        lines.append(f"\nOriginal {basis} label:")
        for label, cell in groups.items():
            fraction = cell["unique_filled_fraction_of_accepted"]
            rate = "n/a" if fraction is None else f"{fraction:.2%}"
            lines.append(
                f"  {label}: sent={cell['scheduled_requests'] if basis == 'decision' else 'n/a'}; "
                f"arrived/accepted/rejected={cell['arrived_requests']}/{cell['accepted_orders']}/{cell['rejected_requests']}; "
                f"unique filled={cell['unique_filled_orders']} ({rate} of accepted); "
                f"partial/full fill events={cell['fill_events']}; filled lots={cell['filled_qty_lots']}; "
                f"pending/live at cutoff={cell['pending_requests']}/{cell['live_orders']}"
            )
    return "\n".join(lines)
