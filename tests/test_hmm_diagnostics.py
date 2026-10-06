"""Independent offline reductions; these are descriptive mechanics, not economics."""

from __future__ import annotations

import copy
import math
import random
import subprocess
import sys
from itertools import groupby
from dataclasses import replace

import pytest

from lob_sim.regime.diagnostics import BASES, MEASURES, RegimeDiagnostics, inspect_run
from lob_sim.regime.features import FEATURE_NAMES
from lob_sim.regime.observation import verify_trace
from lob_sim.regime.validation import canonical_json, strict_json
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from test_hmm_dataset import SECOND, cfg, tape
from test_hmm_observation import run, settings


def sample(index, state=0, active=0, *, reset=None, value=None):
    values = [float(index if value is None else value)] * len(FEATURE_NAMES)
    return {
        "event_type": "sample",
        "sample_ns": index * SECOND,
        "available_at_ns": (index + 1) * SECOND,
        "status": "VALID",
        "raw_map_state": state,
        "active_state": active,
        "features": values,
        "confidence": 0.9,
        "normalized_entropy": 0.3,
        "filter_reset_reason": reset,
    }


def reduce(rows):
    result = RegimeDiagnostics(2, SECOND)
    for row in rows:
        result.observe(row)
    return result


def test_complete_episodes_require_both_observed_edges_and_summary_does_not_close_tail():
    rows = [sample(i, state=s, active=s) for i, s in enumerate([0, 0, 1, 1, 1, 0, 0, 1])]
    reducer = reduce(rows)
    before = reducer.checkpoint()
    report = reducer.summary(len(rows))
    for basis in BASES:
        zero, one = (report["conditioned"][basis][f"STATE_{s}"] for s in (0, 1))
        assert zero["episodes"] == one["episodes"] == 2
        assert zero["complete_episodes"] == one["complete_episodes"] == 1
        assert zero["censored_episodes"] == one["censored_episodes"] == 1
        assert zero["mean_complete_episode_seconds"] == 2
        assert one["mean_complete_episode_seconds"] == 3
        assert zero["censored_duration_ns"] == 2 * SECOND
        assert one["censored_duration_ns"] == SECOND
        assert one["open_tail_samples"] == 1
        assert report["sample_transitions"][basis]["STATE_0"]["STATE_1"] == 2
        assert report["sample_transitions"][basis]["STATE_1"]["STATE_0"] == 1
    assert reducer.checkpoint() == before
    assert report["occupancy_basis"] == "sample_grid;not_joint_valid_wall_clock_coverage"


@pytest.mark.parametrize("interruption", ["invalidation", "invalid_sample", "reset", "grid_gap"])
def test_discontinuities_censor_and_do_not_create_transitions(interruption):
    rows = [sample(0), sample(1)]
    if interruption == "invalidation":
        rows.append({"event_type": "invalidation"})
    elif interruption == "invalid_sample":
        rows.append({**sample(2), "status": "INVALID_BOOK", "features": None})
    next_index = 5 if interruption == "grid_gap" else 3 if interruption == "invalid_sample" else 2
    rows += [
        sample(next_index, 1, 1, reset="epoch_changed" if interruption == "reset" else None),
        sample(next_index + 1, 1, 1),
    ]
    report = reduce(rows).summary(sum(row["event_type"] == "sample" for row in rows))
    assert report["valid_samples"] == 4
    for basis in BASES:
        assert report["sample_transitions"][basis]["STATE_0"]["STATE_1"] == 0
        assert report["conditioned"][basis]["STATE_0"]["censored_episodes"] == 1
        assert report["conditioned"][basis]["STATE_1"]["censored_episodes"] == 1
        assert sum(cell["complete_episodes"] for cell in report["conditioned"][basis].values()) == 0


def test_active_confirmation_is_not_raw_map_occupancy():
    reducer = reduce([sample(0, 1, None), sample(1, 1, None), sample(2, 1, 1), sample(3, 0, 1)])
    report = reducer.summary(5)  # Include one warming grid sample in occupancy denominator.
    assert report["conditioned"]["raw_map"]["STATE_1"]["samples"] == 3
    assert report["conditioned"]["active"]["STATE_1"]["samples"] == 2
    unknown = report["conditioned"]["active"]["UNCONFIRMED"]
    assert unknown["samples"] == 2
    assert unknown["fraction_of_valid_samples"] == 0.5
    assert unknown["fraction_of_all_samples"] == 0.4
    assert report["sample_grid_valid_fraction"] == 0.8


def test_large_stream_moments_match_independent_batch_statistics_with_fixed_cardinality():
    rng = random.Random(74)
    rows = [sample(i, rng.randrange(2), rng.choice([0, 1, None]), value=rng.gauss(100_000, 0.3)) for i in range(8000)]
    reducer = reduce(rows)
    report = reducer.summary(len(rows))
    for basis in BASES:
        for label, cell in report["conditioned"][basis].items():
            selected = [
                row
                for row in rows
                if (
                    f"STATE_{row['raw_map_state']}"
                    if basis == "raw_map"
                    else f"STATE_{row['active_state']}"
                    if row["active_state"] is not None
                    else "UNCONFIRMED"
                )
                == label
            ]
            assert cell["samples"] == len(selected)
            for i, name in enumerate(MEASURES):
                values = [row["features"][i] if i < len(FEATURE_NAMES) else row[name] for row in selected]
                expected_mean = math.fsum(values) / len(values) if values else None
                expected_var = math.fsum((v - expected_mean) ** 2 for v in values) / len(values) if values else None
                assert cell["measures"][name]["mean"] == pytest.approx(expected_mean, abs=1e-8)
                assert cell["measures"][name]["population_variance"] == pytest.approx(expected_var, abs=1e-8)
            assert cell["complete_episodes"] + cell["censored_episodes"] == cell["episodes"]
            assert cell["complete_duration_ns"] + cell["censored_duration_ns"] == cell["sample_grid_duration_ns"]
    state = reducer.checkpoint()
    assert len(state["cells"]) == 2
    assert all(len(cells) == 3 for cells in state["cells"].values())
    assert report["retained_episodes"] == 2
    assert len(canonical_json(state)) < 20_000
    assert reducer.validated_copy(strict_json(canonical_json(state))).checkpoint() == state


@pytest.mark.parametrize("cut", [0, 1, 2, 3, 7, 11])
def test_diagnostic_checkpoint_cut_preserves_moments_episodes_and_transitions(cut):
    rows = [sample(i, i // 3 % 2, None if i < 2 else i // 4 % 2) for i in range(12)]
    rows.insert(7, {"event_type": "invalidation"})
    original = reduce(rows)
    prefix = reduce(rows[:cut])
    continued = prefix.validated_copy(strict_json(canonical_json(prefix.checkpoint())))
    for row in rows[cut:]:
        continued.observe(row)
    assert continued.checkpoint() == original.checkpoint()
    assert continued.summary(12) == original.summary(12)


@pytest.mark.parametrize(
    "change",
    [
        "count",
        "negative_variance",
        "nonfinite",
        "dimension",
        "episodes",
        "duration",
        "open_time",
        "open_label",
        "boolean",
        "transition",
        "empty",
        "raw_unknown",
    ],
)
def test_corrupt_checkpoint_is_rejected_without_mutating_reducer(change):
    reducer = reduce([sample(i, i // 3 % 2, i // 3 % 2) for i in range(7)])
    before = reducer.checkpoint()
    bad = copy.deepcopy(before)
    cell = bad["cells"]["raw_map"]["STATE_0"]
    if change == "count":
        cell["moments"]["spread_bps"]["count"] += 1
    elif change == "negative_variance":
        cell["moments"]["spread_bps"]["m2"] = -1.0
    elif change == "nonfinite":
        cell["moments"]["spread_bps"]["mean"] = float("nan")
    elif change == "dimension":
        del cell["moments"]["spread_bps"]
    elif change == "episodes":
        cell["episodes"] += 1
    elif change == "duration":
        cell["censored_duration_ns"] += 1
    elif change == "open_time":
        bad["open"]["raw_map"]["start_sample_ns"] += SECOND
    elif change == "open_label":
        bad["open"]["raw_map"]["label"] = "STATE_4"
    elif change == "boolean":
        bad["valid_samples"] = True
    elif change == "transition":
        bad["transitions"]["raw_map"]["STATE_1"]["STATE_0"] = -1
    elif change == "empty":
        bad["cells"]["raw_map"]["UNCONFIRMED"]["moments"]["spread_bps"]["mean"] = 1.0
    else:
        bad["cells"]["raw_map"]["UNCONFIRMED"] = copy.deepcopy(cell)
    with pytest.raises(ValueError):
        reducer.validated_copy(bad)
    assert reducer.checkpoint() == before


@pytest.mark.parametrize(
    "bad",
    [
        {"sample_ns": True},
        {"sample_ns": 1},
        {"available_at_ns": 0},
        {"features": [0] * 11},
        {"features": [float("inf")] * 12},
        {"raw_map_state": 2},
        {"active_state": True},
        {"confidence": 1.1},
        {"normalized_entropy": -0.1},
    ],
)
def test_invalid_sample_refused_before_statistical_mutation(bad):
    reducer = reduce([sample(0)])
    before = reducer.checkpoint()
    with pytest.raises(ValueError):
        reducer.observe({**sample(1), **bad})
    assert reducer.checkpoint() == before


def test_empty_state_is_null_not_zero_and_summary_is_independent():
    reducer = RegimeDiagnostics(2, SECOND)
    report = reducer.summary(0)
    assert report["sample_grid_valid_fraction"] is None
    assert report["conditioned"]["raw_map"]["STATE_0"]["measures"]["spread_bps"]["mean"] is None
    report["conditioned"]["active"]["STATE_0"]["samples"] = 99
    assert reducer.summary(0)["conditioned"]["active"]["STATE_0"]["samples"] == 0


def test_native_replay_summary_matches_independent_trace_reduction(tmp_path):
    engine, rows = run(tape(tmp_path / "input.ndjson", same_time_trades=True))
    report = engine.regime.summary()["state_diagnostics"]
    valid = [row for row in rows if row["event_type"] == "sample" and row["status"] == "VALID"]
    assert report["valid_samples"] == len(valid)
    assert report["sampling_interval_ns"] == engine.regime.spec.interval_ns
    for basis in BASES:
        key = "raw_map_state" if basis == "raw_map" else "active_state"
        for state in (0, 1, None):
            group = [row for row in valid if row[key] == state]
            label = f"STATE_{state}" if state is not None else "UNCONFIRMED"
            cell = report["conditioned"][basis][label]
            assert cell["samples"] == len(group)
            for i, name in enumerate(FEATURE_NAMES):
                values = [row["features"][i] for row in group]
                expected = math.fsum(values) / len(values) if values else None
                assert cell["measures"][name]["mean"] == pytest.approx(expected)


def test_serialized_audit_verifies_statistics_not_only_hash_and_count(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    files, summary = run_bounded_simulation(replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), path)
    verify_trace(files["regime_trace"], summary["hmm"])
    corrupted = copy.deepcopy(summary["hmm"])
    corrupted["state_diagnostics"]["conditioned"]["raw_map"]["STATE_1"]["measures"]["spread_bps"]["mean"] = -55
    with pytest.raises(ValueError, match="state diagnostics"):
        verify_trace(files["regime_trace"], corrupted)


def test_engine_rejects_diagnostic_sidecar_before_mutating_core(tmp_path):
    engine, _ = run(tape(tmp_path / "input.ndjson"))
    checkpoint = engine.regime.checkpoint()
    checkpoint["diagnostics"]["valid_samples"] += 1
    before = engine.state_sha256()
    with pytest.raises(ValueError):
        engine.regime.restore(checkpoint)
    assert engine.state_sha256() == before
    assert isinstance(engine, SimulationEngine)


def test_report_cli_verifies_audit_before_printing_market_state_statistics(tmp_path):
    files, _ = run_bounded_simulation(
        replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), tape(tmp_path / "input.ndjson")
    )
    command = [sys.executable, "-m", "lob_sim.cli", "regime-report", "--run-dir", str(files["summary"].parent)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Verified serialized regime audit" in result.stdout
    assert "Raw MAP labels" in result.stdout and "Active hysteretic labels" in result.stdout
    assert "NOT joint-valid wall-clock coverage" in result.stdout
    assert "complete/censored episodes" in result.stdout
    assert "spread_bps: mean=" in result.stdout
    # A finalized name and a self-declared summary cannot hide a corrupt tape.
    text = files["regime_trace"].read_text()
    files["regime_trace"].write_text(text.replace("STATE_1", "STATE_0"))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "cannot inspect regime run" in result.stderr
    assert "Verified" not in result.stdout


@pytest.mark.parametrize("fault", ["incomplete", "partial", "model", "interval", "summary_statistics", "no_hmm"])
def test_report_rejects_incomplete_or_inconsistent_bundle(tmp_path, fault):
    files, summary = run_bounded_simulation(
        replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), tape(tmp_path / "input.ndjson")
    )
    directory = files["summary"].parent
    if fault == "incomplete":
        (directory / "_INCOMPLETE.json").touch()
    elif fault == "partial":
        (directory / "regime_trace.csv.partial").touch()
    else:
        if fault == "model":
            summary["hmm"]["config"]["model_sha256"] = "a" * 64
        elif fault == "interval":
            summary["hmm"]["state_diagnostics"]["sampling_interval_ns"] *= 2
        elif fault == "summary_statistics":
            summary["hmm"]["state_diagnostics"]["conditioned"]["raw_map"]["STATE_1"]["samples"] += 1
        else:
            del summary["hmm"]
        files["summary"].write_text(canonical_json(summary))
    with pytest.raises(ValueError):
        inspect_run(directory)


@pytest.mark.parametrize("states", [2, 3, 4, 5])
@pytest.mark.parametrize("seed", range(10))
def test_generated_episode_census_matches_independent_segmented_run_length_oracle(states, seed):
    rng = random.Random(seed)
    reducer = RegimeDiagnostics(states, SECOND)
    segments, current = [], []
    sample_count, index = 0, 0
    for _ in range(350):
        interrupted = rng.random() < 0.08
        if interrupted and current:
            segments.append(current)
            current = []
        if interrupted:
            reducer.observe({"event_type": "invalidation"})
            index += 2  # A gap is not an invented continuation of an old label.
        row = sample(index, rng.randrange(states), rng.choice([None, *range(states)]))
        reducer.observe(row)
        sample_count += 1
        current.append(row)
        index += 1
    if current:
        segments.append(current)
    report = reducer.summary(sample_count)
    for basis in BASES:
        expected = {
            label: {
                "samples": 0,
                "episodes": 0,
                "complete_episodes": 0,
                "censored_episodes": 0,
                "complete_duration_ns": 0,
                "censored_duration_ns": 0,
            }
            for label in reducer.labels
        }

        def label(row):
            state = row["raw_map_state" if basis == "raw_map" else "active_state"]
            return f"STATE_{state}" if state is not None else "UNCONFIRMED"

        for segment in segments:
            runs = [(name, list(group)) for name, group in groupby(segment, key=label)]
            for i, (name, group) in enumerate(runs):
                cell = expected[name]
                cell["samples"] += len(group)
                cell["episodes"] += 1
                kind = "censored" if i == 0 or i == len(runs) - 1 else "complete"
                cell[kind + "_episodes"] += 1
                cell[kind + "_duration_ns"] += len(group) * SECOND
        for name, values in expected.items():
            assert {key: report["conditioned"][basis][name][key] for key in values} == values
    assert reducer.validated_copy(reducer.checkpoint()).summary(sample_count) == report


@pytest.mark.parametrize("counter", ["sample_count", "raw_map_sample_counts", "active_state_sample_transitions"])
def test_trace_verification_refuses_corrupt_legacy_summary_counters(tmp_path, counter):
    files, summary = run_bounded_simulation(
        replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), tape(tmp_path / "input.ndjson")
    )
    hmm = copy.deepcopy(summary["hmm"])
    if counter == "sample_count":
        hmm[counter] += 1
    elif counter == "raw_map_sample_counts":
        hmm[counter][0] += 1
    else:
        hmm[counter][0][0] += 1
    with pytest.raises(ValueError, match="diagnostics|counters"):
        verify_trace(files["regime_trace"], hmm)
