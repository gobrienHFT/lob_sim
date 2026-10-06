"""Standalone clock regressions; no optional regime dependency."""

from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER
from lob_sim.sim.engine import SimulationEngine
from scripts.check_futures_determinism import _run_once
from scripts.core_regression_probe import SECOND, configuration, write_tape


def test_unaffected_legacy_golden_remains_unchanged():
    result = _run_once(
        Path("docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson"),
        configuration(),
        DEFAULT_REPLAY_ADAPTER,
    )
    assert result["summary_sha256"] == "0b66f7bfa1c7d5b87b5659d7ee67f4d6c5d678e127ed5e75001520a45eaf67b5"
    assert result["event_trace_sha256"] == "ee237a382b3957b36bea2a372a9e9c794708d6140b1e3abe34b19ea376f968d0"


def test_schema_v3_requotes_use_exact_integer_grid(tmp_path):
    engine = SimulationEngine(configuration(mm_requote_ms=40))
    engine.run(write_tape(tmp_path / "tape.ndjson"))
    decisions = [
        Decimal(str(row["ts_local"])) * SECOND for row in engine.event_trace if row["event_type"] == "decision"
    ]
    assert decisions == list(range(2 * SECOND, 14 * SECOND, 40_000_000))
    assert all(a["ts_local"] <= b["ts_local"] for a, b in zip(engine.event_trace, engine.event_trace[1:]))


@pytest.mark.parametrize("scenario", ["valid", "quiet_BTCUSDT", "quiet_ETHUSDT"])
def test_global_timers_are_drained_before_next_market_receipt(tmp_path, scenario):
    engine = SimulationEngine(configuration(symbols=("BTCUSDT", "ETHUSDT")))
    engine.run(write_tape(tmp_path / "tape.ndjson", scenario=scenario, symbols=engine.cfg.symbols))
    rows = engine.event_trace
    assert all(a["ts_local"] <= b["ts_local"] for a, b in zip(rows, rows[1:]))
    boundary = next(i for i, row in enumerate(rows) if row["event_type"] == "market_record" and row["ts_local"] == 3)
    for symbol in engine.cfg.symbols:
        assert any(
            row["symbol"] == symbol and row["event_type"] == "decision" and row["ts_local"] == 2.04
            for row in rows[:boundary]
        )
