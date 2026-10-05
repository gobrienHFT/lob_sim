"""Left-continuous, integer-nanosecond inventory and reservation audit.

This sidecar has no risk-policy or accounting authority. Each boundary freezes
the information actually available then; its state covers [boundary, next).
Staleness is an explicit boundary even when no subsequent market row arrives.
There is no interpolation, EOF extrapolation or sample-count time weighting.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..sim.sinks import EventSink, NullSink
from .execution import RegimeExecutionAudit, _decimal, capture_stage
from .validation import canonical_json, integer, require_keys, strict_json

RISK_FIELDS = (
    "schema_version",
    "symbol",
    "model_sha256",
    "logical_ns",
    "reason",
    "clock_basis",
    "stage",
    "regime_until_ns",
    "mark_until_ns",
    "mid_twice_tick",
    "inventory_lots",
    "live_bid_lots",
    "live_ask_lots",
    "pending_bid_lots",
    "pending_ask_lots",
    "order_notional_tick_lots",
    "halted",
    "tick_size",
    "step_size",
    "contract_multiplier",
    "regime_trace_count",
    "regime_trace_sha256",
)
CHAIN_DOMAIN = b"lob_sim.hmm_risk.v1"
BASES = ("raw_map", "active")
COUNTERS = (
    "duration_ns",
    "inventory_lots_ns",
    "absolute_inventory_lots_ns",
    "squared_inventory_lots_ns",
    "max_absolute_inventory_lots",
    "live_bid_lots_ns",
    "live_ask_lots_ns",
    "pending_bid_lots_ns",
    "pending_ask_lots_ns",
    "halted_ns",
    "marked_duration_ns",
    "absolute_inventory_notional_twice_tick_lots_ns",
    "reserved_notional_twice_tick_lots_ns",
    "max_reserved_notional_twice_tick_lots",
)


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"invalid {name} SHA-256")
    return value


def _signed(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be a signed integer")
    return value


def _advance(digest: str, row: Mapping[str, Any]) -> str:
    return sha256(bytes.fromhex(digest) + canonical_json(dict(row)).encode()).hexdigest()


def _ratio(numerator: int, denominator: int) -> str | None:
    return str(Fraction(numerator, denominator)) if denominator else None


def _quantity(numerator: int, denominator: int, quantum: Decimal) -> str | None:
    if not denominator:
        return None
    # Derived presentation only. Exact integer sufficient statistics are retained.
    with localcontext() as context:
        context.prec = 50
        return str(Decimal(numerator) * quantum / Decimal(denominator))


def boundary(
    regime: Any,
    logical_ns: int,
    reason: str,
    *,
    spec: Any,
    book: Any,
    orders: Any,
    actions: Any,
    inventory_lots: int,
    mark_valid: bool,
    halted: bool,
    receive_clock: bool,
) -> dict[str, Any]:
    """Read-only adapter shared by native boundaries and checkpoint validation."""
    symbol = regime.settings.symbol
    context = regime._contexts.get(symbol)
    last_book = context.sampler._last_book_ns if context is not None else None
    expiry = last_book + regime.spec.stale_after_ns + 1 if last_book is not None else None
    stage = capture_stage(regime.snapshot(logical_ns), logical_ns)
    marked = bool(mark_valid and expiry is not None and logical_ns < expiry and book and book.bids and book.asks)
    live = [order for order in orders if order.symbol == symbol]
    pending = [action for action in actions if action.symbol == symbol and action.kind == "order_arrival"]
    return {
        "schema_version": "lob_sim.hmm_risk_boundary.v1",
        "symbol": symbol,
        "model_sha256": regime.settings.model.model_sha256,
        "logical_ns": logical_ns,
        "reason": reason,
        "clock_basis": "receive_nanoseconds" if receive_clock else "legacy_compatibility_nanoseconds",
        "stage": stage,
        "regime_until_ns": expiry if stage["status"] == "VALID" else None,
        "mark_until_ns": expiry if marked else None,
        "mid_twice_tick": max(book.bids) + min(book.asks) if marked else None,
        "inventory_lots": inventory_lots,
        "live_bid_lots": sum(order.remaining_lots for order in live if order.side == "bid"),
        "live_ask_lots": sum(order.remaining_lots for order in live if order.side == "ask"),
        "pending_bid_lots": sum(action.payload["qty_lots"] for action in pending if action.payload["side"] == "bid"),
        "pending_ask_lots": sum(action.payload["qty_lots"] for action in pending if action.payload["side"] == "ask"),
        "order_notional_tick_lots": sum(order.price_tick * order.remaining_lots for order in live)
        + sum(action.payload["price_tick"] * action.payload["qty_lots"] for action in pending),
        "halted": halted,
        "tick_size": str(spec.tick_size),
        "step_size": str(spec.step_size),
        "contract_multiplier": str(spec.contract_multiplier),
        "regime_trace_count": regime._trace_count,
        "regime_trace_sha256": regime._trace_sha256,
    }


class RegimeRiskAudit:
    """Fixed K+2 cells per labeling basis and one current boundary, never history."""

    def __init__(self, model_sha256: str, symbol: str, state_count: int, sink: EventSink | None = None) -> None:
        self.model_sha256 = _digest(model_sha256, "risk model")
        self.symbol = symbol
        self._stages = RegimeExecutionAudit(model_sha256, symbol, state_count, (), 1)
        self.labels = self._stages.labels
        self.state_count = state_count
        self.sink = sink if sink is not None else NullSink()
        self._cells = {basis: {label: dict.fromkeys(COUNTERS, 0) for label in self.labels} for basis in BASES}
        self._anchor: dict[str, Any] | None = None
        self._first_ns: int | None = None
        self._trace_count = 0
        self._trace_sha256 = sha256(CHAIN_DOMAIN).hexdigest()
        self._grid: tuple[str, str, str] | None = None

    def _row(self, value: object) -> dict[str, Any]:
        row = dict(require_keys(value, set(RISK_FIELDS), "risk boundary"))
        if row["schema_version"] != "lob_sim.hmm_risk_boundary.v1" or (
            row["symbol"] != self.symbol or row["model_sha256"] != self.model_sha256
        ):
            raise ValueError("risk boundary identity mismatch")
        now = integer(row["logical_ns"], "risk boundary time")
        if row["reason"] not in {
            "before_market",
            "after_market",
            "after_action",
            "fill_accounted",
            "after_record",
            "finish",
        }:
            raise ValueError("unknown risk boundary reason")
        if row["clock_basis"] not in {"receive_nanoseconds", "legacy_compatibility_nanoseconds"}:
            raise ValueError("unknown risk clock basis")
        stage = self._stages._stage(row["stage"])
        if stage is None or stage["logical_ns"] != now:
            raise ValueError("risk stage time mismatch")
        row["stage"] = stage
        for key in ("regime_until_ns", "mark_until_ns"):
            if row[key] is not None and integer(row[key], key) <= now:
                raise ValueError("risk expiry must be strictly after its boundary")
        if (stage["status"] == "VALID") != (row["regime_until_ns"] is not None):
            raise ValueError("risk regime validity/expiry mismatch")
        mid = row["mid_twice_tick"]
        if mid is not None:
            integer(mid, "risk mid twice tick", minimum=1)
        if (mid is None) != (row["mark_until_ns"] is None):
            raise ValueError("risk mark validity/expiry mismatch")
        _signed(row["inventory_lots"], "inventory lots")
        for key in (
            "live_bid_lots",
            "live_ask_lots",
            "pending_bid_lots",
            "pending_ask_lots",
            "order_notional_tick_lots",
            "regime_trace_count",
        ):
            integer(row[key], key)
        _digest(row["regime_trace_sha256"], "risk regime trace")
        if type(row["halted"]) is not bool:
            raise ValueError("risk halted must be boolean")
        grid = tuple(row[key] for key in ("tick_size", "step_size", "contract_multiplier"))
        for name, value in zip(("tick_size", "step_size", "contract_multiplier"), grid, strict=True):
            if _decimal(value, name, nonnegative=True) <= 0:
                raise ValueError("risk grid must be positive")
        if self._grid is not None and grid != self._grid:
            raise ValueError("risk audit cannot combine different instrument grids")
        if self._anchor is not None and (
            now < self._anchor["logical_ns"]
            or row["regime_trace_count"] < self._anchor["regime_trace_count"]
            or row["clock_basis"] != self._anchor["clock_basis"]
        ):
            raise ValueError("risk boundary clock/trace regression")
        return deepcopy(row)

    def _integrate(self, end: int) -> None:
        previous = self._anchor
        if previous is None:
            return
        start = previous["logical_ns"]
        boundaries = sorted(
            {
                start,
                end,
                *(
                    v
                    for v in (previous["regime_until_ns"], previous["mark_until_ns"])
                    if v is not None and start < v < end
                ),
            }
        )
        inventory = previous["inventory_lots"]
        for left, right in zip(boundaries, boundaries[1:]):
            duration = right - left
            if not duration:
                continue
            valid = previous["regime_until_ns"] is not None and left < previous["regime_until_ns"]
            marked = previous["mark_until_ns"] is not None and left < previous["mark_until_ns"]
            for basis in BASES:
                state = previous["stage"]["raw_map_state" if basis == "raw_map" else "active_state"]
                label = f"STATE_{state}" if valid and state is not None else "UNCONFIRMED" if valid else "UNAVAILABLE"
                cell = self._cells[basis][label]
                cell["duration_ns"] += duration
                cell["inventory_lots_ns"] += inventory * duration
                cell["absolute_inventory_lots_ns"] += abs(inventory) * duration
                cell["squared_inventory_lots_ns"] += inventory * inventory * duration
                cell["max_absolute_inventory_lots"] = max(cell["max_absolute_inventory_lots"], abs(inventory))
                cell["halted_ns"] += int(previous["halted"]) * duration
                for key in ("live_bid_lots", "live_ask_lots", "pending_bid_lots", "pending_ask_lots"):
                    cell[key + "_ns"] += previous[key] * duration
                if marked:
                    notional = abs(inventory) * previous["mid_twice_tick"]
                    reserved = notional + 2 * previous["order_notional_tick_lots"]
                    cell["marked_duration_ns"] += duration
                    cell["absolute_inventory_notional_twice_tick_lots_ns"] += notional * duration
                    cell["reserved_notional_twice_tick_lots_ns"] += reserved * duration
                    cell["max_reserved_notional_twice_tick_lots"] = max(
                        cell["max_reserved_notional_twice_tick_lots"], reserved
                    )

    def observe(self, value: Mapping[str, Any]) -> None:
        row = self._row(value)  # Invalid input is atomic, including aggregate state.
        digest = _advance(self._trace_sha256, row)
        self.sink.write(deepcopy(row))  # A failed write cannot claim an accepted boundary.
        self._integrate(row["logical_ns"])
        self._anchor = row
        self._grid = tuple(row[key] for key in ("tick_size", "step_size", "contract_multiplier"))
        if self._first_ns is None:
            self._first_ns = row["logical_ns"]
        self._trace_count += 1
        self._trace_sha256 = digest

    def summary(self) -> dict[str, Any]:
        grid = self._grid
        step = Decimal(grid[1]) if grid else Decimal(0)
        with localcontext() as context:
            context.prec = 50
            quantum = Decimal(grid[0]) * step * Decimal(grid[2]) / 2 if grid else Decimal(0)
            variance_quantum = step * step
        conditioned: dict[str, Any] = deepcopy(self._cells)
        total = 0 if self._anchor is None else self._anchor["logical_ns"] - int(self._first_ns or 0)
        for cells in conditioned.values():
            for cell in cells.values():
                duration, marked = cell["duration_ns"], cell["marked_duration_ns"]
                n, s = cell["inventory_lots_ns"], cell["squared_inventory_lots_ns"]
                variance = s * duration - n * n
                cell.update(
                    wall_time_fraction=_ratio(duration, total),
                    mean_inventory_lots=_ratio(n, duration),
                    mean_absolute_inventory_lots=_ratio(cell["absolute_inventory_lots_ns"], duration),
                    inventory_variance_lots=_ratio(variance, duration * duration),
                    mean_absolute_inventory_qty=_quantity(cell["absolute_inventory_lots_ns"], duration, step),
                    inventory_variance_qty=_quantity(variance, duration * duration, variance_quantum),
                    marked_coverage=_ratio(marked, duration),
                    mean_absolute_inventory_notional=_quantity(
                        cell["absolute_inventory_notional_twice_tick_lots_ns"], marked, quantum
                    ),
                    mean_reserved_notional=_quantity(cell["reserved_notional_twice_tick_lots_ns"], marked, quantum),
                    max_reserved_notional=_quantity(cell["max_reserved_notional_twice_tick_lots"], 1, quantum)
                    if marked
                    else None,
                )
        return {
            "schema_version": "lob_sim.hmm_risk_summary.v1",
            "symbol": self.symbol,
            "model_sha256": self.model_sha256,
            "state_count": self.state_count,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "first_ns": self._first_ns,
            "last_ns": self._anchor["logical_ns"] if self._anchor else None,
            "duration_ns": total,
            "grid": list(grid) if grid else None,
            "clock_basis": self._anchor["clock_basis"] if self._anchor else None,
            "conditioned": conditioned,
            "memory_bounded_by_tape_duration": self.sink.memory_bounded,
            "claim_ready": False,
            "time_basis": "left-continuous actual availability; positive-duration intervals only; no extrapolation",
            "reservation_basis": "absolute marked inventory plus live and pending limit-order notional; pending cancels remain live",
            "coverage_note": "missing/stale marks excluded from notional denominators, never valued as zero; inventory always retained",
            "scope_note": "single modeled symbol; not portfolio drawdown, funding, economic benefit or out-of-sample evidence",
        }

    def checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_risk_checkpoint.v1",
            "model_sha256": self.model_sha256,
            "symbol": self.symbol,
            "state_count": self.state_count,
            "anchor": deepcopy(self._anchor),
            "first_ns": self._first_ns,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "cells": deepcopy(self._cells),
        }

    def validated_copy(self, value: object) -> RegimeRiskAudit:
        data = require_keys(value, set(self.checkpoint()), "risk checkpoint")
        for key in ("schema_version", "model_sha256", "symbol", "state_count"):
            if data[key] != self.checkpoint()[key] or type(data[key]) is not type(self.checkpoint()[key]):
                raise ValueError("risk checkpoint identity mismatch")
        clone = RegimeRiskAudit(self.model_sha256, self.symbol, self.state_count, self.sink)
        clone._trace_count = integer(data["trace_count"], "risk trace count")
        clone._trace_sha256 = _digest(data["trace_sha256"], "risk trace")
        if data["anchor"] is not None:
            clone._anchor = clone._row(data["anchor"])
            clone._grid = tuple(clone._anchor[key] for key in ("tick_size", "step_size", "contract_multiplier"))
            clone._first_ns = integer(data["first_ns"], "risk first boundary")
            if not clone._trace_count or clone._first_ns > clone._anchor["logical_ns"]:
                raise ValueError("risk checkpoint anchors inconsistent")
        elif (
            data["first_ns"] is not None
            or clone._trace_count
            or clone._trace_sha256 != sha256(CHAIN_DOMAIN).hexdigest()
        ):
            raise ValueError("empty risk checkpoint inconsistent")
        cells = require_keys(data["cells"], set(BASES), "risk bases")
        totals = []
        duration = 0 if clone._anchor is None else clone._anchor["logical_ns"] - int(clone._first_ns or 0)
        for basis in BASES:
            parsed = {}
            for label, cell in require_keys(cells[basis], set(self.labels), "risk labels").items():
                values = dict(require_keys(cell, set(COUNTERS), "risk cell"))
                for key, amount in values.items():
                    (_signed if key == "inventory_lots_ns" else integer)(amount, key)
                d, n, s = values["duration_ns"], values["inventory_lots_ns"], values["squared_inventory_lots_ns"]
                absolute, maximum = values["absolute_inventory_lots_ns"], values["max_absolute_inventory_lots"]
                if (
                    values["halted_ns"] > d
                    or values["marked_duration_ns"] > d
                    or abs(n) > absolute
                    or absolute > maximum * d
                    or s > maximum * maximum * d
                    or n * n > s * d
                    or absolute * absolute > s * d
                    or (not d and any(values.values()))
                    or (not values["marked_duration_ns"] and any(values[key] for key in COUNTERS[-3:]))
                    or values["reserved_notional_twice_tick_lots_ns"]
                    < values["absolute_inventory_notional_twice_tick_lots_ns"]
                    or values["reserved_notional_twice_tick_lots_ns"]
                    > values["max_reserved_notional_twice_tick_lots"] * values["marked_duration_ns"]
                ):
                    raise ValueError("risk cell conservation inconsistent")
                parsed[label] = values
            clone._cells[basis] = parsed
            if sum(cell["duration_ns"] for cell in parsed.values()) != duration:
                raise ValueError("risk wall-time census inconsistent")
            totals.append(
                {
                    key: max(cell[key] for cell in parsed.values())
                    if key.startswith("max_")
                    else sum(cell[key] for cell in parsed.values())
                    for key in COUNTERS
                }
            )
        if totals[0] != totals[1]:
            raise ValueError("risk labeling marginals inconsistent")
        return clone

    def validate_continuation(self, core: Mapping[str, Any], regime: Any) -> None:
        spec = core["specs"].get(self.symbol)
        if spec is None:
            if self._anchor is not None:
                raise ValueError("risk checkpoint has no core instrument")
            return
        now = core["last_logical_ns"]
        syncer = core["syncers"].get(self.symbol)
        schema = core["capture_schema_version"]
        mark_valid = bool(
            syncer is not None
            and syncer.synced
            and core["depth_stream_valid"].get(self.symbol, schema < 3)
            and not core["clock_invalidated"]
            and not core["clock_regressions"]
            and not core["receive_clock_regressions"]
            and (schema < 3 or core["receive_clock"])
            and core["capture_valid"]
        )
        position = core["metrics"]["position"].get(self.symbol)
        expected = boundary(
            regime,
            now,
            self._anchor["reason"] if self._anchor is not None else "after_record",
            spec=spec,
            book=core["books"].get(self.symbol),
            orders=core["fill_model"]["_orders"].values(),
            actions=core["actions"],
            inventory_lots=position.lot_size if position is not None else 0,
            mark_valid=mark_valid,
            halted=core["trading_halted"],
            receive_clock=schema >= 3 and core["receive_clock"],
        )
        if canonical_json(expected) != canonical_json(self._anchor):
            raise ValueError("risk checkpoint differs from core/regime causal boundary")


def verify_risk_trace(
    path: Path,
    summary: Mapping[str, Any],
    *,
    regime_path: Path | None = None,
    regime_summary: Mapping[str, Any] | None = None,
) -> None:
    """Bounded serialized re-reduction, including every derived denominator."""
    subject = RegimeRiskAudit(summary["model_sha256"], summary["symbol"], summary["state_count"])
    integer_fields = set(RISK_FIELDS) - {
        "schema_version",
        "symbol",
        "model_sha256",
        "reason",
        "clock_basis",
        "stage",
        "halted",
        "tick_size",
        "step_size",
        "contract_multiplier",
        "regime_trace_sha256",
    }
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(RISK_FIELDS):
            raise ValueError("risk trace field contract mismatch")
        for raw in reader:
            if set(raw) != set(RISK_FIELDS) or any(value is None for value in raw.values()):
                raise ValueError("malformed risk trace row")
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key == "stage":
                    row[key] = strict_json(value)
                elif key == "halted":
                    if value not in {"True", "False"}:
                        raise ValueError("invalid risk boolean")
                    row[key] = value == "True"
                elif key in integer_fields:
                    row[key] = int(value) if value else None
                else:
                    row[key] = value
            subject.observe(row)
    expected = subject.summary()
    expected["memory_bounded_by_tape_duration"] = summary["memory_bounded_by_tape_duration"]
    if type(summary["memory_bounded_by_tape_duration"]) is not bool or canonical_json(expected) != canonical_json(
        dict(summary)
    ):
        raise ValueError("serialized risk time-weighted summary mismatch")
    if (regime_path is None) != (regime_summary is None):
        raise ValueError("risk regime-link verification requires both path and summary")
    if regime_path is not None and regime_summary is not None:
        _verify_regime_links(path, regime_path, regime_summary)


def _verify_regime_links(path: Path, regime_path: Path, summary: Mapping[str, Any]) -> None:
    from .observation import CHAIN_DOMAIN as REGIME_DOMAIN, advance_trace_digest, iter_trace_rows

    rows = iter_trace_rows(regime_path)
    latest = None
    count, digest = 0, sha256(REGIME_DOMAIN).hexdigest()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for risk in csv.DictReader(handle):
                target = int(risk["regime_trace_count"])
                now = int(risk["logical_ns"])
                if target < count or target > summary["trace_count"]:
                    raise ValueError("risk regime prefix census inconsistent")
                while count < target:
                    try:
                        latest = next(rows)
                    except StopIteration as exc:
                        raise ValueError("risk exceeds serialized regime prefix") from exc
                    if latest["available_at_ns"] > now:
                        raise ValueError("risk references future regime availability")
                    digest = advance_trace_digest(digest, latest)
                    count += 1
                if digest != risk["regime_trace_sha256"]:
                    raise ValueError("risk regime prefix digest mismatch")
                stage = strict_json(risk["stage"])
                if stage["status"] == "VALID":
                    if (
                        latest is None
                        or latest["event_type"] != "sample"
                        or latest["status"] != "VALID"
                        or (canonical_json(stage) != canonical_json(capture_stage(latest, now)))
                    ):
                        raise ValueError("risk stage differs from available regime prefix")
                elif (
                    latest is not None
                    and stage["status"] != latest["status"]
                    and not (stage["status"] == "STALE" and latest["status"] == "VALID")
                ):
                    raise ValueError("risk invalidity differs from available regime prefix")
        if count != summary["trace_count"] or digest != summary["trace_sha256"]:
            raise ValueError("risk misses finalized regime prefix")
    finally:
        rows.close()


def format_risk_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "Causal time-weighted risk (single symbol, actual availability, not sample fractions).",
        f"Clock: {summary['clock_basis']}; duration: {summary['duration_ns']} ns.",
        "Notional means use marked time only; missing marks are not zero. Maxima exclude zero-duration transients.",
        "Live-plus-pending reservation includes pending cancels. No state-level drawdown contribution is inferred.",
    ]
    for basis in BASES:
        lines.append(f"{basis} labels:")
        for label, cell in summary["conditioned"][basis].items():
            if not cell["duration_ns"]:
                continue
            lines.append(
                f"  {label}: duration_ns={cell['duration_ns']}, mean_abs_qty={cell['mean_absolute_inventory_qty']}, "
                f"variance_qty={cell['inventory_variance_qty']}, max_abs_lots={cell['max_absolute_inventory_lots']}, "
                f"marked_coverage={cell['marked_coverage']}, mean_abs_notional={cell['mean_absolute_inventory_notional']}, "
                f"mean_reserved_notional={cell['mean_reserved_notional']}, halted_ns={cell['halted_ns']}"
            )
    return "\n".join(lines)
