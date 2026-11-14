"""Reject markout observations invented by a timer, cutoff or other symbol.

Integer source receipts are retained on disk. CSV timestamps use the native
floating presentation exactly; this is not a sub-nanosecond accuracy claim.
Book validity itself remains the separately audited native epoch contract.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from lob_sim.research.day_view import source_envelopes
from lob_sim.sim.export import iter_markout_audit_rows


def verify_markout_observations(view_path: Path, markout_path: Path, symbol: str) -> None:
    with TemporaryDirectory(prefix="lob_sim_mark_observations_") as temporary:
        with closing(sqlite3.connect(str(Path(temporary) / "observations.sqlite3"))) as database:
            database.execute("CREATE TABLE observed (ts TEXT PRIMARY KEY, logical_ns INTEGER)")
            for row in source_envelopes(view_path, exact_fields=True):
                if row.instrument == symbol and row.event_kind in {"snapshot", "depthUpdate"}:
                    database.execute(
                        "INSERT OR IGNORE INTO observed VALUES (?,?)",
                        (repr(row.recv_monotonic_ns / 1e9), row.recv_monotonic_ns),
                    )
            for markout in iter_markout_audit_rows(markout_path):
                if markout["symbol"] != symbol:
                    raise ValueError("research markout belongs to an unquoted symbol")
                if markout["status"] == "resolved":
                    actual = database.execute(
                        "SELECT logical_ns FROM observed WHERE ts=?", (repr(markout["markout_ts_local"]),)
                    ).fetchone()
                    if actual is None or actual[0] / 1e9 < markout["deadline_ts"]:
                        raise ValueError("markout lacks an actual selected-symbol depth observation after its deadline")
