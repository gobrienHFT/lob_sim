"""Independent hand-calculated maker/quote/unit/observation-clock oracles."""

import csv
from fractions import Fraction

import pytest

from lob_sim.regime.quotes import QUOTE_FIELDS
from lob_sim.regime.study_periods import PERIOD_NS
from lob_sim.research import study_primary
from lob_sim.research.markout_reader import verify_markout_observations
from test_research_capture_intervals import SECOND as S, WALL
from test_research_day_views import view


def test_maker_bps_excludes_takers_and_uses_original_price_quantity_and_actual_denominators(tmp_path, monkeypatch):
    fills = [
        {
            "symbol": "BTCUSDT",
            "maker": True,
            "fill_source": "agg_trade",
            "order_id": "A",
            "qty": "0.002",
            "price": "100",
        },
        {
            "symbol": "BTCUSDT",
            "maker": True,
            "fill_source": "agg_trade",
            "order_id": "B",
            "qty": "0.001",
            "price": "200",
        },
        {
            "symbol": "BTCUSDT",
            "maker": False,
            "fill_source": "taker_order",
            "order_id": "C",
            "qty": "0.001",
            "price": "80",
        },
    ]
    execution = []
    for i, fill in enumerate(fills, start=1):
        execution.append({**fill, "fill_id": i, "logical_ns": (61 + i) * S, "event_type": "fill"})
    for i, value in enumerate(("2", "-2", "1000"), start=1):
        execution.append(
            {
                **fills[i - 1],
                "maker": None,
                "fill_id": i,
                "logical_ns": (61 + i) * S,
                "event_type": "markout",
                "horizon_ms": 1000,
                "status": "resolved",
                "markout": value,
            }
        )

    def global_fills(path):
        yield from fills

    monkeypatch.setattr(study_primary, "iter_fill_audit_rows", global_fills)
    monkeypatch.setattr(study_primary, "iter_execution_rows", lambda path: iter(execution))
    import lob_sim.regime.risk as risk_module

    monkeypatch.setattr(
        risk_module,
        "iter_risk_rows",
        lambda path: iter(
            [
                {"logical_ns": (67 + i) * S, "halted": halted}
                for i, halted in enumerate((False, True, True, False, True))
            ]
        ),
    )
    path = tmp_path / "quotes.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUOTE_FIELDS)
        writer.writeheader()
        for time, event, reason in ((61, "scheduled", ""), (62, "scheduled", ""), (66, "terminal", "cancelled")):
            writer.writerow({"logical_ns": time * S, "event_type": event, "reason": reason})
    files = {"regime_quotes": path, "regime_execution": path, "regime_risk": path, "trades": path}
    common = {
        "utc_start_ns": WALL + 60 * S,
        "period_ns": PERIOD_NS,
        "epoch": [1, 1, 1],
        "excluded_reason": None,
        "absolute_inventory_lots_ns": 20 * PERIOD_NS,
    }
    summary = {
        "hmm_economics": {"grid": ["0.1", "0.001", "1"]},
        "hmm_execution": {"symbol": "BTCUSDT", "fill_count": 3},
    }
    rows = study_primary.primary_clock_periods(files, summary, (common,), {"wall_offset_ns": WALL})
    ratios, additive = rows[0]["ratios"], rows[0]["additive"]
    markout = ratios["maker_signed_markout_1s_bps"]
    assert Fraction(markout["numerator_rational"]) / Fraction(markout["denominator_rational"]) == 100
    assert ratios["maker_adverse_selection_1s"] == {"numerator_rational": "1", "denominator_rational": "2"}
    assert additive["maker_fills"] == "2" and additive["taker_fills"] == "1"
    assert additive["mean_absolute_inventory_quantity"] == "1/50"
    assert additive["scheduled_quote_requests"] == "2" and additive["cancelled_orders"] == "1"
    assert additive["risk_halt_transitions"] == "2"


@pytest.mark.parametrize("time,accepted", [(3.0, True), (4.0, False), (11.0, False)])
def test_only_real_selected_depth_receipts_can_be_resolved_markout_observations(tmp_path, monkeypatch, time, accepted):
    _, path, _ = view(tmp_path)
    import lob_sim.research.markout_reader as reader

    monkeypatch.setattr(
        reader,
        "iter_markout_audit_rows",
        lambda path: iter([{"symbol": "BTCUSDT", "status": "resolved", "markout_ts_local": time, "deadline_ts": 2.5}]),
    )
    if accepted:
        verify_markout_observations(path, tmp_path / "unused.csv", "BTCUSDT")
    else:
        with pytest.raises(ValueError, match="actual selected-symbol depth"):
            verify_markout_observations(path, tmp_path / "unused.csv", "BTCUSDT")
