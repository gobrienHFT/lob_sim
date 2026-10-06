"""Offline deterministic multi-start diagonal-Gaussian HMM selection.

hmmlearn is a fitting dependency only. Runtime inference remains the separate
stdlib forward filter. No test partition, execution outcome or PnL is accepted.
"""

from __future__ import annotations

import importlib
import math
import platform
import warnings
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Any

from ..sim.checkpoint import checkpoint_code_identity
from .artifact import FrozenRegimeModel
from .dataset import FeaturePartition
from .features import FEATURE_NAMES
from .model import GaussianHMMParameters
from .preprocess import TrainOnlyScaler
from .validation import canonical_json, finite, identity, integer


def _candidate_diagnostics(
    parameters: GaussianHMMParameters, posterior: Any, lengths: tuple[int, ...], interval_ns: int, np: Any
) -> dict[str, Any]:
    """Retrospective TRAINING diagnostics in raw candidate labels, for every K.

    MAP occupancy/episodes here summarize training smoothing, not causal runtime
    recovery. Never concatenate sequence boundaries or call censored edge spans
    complete dwell times. Weighted occupancy uses the full training posterior.
    """
    k = parameters.state_count
    occupancy = np.sum(posterior, axis=0)
    labels = np.argmax(posterior, axis=1).tolist()
    states: list[dict[str, Any]] = [
        {
            "raw_state": i,
            "weighted_observations": float(occupancy[i]),
            "weighted_occupancy": float(occupancy[i] / len(labels)),
            "map_samples": 0,
            "episodes": 0,
            "complete_episodes": 0,
            "complete_steps": 0,
            "censored_episodes": 0,
            "censored_steps": 0,
        }
        for i in range(k)
    ]
    transitions = [[0] * k for _ in range(k)]
    offset = 0
    for length in lengths:
        sequence = labels[offset : offset + length]
        start = 0
        for t, state in enumerate(sequence):
            states[state]["map_samples"] += 1
            if t:
                transitions[sequence[t - 1]][state] += 1
            if t + 1 == length or sequence[t + 1] != state:
                cell = states[state]
                cell["episodes"] += 1
                kind = "censored" if start == 0 or t + 1 == length else "complete"
                cell[kind + "_episodes"] += 1
                cell[kind + "_steps"] += t + 1 - start
                start = t + 1
        offset += length
    if offset != len(labels):
        raise ValueError("candidate diagnostic sequence lengths mismatch")
    for state, duration in zip(states, parameters.expected_durations(interval_ns)):
        state["map_occupancy"] = state["map_samples"] / len(labels)
        complete = state["complete_episodes"]
        state["mean_complete_episode_steps"] = state["complete_steps"] / complete if complete else None
        state["mean_complete_episode_seconds"] = (
            state["complete_steps"] * interval_ns / 1e9 / complete if complete else None
        )
        state["geometric_expected_duration_steps"] = None if duration is None else duration / (interval_ns / 1e9)
        state["geometric_expected_duration_seconds"] = duration
    return {
        "basis": "retrospective_training_smoothing;raw_candidate_labels;not_runtime_inference",
        "episode_basis": "separate_sequences;edge_episodes_censored;steps_are_quantized_observed_spans",
        "sequence_count": len(lengths),
        "states": states,
        "transition_matrix": [list(row) for row in parameters.transition],
        "training_map_transition_counts": transitions,
    }


@dataclass(frozen=True)
class FitConfig:
    state_counts: tuple[int, ...] = (2, 3, 4, 5)
    restarts: int = 10
    seed: int = 7
    max_iterations: int = 300
    tolerance: float = 1e-4
    minimum_variance: float = 1e-8
    minimum_state_observations: float = 1.0
    minimum_separation: float = 0.05
    minimum_rows_per_state: int = 20
    validation_tie_per_observation: float = 0.01

    def __post_init__(self) -> None:
        if not isinstance(self.state_counts, tuple) or tuple(sorted(set(self.state_counts))) != self.state_counts:
            raise ValueError("state_counts must be a sorted distinct tuple")
        if not self.state_counts or any(type(k) is not int or not 2 <= k <= 5 for k in self.state_counts):
            raise ValueError("state_counts must be in 2..5")
        for name in ("restarts", "max_iterations", "minimum_rows_per_state"):
            integer(getattr(self, name), name, minimum=1)
        integer(self.seed, "seed")
        for name in (
            "tolerance",
            "minimum_variance",
            "minimum_state_observations",
            "minimum_separation",
            "validation_tie_per_observation",
        ):
            value = finite(getattr(self, name), name)
            if value < 0 or (name in {"tolerance", "minimum_variance"} and value == 0):
                raise ValueError(name + " has an invalid nonpositive threshold")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def restart_seed(base_seed: int, state_count: int, restart: int) -> int:
    integer(base_seed, "seed")
    integer(state_count, "state_count", minimum=2)
    integer(restart, "restart")
    return int(
        identity({"algorithm": "hmm_restart_seed_v1", "base": base_seed, "k": state_count, "restart": restart})[:8], 16
    )


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        np = importlib.import_module("numpy")
        hmm = importlib.import_module("hmmlearn.hmm")
        threadpool = importlib.import_module("threadpoolctl")
    except ImportError as exc:
        raise RuntimeError('HMM fitting needs the optional extra: python -m pip install ".[hmm]"') from exc
    return np, hmm, threadpool


def _check_partitions(training: FeaturePartition, validation: FeaturePartition) -> None:
    if training.role != "calibration" or validation.role != "validation":
        raise ValueError("fitting accepts calibration and validation only; never test")
    if max(training.days) >= min(validation.days):
        raise ValueError("training must precede validation by whole UTC days")
    if (
        training.split_sha256 != validation.split_sha256
        or training.dataset_sha256 != validation.dataset_sha256
        or training.feature_spec != validation.feature_spec
        or training.symbol != validation.symbol
        or training.instrument_sha256 != validation.instrument_sha256
    ):
        raise ValueError("fit partitions have incompatible dataset/split/features/instrument identities")


def _parameters(estimator: Any, np: Any) -> GaussianHMMParameters:
    return GaussianHMMParameters(
        tuple(estimator.startprob_.tolist()),
        tuple(tuple(row) for row in estimator.transmat_.tolist()),
        tuple(tuple(row) for row in estimator.means_.tolist()),
        tuple(tuple(np.diag(matrix).tolist()) for matrix in estimator.covars_),
    )


def _state_report(
    parameters: GaussianHMMParameters, posterior: Any, raw_rows: Any, scaled_rows: Any, np: Any
) -> tuple[tuple[int, ...], list[dict[str, Any]]]:
    """Training-only risk ordering; no economic outcomes or semantic invention.

    Equal weights: spread, realized volatility, thin visible depth, absolute
    L1 pressure, absolute aggressive trade pressure. Percentile ranks produce
    an interpretable [0,1] relative ordering, not a crash/alpha probability.
    """
    signature = []
    occupancy = np.sum(posterior, axis=0)
    for state in range(parameters.state_count):
        weights = posterior[:, state]
        zmean = np.sum(scaled_rows * weights[:, None], axis=0) / occupancy[state]
        pressure = np.sum(np.abs(raw_rows[:, [3, 5]]) * weights[:, None], axis=0) / occupancy[state]
        signature.append((float(zmean[0]), float(zmean[2]), -float(zmean[6]), float(pressure[0]), float(pressure[1])))
    components = []
    for state in range(parameters.state_count):
        components.append(
            [
                sum(signature[other][column] < signature[state][column] for other in range(parameters.state_count))
                / (parameters.state_count - 1)
                for column in range(5)
            ]
        )
    risk_scores = [math.fsum(row) / len(row) for row in components]
    order = tuple(
        sorted(
            range(parameters.state_count),
            key=lambda s: (risk_scores[s], parameters.means[s], parameters.variances[s], s),
        )
    )
    reports = []
    for canonical, state in enumerate(order):
        weights = posterior[:, state]
        original = np.sum(raw_rows * weights[:, None], axis=0) / occupancy[state]
        reports.append(
            {
                "state": "STATE_" + str(canonical),
                "raw_state": state,
                "training_occupancy": float(occupancy[state] / len(raw_rows)),
                "training_effective_observations": float(occupancy[state]),
                "risk_score": risk_scores[state],
                "risk_component_ranks": components[state],
                "training_feature_means": {name: float(value) for name, value in zip(FEATURE_NAMES, original)},
                "interpretation": "training-feature statistical state; not a discovered economic truth",
            }
        )
    return order, reports


@dataclass(frozen=True)
class FitResult:
    model: FrozenRegimeModel | None
    report_json: str

    def report(self) -> dict[str, Any]:
        from .validation import strict_json

        return strict_json(self.report_json)


def fit_candidates(
    training: FeaturePartition, validation: FeaturePartition, config: FitConfig = FitConfig()
) -> FitResult:
    """Best valid train-likelihood restart per K, then validation/BIC selection.

    Fit multiple independent sequences with ``lengths``. Only the calibration
    partition estimates clipping/scaling, EM parameters, occupancy and labels.
    Every requested restart appears in the ledger, including hard failures.
    Floating-point identity is promised within a pinned software/CPU environment,
    not across arbitrary BLAS/compiler/library versions.
    """
    _check_partitions(training, validation)
    np, hmm, threadpool = _dependencies()
    scaler = TrainOnlyScaler.fit_training(
        training.rows,
        feature_names=FEATURE_NAMES,
        feature_identity=training.feature_spec.digest,
    )
    train_x = np.array(
        [
            scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=training.feature_spec.digest)
            for row in training.rows
        ]
    )
    val_x = np.array(
        [
            scaler.transform(row, feature_names=FEATURE_NAMES, feature_identity=training.feature_spec.digest)
            for row in validation.rows
        ]
    )
    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    fitted: dict[int, tuple[GaussianHMMParameters, Any]] = {}
    active_columns = [i for i, constant in enumerate(scaler.constant_features) if not constant]
    with threadpool.threadpool_limits(limits=1):
        for k in config.state_counts:
            best: tuple[float, int, GaussianHMMParameters, Any] | None = None
            for restart in range(config.restarts):
                seed = restart_seed(config.seed, k, restart)
                attempt: dict[str, Any] = {"k": k, "restart": restart, "seed": seed, "status": "failed"}
                attempts.append(attempt)
                if len(training.rows) < k * config.minimum_rows_per_state or not active_columns:
                    attempt["reason"] = "insufficient_training_rows_or_all_features_constant"
                    continue
                try:
                    estimator = hmm.GaussianHMM(
                        n_components=k,
                        covariance_type="diag",
                        n_iter=config.max_iterations,
                        tol=config.tolerance,
                        random_state=seed,
                        min_covar=1e-3,
                        implementation="log",
                        algorithm="viterbi",
                    )
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        estimator.fit(train_x, lengths=list(training.lengths))
                    attempt["warning_types"] = sorted({type(w.message).__name__ for w in caught})
                    history = [finite(float(value), "EM log likelihood") for value in estimator.monitor_.history]
                    gain = history[-1] - history[-2] if len(history) >= 2 else None
                    converged = gain is not None and -config.tolerance <= gain < config.tolerance
                    attempt.update(
                        {
                            "iterations": estimator.monitor_.iter,
                            "last_em_gain": gain,
                            "converged": converged,
                            "last_em_training_log_likelihood": history[-1] if history else None,
                        }
                    )
                    if not converged:
                        attempt["reason"] = "not_converged_or_likelihood_decreased"
                        continue
                    parameters = _parameters(estimator, np)
                    train_ll, posterior = estimator.score_samples(train_x, lengths=list(training.lengths))
                    train_ll = finite(float(train_ll), "training log likelihood")
                    if not np.isfinite(posterior).all():
                        raise ValueError("non-finite training posterior")
                    occupancy = np.sum(posterior, axis=0)
                    separation = min(
                        math.sqrt(
                            math.fsum((parameters.means[a][c] - parameters.means[b][c]) ** 2 for c in active_columns)
                            / len(active_columns)
                        )
                        for a in range(k)
                        for b in range(a + 1, k)
                    )
                    attempt.update(
                        {
                            "training_log_likelihood": train_ll,
                            "effective_state_observations": occupancy.tolist(),
                            "minimum_mean_separation": separation,
                            "parameter_sha256": identity(parameters.as_dict()),
                        }
                    )
                    if min(value for row in parameters.variances for value in row) < config.minimum_variance:
                        attempt["reason"] = "variance_collapse"
                        continue
                    if float(np.min(occupancy)) < config.minimum_state_observations:
                        attempt["reason"] = "unoccupied_state"
                        continue
                    if separation < config.minimum_separation:
                        attempt["reason"] = "indistinguishable_emission_means"
                        continue
                    attempt["status"] = "valid"
                    if best is None or train_ll > best[0]:
                        best = (train_ll, restart, parameters, estimator)
                except (ArithmeticError, TypeError, ValueError) as exc:
                    attempt["reason"] = "numerical_fit_failure"
                    attempt["error_type"] = type(exc).__name__
            if best is None:
                candidates.append({"k": k, "status": "failed", "reason": "no_valid_restart"})
                continue
            train_ll, restart, parameters, estimator = best
            try:
                validation_ll = finite(
                    float(estimator.score(val_x, lengths=list(validation.lengths))), "validation log likelihood"
                )
            except (ArithmeticError, ValueError) as exc:
                candidates.append(
                    {"k": k, "status": "failed", "reason": "invalid_validation_score", "error_type": type(exc).__name__}
                )
                continue
            winning_attempt = attempts[-config.restarts + restart]
            candidate = {
                "k": k,
                "status": "valid",
                "restart": restart,
                "training_log_likelihood": train_ll,
                "training_log_likelihood_per_observation": train_ll / len(training.rows),
                "validation_log_likelihood": validation_ll,
                "validation_log_likelihood_per_observation": validation_ll / len(validation.rows),
                "parameter_count": parameters.parameter_count,
                "training_bic": parameters.parameter_count * math.log(len(training.rows)) - 2 * train_ll,
                "convergence": {
                    key: winning_attempt[key]
                    for key in ("seed", "iterations", "last_em_gain", "converged", "warning_types", "parameter_sha256")
                },
            }
            _, candidate_posterior = estimator.score_samples(train_x, lengths=list(training.lengths))
            candidate["training_diagnostics"] = _candidate_diagnostics(
                parameters, candidate_posterior, training.lengths, training.feature_spec.interval_ns, np
            )
            candidates.append(candidate)
            fitted[k] = (parameters, estimator)
        valid = [candidate for candidate in candidates if candidate["status"] == "valid"]
        selected = None
        model = None
        state_report: list[dict[str, Any]] = []
        if valid:
            best_score = max(candidate["validation_log_likelihood_per_observation"] for candidate in valid)
            near = [
                candidate
                for candidate in valid
                if best_score - candidate["validation_log_likelihood_per_observation"]
                <= config.validation_tie_per_observation
            ]
            selected = min(
                near, key=lambda candidate: (candidate["training_bic"], candidate["k"], candidate["restart"])
            )
            parameters, estimator = fitted[selected["k"]]
            _, training_posterior = estimator.score_samples(train_x, lengths=list(training.lengths))
            order, state_report = _state_report(parameters, training_posterior, np.array(training.rows), train_x, np)
            parameters = parameters.permute(order)
            durations = parameters.expected_durations(training.feature_spec.interval_ns)
            for state, duration in zip(state_report, durations):
                state["geometric_expected_duration_seconds"] = duration
            provenance = {
                "purpose": "offline_fitted_market_feature_model;not_economic_evidence",
                "training": training.provenance(),
                "validation": validation.provenance(),
                "fit_config": config.as_dict(),
                "attempts": attempts,
                "candidates": candidates,
                "selection": selected,
                "selection_rule": "best_train_restart_per_K;max_validation_LL_per_row;within_tolerance_min_train_BIC_then_K",
                "state_characterization": state_report,
                "canonical_to_raw_state": list(order),
                "risk_signature": "equal_rank_weights:spread,volatility,negative_depth,absolute_L1_pressure,absolute_trade_pressure;training_only",
                "occupancy_method": "retrospective_training_smoothing;never_runtime_inference",
                "code_identity": checkpoint_code_identity(),
                "dependencies": {
                    name: version(name) for name in ("hmmlearn", "numpy", "scipy", "scikit-learn", "threadpoolctl")
                },
                "environment": {"python": platform.python_version(), "platform": platform.platform(), "threads": 1},
                "claim_ready": False,
            }
            model = FrozenRegimeModel(training.feature_spec, scaler, parameters, canonical_json(provenance))
    report = {
        "schema_version": "lob_sim.hmm_fit_report.v1",
        "fit_config": config.as_dict(),
        "training": training.provenance(),
        "validation": validation.provenance(),
        "attempts": attempts,
        "candidates": candidates,
        "selected": selected,
        "state_characterization": state_report,
        "model_sha256": model.model_sha256 if model is not None else None,
        "status": "fitted" if model is not None else "no_valid_candidate",
        "claim_ready": False,
    }
    report["report_sha256"] = identity(report)
    return FitResult(model, canonical_json(report))


def inspect_model(model: FrozenRegimeModel) -> str:
    from .validation import strict_json

    metadata = strict_json(model.provenance_json)
    lines = [
        f"Model: {model.model_sha256}",
        f"Diagonal Gaussian HMM: {model.parameters.state_count} states; {len(FEATURE_NAMES)} features",
        f"Sample interval: {model.features.interval_ns / 1e9:g}s; window: {model.features.window_ns / 1e9:g}s",
        "Inference: causal forward filtering. Training signatures may use retrospective smoothing.",
        "States are statistical labels, not true exchange regimes or evidence of alpha.",
    ]
    for state in metadata.get("state_characterization", []):
        lines.append(
            f"{state['state']}: training occupancy={state['training_occupancy']:.3%}; relative risk={state['risk_score']:.3f}; geometric duration={state['geometric_expected_duration_seconds']}s"
        )
        lines.extend(f"  {name}: {value:.6g}" for name, value in state["training_feature_means"].items())
    lines.append("Transition matrix (canonical states):")
    lines.extend("  " + " ".join(f"{value:.6f}" for value in row) for row in model.parameters.transition)
    if not metadata.get("state_characterization"):
        lines.append("No fitted training characterization supplied; inspect provenance before research use.")
    candidates = metadata.get("candidates", [])
    if candidates:
        lines.append("Candidate selection (training-only restarts; validation likelihood/BIC):")
        for candidate in candidates:
            if candidate["status"] != "valid":
                lines.append(f"  K={candidate['k']}: failed ({candidate['reason']})")
                continue
            train_per_row = candidate.get("training_log_likelihood_per_observation")
            train_display = "not recorded" if train_per_row is None else f"{train_per_row:.6g}"
            lines.append(
                f"  K={candidate['k']}: train LL/row={train_display}; "
                f"validation LL/row={candidate['validation_log_likelihood_per_observation']:.6g}; "
                f"BIC={candidate['training_bic']:.6g}; restart={candidate['restart']}"
            )
            diagnostics = candidate.get("training_diagnostics")
            if diagnostics is None:
                lines.append("    Per-K occupancy/episode diagnostics were not recorded in this older artifact.")
                continue
            for state in diagnostics["states"]:
                lines.append(
                    f"    raw state {state['raw_state']}: weighted occupancy={state['weighted_occupancy']:.3%}; "
                    f"MAP occupancy={state['map_occupancy']:.3%}; geometric duration={state['geometric_expected_duration_seconds']}s; "
                    f"complete/censored episodes={state['complete_episodes']}/{state['censored_episodes']}"
                )
    return "\n".join(lines)
