"""Independent diagnostic oracles and actual raw-tape replay/fit recovery."""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.regime.artifact import load_model
from lob_sim.regime.dataset import dataset_split, read_partition
from lob_sim.regime.fit import FitConfig, FitResult
from lob_sim.regime.recovery import (
    RecoveryObservation,
    adjusted_rand_index,
    format_recovery_report,
    predict_partition,
    recovery_metrics,
    run_synthetic_recovery,
    training_alignment,
)
from lob_sim.regime.synthetic import SECOND, SyntheticTapeConfig, generate_synthetic_tape, synthetic_rows
from lob_sim.regime.validation import canonical_json
from lob_sim.research.protocol import ResearchRegistry


def cfg():
    return load_config(".env.example", inherit_environment=False)


def rows(truth, raw=None, active=None, *, sequence="a", start=1, lag_ns=500_000_000):
    raw = truth if raw is None else raw
    active = raw if active is None else active
    return [
        RecoveryObservation((start + i) * SECOND, (start + i) * SECOND + lag_ns, sequence, t, p, a)
        for i, (t, p, a) in enumerate(zip(truth, raw, active))
    ]


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("size", (1, 5, 100))
def test_exact_pair_count_ari_matches_independent_sklearn(seed, size):
    metrics = pytest.importorskip("sklearn.metrics")
    rng = random.Random(seed)
    truth = [rng.randrange(2) for _ in range(size)]
    prediction = [rng.randrange(5) for _ in range(size)]
    assert adjusted_rand_index(truth, prediction) == pytest.approx(
        metrics.adjusted_rand_score(truth, prediction), abs=1e-15
    )
    assert adjusted_rand_index(truth, [p + 50 for p in prediction]) == adjusted_rand_index(truth, prediction)


def test_train_only_permutation_and_many_to_one_are_distinct_and_deterministic():
    training = rows([0, 0, 1, 1], [1, 1, 0, 0])
    alignment = training_alignment(training, 2)
    assert alignment["raw_to_truth"] == [1, 0]
    assert "bijective" in alignment["basis"]
    assert recovery_metrics(training, alignment)["phases"]["raw"]["accuracy_all_observations"] == 1
    # Evaluation truth cannot repair the frozen inverse mapping.
    wrong_test = recovery_metrics(rows([0, 0, 1, 1]), alignment)
    assert wrong_test["phases"]["raw"]["accuracy_all_observations"] == 0
    split = training_alignment(rows([0, 0, 1, 1], [0, 1, 2, 2]), 3)
    assert split["raw_to_truth"] == [0, 0, 1]
    assert split["basis"].endswith("not_a_permutation")
    report = recovery_metrics(rows([0, 0, 1, 1], [0, 1, 2, 2]), split)
    assert report["native_raw_adjusted_rand_index"] < 1
    assert report["phases"]["raw"]["aligned_adjusted_rand_index_available"] == 1
    empty = training_alignment(rows([0, 1], [1, 2]), 4)
    assert empty["unobserved_training_states"] == [0, 3]
    tie = training_alignment(rows([0, 1], [0, 0]), 2)
    assert tie["raw_to_truth"] == [0, 1]


def test_lag_is_confirmation_availability_not_backdated_first_match_and_short_switch_censored():
    alignment = training_alignment(rows([0, 1]), 2)
    observations = rows([0, 0, 1, 1, 1, 1, 0, 0], [0, 0, 0, 1, 1, 1, 0, 0])
    report = recovery_metrics(observations, alignment)
    lag = report["phases"]["raw"]["switch_detection"]
    assert lag[0]["switch_sample_ns"] == 3 * SECOND
    assert lag[0]["confirmed_at_sample_ns"] == 6 * SECOND
    assert lag[0]["available_lag_ns"] == 3_500_000_000
    assert lag[1]["available_lag_ns"] is None
    assert report["phases"]["raw"]["confirmed_switches"] == 1
    assert report["phases"]["raw"]["truth_switches"] == 2
    assert report["phases"]["raw"]["lag_confirmation_samples"] == 3
    assert report["phases"]["active"]["lag_confirmation_samples"] == 1
    assert report["phases"]["active"]["switch_detection"][0]["available_lag_ns"] == 1_500_000_000


def test_sequence_edges_unavailable_labels_and_complete_duration_denominators():
    alignment = training_alignment(rows([0, 1]), 2)
    observed = rows([0, 0, 1, 1, 1, 0, 0], active=[None, 0, 1, 1, 1, 0, 0])
    observed += rows([1, 1, 0], sequence="b", start=20)
    report = recovery_metrics(observed, alignment)
    raw, active = report["phases"]["raw"], report["phases"]["active"]
    assert sum(map(sum, raw["transition_counts"])) == 8  # n - independent sequences
    assert raw["truth_switches"] == 3  # no fake transition from end of a to start of b
    assert active["confusion"][0] == [4, 0, 1]
    assert active["available_observations"] == 9
    assert active["accuracy_available"] == 1
    assert active["accuracy_all_observations"] == 0.9
    duration = report["truth_durations"][1]
    assert duration["complete_episodes"] == 1 and duration["complete_steps"] == 3
    assert duration["mean_complete_seconds"] == 3
    assert duration["censored_episodes"] == 1 and duration["censored_steps"] == 2


@pytest.mark.parametrize("case", ("time", "available", "gap", "state", "alignment", "reused_sequence"))
def test_bad_recovery_metadata_fails_closed(case):
    observed = rows([0, 0, 1, 1])
    alignment = training_alignment(observed, 2)
    if case == "time":
        observed[1] = replace(observed[1], sample_ns=0)
    elif case == "available":
        observed[2] = replace(observed[2], available_at_ns=3 * SECOND)
        observed[1] = replace(observed[1], available_at_ns=5 * SECOND)
    elif case == "gap":
        observed[1] = replace(observed[1], sample_ns=observed[1].sample_ns + 1)
    elif case == "state":
        observed[1] = replace(observed[1], raw_state=5)
    elif case == "alignment":
        alignment["raw_to_truth"] = [1, 0]
    else:
        observed[1] = replace(observed[1], sequence_id="b")
    with pytest.raises(ValueError):
        recovery_metrics(observed, alignment)


@pytest.mark.parametrize(
    "change",
    (
        {"seed": True},
        {"days": 2},
        {"days": 31},
        {"seconds_per_day": 59},
        {"seconds_per_day": 3601},
        {"start_day": "2025-1-1"},
    ),
)
def test_generator_strict_caps_and_calendar(change):
    with pytest.raises(ValueError):
        SyntheticTapeConfig(**change)


def test_raw_tape_truth_separation_grid_continuity_and_independent_visible_state():
    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    all_rows = list(synthetic_rows(config))
    assert all_rows == list(synthetic_rows(config))
    raw = [r for kind, r in all_rows if kind == "raw"]
    assert [r["data"]["_capture"]["recvSeq"] for r in raw] == list(range(len(raw)))
    times = [r["data"]["_capture"]["recvMonotonicNs"] for r in raw]
    assert times == sorted(times)
    assert all("state" not in r["data"] and "truth" not in r["data"] for r in raw)
    # Independent integer book reducer reads raw levels, never the feature code.
    bids, asks, previous_u = {}, {}, None
    raw_index = 0
    for kind, truth in all_rows:
        if kind != "truth":
            continue
        while raw_index < len(raw) and times[raw_index] <= truth["sample_ns"]:
            rec = raw[raw_index]
            if rec["type"] == "snapshot":
                bids = {round(float(p) * 10): round(float(q) * 1000) for p, q in rec["data"]["bids"]}
                asks = {round(float(p) * 10): round(float(q) * 1000) for p, q in rec["data"]["asks"]}
                previous_u = rec["data"]["lastUpdateId"]
            if rec["type"] == "depthUpdate":
                assert rec["data"]["pu"] == previous_u
                previous_u = rec["data"]["u"]
                for book, side in ((bids, "b"), (asks, "a")):
                    for p, q in rec["data"][side]:
                        tick, lots = round(float(p) * 10), round(float(q) * 1000)
                        if lots:
                            book[tick] = lots
                        else:
                            book.pop(tick, None)
            raw_index += 1
        assert max(bids) < min(asks)
        spread = min(asks) - max(bids)
        depth = sum(bids.values()) + sum(asks.values())
        assert (2 <= spread <= 4 and depth > 2000) if truth["state"] == 0 else (12 <= spread <= 18 and depth < 1000)


def test_generator_no_clobber_and_identical_parent_hashes(tmp_path):
    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    left = generate_synthetic_tape(tmp_path / "left", config)
    right = generate_synthetic_tape(tmp_path / "right", config)
    assert left == right
    old = (tmp_path / "left/market.ndjson").read_bytes()
    with pytest.raises(FileExistsError):
        generate_synthetic_tape(tmp_path / "left", config)
    assert (tmp_path / "left/market.ndjson").read_bytes() == old
    assert len(left["truth"]["day_files"]) == 3
    assert left["truth"]["records"] == 180
    assert left["claim_ready"] is False


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    pytest.importorskip("hmmlearn")
    root = tmp_path_factory.mktemp("raw_recovery") / "study"
    report = run_synthetic_recovery(
        root,
        cfg(),
        tape_config=SyntheticTapeConfig(seconds_per_day=180),
        fit_config=FitConfig(restarts=2, max_iterations=100),
    )
    assert report["status"] == "completed", report
    return root, report


def test_raw_replay_to_frozen_fit_and_independent_test_recovery(bundle):
    root, report = bundle
    model = load_model(root / "model.json")
    assert model.model_sha256 == report["model_sha256"]
    assert report["claim_ready"] is False
    assert report["split"]["calibration_days"] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert report["split"]["test_days"] == ["2025-01-05"]
    test = report["partitions"]["test"]
    assert test["observations"] == 170
    assert sum(sum(row) for row in test["phases"]["raw"]["confusion"]) == 170
    # Gross separated observable states can be recovered after TRAIN-only mapping;
    # no perfect ARI/K/hysteretic lag requirement or economic-benefit assertion.
    assert test["phases"]["raw"]["accuracy_all_observations"] > 0.8
    ledger = json.loads((root / "fit_report.json").read_text())
    assert len(ledger["attempts"]) == 8
    assert [c["k"] for c in ledger["candidates"]] == [2, 3, 4, 5]
    assert "DIAGNOSTIC ONLY" in format_recovery_report(report)
    assert model.provenance_json.find('"role":"test"') == -1


def test_test_features_and_truth_never_open_before_persisted_registry_freeze(tmp_path, monkeypatch):
    pytest.importorskip("hmmlearn")
    original = Path.open
    root = tmp_path / "study"
    reads = []

    def guarded(path, mode="r", *args, **kwargs):
        if path.name == "2025-01-03.jsonl" and "r" in mode:
            registered = json.loads((root / "registry.json").read_text())
            assert registered["frozen"] is True
            reads.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    report = run_synthetic_recovery(
        root,
        cfg(),
        tape_config=SyntheticTapeConfig(days=3, seconds_per_day=120),
        fit_config=FitConfig(state_counts=(2,), restarts=1, max_iterations=100),
    )
    assert report["status"] == "completed"
    assert any(path.parent.name == "truth" for path in reads)
    assert any(path.parent.name == "features" for path in reads)


def test_no_valid_fit_publishes_failure_ledger_without_opening_test(tmp_path, monkeypatch):
    import lob_sim.regime.recovery as module

    monkeypatch.setattr(
        module,
        "fit_candidates",
        lambda *args: FitResult(None, canonical_json({"report_sha256": "a" * 64, "attempts": [{"status": "failed"}]})),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("no valid fitted model must not open test predictions")

    monkeypatch.setattr(module, "predict_partition", forbidden)
    report = run_synthetic_recovery(
        tmp_path / "failed", cfg(), tape_config=SyntheticTapeConfig(days=3, seconds_per_day=60)
    )
    assert report["status"] == "no_valid_candidate" and report["partitions"] == {}
    assert report["model_sha256"] is None


def test_frozen_test_guard_and_tampered_truth_fail_closed(bundle, tmp_path):
    root, report = bundle
    split = dataset_split(root / "features")
    model = load_model(root / "model.json")
    with pytest.raises(ValueError, match="freeze ResearchRegistry"):
        predict_partition(root / "features", split, "test", model, root / "tape", report["tape"], ResearchRegistry())
    # Mutate only an external copied truth file, never shared module fixture.
    copied = tmp_path / "truth"
    copied.mkdir()
    for path in (root / "tape/truth").iterdir():
        (copied / path.name).write_bytes(path.read_bytes())
    path = copied / "2025-01-05.jsonl"
    data = path.read_text().replace('"state":0', '"state":1', 1)
    path.write_text(data)
    registry = ResearchRegistry()
    registry.freeze()
    with pytest.raises(ValueError, match="checksum mismatch"):
        predict_partition(root / "features", split, "test", model, tmp_path, report["tape"], registry)


def test_serialized_summary_tampering_rejected(bundle):
    _, report = bundle
    altered = dict(report, status="perfect")
    with pytest.raises(ValueError, match="checksum"):
        format_recovery_report(altered)


def test_future_test_corruption_preserves_training_inputs_and_alignment(bundle, tmp_path):
    root, original = bundle
    # The core prefix-causality tests already mutate future book events. Here
    # future TEST feature corruption cannot be seen by physical training readers.
    copied = tmp_path / "features"
    shutil.copytree(root / "features", copied)
    (copied / "2025-01-05.jsonl").write_bytes(b"not JSON; future test bytes deliberately corrupt\n")
    split = dataset_split(copied)
    train = read_partition(copied, split, "calibration", symbol="BTCUSDT")
    validation = read_partition(copied, split, "validation", symbol="BTCUSDT")
    model = load_model(root / "model.json")
    registry = ResearchRegistry()
    registry.freeze()
    training_predictions = predict_partition(
        copied, split, "calibration", model, root / "tape", original["tape"], registry
    )
    assert training_alignment(training_predictions, model.parameters.state_count) == original["alignment"]
    metadata = json.loads(model.provenance_json)
    assert train.provenance() == metadata["training"]
    assert validation.provenance() == metadata["validation"]
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_partition(copied, split, "test", symbol="BTCUSDT", registry=registry)
