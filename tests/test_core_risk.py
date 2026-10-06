"""Missing instrument units cannot silently release portfolio exposure."""

from decimal import Decimal

import pytest

from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.metrics import PositionState
from lob_sim.sim.orders import Order
from scripts.core_regression_probe import configuration


@pytest.mark.parametrize("exposure", ["long", "short", "live", "pending", "extra", "empty"])
def test_unknown_instrument_units_never_become_zero_exposure(exposure):
    engine = SimulationEngine(configuration(mm_max_portfolio_notional=Decimal("100")))
    kwargs = {}
    if exposure in {"long", "short", "empty"}:
        engine.metrics.position["UNKNOWN"] = PositionState(lot_size={"long": 1, "short": -1, "empty": 0}[exposure])
    if exposure == "live":
        engine.fill_model._orders[("UNKNOWN", "bid", "base")] = Order(
            "unknown", "UNKNOWN", "bid", 100, 1, remaining_lots=1
        )
    if exposure == "pending":
        engine._schedule(1.0, "order_arrival", "UNKNOWN", {"price_tick": 100, "qty_lots": 1, "side": "bid"})
    if exposure == "extra":
        kwargs = {"extra_symbol": "UNKNOWN", "extra_price_tick": 100, "extra_qty_lots": 1}
    assert engine._portfolio_notional_reservation(**kwargs) == (
        (Decimal(0), {}, ()) if exposure == "empty" else (None, {}, ("UNKNOWN",))
    )
