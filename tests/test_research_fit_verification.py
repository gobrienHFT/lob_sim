"""Gaussian mechanics/ledger oracles; never empirical market evidence."""

from copy import deepcopy

import pytest

from lob_sim.regime.fit import FitConfig, fit_candidates
from lob_sim.regime.validation import identity
from lob_sim.research.fit_validation import select_validation_candidate, verify_fit_candidate
from test_hmm_fit import partition


@pytest.fixture(scope="module")
def mechanical_fit():
    training = partition("calibration", sequences=2, samples=120)
    validation = partition("validation", sequences=1, samples=120, seed=19)
    cfg = FitConfig(state_counts=(2,), restarts=2, max_iterations=100)
    result = fit_candidates(training, validation, cfg)
    assert result.model is not None, result.report()
    return training, validation, cfg, result


@pytest.fixture(scope="module")
def mechanical_failed_fit():
    train = partition("calibration", sequences=1, samples=10)
    validation = partition("validation", sequences=1, samples=10, seed=19)
    cfg = FitConfig(state_counts=(2,), restarts=2)
    result = fit_candidates(train, validation, cfg)
    assert result.model is None
    return train, validation, cfg, result


def test_saved_candidate_matches_independent_scaler_and_stdlib_forward_likelihood(mechanical_fit):
    train, validation, cfg, result = mechanical_fit
    verify_fit_candidate(result.report(), result.model, train, validation, cfg)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["attempts"].pop(),
        lambda d: d["attempts"][0].update(seed=123),
        lambda d: d["training"].update(rows=1),
        lambda d: d["validation"].update(role="test"),
        lambda d: d["selected"].update(validation_log_likelihood=1e6),
        lambda d: d["candidates"][0].update(training_bic=1e6),
        lambda d: d.update(model_sha256="e" * 64),
    ],
)
def test_rehashed_saved_fit_cannot_change_parents_ledger_selection_or_score(mechanical_fit, mutation):
    train, validation, cfg, result = mechanical_fit
    report = deepcopy(result.report())
    mutation(report)
    report["report_sha256"] = identity({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError):
        verify_fit_candidate(report, result.model, train, validation, cfg)


def test_validation_selection_uses_near_tie_training_bic_not_best_simulated_pnl():
    candidates = [
        {
            "k": 2,
            "restart": 1,
            "status": "valid",
            "validation_log_likelihood_per_observation": -2.0,
            "training_bic": 100.0,
        },
        {
            "k": 3,
            "restart": 0,
            "status": "valid",
            "validation_log_likelihood_per_observation": -1.995,
            "training_bic": 200.0,
        },
        {"k": 4, "restart": 0, "status": "failed", "reason": "mechanical failure"},
    ]
    assert select_validation_candidate(candidates, 0.01)["k"] == 2
    assert select_validation_candidate(candidates, 0.001)["k"] == 3
    assert select_validation_candidate([candidates[-1]], 0.01) is None


def test_failed_fit_ledger_is_preserved_and_cannot_supply_a_model(mechanical_failed_fit):
    from dataclasses import replace

    train, validation, cfg, result = mechanical_failed_fit
    verify_fit_candidate(result.report(), None, train, validation, cfg)
    assert len(result.report()["attempts"]) == 2
    with pytest.raises(ValueError):
        verify_fit_candidate(result.report(), None, replace(train, days=("2025-01-03",)), validation, cfg)


@pytest.mark.parametrize("mutation", ["restart_boolean", "seed_float", "candidate_k_float", "training_rows_float"])
def test_rehashed_failed_fit_preserves_exact_json_ledger_and_parent_types(mechanical_failed_fit, mutation):
    # Too few rows deliberately produces a complete failed ledger without EM.
    # Failed outcomes deserve the same immutable identity as successful fits.
    train, validation, cfg, result = mechanical_failed_fit
    verify_fit_candidate(result.report(), None, train, validation, cfg)
    report = deepcopy(result.report())
    if mutation == "restart_boolean":
        report["attempts"][0]["restart"] = False
    elif mutation == "seed_float":
        report["attempts"][0]["seed"] = float(report["attempts"][0]["seed"])
    elif mutation == "candidate_k_float":
        report["candidates"][0]["k"] = 2.0
    else:
        report["training"]["rows"] = float(report["training"]["rows"])
    report["report_sha256"] = identity({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError):
        verify_fit_candidate(report, None, train, validation, cfg)
