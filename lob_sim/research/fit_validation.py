"""Stdlib model/ledger re-reader; never runs EM or reads held-out rows.

Likelihoods use the separately tested causal forward recursion, not hmmlearn's
scorer. This verifies the saved winning candidate, not discarded restart
parameters or that a researcher has never inspected another filesystem path.
"""

from __future__ import annotations

import math
from typing import Any

from lob_sim.regime.artifact import FrozenRegimeModel
from lob_sim.regime.dataset import FeaturePartition
from lob_sim.regime.features import FEATURE_NAMES
from lob_sim.regime.filter import ForwardFilter
from lob_sim.regime.fit import FitConfig, restart_seed
from lob_sim.regime.validation import canonical_json, finite, identity, require_keys, strict_json


def _close(observed: Any, expected: float, name: str) -> None:
    if not math.isclose(finite(observed, name), expected, rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError("independent numeric model check differs: " + name)


def _verify_scaler(model: FrozenRegimeModel, train: FeaturePartition) -> None:
    scaler = model.scaler
    if scaler.clip_quantiles != (0.005, 0.995) or scaler.training_rows != len(train.rows):
        raise ValueError("frozen calibration-only preprocessing contract differs")
    for index in range(len(FEATURE_NAMES)):
        column = sorted(row[index] for row in train.rows)
        quantiles = []
        for probability in (0.005, 0.995):
            position = probability * (len(column) - 1)
            left, right = math.floor(position), math.ceil(position)
            quantiles.append(column[left] + (position - left) * (column[right] - column[left]))
        lower, upper = quantiles
        clipped = [min(upper, max(lower, value)) for value in column]
        center = math.fsum(clipped) / len(clipped)
        deviation = math.sqrt(math.fsum((value - center) ** 2 for value in clipped) / len(clipped))
        constant = deviation == 0.0
        assert scaler.lower is not None and scaler.upper is not None
        for observed, expected, name in (
            (scaler.lower[index], lower, "clip lower"),
            (scaler.upper[index], upper, "clip upper"),
            (scaler.center[index], center, "calibration center"),
            (scaler.scale[index], 1.0 if constant else deviation, "calibration scale"),
        ):
            _close(observed, expected, name)
        if scaler.constant_features[index] != constant:
            raise ValueError("calibration constant-column declaration differs")


def forward_likelihood(model: FrozenRegimeModel, partition: FeaturePartition) -> float:
    """Sequence-reset forward score, without fitting-library imports."""
    filter_ = ForwardFilter(model.parameters)
    offset = 0
    total = 0.0
    for length in partition.lengths:
        filter_.reset()
        increments = []
        for row in partition.rows[offset : offset + length]:
            scaled = model.scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=model.features.digest)
            increments.append(filter_.update(scaled).log_likelihood_increment)
        total += math.fsum(increments)
        offset += length
    return total


def verify_fit_candidate(
    report: dict[str, Any],
    model: FrozenRegimeModel | None,
    train: FeaturePartition,
    validation: FeaturePartition,
    cfg: FitConfig,
) -> None:
    require_keys(
        report,
        {
            "schema_version",
            "fit_config",
            "training",
            "validation",
            "attempts",
            "candidates",
            "selected",
            "state_characterization",
            "model_sha256",
            "status",
            "claim_ready",
            "report_sha256",
        },
        "saved fit report",
    )
    if (
        report["schema_version"] != "lob_sim.hmm_fit_report.v1"
        or report["claim_ready"] is not False
        or report["report_sha256"] != identity({k: v for k, v in report.items() if k != "report_sha256"})
    ):
        raise ValueError("saved fit report identity differs")
    if (
        canonical_json(report["fit_config"]) != canonical_json(cfg.as_dict())
        or report["training"] != train.provenance()
        or report["validation"] != validation.provenance()
    ):
        raise ValueError("saved fit parents or configuration differ")
    attempts = report["attempts"]
    expected = [(k, r, restart_seed(cfg.seed, k, r)) for k in cfg.state_counts for r in range(cfg.restarts)]
    if not isinstance(attempts, list) or [(a.get("k"), a.get("restart"), a.get("seed")) for a in attempts] != expected:
        raise ValueError("saved fit attempt ledger is incomplete or reordered")
    candidates = report["candidates"]
    if not isinstance(candidates, list) or [c.get("k") for c in candidates] != list(cfg.state_counts):
        raise ValueError("saved candidate census differs")
    for candidate in candidates:
        if candidate["status"] == "valid":
            valid = [a for a in attempts if a["k"] == candidate["k"] and a["status"] == "valid"]
            if not valid:
                raise ValueError("selected K has no valid training restart")
            winner = max(valid, key=lambda a: finite(a["training_log_likelihood"], "restart likelihood"))
            if candidate["restart"] != winner["restart"]:
                raise ValueError("validation cannot select a non-winning training restart")
            _close(candidate["training_log_likelihood"], winner["training_log_likelihood"], "restart winner likelihood")
        elif candidate["status"] != "failed":
            raise ValueError("unsupported fitted candidate status")
    selected = select_validation_candidate(candidates, cfg.validation_tie_per_observation)
    if report["selected"] != selected:
        raise ValueError("saved validation selection differs from the frozen rule")
    if selected is None:
        if model is not None or report["status"] != "no_valid_candidate" or report["model_sha256"] is not None:
            raise ValueError("failed fitting cannot supply a model")
        return
    if (
        model is None
        or report["status"] != "fitted"
        or report["model_sha256"] != model.model_sha256
        or model.features != train.feature_spec
    ):
        raise ValueError("saved model differs from fit report")
    provenance = strict_json(model.provenance_json)
    for key in ("training", "validation", "fit_config", "attempts", "candidates", "state_characterization"):
        if provenance.get(key) != report[key]:
            raise ValueError("model provenance differs from saved fit ledger: " + key)
    if provenance.get("selection") != selected or provenance.get("claim_ready") is not False:
        raise ValueError("model selection/claim provenance differs")
    order = provenance.get("canonical_to_raw_state")
    if not isinstance(order, list) or sorted(order) != list(range(model.parameters.state_count)):
        raise ValueError("model state permutation is invalid")
    raw = model.parameters.permute(tuple(order.index(i) for i in range(len(order))))
    if identity(raw.as_dict()) != selected["convergence"]["parameter_sha256"]:
        raise ValueError("saved winning parameter bytes differ from the fit ledger")
    _verify_scaler(model, train)
    for partition, label in ((train, "training"), (validation, "validation")):
        score = forward_likelihood(model, partition)
        _close(selected[label + "_log_likelihood"], score, label + " forward likelihood")
        _close(
            selected[label + "_log_likelihood_per_observation"],
            score / len(partition.rows),
            label + " likelihood per row",
        )
    expected_bic = (
        model.parameters.parameter_count * math.log(len(train.rows)) - 2 * selected["training_log_likelihood"]
    )
    _close(selected["training_bic"], expected_bic, "training BIC")


def select_validation_candidate(candidates: list[dict[str, Any]], tolerance: float) -> dict[str, Any] | None:
    """Pure selection independent of the producer's fitter or test outcomes."""
    available = [candidate for candidate in candidates if candidate["status"] == "valid"]
    if not available:
        return None
    best = max(finite(c["validation_log_likelihood_per_observation"], "validation likelihood") for c in available)
    near = [c for c in available if best - c["validation_log_likelihood_per_observation"] <= tolerance]
    return min(near, key=lambda c: (finite(c["training_bic"], "training BIC"), c["k"], c["restart"]))
