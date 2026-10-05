"""Independent, bounded reconstruction of single-symbol scenario economics.

Trade cash flows are exact integer tick-lots; quote-currency fees and marked
values use rational arithmetic. This is an analysis consumer of existing
audits, never an execution, risk or core accounting authority.
"""

from __future__ import annotations

import csv
from collections.abc import Generator, Mapping
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..sim.export import iter_fill_audit_rows
from ..sim.metrics import FILL_AUDIT_CHAIN_DOMAIN, advance_audit_digest
from .execution import EXECUTION_FIELDS, _decimal, verify_execution_trace
from .risk import BASES, iter_risk_rows, verify_risk_trace
from .validation import integer, strict_json


def _fraction(value: object, name: str, *, positive: bool = False) -> Fraction:
    result = Fraction(_decimal(value, name))
    if positive and result <= 0:
        raise ValueError(f"economic {name} must be positive")
    return result


def _label(row: Mapping[str, Any] | None, basis: str, now: int | None = None) -> str:
    if row is None or row["stage"]["status"] != "VALID":
        return "UNAVAILABLE"
    if now is not None and now >= row["regime_until_ns"]:
        return "UNAVAILABLE"
    key = "raw_map_state" if basis == "raw_map" else "active_state"
    state = row["stage"][key]
    return f"STATE_{state}" if state is not None else "UNCONFIRMED"


def _cell() -> dict[str, Any]:
    return {
        "fill_count": 0,
        "turnover_tick_lots": 0,
        "cash_tick_lots": 0,
        "fees": Fraction(0),
        "observed_gross_delta": Fraction(0),
        "observed_net_delta": Fraction(0),
        "max_observed_drawdown": Fraction(0),
        "drawdown_extension": Fraction(0),
    }


class EconomicLedger:
    """One current/last-valued boundary and fixed cells, without a fill history."""

    def __init__(self, grid: list[str], state_count: int) -> None:
        integer(state_count, "economic state count", minimum=2)
        if state_count > 5:
            raise ValueError("economic state count exceeds model contract")
        self.tick, self.step, self.multiplier = (
            _fraction(value, name, positive=True)
            for name, value in zip(("tick", "step", "contract multiplier"), grid, strict=True)
        )
        self.quantum = self.tick * self.step * self.multiplier
        self.labels = tuple(f"STATE_{i}" for i in range(state_count)) + ("UNCONFIRMED", "UNAVAILABLE", "UNATTRIBUTED")
        self.cells = {basis: {label: _cell() for label in self.labels} for basis in BASES}
        self.inventory = self.cash = self.turnover = self.fills = 0
        self.fees = self.paid_fees = self.rebates = Fraction(0)
        self.currency: str | None = None
        self.previous: Mapping[str, Any] | None = None
        self.last_gross = self.last_net = Fraction(0)
        self.final_gross: Fraction | None = None
        self.final_net: Fraction | None = None
        self.peak = self.max_drawdown = Fraction(0)
        self.boundaries = self.known_boundaries = self.gap_bridges = self.unpriced_inventory_ns = 0
        self.gap = False

    def on_fill(self, fill: Mapping[str, Any], execution: Mapping[str, Any], now: int) -> None:
        """Reconstruct cash/position from serialized fills, not average-cost PnL."""
        lots = _fraction(fill["qty"], "fill quantity", positive=True) / self.step
        ticks = _fraction(fill["price"], "fill price", positive=True) / self.tick
        if lots.denominator != 1 or ticks.denominator != 1:
            raise ValueError("economic fill is off the instrument grid")
        if fill["side"] not in {"bid", "ask"}:
            raise ValueError("economic fill side invalid")
        fee = _fraction(fill["fee"], "fill fee")
        if (
            _fraction(fill["contract_multiplier"], "fill multiplier", positive=True) != self.multiplier
            or _fraction(fill["notional"], "fill notional", positive=True) != ticks * lots * self.quantum
        ):
            raise ValueError("economic fill contract/notional mismatch")
        currency = fill["fee_currency"]
        if not isinstance(currency, str) or not currency or (self.currency is not None and currency != self.currency):
            raise ValueError("economic fees cannot combine different quote currencies")
        if (
            integer(execution["logical_ns"], "economic fill availability") > now
            or execution["side"] != fill["side"]
            or execution["symbol"] != fill["symbol"]
            or integer(execution["qty_lots"], "economic execution lots", minimum=1) != lots
            or _fraction(execution["qty"], "execution quantity") != lots * self.step
            or _fraction(execution["fee"], "execution fee") != fee
            or execution["fill_source"] != fill["fill_source"]
            or execution["order_id"] != (fill["order_id"] or "")
        ):
            raise ValueError("economic fill differs from frozen execution evidence")
        sign = 1 if fill["side"] == "bid" else -1
        cash = -sign * int(ticks * lots)
        stage = execution["pre_fill"]
        frozen = {"stage": stage} if stage is not None else None
        for basis in BASES:
            cell = self.cells[basis][_label(frozen, basis)]
            cell["fill_count"] += 1
            cell["turnover_tick_lots"] += int(ticks * lots)
            cell["cash_tick_lots"] += cash
            cell["fees"] += fee
        self.inventory += sign * int(lots)
        self.cash += cash
        self.turnover += int(ticks * lots)
        self.fees += fee
        self.paid_fees += max(fee, Fraction(0))
        self.rebates += max(-fee, Fraction(0))
        self.fills += 1
        self.currency = currency

    def on_boundary(self, row: Mapping[str, Any]) -> None:
        if row["inventory_lots"] != self.inventory:
            raise ValueError("economic fill ledger does not conserve boundary inventory")
        previous = self.previous
        now = row["logical_ns"]
        if previous is not None and previous["inventory_lots"]:
            start = previous["logical_ns"]
            priced_until = previous["mark_until_ns"] or start
            unpriced = max(0, now - max(start, priced_until))
            self.unpriced_inventory_ns += unpriced
            self.gap |= bool(unpriced)
        self.boundaries += 1
        mid = row["mid_twice_tick"]
        marked_inventory = Fraction(self.inventory * mid, 2) * self.quantum if mid is not None else None
        self.final_gross = (
            self.cash * self.quantum + marked_inventory
            if marked_inventory is not None
            else self.cash * self.quantum
            if not self.inventory
            else None
        )
        self.final_net = self.final_gross - self.fees if self.final_gross is not None else None
        if self.final_net is None:
            self.gap = True
        else:
            assert self.final_gross is not None
            self.known_boundaries += 1
            gross_delta = self.final_gross - self.last_gross
            net_delta = self.final_net - self.last_net
            self.peak = max(self.peak, self.final_net)
            drawdown = self.peak - self.final_net
            extension = max(Fraction(0), drawdown - self.max_drawdown)
            self.max_drawdown = max(self.max_drawdown, drawdown)
            for basis in BASES:
                # No later regime is backfilled into an earlier holding interval.
                holding = "UNATTRIBUTED" if self.gap else _label(previous or row, basis, now)
                cell = self.cells[basis][holding]
                cell["observed_gross_delta"] += gross_delta
                cell["observed_net_delta"] += net_delta
                point = self.cells[basis][_label(row, basis)]
                point["max_observed_drawdown"] = max(point["max_observed_drawdown"], drawdown)
                point["drawdown_extension"] += extension
            self.gap_bridges += int(self.gap)
            self.gap = False
            self.last_gross, self.last_net = self.final_gross, self.final_net
        self.previous = row

    def summary(self) -> dict[str, Any]:
        def money(value: Fraction | None) -> str | None:
            return str(value) if value is not None else None

        conditioned: dict[str, Any] = {}
        for basis, groups in self.cells.items():
            conditioned[basis] = {}
            for label, cell in groups.items():
                conditioned[basis][label] = {
                    **{key: cell[key] for key in ("fill_count", "cash_tick_lots", "turnover_tick_lots")},
                    **{
                        key + "_quote_rational": money(cell[key])
                        for key in (
                            "fees",
                            "observed_gross_delta",
                            "observed_net_delta",
                            "max_observed_drawdown",
                            "drawdown_extension",
                        )
                    },
                }
        return {
            "schema_version": "lob_sim.hmm_economics.v1",
            "quote_currency": self.currency,
            "fill_count": self.fills,
            "inventory_lots": self.inventory,
            "cash_tick_lots": self.cash,
            "turnover_tick_lots": self.turnover,
            "cash_quote_rational": money(self.cash * self.quantum),
            "turnover_quote_rational": money(self.turnover * self.quantum),
            "fees_quote_rational": money(self.fees),
            "paid_fees_quote_rational": money(self.paid_fees),
            "rebates_quote_rational": money(self.rebates),
            "net_cash_quote_rational": money(self.cash * self.quantum - self.fees),
            "gross_marked_pnl_quote_rational": money(self.final_gross),
            "net_marked_pnl_quote_rational": money(self.final_net),
            "last_observed_net_equity_quote_rational": money(self.last_net),
            "observed_max_drawdown_quote_rational": money(self.max_drawdown),
            "boundary_count": self.boundaries,
            "equity_known_boundary_count": self.known_boundaries,
            "unpriced_inventory_ns": self.unpriced_inventory_ns,
            "gap_bridge_count": self.gap_bridges,
            "conditioned": conditioned,
            "memory_bounded_by_tape_duration": True,
            "claim_ready": False,
            "valuation_note": "linear contract, zero initial inventory/cash; net cash plus fresh marked inventory; flat equity needs no price",
            "fill_label_note": "cash/turnover/fees use frozen pre-fill labels; these fee cells are not subtracted again from net-delta cells",
            "delta_label_note": "endpoint changes use the prior available holding label; gap bridges are UNATTRIBUTED; not causal state PnL",
            "drawdown_note": "running peak of observed net equity from zero; missing marks do not reset peaks; maxima by current label are not additive",
            "extension_note": "new observed maximum-drawdown increments tagged at detection; additive bookkeeping, not causal drawdown contribution",
            "scope_note": "single-symbol configured scenario; no funding, portfolio netting, continuous-path drawdown, private fills or out-of-sample benefit",
        }


def _execution_fills(path: Path) -> Generator[dict[str, Any], None, None]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(EXECUTION_FIELDS):
            raise ValueError("economic execution schema mismatch")
        for row in reader:
            if row["event_type"] == "fill":
                yield {
                    **row,
                    "fill_id": int(row["fill_id"]),
                    "logical_ns": int(row["logical_ns"]),
                    "qty_lots": int(row["qty_lots"]),
                    "pre_fill": strict_json(row["pre_fill"]),
                }


def reconstruct_economics(
    risk_path: Path,
    trades_path: Path,
    execution_path: Path,
    *,
    risk_summary: Mapping[str, Any],
    execution_summary: Mapping[str, Any],
    fill_count: int,
    fill_sha256: str,
) -> dict[str, Any]:
    """Re-read three immutable streams, joining exact causal fill-prefix IDs.

    Every global fill is hashed, even for other symbols. Selected-symbol cash
    cannot silently inherit global PnL, nor can later fills enter earlier marks.
    No result is returned before all three streams and their totals reconcile.
    """
    verify_risk_trace(risk_path, risk_summary)
    verify_execution_trace(execution_path, execution_summary)
    if (
        risk_summary["model_sha256"] != execution_summary["model_sha256"]
        or risk_summary["symbol"] != execution_summary["symbol"]
        or risk_summary["grid"] is None
        or risk_summary["state_count"] != len(execution_summary["conditioned"]["pre_fill"]) - 2
    ):
        raise ValueError("economic parent identities/grid mismatch")
    integer(fill_count, "economic global fill count")
    ledger = EconomicLedger(risk_summary["grid"], risk_summary["state_count"])
    count, digest = 0, sha256(FILL_AUDIT_CHAIN_DOMAIN.encode()).digest()
    fills, executions = iter_fill_audit_rows(trades_path), _execution_fills(execution_path)
    try:
        for boundary in iter_risk_rows(risk_path):
            target = boundary["fill_audit_count"]
            if target < count or target > fill_count:
                raise ValueError("economic fill prefix count mismatch")
            if (
                ledger.boundaries
                and target != count
                and (target != count + 1 or boundary["reason"] != "fill_accounted")
            ):
                raise ValueError("economic fill prefix lacks its single accounting boundary")
            while count < target:
                try:
                    fill = next(fills)
                except StopIteration as exc:
                    raise ValueError("economic fill prefix exceeds trade stream") from exc
                count += 1
                digest = advance_audit_digest(digest, fill)
                if fill["symbol"] == risk_summary["symbol"]:
                    try:
                        execution = next(executions)
                    except StopIteration as exc:
                        raise ValueError("economic fill missing execution evidence") from exc
                    if execution["fill_id"] != count or boundary["reason"] != "fill_accounted":
                        raise ValueError("economic fill is not at its causal accounting boundary")
                    ledger.on_fill(fill, execution, boundary["logical_ns"])
            if boundary["fill_audit_sha256"] != digest.hex():
                raise ValueError("economic fill prefix digest mismatch")
            ledger.on_boundary(boundary)
        if (
            count != fill_count
            or digest.hex() != fill_sha256
            or next(fills, None) is not None
            or next(executions, None) is not None
            or ledger.fills != execution_summary["fill_count"]
        ):
            raise ValueError("economic finalized fill census/digest mismatch")
    finally:
        close_fills = getattr(fills, "close", None)
        if close_fills is not None:
            close_fills()
        executions.close()
    return {
        **ledger.summary(),
        "model_sha256": risk_summary["model_sha256"],
        "symbol": risk_summary["symbol"],
        "grid": risk_summary["grid"],
        "clock_basis": risk_summary["clock_basis"],
        "first_ns": risk_summary["first_ns"],
        "last_ns": risk_summary["last_ns"],
        "parents": {
            "risk_trace_count": risk_summary["trace_count"],
            "risk_trace_sha256": risk_summary["trace_sha256"],
            "execution_trace_count": execution_summary["trace_count"],
            "execution_trace_sha256": execution_summary["trace_sha256"],
            "global_fill_count": count,
            "global_fill_sha256": digest.hex(),
        },
    }


def format_economic_report(summary: Mapping[str, Any]) -> str:
    def display(value: str | None) -> str:
        if value is None:
            return "unknown"
        with localcontext() as context:
            context.prec = 50
            number = Fraction(value)
            return str(Decimal(number.numerator) / Decimal(number.denominator))

    lines = [
        "Reconciled single-symbol scenario economics (exact cash/fee ledger, not a profitability claim).",
        f"Fills: {summary['fill_count']}; inventory: {summary['inventory_lots']} lots; currency: {summary['quote_currency']}.",
        f"Gross marked PnL: {display(summary['gross_marked_pnl_quote_rational'])}; "
        f"fees: {display(summary['fees_quote_rational'])}; net: {display(summary['net_marked_pnl_quote_rational'])}; "
        f"turnover: {display(summary['turnover_quote_rational'])}.",
        f"Observed drawdown: {display(summary['observed_max_drawdown_quote_rational'])}; "
        f"unpriced held-inventory duration: {summary['unpriced_inventory_ns']} ns; gap bridges: {summary['gap_bridge_count']}.",
        "Drawdown is observed only; state maxima are not additive. Gap returns are UNATTRIBUTED, not backfilled.",
    ]
    for basis, cells in summary["conditioned"].items():
        lines.append(f"{basis} labels (holding endpoint deltas, separate pre-fill fee/turnover attribution):")
        for label, cell in cells.items():
            if cell["fill_count"] or any(
                Fraction(cell[key])
                for key in ("observed_net_delta_quote_rational", "max_observed_drawdown_quote_rational")
            ):
                lines.append(
                    f"  {label}: fills={cell['fill_count']}, fees={display(cell['fees_quote_rational'])}, "
                    f"observed_net_delta={display(cell['observed_net_delta_quote_rational'])}, "
                    f"max_observed_drawdown={display(cell['max_observed_drawdown_quote_rational'])}"
                )
    return "\n".join(lines)
