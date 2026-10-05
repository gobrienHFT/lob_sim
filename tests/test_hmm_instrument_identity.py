from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from lob_sim.book.types import InstrumentSpec
from lob_sim.regime.dataset import compatible_instrument, instrument_grid_identity
from lob_sim.regime.validation import identity
from lob_sim.sim.engine import SimulationEngine
from test_hmm_dataset import cfg, tape
from test_hmm_observation import settings


BASE = InstrumentSpec("BTCUSDT", Decimal("0.1"), Decimal("0.001"), venue="BINANCE_USDM")
LEGACY = identity(
    {"symbol": "BTCUSDT", "tick_size": "0.1", "step_size": "0.001", "contract_multiplier": "1", "venue": "BINANCE_USDM"}
)


@pytest.mark.parametrize("tick,step,multiplier", [("0.10", "0.0010", "1.00"), ("1E-1", "1E-3", "1")])
def test_only_insignificant_decimal_spelling_is_compatible(tick, step, multiplier):
    spec = replace(BASE, tick_size=Decimal(tick), step_size=Decimal(step), contract_multiplier=Decimal(multiplier))
    with localcontext() as context:
        context.prec = 1  # No rounding from the ambient Decimal context.
        assert compatible_instrument(spec, LEGACY)
        assert instrument_grid_identity(spec, canonical_grid=True) == LEGACY
    assert instrument_grid_identity(BASE) == LEGACY  # Historical hash stays exact.


@pytest.mark.parametrize(
    "change",
    [
        {"tick_size": Decimal("0.10000000000000000000000000001")},
        {"step_size": Decimal("0.002")},
        {"contract_multiplier": Decimal("10")},
        {"symbol": "ETHUSDT"},
        {"venue": "OTHER"},
    ],
)
def test_real_grid_or_identity_change_is_not_compatible_even_under_low_precision(change):
    with localcontext() as context:
        context.prec = 1
        assert not compatible_instrument(replace(BASE, **change), LEGACY)


def test_opaque_nonminimal_legacy_hash_is_not_guessed():
    nonminimal = replace(BASE, tick_size=Decimal("0.10"))
    old_hash = instrument_grid_identity(nonminimal)
    assert old_hash != LEGACY
    assert compatible_instrument(nonminimal, old_hash)
    assert not compatible_instrument(BASE, old_hash)


def test_equivalent_metadata_replay_and_resume_preserve_raw_anchors_and_frozen_model(tmp_path):
    path = tape(tmp_path / "same_grid.ndjson", same_time_trades=True)
    path.write_text(path.read_text().replace('"tickSize": "0.1"', '"tickSize": "0.10"'), encoding="utf-8")
    config = replace(cfg(), hmm=settings(training={"symbol": "BTCUSDT", "instrument_sha256": LEGACY}))
    model_hash = config.hmm.model.model_sha256
    uninterrupted = SimulationEngine(config, retain_event_trace=False, retain_audit_rows=False)
    uninterrupted.run(path)
    paused = SimulationEngine(config, retain_event_trace=False, retain_audit_rows=False)
    checkpoint = tmp_path / "checkpoint.json"
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=10)
    resumed = SimulationEngine(config, retain_event_trace=False, retain_audit_rows=False)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.regime.checkpoint() == uninterrupted.regime.checkpoint()
    assert resumed.metrics.get_summary(resumed._books, resumed._specs) == uninterrupted.metrics.get_summary(
        uninterrupted._books, uninterrupted._specs
    )
    assert config.hmm.model.model_sha256 == model_hash
    anchor = resumed.regime.checkpoint()["contexts"][0]["instrument_sha256"]
    assert anchor != LEGACY  # Exact observed spelling remains in audit/checkpoint.
