"""Fast selected comparisons against the independently produced core reference."""

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_snapshot
from scripts.core_regression_probe import configuration, digest, fingerprint, write_tape

REFERENCE = Path(__file__).resolve().parents[1] / "docs/regression_results/repaired_core_baseline.json"


def load_reference():
    result = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert result["behavior_sha256"] == digest(result["behavior"])
    assert len(result["behavior"]["cases"]) == 112
    assert sum(len(case.get("checkpoints", [])) for case in result["behavior"]["cases"]) == 648
    return {case["case"]: case for case in result["behavior"]["cases"]}


@pytest.mark.parametrize("profile", ["baseline", "layered_mm", "research_mm"])
@pytest.mark.parametrize("fill", ["trade", "depth"])
@pytest.mark.parametrize("latency", ["fixed", "empirical", "stress_tail"])
def test_existing_profiles_match_repaired_core_reference(tmp_path, profile, fill, latency):
    cfg = configuration(
        mm_strategy_profile=profile,
        sim_fill_model=fill,
        sim_latency_mode=latency,
        sim_latency_samples_ms=(0.0, 1.0, 5.0, 25.0),
        sim_order_latency_ms=5.0,
        sim_cancel_latency_ms=10.0,
        sim_latency_stress_multiplier=2.0,
        mm_half_spread_bps=Decimal(0),
        mm_order_qty=Decimal("0.01"),
        mm_requote_ms=40,
    )
    expected = load_reference()[f"{profile}/{fill}/{latency}/valid"]
    tape = write_tape(tmp_path / "input.ndjson")
    assert sha256(tape.read_bytes()).hexdigest() == expected["input_sha256"]
    assert config_snapshot(cfg) == expected["config"]
    engine = SimulationEngine(cfg)
    engine.run(tape)
    assert fingerprint(engine) == expected["complete"]


@pytest.mark.parametrize("scenario", ["gap", "off_grid", "depth_disconnect", "trade_disconnect", "overflow"])
def test_fault_behavior_matches_repaired_core_reference(tmp_path, scenario):
    cfg = configuration(
        mm_strategy_profile="baseline",
        sim_fill_model="trade",
        sim_latency_mode="fixed",
        sim_latency_samples_ms=(0.0, 1.0, 5.0, 25.0),
        sim_order_latency_ms=5.0,
        sim_cancel_latency_ms=10.0,
        sim_latency_stress_multiplier=2.0,
        mm_half_spread_bps=Decimal(0),
        mm_order_qty=Decimal("0.01"),
        mm_requote_ms=40,
    )
    expected = load_reference()["baseline/trade/fixed/" + scenario]
    tape = write_tape(tmp_path / "input.ndjson", scenario=scenario)
    assert sha256(tape.read_bytes()).hexdigest() == expected["input_sha256"]
    assert config_snapshot(cfg) == expected["config"]
    engine = SimulationEngine(cfg)
    engine.run(tape)
    assert fingerprint(engine) == expected["complete"]
