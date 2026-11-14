"""Primary maker-bps, inventory and quote-clock outcomes from native audits.

Exact fill identity joins use temporary SQLite, not a tape-sized Python map.
This consumer does not price fills, infer executions or change markouts.
"""

from __future__ import annotations

from collections.abc import Sequence, Generator
from contextlib import closing
from fractions import Fraction
from pathlib import Path
import csv
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, cast

from lob_sim.regime.execution import iter_execution_rows
from lob_sim.regime.quotes import QUOTE_FIELDS
from lob_sim.regime.study_periods import PERIOD_NS
from lob_sim.sim.export import iter_fill_audit_rows

ADDITIVE = {
    "mean_absolute_inventory_quantity": "base quantity;time-weighted absolute inventory over a complete UTC minute",
    "scheduled_quote_requests": "new quote requests per complete UTC minute;not strategy target count",
    "cancelled_orders": "cancel acknowledgements terminating accepted orders per complete UTC minute;not cancel requests",
    "risk_halt_transitions": "native global risk halt transitions per complete UTC minute",
    "maker_fills": "maker fill events per complete UTC minute",
    "taker_fills": "taker fill events per complete UTC minute",
}
RATIOS = {
    "maker_signed_markout_1s_bps": "quantity-weighted maker signed markout bps:10000*signed_markout/fill_price;resolved only",
    "maker_markout_1s_coverage": "resolved one-second maker observations / maker fills;unresolved and invalidated never zero-filled",
    "maker_adverse_selection_1s": "negative one-second maker observations / resolved one-second maker observations",
}


def primary_clock_periods(
    files: dict[str, Path], summary: dict[str, Any], risk: Sequence[dict[str, Any]], span: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    offset = span["wall_offset_ns"]
    grid = summary["hmm_economics"]["grid"]
    cells = {
        p["utc_start_ns"]: {
            "scheduled_quote_requests": 0,
            "cancelled_orders": 0,
            "risk_halt_transitions": 0,
            "maker_fills": 0,
            "taker_fills": 0,
            "maker_resolved": 0,
            "maker_adverse": 0,
            "maker_weighted_bps": Fraction(0),
            "maker_resolved_qty": Fraction(0),
        }
        for p in risk
    }

    def bucket(logical: int) -> dict[str, Any] | None:
        return cells.get((logical + offset) // PERIOD_NS * PERIOD_NS)

    fills = iter_fill_audit_rows(files["trades"])
    count = 0
    with (
        closing(cast(Generator[dict[str, Any], None, None], fills)),
        TemporaryDirectory(prefix="lob_sim_primary_join_") as temporary,
    ):
        with closing(sqlite3.connect(str(Path(temporary) / "fills.sqlite3"))) as database:
            database.execute(
                "CREATE TABLE fills (id INTEGER PRIMARY KEY, price TEXT, qty TEXT, maker INTEGER, source TEXT, order_id TEXT)"
            )
            for row in iter_execution_rows(files["regime_execution"]):
                cell = bucket(row["logical_ns"])
                if row["event_type"] == "fill":
                    fill = next(fills, None)
                    count += 1
                    if fill is None or row["fill_id"] != count or fill["symbol"] != summary["hmm_execution"]["symbol"]:
                        raise ValueError("primary outcome lost its exact global fill identity")
                    if any(fill[k] != row[k] for k in ("maker", "fill_source", "order_id", "qty")):
                        raise ValueError("primary execution/fill linkage differs")
                    price, qty = Fraction(fill["price"]), Fraction(fill["qty"])
                    if price <= 0 or qty <= 0:
                        raise ValueError("primary fill price/quantity must be positive")
                    database.execute(
                        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?)",
                        (count, str(price), str(qty), int(fill["maker"]), fill["fill_source"], fill["order_id"]),
                    )
                    if cell is not None:
                        cell["maker_fills" if fill["maker"] else "taker_fills"] += 1
                elif row["event_type"] == "markout":
                    original = database.execute(
                        "SELECT price,qty,maker,source,order_id FROM fills WHERE id=?", (row["fill_id"],)
                    ).fetchone()
                    # Native markout rows deliberately omit maker/taker:
                    # liquidity classification comes from the original fill.
                    if (
                        row["maker"] is not None
                        or original is None
                        or (Fraction(original[1]), original[3], original[4])
                        != (
                            Fraction(row["qty"]),
                            row["fill_source"],
                            row["order_id"],
                        )
                    ):
                        raise ValueError("primary markout lost or changed its original fill")
                    if cell is not None and original[2] and row["horizon_ms"] == 1000 and row["status"] == "resolved":
                        value, qty = Fraction(row["markout"]), Fraction(row["qty"])
                        cell["maker_resolved"] += 1
                        cell["maker_adverse"] += int(value < 0)
                        cell["maker_resolved_qty"] += qty
                        cell["maker_weighted_bps"] += 10_000 * value / Fraction(original[0]) * qty
                else:
                    raise ValueError("primary outcome encountered an unknown execution event")
            if next(fills, None) is not None or count != summary["hmm_execution"]["fill_count"]:
                raise ValueError("primary outcome global fill census differs")
    with files["regime_quotes"].open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(QUOTE_FIELDS):
            raise ValueError("primary quote clock schema differs")
        for row in reader:
            cell = bucket(int(row["logical_ns"]))
            if cell is not None:
                cell["scheduled_quote_requests"] += int(row["event_type"] == "scheduled")
                cell["cancelled_orders"] += int(row["event_type"] == "terminal" and row["reason"] == "cancelled")
    from lob_sim.regime.risk import iter_risk_rows

    previously_halted = False
    for row in iter_risk_rows(files["regime_risk"]):
        cell = bucket(row["logical_ns"])
        if cell is not None:
            cell["risk_halt_transitions"] += int(row["halted"] and not previously_halted)
        previously_halted = row["halted"]
    result = []
    for period in risk:
        cell = cells[period["utc_start_ns"]]
        additive = {k: str(cell[k]) for k in ADDITIVE if k != "mean_absolute_inventory_quantity"}
        additive["mean_absolute_inventory_quantity"] = str(
            Fraction(period["absolute_inventory_lots_ns"], PERIOD_NS) * Fraction(grid[1])
        )
        sufficient = {
            "maker_signed_markout_1s_bps": (cell["maker_weighted_bps"], cell["maker_resolved_qty"]),
            "maker_markout_1s_coverage": (cell["maker_resolved"], cell["maker_fills"]),
            "maker_adverse_selection_1s": (cell["maker_adverse"], cell["maker_resolved"]),
        }
        result.append(
            {
                "utc_start_ns": period["utc_start_ns"],
                "period_ns": PERIOD_NS,
                "epoch": period["epoch"],
                "excluded_reason": period["excluded_reason"],
                "additive": additive,
                "ratios": {
                    k: {"numerator_rational": str(n), "denominator_rational": str(d)}
                    for k, (n, d) in sufficient.items()
                },
            }
        )
    return tuple(result)
