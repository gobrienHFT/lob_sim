"""The cross-revision observer must not alter the system it measures."""

from lob_sim.sim.checkpoint import encode
from lob_sim.sim.engine import SimulationEngine
from scripts.core_regression_probe import configuration, fingerprint, write_tape


def test_behavioral_fingerprint_is_read_only_and_repeatable(tmp_path):
    engine = SimulationEngine(configuration())
    engine.run(write_tape(tmp_path / "input.ndjson"))
    before = encode(engine._checkpoint_mutable_state())
    first = fingerprint(engine)
    assert encode(engine._checkpoint_mutable_state()) == before
    assert fingerprint(engine) == first
