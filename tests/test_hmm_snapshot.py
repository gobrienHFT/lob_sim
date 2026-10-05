"""Decision snapshots retain the pre-optimization JSON ownership contract."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from lob_sim.book.types import SymbolSpec
from lob_sim.record.envelope import ValidityState
from lob_sim.regime.observation import RegimeObserver
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
