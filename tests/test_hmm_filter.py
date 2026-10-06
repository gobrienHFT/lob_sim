"""Independent probability-space enumeration, not a second forward implementation."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import replace

import pytest

from lob_sim.regime.filter import ForwardFilter
from lob_sim.regime.model import GaussianHMMParameters


def _parameters() -> GaussianHMMParameters:
    return GaussianHMMParameters((0.6, 0.4), ((0.85, 0.15), (0.2, 0.8)), ((-1.0,), (1.0,)), ((1.5,), (0.75,)))


def _enumerated_posterior(model: GaussianHMMParameters, observations: list[float], at: int) -> tuple[float, ...]:
    """Enumerate every latent path for a short sequence, optionally including future data."""

    masses = [0.0] * model.state_count
    for path in itertools.product(range(model.state_count), repeat=len(observations)):
        weight = model.start[path[0]]
        for index, (state, x) in enumerate(zip(path, observations)):
            if index:
                weight *= model.transition[path[index - 1]][state]
            mean, variance = model.means[state][0], model.variances[state][0]
            weight *= math.exp(-((x - mean) ** 2) / (2 * variance)) / math.sqrt(2 * math.pi * variance)
        masses[path[at]] += weight
    total = sum(masses)
    return tuple(value / total for value in masses)


def test_forward_filter_matches_independently_enumerated_latent_paths() -> None:
    model = _parameters()
    observations = [-1.1, -0.2, 1.8, 1.1, -1.6]
    runtime = ForwardFilter(model)
    for index, observation in enumerate(observations):
        result = runtime.update((observation,))
        expected = _enumerated_posterior(model, observations[: index + 1], index)
        assert result.posterior == pytest.approx(expected, abs=1e-14)
        assert sum(result.next_prior) == pytest.approx(1.0)
        assert result.samples_seen == index + 1
        assert result.normalized_entropy == pytest.approx(result.entropy / math.log(2))


def test_future_mutation_cannot_change_historical_filter_posteriors() -> None:
    prefix = [-0.6, 0.2, -0.9]
    left, right = ForwardFilter(_parameters()), ForwardFilter(_parameters())
    a = [left.update((value,)) for value in prefix + [2.0, 2.0, 2.0]]
    b = [right.update((value,)) for value in prefix + [-3.0, -3.0, -3.0]]
    assert a[:3] == b[:3]
    assert a[-1] != b[-1]


def test_smoothing_changes_past_but_forward_filter_does_not() -> None:
    model = GaussianHMMParameters((0.5, 0.5), ((0.99, 0.01), (0.01, 0.99)), ((-1.0,), (1.0,)), ((1.0,), (1.0,)))
    prefix = [0.0]
    historical = ForwardFilter(model).update(prefix).posterior
    smoothed_positive = _enumerated_posterior(model, [0.0, 2.0, 2.0], 0)
    smoothed_negative = _enumerated_posterior(model, [0.0, -2.0, -2.0], 0)
    assert historical == (0.5, 0.5)
    assert smoothed_positive[1] > 0.95
    assert smoothed_negative[0] > 0.95


def test_log_filter_survives_emission_underflow_without_losing_state_mass() -> None:
    model = GaussianHMMParameters((0.5, 0.5), ((1.0, 0.0), (0.0, 1.0)), ((0.0,), (1000.0,)), ((1.0,), (1.0,)))
    runtime = ForwardFilter(model)
    assert runtime.update((0.0,)).posterior == (1.0, 0.0)
    checkpoint = json.loads(json.dumps(runtime.checkpoint(), allow_nan=False))
    resumed = ForwardFilter(model)
    resumed.restore(checkpoint)
    # Both paths have identical joint density after the opposite observation.
    assert runtime.update((1000.0,)).posterior == pytest.approx((0.5, 0.5))
    assert resumed.update((1000.0,)).posterior == runtime.posterior


@pytest.mark.parametrize("observation", [(float("nan"),), (float("inf"),), (1e308,), (), (1.0, 2.0), (True,)])
def test_rejected_observation_is_atomic(observation) -> None:
    runtime = ForwardFilter(_parameters())
    runtime.update((0.5,))
    before = runtime.checkpoint()
    with pytest.raises(ValueError):
        runtime.update(observation)
    assert runtime.checkpoint() == before


def test_extreme_finite_likelihood_is_normalized_in_log_space() -> None:
    result = ForwardFilter(_parameters()).update((1e6,))
    assert all(math.isfinite(value) for value in result.posterior)
    assert sum(result.posterior) == pytest.approx(1)
    assert math.isfinite(result.log_likelihood_increment)
    assert 0 <= result.normalized_entropy <= 1


def test_reset_starts_independent_sequence_and_checkpoint_preserves_zero_probability() -> None:
    runtime = ForwardFilter(_parameters())
    runtime.update((2.0,))
    runtime.reset()
    assert runtime.posterior is None
    assert runtime.update((0.3,)) == ForwardFilter(_parameters()).update((0.3,))
    parameters = replace(_parameters(), start=(1.0, 0.0), transition=((1.0, 0.0), (0.0, 1.0)))
    a = ForwardFilter(parameters)
    a.update((0.0,))
    b = ForwardFilter(parameters)
    b.restore(json.loads(json.dumps(a.checkpoint(), allow_nan=False)))
    assert a.update((1.0,)) == b.update((1.0,))


@pytest.mark.parametrize(
    "update",
    [
        {"samples_seen": True},
        {"samples_seen": 0},
        {"log_posterior": [0.0, 0.0]},
        {"log_posterior": [1e308, None]},
        {"parameters_sha256": "0" * 64},
    ],
)
def test_invalid_checkpoint_does_not_change_state(update: dict) -> None:
    runtime = ForwardFilter(_parameters())
    runtime.update((0.2,))
    before = runtime.checkpoint()
    with pytest.raises(ValueError):
        runtime.restore({**before, **update})
    assert runtime.checkpoint() == before


@pytest.mark.parametrize(
    "update",
    [
        {"start": (0.2, 0.2)},
        {"start": (True, False)},
        {"transition": ((0.5, 0.5), (0.2, 0.2))},
        {"means": ((1.0,), (float("nan"),))},
        {"variances": ((0.0,), (1.0,))},
        {"variances": ((1.0,), (-1.0,))},
        {"means": ((1.0,), (2.0, 3.0))},
    ],
)
def test_model_parameters_reject_invalid_shapes_and_probabilities(update: dict) -> None:
    with pytest.raises(ValueError):
        replace(_parameters(), **update)


def test_model_copies_input_arrays_and_state_permutation_preserves_likelihood() -> None:
    raw = [0.6, 0.4]
    model = replace(_parameters(), start=raw)
    raw[0] = 0.0
    assert model.start == (0.6, 0.4)
    a, b = ForwardFilter(model), ForwardFilter(model.permute((1, 0)))
    for value in (-1.0, 0.5, 2.0):
        left, right = a.update((value,)), b.update((value,))
        assert left.log_likelihood_increment == pytest.approx(right.log_likelihood_increment)
        assert left.posterior == pytest.approx(right.posterior[::-1])
    assert model.parameter_count == 7
    assert model.expected_durations(1_000_000_000) == pytest.approx((1 / 0.15, 1 / 0.2))
    assert replace(model, transition=((1.0, 0.0), (0.0, 1.0))).expected_durations(1_000_000_000) == (None, None)


def test_filter_parameter_identity_cannot_be_replaced_in_place() -> None:
    runtime = ForwardFilter(_parameters())
    with pytest.raises(AttributeError):
        runtime.parameters = replace(_parameters(), start=(0.5, 0.5))
