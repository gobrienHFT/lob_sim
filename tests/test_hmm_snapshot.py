"""Decision snapshots retain the pre-optimization JSON ownership contract."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.types import SymbolSpec
from lob_sim.config import load_config
from lob_sim.record.envelope import ValidityState
from lob_sim.regime import observation
from lob_sim.regime.observation import RegimeObserver
from lob_sim.regime.settings import HMMSettings
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.observation import MarketObservation
from test_hmm_dataset import SECOND, tape
from test_hmm_observation import settings
from test_hmm_policy import configuration, policy_tape


def reference_snapshot(subject, logical_ns):
    """Independent stdlib JSON oracle for the original valid-snapshot path."""
    if type(logical_ns) is not int or logical_ns < 0:
        raise ValueError("invalid observation time")
    last = subject._latest
    context = subject._contexts.get(subject.settings.symbol)
    stale = context is not None and (
        context.sampler._last_book_ns is None
        or logical_ns - context.sampler._last_book_ns > subject.spec.stale_after_ns
    )
    if last is not None and last["available_at_ns"] <= logical_ns and last["status"] == "VALID" and not stale:
        return json.loads(json.dumps(last, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    status = subject._immediate_status
    if stale and status == "VALID":
        status = "STALE"
    elif last is not None and last["available_at_ns"] > logical_ns:
        status = "WARMING_UP"
    return {
        "model_sha256": subject.settings.model.model_sha256,
        "status": status,
        "posterior": None,
        "active_state": None,
        "raw_map_state": None,
        "confidence": None,
        "entropy": None,
        "normalized_entropy": None,
        "confident": False,
        "filter_reset_reason": subject._immediate_reason,
    }


def observe_book(subject, index, *, validity=None, epochs=(0, 0, 0)):
    now = index * SECOND
    subject.before_record(now)
    subject.observe(
        MarketObservation(
            "BTCUSDT",
            SymbolSpec("BTCUSDT", Decimal("0.1"), Decimal("0.001")),
            now,
            index,
            index,
            now,
            True,
            validity or ValidityState(True, True, True, True),
            epochs,
            ((990, 10 + index),),
            ((1010, 10),),
            True,
        )
    )


def valid_observer():
    subject = RegimeObserver(settings())
    subject.bind_input("a" * 64)
    for index in range(8):
        observe_book(subject, index)
    assert subject._latest["status"] == "VALID"
    return subject


@pytest.mark.parametrize("offset", [-SECOND, -1, 0, 1, 5 * SECOND, 5 * SECOND + 1])
def test_snapshot_matches_json_oracle_at_availability_and_staleness_edges(offset):
    subject = valid_observer()
    now = subject._latest["available_at_ns"] + offset
    before = subject.checkpoint()
    expected = reference_snapshot(subject, now)
    actual = subject.snapshot(now)
    assert actual == expected
    assert json.dumps(actual) == json.dumps(expected)  # Canonical key order, too.
    assert subject.checkpoint() == before


def test_snapshot_consumers_have_no_shared_mutable_handles():
    subject = valid_observer()
    now = subject._latest["available_at_ns"]
    expected = reference_snapshot(subject, now)
    before = subject.checkpoint()
    first, second = subject.snapshot(now), subject.snapshot(now)
    for key, value in first.items():
        if isinstance(value, (dict, list)):
            assert value is not second[key]
            value.clear()
    first.clear()
    assert second == subject.snapshot(now) == expected
    assert subject.checkpoint() == before


@pytest.mark.parametrize(
    "validity,epochs",
    [
        (ValidityState(False, True, True, True), (0, 0, 0)),
        (ValidityState(True, False, True, True), (0, 0, 0)),
        (ValidityState(True, True, False, True), (0, 0, 0)),
        (ValidityState(True, True, True, False), (0, 0, 0)),
        (ValidityState(True, True, True, True), (1, 0, 0)),
    ],
)
def test_cached_valid_snapshot_cannot_survive_fault_or_epoch_boundary(validity, epochs):
    subject = valid_observer()
    assert subject.snapshot(7 * SECOND)["posterior"] is not None
    observe_book(subject, 8, validity=validity, epochs=epochs)
    assert subject.snapshot(8 * SECOND) == reference_snapshot(subject, 8 * SECOND)
    assert subject.snapshot(8 * SECOND)["posterior"] is None
    assert subject._snapshot_template is None
    for index in range(9, 13):
        observe_book(subject, index, epochs=epochs)
        assert subject.snapshot(index * SECOND) == reference_snapshot(subject, index * SECOND)
    assert subject.snapshot(12 * SECOND)["posterior"] is not None


@pytest.mark.parametrize("logical_ns", [True, -1, 1.0, None])
def test_snapshot_time_rejection_is_atomic(logical_ns):
    subject = valid_observer()
    subject.snapshot(7 * SECOND)
    before = subject.checkpoint()
    with pytest.raises(ValueError):
        subject.snapshot(logical_ns)
    assert subject.checkpoint() == before


@pytest.mark.parametrize("mode", ["observe", "policy"])
@pytest.mark.parametrize("fault", [None, "public", "market", "control"])
def test_full_replay_matches_original_json_snapshots_event_by_event(tmp_path, monkeypatch, mode, fault):
    path = (
        policy_tape(tmp_path / "input.ndjson")
        if fault is None
        else tape(
            tmp_path / "input.ndjson",
            control=("overflow", "control", "*") if fault == "control" else ("disconnect", fault, "BTCUSDT"),
            same_time_trades=True,
        )
    )
    config = configuration(mm_requote_ms=500)
    if mode == "observe":
        config = replace(
            config, hmm=replace(config.hmm, mode="observe", policy=None), mm_strategy_profile="research_mm"
        )
    current = SimulationEngine(config)
    current.run(path)
    with monkeypatch.context() as patch:
        patch.setattr(RegimeObserver, "snapshot", reference_snapshot)
        old = SimulationEngine(config)
        old.run(path)
    assert current.event_trace == old.event_trace
    assert current.state_sha256() == old.state_sha256()
    assert current._checkpoint_mutable_state() == old._checkpoint_mutable_state()
    assert current.metrics.fill_audit_sha256 == old.metrics.fill_audit_sha256
    assert current.metrics.markout_audit_sha256 == old.metrics.markout_audit_sha256
    assert current.latency_model.sampler_state() == old.latency_model.sampler_state()
    for name in ("regime", "hmm_risk", "hmm_execution"):
        assert getattr(current, name).checkpoint() == getattr(old, name).checkpoint()
        assert getattr(current, name).summary() == getattr(old, name).summary()
    if fault is None:
        assert current.metrics.fill_count > 0


@pytest.mark.parametrize("cut", [10, 14])
@pytest.mark.parametrize("mode", ["observe", "policy"])
def test_primed_snapshot_checkpoint_resume_matches_original_oracle(tmp_path, monkeypatch, cut, mode):
    path = policy_tape(tmp_path / "input.ndjson")
    config = configuration(mm_requote_ms=500)
    if mode == "observe":
        config = replace(
            config, hmm=replace(config.hmm, mode="observe", policy=None), mm_strategy_profile="research_mm"
        )
    full = SimulationEngine(config)
    full.run(path)
    checkpoint = tmp_path / "state.json"
    paused = SimulationEngine(config)
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    resumed = SimulationEngine(config)
    resumed.run(path, resume_from=checkpoint)
    with monkeypatch.context() as patch:
        patch.setattr(RegimeObserver, "snapshot", reference_snapshot)
        old = SimulationEngine(config)
        old.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == full.state_sha256() == old.state_sha256()
    assert resumed.event_trace == full.event_trace == old.event_trace
    assert resumed.regime.checkpoint() == full.regime.checkpoint() == old.regime.checkpoint()


def test_restoring_older_checkpoint_cannot_reuse_newer_snapshot():
    subject = valid_observer()
    checkpoint = copy.deepcopy(subject.checkpoint())
    old_snapshot = subject.snapshot(7 * SECOND)
    observe_book(subject, 8)
    assert subject.snapshot(8 * SECOND) != old_snapshot
    subject.restore(checkpoint)
    assert subject.snapshot(7 * SECOND) == old_snapshot
    assert subject.checkpoint() == checkpoint


def test_repeated_reads_normalize_once_and_new_sample_resets_template(monkeypatch):
    subject = valid_observer()
    calls = []
    original = observation.canonical_json

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(observation, "canonical_json", counted)
    expected = reference_snapshot(subject, 7 * SECOND)
    for _ in range(100):
        assert subject.snapshot(7 * SECOND) == expected
    assert len(calls) == 1
    template = subject._snapshot_template
    observe_book(subject, 8)
    assert subject._snapshot_template is None
    calls.clear()
    for _ in range(100):
        assert subject.snapshot(8 * SECOND) == reference_snapshot(subject, 8 * SECOND)
    assert len(calls) == 1
    assert subject._snapshot_template is not template
    assert "snapshot_template" not in subject.checkpoint()


def test_future_sample_does_not_populate_cache_and_stale_read_never_exposes_it():
    subject = valid_observer()
    now = subject._latest["available_at_ns"]
    assert subject._snapshot_template is None
    assert subject.snapshot(now - 1)["posterior"] is None
    assert subject._snapshot_template is None
    assert subject.snapshot(now)["posterior"] is not None
    assert subject._snapshot_template is not None
    assert subject.snapshot(now + subject.spec.stale_after_ns + 1)["posterior"] is None
    # A later query must not irreversibly relabel the current signal.
    assert subject.snapshot(now) == reference_snapshot(subject, now)


def test_rejected_checkpoint_restore_preserves_primed_snapshot_and_state():
    subject = valid_observer()
    expected = subject.snapshot(7 * SECOND)
    template = subject._snapshot_template
    before = subject.checkpoint()
    corrupt = copy.deepcopy(before)
    corrupt["latest"]["confidence"] = 0.5
    with pytest.raises(ValueError, match="hysteresis mismatch"):
        subject.restore(corrupt)
    assert subject._snapshot_template is template
    assert subject.snapshot(7 * SECOND) == expected
    assert subject.checkpoint() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"unicode": "\u03b1\u4e2d", "empty": [], "zero": -0.0, "huge": 10**80},
        {"mixed": (None, True, 1, 1.0, "1"), "nested": [{"values": ((1, 2), [])}]},
        {1: [{"nested": [None, {"deep": [False, -3, "x"]}]}]},
    ],
)
def test_cached_json_normalization_and_recursive_ownership_match_stdlib(payload):
    subject = valid_observer()
    # Deliberate structural oracle, including future nested schema shapes.
    # This is not an accepted checkpoint or a new public signal field.
    subject._latest["structural_test"] = payload
    expected = reference_snapshot(subject, 7 * SECOND)
    first = subject.snapshot(7 * SECOND)
    assert json.dumps(first) == json.dumps(expected)
    first["structural_test"].clear()
    assert subject.snapshot(7 * SECOND) == expected
    assert subject._latest["structural_test"] == payload


@pytest.mark.parametrize("mode", ["observe", "policy"])
def test_original_public_capture_full_engine_matches_json_oracle(monkeypatch, mode):
    root = Path(__file__).resolve().parents[1]
    directory = root / "docs/benchmark_inputs/hmm_public_btcusdt_20261005"
    path = directory / "capture_1791203092_f912172cf0594437a728d2eb9160d85e.manifest.json"
    config = load_config(root / "docs/benchmark_results/hmm_active_public_reference.env", inherit_environment=False)
    config = replace(
        config,
        hmm=HMMSettings.load(directory / "synthetic_frozen_model.json", symbol="BTCUSDT", mode=mode),
        mm_strategy_profile="hmm_regime_mm" if mode == "policy" else "research_mm",
    )
    current = SimulationEngine(config)
    current.run(path)
    with monkeypatch.context() as patch:
        patch.setattr(RegimeObserver, "snapshot", reference_snapshot)
        old = SimulationEngine(config)
        old.run(path)
    assert current.regime.summary()["status_counts"]["VALID"] == 112
    assert current.metrics.fill_count > 0
    assert current.event_trace == old.event_trace
    assert current.state_sha256() == old.state_sha256()
    assert current._checkpoint_mutable_state() == old._checkpoint_mutable_state()
    for name in ("regime", "hmm_risk", "hmm_execution"):
        assert getattr(current, name).checkpoint() == getattr(old, name).checkpoint()
        assert getattr(current, name).summary() == getattr(old, name).summary()
    assert current.metrics.fill_audit_sha256 == old.metrics.fill_audit_sha256
    assert current.metrics.markout_audit_sha256 == old.metrics.markout_audit_sha256
    assert current.latency_model.sampler_state() == old.latency_model.sampler_state()
