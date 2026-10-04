"""Independent synthetic Gaussian emissions; not a model of exchange events."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from lob_sim.regime.artifact import FrozenRegimeModel, load_model, save_model
from lob_sim.regime.dataset import FeaturePartition
from lob_sim.regime.features import FEATURE_NAMES, FeatureSpec
from lob_sim.regime.filter import ForwardFilter
from lob_sim.regime.fit import FitConfig, _state_report, fit_candidates, inspect_model, restart_seed
from lob_sim.regime.validation import strict_json

pytest.importorskip("hmmlearn", reason='optional fit tests require the "hmm" extra; reviewer requirements include it')
import numpy as np
from hmmlearn.hmm import GaussianHMM


def partition(role, *, sequences=4, samples=160, seed=13):
    rng = random.Random(seed)
    rows = []
    # Separate conditionally Gaussian emission states, with a known 40-step
    # switching schedule. These numeric emissions are NOT an exchange fixture.
    centers = (
        (1, 0, 0.3, 0.0, 0.0, 0.0, 5.0, 0.0, 3.0, 2.0, 0.0, 0.0),
        (5, 0, 3.0, 0.3, 0.2, 0.5, 2.0, 0.0, 5.0, 4.0, 0.3, 0.0),
    )
    for sequence in range(sequences):
        for t in range(samples):
            state = (t // 40 + sequence) % 2
            rows.append(tuple(value + rng.gauss(0, 0.04) for value in centers[state]))
    return FeaturePartition(
        role,
        ("2025-01-01" if role == "calibration" else "2025-01-02",),
        "a" * 64,
        "b" * 64,
        FeatureSpec(),
        "BTCUSDT",
        "c" * 64,
        tuple(rows),
        (samples,) * sequences,
    )


@pytest.fixture(scope="module")
def fitted():
    training, validation = partition("calibration"), partition("validation", sequences=2, seed=19)
    config = FitConfig(state_counts=(2,), restarts=2, max_iterations=100)
    result = fit_candidates(training, validation, config)
    assert result.model is not None, result.report()
    return training, validation, config, result


def test_repeated_fit_reproduces_full_model_and_attempt_ledger(fitted):
    training, validation, config, result = fitted
    repeated = fit_candidates(training, validation, config)
    assert repeated.report_json == result.report_json
    assert repeated.model == result.model
    assert repeated.model.model_sha256 == result.model.model_sha256
    assert len(result.report()["attempts"]) == config.restarts
    assert all(attempt["status"] == "valid" for attempt in result.report()["attempts"])


def test_k_2_to_5_reports_every_attempt_and_bic_count(fitted):
    training, validation, _, _ = fitted
    result = fit_candidates(training, validation, FitConfig(restarts=2, max_iterations=100))
    report = result.report()
    assert [candidate["k"] for candidate in report["candidates"]] == [2, 3, 4, 5]
    assert [(attempt["k"], attempt["restart"]) for attempt in report["attempts"]] == [
        (k, restart) for k in (2, 3, 4, 5) for restart in (0, 1)
    ]
    assert result.model is not None
    for candidate in report["candidates"]:
        if candidate["status"] == "valid":
            k = candidate["k"]
            expected_parameters = (k - 1) + k * (k - 1) + 2 * k * len(FEATURE_NAMES)
            assert candidate["parameter_count"] == expected_parameters
            assert candidate["training_bic"] == pytest.approx(
                expected_parameters * math.log(len(training.rows)) - 2 * candidate["training_log_likelihood"]
            )


def test_synthetic_latent_emission_recovery_after_training_only_alignment(fitted):
    _, validation, _, result = fitted
    model = result.model
    filter_ = ForwardFilter(model.parameters)
    predicted, truth = [], []
    offset = 0
    for sequence, length in enumerate(validation.lengths):
        filter_.reset()
        for t, row in enumerate(validation.rows[offset : offset + length]):
            values = model.scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=model.features.digest)
            predicted.append(filter_.update(values).map_state)
            truth.append((t // 40 + sequence) % 2)
        offset += length
    assert sum(p == actual for p, actual in zip(predicted, truth)) / len(truth) > 0.95
    metadata = strict_json(model.provenance_json)
    assert (
        metadata["state_characterization"][0]["training_feature_means"]["spread_bps"]
        < metadata["state_characterization"][1]["training_feature_means"]["spread_bps"]
    )
    assert metadata["state_characterization"][0]["risk_score"] <= metadata["state_characterization"][1]["risk_score"]


def trusted_model(model):
    estimator = GaussianHMM(n_components=model.parameters.state_count, covariance_type="diag", init_params="")
    estimator.startprob_ = np.array(model.parameters.start)
    estimator.transmat_ = np.array(model.parameters.transition)
    estimator.means_ = np.array(model.parameters.means)
    estimator.covars_ = np.array(model.parameters.variances)
    return estimator


def test_forward_filter_matches_trusted_prefix_end_posterior_not_full_tape_smoothing(fitted):
    _, validation, _, result = fitted
    model = result.model
    x = np.array(
        [
            model.scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=model.features.digest)
            for row in validation.rows[:60]
        ]
    )
    reference = trusted_model(model)
    filter_ = ForwardFilter(model.parameters)
    for end in range(1, len(x) + 1):
        actual = filter_.update(x[end - 1].tolist())
        # Last posterior of EACH PREFIX is forward-only at that endpoint.
        # Full-tape score_samples' earlier posteriors would leak the future.
        _, posterior = reference.score_samples(x[:end])
        assert actual.posterior == pytest.approx(posterior[-1], abs=1e-10)


def test_sequence_lengths_agree_with_reset_filter_and_do_not_invent_boundary_transitions(fitted):
    training, _, _, result = fitted
    model = result.model
    x = np.array(
        [
            model.scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=model.features.digest)
            for row in training.rows
        ]
    )
    reference = trusted_model(model)
    expected = reference.score(x, lengths=list(training.lengths))
    filter_ = ForwardFilter(model.parameters)
    likelihood, offset = 0.0, 0
    for length in training.lengths:
        filter_.reset()
        for row in x[offset : offset + length]:
            likelihood += filter_.update(row.tolist()).log_likelihood_increment
        offset += length
    assert likelihood == pytest.approx(expected, abs=1e-8)
    assert abs(reference.score(x) - expected) > 0.1  # Joining episodes changes likelihood.


def test_validation_mutation_does_not_refit_scaler_or_selected_train_restart(fitted):
    training, validation, config, original = fitted
    mutated = replace(validation, rows=tuple(tuple(value + 10 for value in row) for row in validation.rows))
    result = fit_candidates(training, mutated, config)
    assert result.model is not None
    assert result.model.scaler == original.model.scaler
    assert result.model.parameters == original.model.parameters
    assert result.report()["attempts"] == original.report()["attempts"]
    assert result.report()["candidates"] != original.report()["candidates"]


@pytest.mark.parametrize(
    "change", ["test_train", "test_validation", "future_train", "symbol", "features", "dataset", "split", "instrument"]
)
def test_partition_rejections_happen_before_fitting_dependency_is_loaded(monkeypatch, change):
    training, validation = partition("calibration"), partition("validation")
    if change == "test_train":
        training = replace(training, role="test")
    elif change == "test_validation":
        validation = replace(validation, role="test")
    elif change == "future_train":
        training = replace(training, days=("2025-01-03",))
    elif change == "symbol":
        validation = replace(validation, symbol="ETHUSDT")
    elif change == "features":
        validation = replace(validation, feature_spec=FeatureSpec(window_steps=3))
    else:
        validation = replace(validation, **{change + "_sha256": "d" * 64})

    def forbidden():
        raise AssertionError("fitting started before validating partitions")

    monkeypatch.setattr("lob_sim.regime.fit._dependencies", forbidden)
    with pytest.raises(ValueError):
        fit_candidates(training, validation)


def test_iteration_limit_does_not_count_as_convergence_and_all_failures_are_kept():
    result = fit_candidates(partition("calibration"), partition("validation"), FitConfig(restarts=3, max_iterations=1))
    assert result.model is None
    assert result.report()["status"] == "no_valid_candidate"
    assert len(result.report()["attempts"]) == 12
    assert all(attempt["reason"] == "not_converged_or_likelihood_decreased" for attempt in result.report()["attempts"])


def test_all_constant_or_insufficient_samples_fail_with_complete_ledger():
    training = replace(partition("calibration"), rows=((0.0,) * 12,) * 4, lengths=(4,))
    result = fit_candidates(training, partition("validation"), FitConfig(restarts=2))
    assert result.model is None
    assert len(result.report()["attempts"]) == 8
    assert all(
        attempt["reason"] == "insufficient_training_rows_or_all_features_constant"
        for attempt in result.report()["attempts"]
    )


def test_fitted_artifact_roundtrip_and_independent_human_inspection(tmp_path, fitted):
    _, _, _, result = fitted
    path = tmp_path / "model.json"
    save_model(path, result.model)
    restored = load_model(path)
    assert restored == result.model
    assert FrozenRegimeModel.from_dict(json_value := restored.as_dict()) == restored
    metadata = json_value["provenance"]
    assert metadata["training"]["role"] == "calibration"
    assert metadata["validation"]["role"] == "validation"
    assert metadata["occupancy_method"].startswith("retrospective_training_smoothing")
    assert metadata["dependencies"]["hmmlearn"] == "0.3.3"
    assert set(metadata["canonical_to_raw_state"]) == {0, 1}
    assert "Transition matrix" in inspect_model(restored)
    assert "spread_bps" in inspect_model(restored)


def test_canonical_state_order_is_invariant_to_raw_label_permutation(fitted):
    training, _, _, result = fitted
    parameters = result.model.parameters
    posterior = np.array(
        [(0.9, 0.1) if (t // 40 + t // 160) % 2 == 0 else (0.1, 0.9) for t in range(len(training.rows))]
    )
    raw = np.array(training.rows)
    scaled = np.array(
        [
            result.model.scaler.transform(
                row, feature_names=FEATURE_NAMES, feature_identity=result.model.features.digest
            )
            for row in training.rows
        ]
    )
    order, reports = _state_report(parameters, posterior, raw, scaled, np)
    permuted = parameters.permute((1, 0))
    reordered, swapped_reports = _state_report(permuted, posterior[:, ::-1], raw, scaled, np)
    assert parameters.permute(order) == permuted.permute(reordered)
    for original, swapped in zip(reports, swapped_reports):
        assert {key: value for key, value in original.items() if key != "raw_state"} == {
            key: value for key, value in swapped.items() if key != "raw_state"
        }


def test_restart_seeds_are_distinct_stable_and_not_global_rng_dependent():
    expected = [restart_seed(7, k, restart) for k in range(2, 6) for restart in range(10)]
    random.seed(999)
    assert expected == [restart_seed(7, k, restart) for k in range(2, 6) for restart in range(10)]
    assert len(set(expected)) == 40


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state_counts": ()},
        {"state_counts": (3, 2)},
        {"state_counts": (2, 2)},
        {"state_counts": (True,)},
        {"restarts": True},
        {"seed": -1},
        {"max_iterations": 0},
        {"tolerance": float("nan")},
        {"minimum_variance": 0},
        {"minimum_state_observations": -1},
        {"minimum_separation": False},
    ],
)
def test_fit_configuration_is_strict(kwargs):
    with pytest.raises(ValueError):
        FitConfig(**kwargs)
