"""Train-aligned causal recovery diagnostics for known synthetic market tapes.

Truth labels never enter model fitting, scaling, state selection or emissions.
This is bounded offline diagnostic analysis, not execution or economic evidence.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from itertools import permutations
from pathlib import Path
from typing import Any, cast

from ..config import Config
from ..research.protocol import ResearchRegistry, UTCDaySplit
from .artifact import FrozenRegimeModel, save_model
from .dataset import (
    _rows,
    dataset_split,
    extract_features,
    load_dataset_manifest,
    publish_json,
    read_partition,
    utc_day,
)
from .features import FeatureSample, FeatureSpec
from .fit import FitConfig, fit_candidates
from .hysteresis import HysteresisConfig
from .runtime import CausalRegimeEstimator
from .synthetic import SECOND, TRANSITION, SyntheticTapeConfig, generate_synthetic_tape
from .validation import identity, integer, require_keys


@dataclass(frozen=True)
class RecoveryObservation:
    sample_ns: int
    available_at_ns: int
    sequence_id: str
    truth: int
    raw_state: int
    active_state: int | None

    def __post_init__(self) -> None:
        integer(self.sample_ns, "sample_ns")
        integer(self.available_at_ns, "available_at_ns", minimum=self.sample_ns)
        if not isinstance(self.sequence_id, str) or not self.sequence_id:
            raise ValueError("sequence identity required")
        if type(self.truth) is not int or self.truth not in (0, 1):
            raise ValueError("synthetic truth must be 0 or 1")
        integer(self.raw_state, "raw_state")
        if self.active_state is not None:
            integer(self.active_state, "active_state")


def adjusted_rand_index(truth: Sequence[int], predicted: Sequence[int]) -> float:
    """Exact pair-count ARI; invariant to label names and differing state counts."""
    if not truth or len(truth) != len(predicted):
        raise ValueError("ARI needs nonempty paired labels")
    cells = Counter(zip(truth, predicted))

    def choose2(n: int) -> int:
        return n * (n - 1) // 2

    pairs = choose2(len(truth))
    if not pairs:
        return 1.0
    joint = sum(choose2(n) for n in cells.values())
    left = sum(choose2(n) for n in Counter(truth).values())
    right = sum(choose2(n) for n in Counter(predicted).values())
    expected = Fraction(left * right, pairs)
    maximum = Fraction(left + right, 2)
    return float((joint - expected) / (maximum - expected)) if maximum != expected else 1.0


def training_alignment(rows: Sequence[RecoveryObservation], state_count: int) -> dict[str, Any]:
    """Two-state permutation, or explicitly many-to-one when selection picks K>2.

    All ties use lexicographic labels. Empty training states map to 0 and remain
    disclosed. Neither validation nor test truth may refine this mapping.
    """
    integer(state_count, "state_count", minimum=2)
    if state_count > 5 or not rows:
        raise ValueError("alignment requires training observations and K=2..5")
    confusion = [[0] * state_count for _ in range(2)]
    for row in rows:
        if row.raw_state >= state_count:
            raise ValueError("raw state exceeds fitted K")
        confusion[row.truth][row.raw_state] += 1
    if state_count == 2:
        mapping = min(permutations(range(2)), key=lambda p: (-sum(confusion[p[s]][s] for s in range(2)), p))
        kind = "training_only_bijective_permutation"
    else:
        mapping = tuple(max(range(2), key=lambda t: (confusion[t][s], -t)) for s in range(state_count))
        kind = "training_only_many_to_one;not_a_permutation"
    result = {
        "basis": kind,
        "raw_to_truth": list(mapping),
        "training_confusion": confusion,
        "unobserved_training_states": [s for s in range(state_count) if not sum(c[s] for c in confusion)],
    }
    result["alignment_sha256"] = identity(result)
    return result


def _check_rows(rows: Sequence[RecoveryObservation], state_count: int, interval_ns: int) -> None:
    if not rows:
        raise ValueError("recovery requires observations")
    seen: set[str] = set()
    previous = None
    for row in rows:
        if row.raw_state >= state_count or (row.active_state is not None and row.active_state >= state_count):
            raise ValueError("prediction outside fitted K")
        if previous is not None:
            if row.sample_ns <= previous.sample_ns or row.available_at_ns < previous.available_at_ns:
                raise ValueError("regressing recovery observation")
            if row.sequence_id == previous.sequence_id and row.sample_ns != previous.sample_ns + interval_ns:
                raise ValueError("noncontiguous recovery sequence")
        if previous is None or row.sequence_id != previous.sequence_id:
            if row.sequence_id in seen:
                raise ValueError("reused disjoint recovery sequence")
            seen.add(row.sequence_id)
        previous = row


def _duration_summary(
    rows: Sequence[RecoveryObservation], labels: Sequence[int | None], interval_ns: int
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = [
        {"state": s, "complete_episodes": 0, "complete_steps": 0, "censored_episodes": 0, "censored_steps": 0}
        for s in range(2)
    ]
    start = 0
    for i, (row, label) in enumerate(zip(rows, labels)):
        if i + 1 == len(rows) or labels[i + 1] != label or rows[i + 1].sequence_id != row.sequence_id:
            if label is not None:
                left_edge = start == 0 or rows[start - 1].sequence_id != row.sequence_id or labels[start - 1] is None
                right_edge = i + 1 == len(rows) or rows[i + 1].sequence_id != row.sequence_id or labels[i + 1] is None
                kind = "censored" if left_edge or right_edge else "complete"
                states[label][kind + "_episodes"] += 1
                states[label][kind + "_steps"] += i + 1 - start
            start = i + 1
    for state in states:
        n = state["complete_episodes"]
        state["mean_complete_seconds"] = state["complete_steps"] * interval_ns / SECOND / n if n else None
    return states


def recovery_metrics(
    rows: Sequence[RecoveryObservation],
    alignment: dict[str, Any],
    *,
    interval_ns: int = SECOND,
    confirmation_samples: int = 3,
) -> dict[str, Any]:
    """Report raw and hysteretic recovery with causal, right-censored switch lags."""
    integer(interval_ns, "interval_ns", minimum=1)
    integer(confirmation_samples, "confirmation_samples", minimum=1)
    mapping = alignment["raw_to_truth"]
    if (
        not isinstance(mapping, list)
        or not 2 <= len(mapping) <= 5
        or any(type(x) is not int or x not in (0, 1) for x in mapping)
    ):
        raise ValueError("invalid frozen alignment")
    if alignment.get("alignment_sha256") != identity({k: v for k, v in alignment.items() if k != "alignment_sha256"}):
        raise ValueError("alignment checksum mismatch")
    _check_rows(rows, len(mapping), interval_ns)
    truth = [row.truth for row in rows]
    result: dict[str, Any] = {
        "observations": len(rows),
        "sequences": len({r.sequence_id for r in rows}),
        "native_raw_adjusted_rand_index": adjusted_rand_index(truth, [r.raw_state for r in rows]),
        "truth_durations": _duration_summary(rows, truth, interval_ns),
        "phases": {},
    }
    for phase in ("raw", "active"):
        labels: list[int | None] = [
            mapping[state] if state is not None else None
            for r in rows
            for state in (r.raw_state if phase == "raw" else r.active_state,)
        ]
        confusion = [[0, 0, 0] for _ in range(2)]
        transitions = [[0, 0] for _ in range(2)]
        truth_transitions = [[0, 0] for _ in range(2)]
        lag_rows = []
        for i, row in enumerate(rows):
            label = labels[i]
            confusion[row.truth][2 if label is None else label] += 1
            if i and row.sequence_id == rows[i - 1].sequence_id:
                truth_transitions[rows[i - 1].truth][row.truth] += 1
                previous_label = labels[i - 1]
                if label is not None and previous_label is not None:
                    transitions[previous_label][label] += 1
                if row.truth != rows[i - 1].truth:
                    stop = i + 1
                    while (
                        stop < len(rows) and rows[stop].sequence_id == row.sequence_id and rows[stop].truth == row.truth
                    ):
                        stop += 1
                    # Confirm only through the end of this known regime episode.
                    required = confirmation_samples if phase == "raw" else 1
                    streak = 0
                    detected = None
                    for j in range(i, stop):
                        streak = streak + 1 if labels[j] == row.truth else 0
                        if streak == required:
                            detected = j
                            break
                    lag_rows.append(
                        {
                            "switch_sample_ns": row.sample_ns,
                            "new_truth_state": row.truth,
                            "confirmed_at_sample_ns": rows[detected].sample_ns if detected is not None else None,
                            "confirmed_at_available_ns": rows[detected].available_at_ns
                            if detected is not None
                            else None,
                            "available_lag_ns": rows[detected].available_at_ns - row.sample_ns
                            if detected is not None
                            else None,
                            "censored_at_sample_ns": rows[stop - 1].sample_ns,
                            "status": "detected"
                            if detected is not None
                            else "not_confirmed_before_next_truth_switch_or_sequence_end",
                        }
                    )
        available = [i for i, label in enumerate(labels) if label is not None]
        lags = [cast(int, item["available_lag_ns"]) for item in lag_rows if item["available_lag_ns"] is not None]
        probabilities = [[value / sum(counts) for value in counts] if sum(counts) else None for counts in transitions]
        empirical_error = (
            math.fsum(
                abs(cast(list[float], probabilities[i])[j] - TRANSITION[i][j]) for i in range(2) for j in range(2)
            )
            / 4
            if all(row is not None for row in probabilities)
            else None
        )
        result["phases"][phase] = {
            "confusion_columns": ["truth_0", "truth_1", "unavailable"],
            "confusion": confusion,
            "available_observations": len(available),
            "accuracy_all_observations": sum(confusion[s][s] for s in range(2)) / len(rows),
            "accuracy_available": sum(confusion[s][s] for s in range(2)) / len(available) if available else None,
            "aligned_adjusted_rand_index_available": adjusted_rand_index(
                [truth[i] for i in available], [cast(int, labels[i]) for i in available]
            )
            if available
            else None,
            "transition_counts": transitions,
            "truth_transition_counts": truth_transitions,
            "empirical_transition_probabilities": probabilities,
            "empirical_transition_mean_absolute_error_against_generator": empirical_error,
            "transition_error_basis": "sampled mapped labels;sequence boundaries excluded;unavailable adjacent pairs omitted;not fitted parameter recovery",
            "duration_summary": _duration_summary(rows, labels, interval_ns),
            "switch_detection": lag_rows,
            "lag_confirmation_samples": confirmation_samples if phase == "raw" else 1,
            "confirmed_switches": len(lags),
            "truth_switches": len(lag_rows),
            "mean_confirmed_available_lag_seconds": math.fsum(lags) / len(lags) / SECOND if lags else None,
        }
    return result


def _truth(root: Path, manifest: dict[str, Any], days: tuple[str, ...]) -> dict[int, int]:
    if manifest.get("manifest_sha256") != identity({k: v for k, v in manifest.items() if k != "manifest_sha256"}):
        raise ValueError("synthetic truth manifest checksum mismatch")
    result = {}
    for entry in manifest["truth"]["day_files"]:
        if entry["utc_day"] not in days:
            continue
        if entry["path"] != "truth/" + entry["utc_day"] + ".jsonl":
            raise ValueError("unsafe truth path")
        count = 0
        for row in _rows(root / entry["path"], entry["sha256"]):
            require_keys(
                row,
                {"schema_version", "symbol", "utc_day", "sample_ns", "wall_ns", "state", "sequence"},
                "synthetic truth",
            )
            ns = integer(row["sample_ns"], "truth sample_ns")
            if (
                row["schema_version"] != "lob_sim.synthetic_regime_truth.v1"
                or row["symbol"] != "BTCUSDT"
                or row["utc_day"] != entry["utc_day"]
                or utc_day(row["wall_ns"]) != entry["utc_day"]
            ):
                raise ValueError("truth identity mismatch")
            if type(row["state"]) is not int or row["state"] not in (0, 1) or ns in result:
                raise ValueError("invalid/duplicate truth label")
            result[ns] = row["state"]
            count += 1
        if count != entry["records"]:
            raise ValueError("truth row census mismatch")
    return result


def predict_partition(
    directory: Path,
    split: UTCDaySplit,
    role: str,
    model: FrozenRegimeModel,
    truth_root: Path,
    truth_manifest: dict[str, Any],
    registry: ResearchRegistry,
    hysteresis: HysteresisConfig = HysteresisConfig(),
) -> list[RecoveryObservation]:
    """Verified selected-day features only; reset the actual runtime per sequence."""
    if role not in ("calibration", "validation", "test"):
        raise ValueError("unknown recovery partition")
    partition = read_partition(directory, split, role, symbol="BTCUSDT", registry=registry)  # type: ignore[arg-type]
    truth = _truth(truth_root, truth_manifest, partition.days)
    estimator = CausalRegimeEstimator(model, hysteresis)
    rows: list[RecoveryObservation] = []
    previous_sequence = None
    manifest = load_dataset_manifest(directory)
    if manifest["input_sha256"] != truth_manifest["raw"]["sha256"]:
        raise ValueError("features and synthetic truth have different raw-tape parents")
    for entry in manifest["day_files"]:
        if entry["utc_day"] not in partition.days:
            continue
        for row in _rows(directory / entry["path"], entry["file_sha256"]):
            if row["status"] != "VALID" or row["symbol"] != "BTCUSDT":
                continue
            if len(rows) >= len(partition.rows) or tuple(row["features"]) != partition.rows[len(rows)]:
                raise ValueError("feature changed between validation and prediction")
            if row["sequence_id"] != previous_sequence:
                estimator = CausalRegimeEstimator(model, hysteresis)
                previous_sequence = row["sequence_id"]
            sample = FeatureSample(
                row["symbol"],
                row["sample_ns"],
                row["receive_seq"],
                tuple(row["epochs"]),
                row["feature_identity"],
                row["status"],
                tuple(row["features"]),
                row["reset_reason"],
            )
            signal = estimator.update(sample)
            assert signal.hysteresis is not None
            if sample.sample_ns not in truth:
                raise ValueError("valid sample has no independent truth label")
            rows.append(
                RecoveryObservation(
                    sample.sample_ns,
                    row["available_at_ns"],
                    row["sequence_id"],
                    truth[sample.sample_ns],
                    signal.hysteresis.raw_map_state,
                    signal.hysteresis.active_state,
                )
            )
    if len(rows) != len(partition.rows):
        raise ValueError("prediction feature census mismatch")
    return rows


def run_synthetic_recovery(
    directory: str | Path,
    cfg: Config,
    *,
    tape_config: SyntheticTapeConfig = SyntheticTapeConfig(),
    feature_spec: FeatureSpec = FeatureSpec(),
    fit_config: FitConfig = FitConfig(),
    hysteresis: HysteresisConfig = HysteresisConfig(),
) -> dict[str, Any]:
    """Raw tape -> authoritative features -> train/validation selection -> test.

    Freeze every attempted specification before test access. Alignment uses
    forward-filtered TRAINING labels only after unsupervised model selection.
    The truth Markov process is not conditionally Gaussian after rolling feature
    transforms; imperfect recovery and K>2 are valid outcomes, not failures.
    """
    if feature_spec.interval_ns != SECOND:
        raise ValueError("this labeled fixture uses a frozen one-second hidden clock")
    if cfg.hmm is not None:
        raise ValueError("synthetic extraction requires HMM disabled")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    registry = ResearchRegistry()
    registry.register(
        "synthetic_raw_tape_recovery",
        {
            "tape": tape_config.as_dict(),
            "feature_spec": feature_spec.as_dict(),
            "fit": fit_config.as_dict(),
            "hysteresis": hysteresis.as_dict(),
            "alignment": "train_forward_only;bijective_K2_else_many_to_one",
            "raw_lag_confirmation_samples": 3,
            "active_lag_confirmation_samples": 1,
        },
    )
    frozen = registry.freeze()
    publish_json(root / "registry.json", frozen)
    tape = generate_synthetic_tape(root / "tape", tape_config)
    features = extract_features(
        root / "tape/market.ndjson",
        root / "features",
        replace(cfg, mm_enabled=False),
        spec=feature_spec,
        symbols=("BTCUSDT",),
    )
    split = dataset_split(root / "features")
    training = read_partition(root / "features", split, "calibration", symbol="BTCUSDT")
    validation = read_partition(root / "features", split, "validation", symbol="BTCUSDT")
    fitted = fit_candidates(training, validation, fit_config)
    publish_json(root / "fit_report.json", fitted.report())
    report: dict[str, Any] = {
        "schema_version": "lob_sim.synthetic_regime_recovery.v1",
        "claim_ready": False,
        "purpose": "known synthetic market-tape recovery;not_Binance_or_economic_evidence",
        "tape": tape,
        "dataset_sha256": features["dataset_sha256"],
        "feature_sample_count": features["sample_count"],
        "feature_status_counts": features["status_counts"],
        "feature_spec": feature_spec.as_dict(),
        "split": split.as_dict(),
        "registry_sha256": frozen["registry_sha256"],
        "fit_report_sha256": fitted.report()["report_sha256"],
        "model_sha256": fitted.model.model_sha256 if fitted.model else None,
        "status": "no_valid_candidate" if fitted.model is None else "completed",
        "partitions": {},
        "lag_basis": "raw:three consecutive mapped matches;active:first match of already hysteresis-confirmed state;availability,not backdated sample;right censored at next truth switch or sequence end",
        "duration_basis": "quantized sample spans;sequence edges/unavailable neighbors censored;rolling emissions are not a Gaussian Markov ground truth",
        "limitations": [
            "short synthetic UTC snippets, not valid full days or holdout market evidence",
            "train-only label alignment, not a discovery of objectively real market states",
            "native ARI penalizes splitting truth into additional fitted states",
            "no policy, fills, PnL, private FIFO, profitability or performance claim",
        ],
    }
    if fitted.model is not None:
        save_model(root / "model.json", fitted.model)
        train_rows = predict_partition(
            root / "features", split, "calibration", fitted.model, root / "tape", tape, registry, hysteresis
        )
        alignment = training_alignment(train_rows, fitted.model.parameters.state_count)
        publish_json(root / "alignment.json", alignment)
        report["alignment"] = alignment
        report["generating_geometric_duration_seconds"] = [1 / (1 - TRANSITION[s][s]) for s in range(2)]
        report["fitted_geometric_duration_seconds"] = list(
            fitted.model.parameters.expected_durations(feature_spec.interval_ns)
        )
        report["selected_state_count"] = fitted.model.parameters.state_count
        for role in ("calibration", "validation", "test"):
            rows = (
                train_rows
                if role == "calibration"
                else predict_partition(
                    root / "features", split, role, fitted.model, root / "tape", tape, registry, hysteresis
                )
            )
            report["partitions"][role] = recovery_metrics(rows, alignment)
            report["partitions"][role]["prediction_sha256"] = identity([asdict(row) for row in rows])
        # The fitted transition has a direct truth-label interpretation only K=2.
        mapping = alignment["raw_to_truth"]
        if fitted.model.parameters.state_count == 2:
            inverse = [mapping.index(s) for s in range(2)]
            matrix = [[fitted.model.parameters.transition[inverse[i]][inverse[j]] for j in range(2)] for i in range(2)]
            report["permutation_aligned_transition"] = {
                "matrix": matrix,
                "mean_absolute_parameter_error": math.fsum(
                    abs(matrix[i][j] - TRANSITION[i][j]) for i in range(2) for j in range(2)
                )
                / 4,
                "caveat": "rolling correlated features violate the generating process's emission assumptions;parameter equality is not expected",
            }
        else:
            report["permutation_aligned_transition"] = None
    report["report_sha256"] = identity(report)
    publish_json(root / "recovery_report.json", report)
    return report


def format_recovery_report(report: dict[str, Any]) -> str:
    if report.get("report_sha256") != identity({k: v for k, v in report.items() if k != "report_sha256"}):
        raise ValueError("recovery report checksum mismatch")
    lines = [
        "Synthetic raw market-tape recovery (DIAGNOSTIC ONLY)",
        f"Status: {report['status']}; model: {report['model_sha256']}",
        f"Registry: {report['registry_sha256']}",
        "Training-only alignment; forward filtering; no Binance, alpha or economic claim.",
    ]
    for role, metrics in report["partitions"].items():
        lines.append(
            f"{role}: {metrics['observations']} observations; native ARI={metrics['native_raw_adjusted_rand_index']:.4f}"
        )
        for phase, cell in metrics["phases"].items():
            lines.append(
                f"  {phase}: mapped accuracy(all)={cell['accuracy_all_observations']:.2%}; confirmed switches={cell['confirmed_switches']}/{cell['truth_switches']}; mean confirmation-available lag={cell['mean_confirmed_available_lag_seconds']}s"
            )
    return "\n".join(lines)
