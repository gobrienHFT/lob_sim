"""Control/rejected receipts remain causal checkpoint and audit boundaries."""

from decimal import Decimal

import pytest

from lob_sim.sim.engine import SimulationEngine
from scripts.core_regression_probe import configuration, fingerprint, write_tape


@pytest.mark.parametrize("scenario", ["valid", "gap", "off_grid", "depth_disconnect", "trade_disconnect", "overflow"])
@pytest.mark.parametrize("cut", [1, 2, 3, 4, 13, 14])
def test_every_control_and_rejected_record_is_a_checkpoint_boundary(tmp_path, scenario, cut):
    path = write_tape(tmp_path / "tape.ndjson", scenario=scenario)
    cfg = configuration(mm_half_spread_bps=Decimal(0), mm_order_qty=Decimal("0.01"))
    complete, paused, resumed = (SimulationEngine(cfg) for _ in range(3))
    complete.run(path)
    checkpoint = tmp_path / "state.json"
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    assert checkpoint.is_file() and paused._last_event_index == cut
    resumed.run(path, resume_from=checkpoint)
    assert fingerprint(resumed) == fingerprint(complete)


@pytest.mark.parametrize("scenario", ["depth_disconnect", "overflow"])
def test_pending_markout_invalidations_are_traced_at_the_fault_boundary(tmp_path, scenario):
    cfg = configuration(mm_half_spread_bps=Decimal(0), mm_order_qty=Decimal("0.01"))
    engine = SimulationEngine(cfg)
    engine.run(write_tape(tmp_path / "tape.ndjson", scenario=scenario))
    assert engine.metrics.fill_count > 0
    invalidations = [
        row
        for row in engine.event_trace
        if row["event_type"] == "markout" and row["details"].get("status") == "invalidated"
    ]
    assert invalidations
    fault = next(
        i for i, row in enumerate(engine.event_trace) if row["event_type"] == "market_record" and row["ts_local"] == 6.1
    )
    later = next(
        i for i, row in enumerate(engine.event_trace) if row["event_type"] == "market_record" and row["ts_local"] == 7
    )
    assert all(row in engine.event_trace[fault:later] for row in invalidations)
    assert all(a["ts_local"] <= b["ts_local"] for a, b in zip(engine.event_trace, engine.event_trace[1:]))
