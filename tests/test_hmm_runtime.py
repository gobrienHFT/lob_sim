from __future__ import annotations

from dataclasses import replace

import pytest

from lob_sim.regime.artifact import FrozenRegimeModel
from lob_sim.regime.features import FEATURE_NAMES, FeatureSample, FeatureSpec
from lob_sim.regime.hysteresis import HysteresisConfig, RegimeHysteresis
from lob_sim.regime.model import GaussianHMMParameters
from lob_sim.regime.preprocess import TrainOnlyScaler
from lob_sim.regime.runtime import CausalRegimeEstimator


def _model() -> FrozenRegimeModel:
    spec = FeatureSpec()
    d = len(FEATURE_NAMES)
    scaler = TrainOnlyScaler.fit_training(
        [(-1.0,) * d, (1.0,) * d], feature_names=FEATURE_NAMES, feature_identity=spec.digest, clip_quantiles=None
    )
    parameters = GaussianHMMParameters(
        (0.5, 0.5), ((0.9, 0.1), (0.1, 0.9)), ((-1.0,) * d, (1.0,) * d), ((1.0,) * d, (1.0,) * d)
    )
    return FrozenRegimeModel(spec, scaler, parameters, '{"purpose":"synthetic_unit_test"}')


def _sample(index: int, value: float = 1.0) -> FeatureSample:
    return FeatureSample(
        "BTCUSDT",
        index * 1_000_000_000,
        index,
        (0, 0, 0),
        FeatureSpec().digest,
        "VALID",
        (value,) * len(FEATURE_NAMES),
        None,
    )


def test_hysteresis_requires_consecutive_confidence_and_minimum_age() -> None:
    state = RegimeHysteresis(2, HysteresisConfig(confirmation_samples=2, minimum_state_age_samples=3))
    assert state.update((0.9, 0.1)).active_state is None
    assert state.update((0.9, 0.1)).active_state == 0
    assert state.update((0.1, 0.9)).confirmation_streak == 1
    held = state.update((0.1, 0.9))
    assert held.active_state == 0 and held.reason == "minimum_state_age"
    switched = state.update((0.1, 0.9))
    assert switched.active_state == 1 and switched.switched and switched.state_age_samples == 0
    uncertain = state.update((0.5, 0.5))
    assert not uncertain.confident and uncertain.active_state == 1
    assert uncertain.candidate_state is None and uncertain.confirmation_streak == 0


def test_uncertain_sample_breaks_confirmation_streak_and_entropy_can_gate_entry() -> None:
    state = RegimeHysteresis(2)
    state.update((0.8, 0.2))
    state.update((0.5, 0.5))
    assert state.update((0.8, 0.2)).confirmation_streak == 1
    assert state.active_state is None
    gated = RegimeHysteresis(2, HysteresisConfig(maximum_normalized_entropy=0.1))
    assert gated.update((0.8, 0.2)).reason == "uncertain"
    assert gated.update((1.0, 0.0)).confident


def test_hysteresis_checkpoint_resume_and_reset() -> None:
    first = RegimeHysteresis(2)
    first.update((0.9, 0.1))
    second = RegimeHysteresis(2)
    second.restore(first.checkpoint())
    for posterior in ((0.9, 0.1), (0.9, 0.1), (0.1, 0.9), (0.5, 0.5)):
        assert first.update(posterior) == second.update(posterior)
    first.reset()
    assert first.active_state is None and first.state_age_samples == 0


@pytest.mark.parametrize(
    "update",
    [
        {"active_state": 2},
        {"active_state": True},
        {"state_age_samples": 1},
        {"confirmation_streak": 0},
        {"state_count": True},
    ],
)
def test_hysteresis_checkpoint_rejection_is_atomic(update: dict) -> None:
    state = RegimeHysteresis(2)
    state.update((0.9, 0.1))
    before = state.checkpoint()
    with pytest.raises(ValueError):
        state.restore({**before, **update})
    assert state.checkpoint() == before


@pytest.mark.parametrize(
    "status",
    [
        "WARMING_UP",
        "INVALID_BOOK",
        "INVALID_TRADE_STREAM",
        "INVALID_CAPTURE",
        "INVALID_CLOCK",
        "STALE",
        "NONFINITE_FEATURE",
    ],
)
def test_invalid_sample_never_exports_a_stale_confident_posterior(status) -> None:
    runtime = CausalRegimeEstimator(_model())
    for index in range(1, 4):
        runtime.update(_sample(index))
    invalid = runtime.update(replace(_sample(4), status=status, values=None))
    assert invalid.posterior is invalid.confidence is invalid.hysteresis is None
    assert invalid.as_dict()["active_state"] is None
    assert invalid.as_dict()["confident"] is False
    recovered = runtime.update(_sample(5))
    assert recovered.posterior == CausalRegimeEstimator(_model()).update(_sample(5)).posterior
    assert recovered.hysteresis.active_state is None
    assert recovered.filter_reset_reason == status


def test_epoch_boundary_restarts_filter_and_hysteresis_and_records_reason() -> None:
    runtime = CausalRegimeEstimator(_model())
    runtime.update(_sample(1, -1))
    changed = replace(_sample(2), epochs=(1, 1, 1), reset_reason="epoch_changed")
    signal = runtime.update(changed)
    assert signal.posterior == CausalRegimeEstimator(_model()).update(changed).posterior
    assert signal.filter_reset_reason == "epoch_changed"


def test_missing_sample_is_not_an_implicit_long_transition() -> None:
    runtime = CausalRegimeEstimator(_model())
    runtime.update(_sample(1, -1))
    result = runtime.update(_sample(3, 1))
    assert result.filter_reset_reason == "sample_interval_gap"
    assert result.posterior == CausalRegimeEstimator(_model()).update(_sample(3, 1)).posterior


def test_runtime_future_mutation_preserves_posterior_map_active_and_switches() -> None:
    def run(tail: float) -> list:
        runtime = CausalRegimeEstimator(_model())
        return [runtime.update(_sample(index, 1 if index <= 4 else tail)).as_dict() for index in range(1, 8)]

    first, second = run(-1), run(1)
    assert first[:4] == second[:4]
    assert first[-1]["active_state"] != second[-1]["active_state"]


@pytest.mark.parametrize(
    "update",
    [
        {"feature_identity": "0" * 64},
        {"sample_ns": 1},
        {"symbol": "ETHUSDT"},
        {"receive_seq": 0},
        {"values": None},
        {"values": (float("nan"),) * 12},
    ],
)
def test_runtime_rejects_mismatched_samples_before_changing_state(update: dict) -> None:
    runtime = CausalRegimeEstimator(_model())
    runtime.update(_sample(1))
    before = runtime.filter.checkpoint(), runtime.hysteresis.checkpoint()
    with pytest.raises(ValueError):
        runtime.update(replace(_sample(2), **update))
    assert (runtime.filter.checkpoint(), runtime.hysteresis.checkpoint()) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enter_probability": True},
        {"confirmation_samples": 0},
        {"minimum_state_age_samples": -1},
        {"maximum_normalized_entropy": 1.1},
    ],
)
def test_invalid_hysteresis_configuration_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        HysteresisConfig(**kwargs)


def test_runtime_model_identity_cannot_be_replaced_in_place() -> None:
    runtime = CausalRegimeEstimator(_model())
    with pytest.raises(AttributeError):
        runtime.model = _model()
