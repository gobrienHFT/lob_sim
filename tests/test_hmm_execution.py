from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from decimal import Decimal

import pytest

from lob_sim.regime.execution import RegimeExecutionAudit, capture_stage, verify_execution_trace
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.checkpoint import decode, encode
from lob_sim.oracle import Checkpoint, read_checkpoint, write_checkpoint
from test_hmm_dataset import SECOND, cfg, tape
from test_hmm_observation import Recorder, settings, without_diagnostics


def stage(state, time, *, status="VALID"):
    return capture_stage(
        {
            "model_sha256": "a" * 64,
            "status": status,
            "raw_map_state": state,
            "active_state": state,
            "posterior": [0.9, 0.1] if state == 0 else [0.1, 0.9],
            "confidence": 0.9,
            "normalized_entropy": -(0.9 * math.log(0.9) + 0.1 * math.log(0.1)) / math.log(2),
            "sample_ns": time - 1,
            "available_at_ns": time,
            "receive_seq": time,
            "epochs": [1, 2, 3],
        },
        time,
    )


def fill_row(*, qty="2", spread="3", fee="0.1"):
    return {
        "symbol": "BTCUSDT",
        "side": "bid",
        "order_id": "order-1",
        "qty": qty,
        "qty_lots": int(qty),
        "spread_capture_value": spread,
        "fee": fee,
        "maker": True,
        "fill_source": "agg_trade",
        "scenario_id": "unit_scenario",
        "evidence_ids": ["trade-1"],
        "validity": {"execution_valid": True},
        "latency_draws_ms": {"new_order": 5.0},
        "order_state_at_fill": "pending_cancel",
        "time_in_book_ms": 12.0,
        "ts_local": 1.0,
    }


def audit(sink=None):
    return RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100, 1000), 4, sink)


def execution_tape(path):
    tape(path, same_time_trades=True)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if row["type"] == "snapshot":
            row["data"]["bids"][0][0] = "99.9"
            row["data"]["asks"][0][0] = "100.1"
        elif row["type"] == "depthUpdate":
            row["data"]["b"][0][0] = "99.9"
            row["data"]["a"][0][0] = "100.1"
        elif row["type"] == "aggTrade":
            row["data"]["p"] = "99.9" if row["data"]["m"] else "100.1"
            row["data"]["q"] = "0.1"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_three_information_sets_frozen_and_transition_metrics_independently_correct():
    sink = Recorder()
    subject = audit(sink)
    decision, arrival, pre_fill = stage(0, 1), stage(1, 2), stage(1, 3)
    subject.accept_order("order-1", decision, arrival)
    frozen = subject.at_fill("order-1", pre_fill)
    frozen = subject.on_fill(7, fill_row(), frozen)
    # Later label or consumer mutation cannot relabel either fill or markout.
    decision["active_state"] = 1
    sink.rows[0]["decision"]["active_state"] = 1
    entry = {
        "hmm_attribution": frozen,
        "fill_source": "agg_trade",
        "symbol": "BTCUSDT",
        "side": "bid",
        "order_id": "order-1",
        "qty": "2",
        "qty_lots": 2,
        "ts_local": 1.0,
        "deadline_ts": 1.1,
    }
    subject.on_markout(entry, 100, markout=Decimal("-2"), observed_ts=1.15)
    subject.on_markout(entry, 1000, markout=None, observed_ts=1.2, invalid_reason="gap")
    summary = subject.summary()
    assert summary["fill_transition_counts"]["STATE_0"]["STATE_1"] == 1
    assert summary["conditioned"]["decision"]["STATE_0"]["fill_count"] == 1
    assert summary["conditioned"]["arrival"]["STATE_1"]["fill_count"] == 1
    cell = summary["conditioned"]["pre_fill"]["STATE_1"]
    assert cell["qty"] == "2" and cell["fee"] == "0.1"
    assert cell["spread_capture_value"] == "3"
    assert cell["pending_cancel_fill_count"] == 1
    assert cell["markouts"]["100"]["mean_signed_markout"] == -2.0
    assert cell["markouts"]["100"]["adverse_rate"] == 1.0
    assert cell["markouts"]["100"]["mean_actual_lag_seconds"] == pytest.approx(0.15)
    assert cell["markouts"]["1000"]["invalidated_samples"] == 1
    assert cell["markouts"]["1000"]["mean_signed_markout"] is None
    assert sink.rows[1]["decision"]["active_state"] == 0
    assert sink.rows[1]["fill_id"] == 7


def test_repeated_partial_fill_keys_have_unique_ordinals_and_weighted_markouts():
    subject = audit()
    subject.accept_order("order-1", stage(0, 1), stage(0, 2))
    for ordinal, qty, value in ((1, "2", "-2"), (2, "1", "4")):
        frozen = subject.on_fill(ordinal, fill_row(qty=qty), subject.at_fill("order-1", stage(1, 3)))
        subject.on_markout(
            {
                "hmm_attribution": frozen,
                "fill_source": "agg_trade",
                "qty": qty,
                "qty_lots": int(qty),
                "symbol": "BTCUSDT",
                "side": "bid",
                "order_id": "order-1",
                "ts_local": 1.0,
                "deadline_ts": 1.1,
            },
            100,
            markout=Decimal(value),
            observed_ts=1.1,
        )
    cell = subject.summary()["conditioned"]["pre_fill"]["STATE_1"]
    assert cell["markouts"]["100"]["mean_signed_markout"] == 0.0
    assert cell["markouts"]["100"]["adverse_rate"] == 0.5
    assert cell["markouts"]["1000"]["unresolved_samples"] == 2
    assert cell["markouts"]["1000"]["coverage"] == 0.0
    with pytest.raises(ValueError, match="ordinal"):
        subject.on_fill(2, fill_row(), subject.at_fill("order-1", stage(1, 3)))


@pytest.mark.parametrize("invalid", ["STALE", "INVALID_BOOK", "INVALID_TRADE_STREAM", "WARMING_UP"])
def test_invalid_stage_cannot_retain_high_confidence_or_posterior(invalid):
    snapshot = stage(0, 5, status=invalid)
    assert snapshot["posterior"] is None and snapshot["confidence"] is None
    assert snapshot["active_state"] is None


def test_future_available_stage_rejected():
    future = stage(1, 10)
    with pytest.raises(ValueError, match="future"):
        capture_stage(future, 9)


def test_checkpoint_is_atomic_bounded_and_independent():
    subject = audit()
    subject.accept_order("order-1", stage(0, 1), stage(1, 2))
    subject.on_fill(1, fill_row(), subject.at_fill("order-1", stage(1, 3)))
    original = subject.checkpoint()
    clone = subject.validated_copy(original)
    assert clone.checkpoint() == original
    changed = copy.deepcopy(original)
    changed["orders"]["order-1"]["arrival"]["posterior"] = [0.5, 1.0]
    with pytest.raises(ValueError):
        subject.validated_copy(changed)
    assert subject.checkpoint() == original
    original["orders"].clear()
    assert len(subject.checkpoint()["orders"]) == 1
    for index in range(3):
        subject.accept_order(f"extra-{index}", stage(0, 1), stage(1, 2))
    with pytest.raises(ValueError, match="capacity"):
        subject.accept_order("overflow", stage(0, 1), stage(1, 2))
    subject.release_order("order-1")
    assert len(subject.checkpoint()["orders"]) == 3


@pytest.mark.parametrize("fill_mode", ["trade", "depth"])
def test_native_execution_audit_does_not_change_fills_or_accounting(tmp_path, fill_mode):
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(
        cfg(), sim_fill_model=fill_mode, mm_half_spread_bps=Decimal("0"), sim_markout_horizons_ms=(100, 1000, 30000)
    )
    base = SimulationEngine(config)
    base.run(path)
    sink = Recorder()
    observed = SimulationEngine(replace(config, hmm=settings()), regime_execution_sink=sink)
    observed.run(path)
    assert without_diagnostics(observed.event_trace) == base.event_trace
    assert observed.metrics.get_summary(observed._books, observed._specs) == base.metrics.get_summary(
        base._books, base._specs
    )
    fills = [row for row in sink.rows if row["event_type"] == "fill"]
    assert len(fills) == observed.metrics.fill_count
    if fill_mode == "trade":
        assert fills  # Consume visible queue plus the resting strategy quote.
    assert len({row["fill_id"] for row in fills}) == len(fills)
    for row in fills:
        for phase in ("decision", "arrival", "pre_fill"):
            value = row[phase]
            if value is not None:
                assert value["logical_ns"] <= row["logical_ns"]
                assert value["available_at_ns"] is None or value["available_at_ns"] <= value["logical_ns"]
    assert len(observed.hmm_execution.checkpoint()["orders"]) <= 2
    continuation = copy.deepcopy(observed._checkpoint_mutable_state())
    continuation["event_trace"] = without_diagnostics(continuation["event_trace"])
    for entry in [*continuation["metrics"]["_pending_markouts"], *continuation["metrics"]["_pending_markout_horizons"]]:
        entry.pop("hmm_attribution", None)
    for action in continuation["actions"]:
        action.payload.pop("hmm_decision", None)
        action.payload.pop("hmm_attributions", None)
        action.payload.pop("hmm_request_id", None)
    assert encode(continuation) == encode(base._checkpoint_mutable_state())


def test_execution_audit_transactional_bundle_and_serialized_hash(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    files, summary = run_bounded_simulation(replace(cfg(), record_dir=tmp_path / "runs", hmm=settings()), path)
    assert len(files) == 11
    assert not (files["manifest"].parent / "_INCOMPLETE.json").exists()
    verify_execution_trace(files["regime_execution"], summary["hmm_execution"])
    content = files["regime_execution"].read_text()
    files["regime_execution"].write_text(content.replace("STATE_", "CORRUPT_"))
    # A tampering test must actually change bytes even when all fills are warm-up.
    if files["regime_execution"].read_text() == content:
        files["regime_execution"].write_text(content.replace("BTCUSDT", "ETHUSDT"))
    with pytest.raises(ValueError):
        verify_execution_trace(files["regime_execution"], summary["hmm_execution"])


@pytest.mark.parametrize("cut", [7, 10, 14, 16])
def test_execution_checkpoint_resume_all_horizons_and_immutable_labels(tmp_path, cut):
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(cfg(), hmm=settings(), sim_markout_horizons_ms=(100, 1000, 5000, 30000))
    full = SimulationEngine(config)
    full.run(path)
    checkpoint = tmp_path / "checkpoint.json"
    SimulationEngine(config).run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    resumed = SimulationEngine(config)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == full.state_sha256()
    assert resumed.hmm_execution.checkpoint() == full.hmm_execution.checkpoint()


def test_native_decision_arrival_and_pending_cancel_fill_can_have_different_states(tmp_path):
    """Controlled estimator oracle isolates the scheduler/attribution contract."""
    path = execution_tape(tmp_path / "input.ndjson")
    sink = Recorder()
    config = replace(cfg(), hmm=settings(), sim_cancel_latency_ms=8000)
    engine = SimulationEngine(config, regime_execution_sink=sink)

    def controlled_snapshot(logical_ns):
        # The snapshot bridge becomes valid at 2 s. The existing scheduler's
        # modeled 10 ms transit crosses this controlled 2.005 s transition.
        state = 0 if logical_ns <= 2_005_000_000 else 1
        snapshot = stage(state, logical_ns)
        snapshot["model_sha256"] = config.hmm.model.model_sha256
        snapshot["entropy"] = 0.3
        snapshot["epochs"] = [engine._syncers["BTCUSDT"].epoch, 0, 0]
        return snapshot

    engine.regime.snapshot = controlled_snapshot
    # This artificial attribution oracle intentionally emits VALID before a
    # real feature/book context exists. Time-weighted risk has its own native
    # estimator tests and must not infer staleness from this substituted oracle.
    engine.hmm_risk = None
    engine.run(path)
    fills = [row for row in sink.rows if row["event_type"] == "fill"]
    assert fills
    assert all(row["decision"]["active_state"] == 0 for row in fills), [
        (row["decision"]["logical_ns"], row["arrival"]["logical_ns"]) for row in fills
    ]
    assert all(row["arrival"]["active_state"] == 1 for row in fills)
    assert all(row["pre_fill"]["active_state"] == 1 for row in fills)
    assert all(row["order_state_at_fill"] == "pending_cancel" for row in fills)
    assert engine.hmm_execution.summary()["fill_transition_counts"]["STATE_0"]["STATE_1"] == len(fills)


def test_future_tape_cannot_relabel_past_fills_and_current_trade_not_in_pre_fill(tmp_path):
    left = execution_tape(tmp_path / "left.ndjson")
    right = execution_tape(tmp_path / "right.ndjson")
    rows = [json.loads(line) for line in right.read_text().splitlines()]
    for row in rows:
        if row["type"] == "depthUpdate" and row["data"]["_capture"]["recvMonotonicNs"] >= 10 * SECOND:
            row["data"]["b"][0][1] = "0.5"
    right.write_text("".join(json.dumps(row) + "\n" for row in rows))
    audits = []
    for path in (left, right):
        sink = Recorder()
        engine = SimulationEngine(replace(cfg(), hmm=settings()), regime_execution_sink=sink)
        engine.run(path)
        audits.append([row for row in sink.rows if row["logical_ns"] < 7 * SECOND and row["event_type"] == "fill"])
    assert audits[0] == audits[1] and audits[0]
    assert all(row["pre_fill"]["sample_ns"] < 6 * SECOND for row in audits[0])


@pytest.mark.parametrize("route", ["public", "market", "control"])
def test_gap_preserves_markout_semantics_and_frozen_labels(tmp_path, route):
    path = execution_tape(tmp_path / "input.ndjson")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    index = next(i for i, row in enumerate(rows) if row["type"] == "aggTrade") + 2
    inserted = copy.deepcopy(rows[index - 1])
    inserted["type"] = "captureEvent"
    inserted["symbol"] = "*" if route == "control" else "BTCUSDT"
    inserted["data"] = {
        "event": "overflow" if route == "control" else "disconnect",
        "route": route,
        "_capture": copy.deepcopy(inserted["data"]["_capture"]),
    }
    inserted["data"]["_capture"].update({"recvMonotonicNs": 6_050_000_000, "route": route})
    inserted["data"]["_capture"]["recvWallNs"] += 50_000_000
    inserted["ts_local"] += 0.05
    rows.insert(index, inserted)
    for index, row in enumerate(rows):
        row["data"]["_capture"]["recvSeq"] = index
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    sink = Recorder()
    config = replace(cfg(), hmm=settings(), sim_markout_horizons_ms=(100, 1000, 5000, 30000))
    baseline = SimulationEngine(replace(config, hmm=None))
    baseline.run(path)
    engine = SimulationEngine(config, regime_execution_sink=sink)
    engine.run(path)
    fills = [row for row in sink.rows if row["event_type"] == "fill"]
    invalidated = [row for row in sink.rows if row["status"] == "invalidated"]
    assert len(fills) == 2
    assert without_diagnostics(engine.event_trace) == baseline.event_trace
    # Trade reconnects invalidate future execution, not already completed
    # fills' book-only marks. Book/capture gaps invalidate all pending horizons.
    if route == "market":
        assert not invalidated
        resolved = [row for row in sink.rows if row["status"] == "resolved"]
        assert len(resolved) == 6
        assert {row["horizon_ms"] for row in resolved} == {100, 1000, 5000}
        return
    assert len(invalidated) == 8
    for fill in fills:
        followups = [row for row in invalidated if row["fill_id"] == fill["fill_id"]]
        assert {row["horizon_ms"] for row in followups} == {100, 1000, 5000, 30000}
        for followup in followups:
            assert followup["pre_fill"] == fill["pre_fill"]
            assert followup["markout"] is None and followup["actual_lag_seconds"] is None
    assert not engine.hmm_execution.checkpoint()["orders"]


@pytest.mark.parametrize("corruption", ["posterior", "ordinal", "missing_context", "missing_pending"])
def test_malformed_execution_checkpoint_rejected_before_core_mutation(tmp_path, corruption):
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(cfg(), hmm=settings())
    checkpoint = tmp_path / "checkpoint.json"
    paused = SimulationEngine(config)
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=14)
    original = read_checkpoint(checkpoint)
    state = copy.deepcopy(original.state)
    engine_state = decode(state["engine"])
    pending = engine_state["metrics"]["_pending_markout_horizons"]
    assert pending and state["hmm_execution"]["orders"]
    if corruption == "posterior":
        pending[0]["hmm_attribution"]["pre_fill"]["posterior"] = [0.5, 1.0]
    elif corruption == "ordinal":
        pending[0]["hmm_attribution"]["fill_id"] = 10000
    elif corruption == "missing_context":
        state["hmm_execution"]["orders"].clear()
    else:
        pending.pop()
    state["engine"] = encode(engine_state)
    write_checkpoint(
        checkpoint,
        Checkpoint.create(
            original.event_index,
            original.logical_time,
            state,
            schema_version=original.schema_version,
        ),
    )
    resumed = SimulationEngine(config)
    before = encode(resumed._checkpoint_mutable_state())
    with pytest.raises(ValueError):
        resumed.run(path, resume_from=checkpoint)
    assert encode(resumed._checkpoint_mutable_state()) == before


def test_pending_state_readers_cannot_mutate_frozen_attribution(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    engine = SimulationEngine(replace(cfg(), hmm=settings(), sim_markout_horizons_ms=(100, 1000, 30000)))
    engine.run(path)
    original = engine.hmm_execution.checkpoint()
    pending = engine.metrics.pending_markout_state()
    assert pending
    pending[0]["hmm_attribution"]["pre_fill"]["active_state"] = 999
    assert engine.metrics.pending_markout_state()[0]["hmm_attribution"]["pre_fill"]["active_state"] != 999
    assert engine.hmm_execution.checkpoint() == original


def test_missing_marks_and_no_samples_are_not_reported_as_zero_means():
    subject = audit()
    subject.on_fill(1, fill_row(spread=None), subject.at_fill(None, stage(0, 3, status="STALE")))
    cell = subject.summary()["conditioned"]["pre_fill"]["UNAVAILABLE"]
    assert cell["marked_qty"] == "0" and cell["spread_capture_coverage"] == 0.0
    assert cell["mean_marked_spread_capture"] is None
    assert cell["markouts"]["100"]["unresolved_samples"] == 1
    assert cell["markouts"]["100"]["mean_signed_markout"] is None
    empty = subject.summary()["conditioned"]["pre_fill"]["STATE_0"]
    assert empty["mean_time_in_book_ms"] is None and empty["markouts"]["100"]["coverage"] is None


def test_mutating_execution_sink_cannot_rewrite_hash_or_live_order_contexts():
    class MutatingSink(Recorder):
        def write(self, row):
            row["decision"]["posterior"][0] = -999
            row["pre_fill"]["active_state"] = 999

    subjects = [audit(), audit(MutatingSink())]
    for subject in subjects:
        subject.accept_order("order-1", stage(0, 1), stage(1, 2))
        subject.on_fill(1, fill_row(), subject.at_fill("order-1", stage(1, 3)))
    assert subjects[0].checkpoint() == subjects[1].checkpoint()


def test_execution_audit_state_cardinality_independent_of_fill_history():
    subject = audit()
    for index in range(1, 2001):
        order = f"order-{index}"
        subject.accept_order(order, stage(0, 1), stage(1, 2))
        frozen = subject.on_fill(index, {**fill_row(), "order_id": order}, subject.at_fill(order, stage(1, 3)))
        for horizon in (100, 1000):
            subject.on_markout(
                {
                    "hmm_attribution": frozen,
                    "fill_source": "agg_trade",
                    "symbol": "BTCUSDT",
                    "side": "bid",
                    "order_id": order,
                    "qty": "2",
                    "qty_lots": 2,
                    "ts_local": 1.0,
                    "deadline_ts": 1.0 + horizon / 1000,
                },
                horizon,
                markout=Decimal("-1"),
                observed_ts=2.0,
            )
        subject.release_order(order)
    checkpoint = subject.checkpoint()
    assert checkpoint["orders"] == {} and checkpoint["trace_count"] == 6000
    # Fixed K-by-source cells grow the constant bound, not the history retained.
    assert len(json.dumps(checkpoint)) < 100000
    assert subject.validated_copy(checkpoint).checkpoint() == checkpoint


def test_native_state_conditioned_metrics_agree_with_independent_trace_reduction(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    sink = Recorder()
    engine = SimulationEngine(
        replace(cfg(), hmm=settings(), sim_markout_horizons_ms=(100, 1000, 5000, 30000)), regime_execution_sink=sink
    )
    engine.run(path)
    summary = engine.hmm_execution.summary()
    for phase in ("decision", "arrival", "pre_fill"):
        for label, cell in summary["conditioned"][phase].items():
            fills = [r for r in sink.rows if r["event_type"] == "fill" and r[f"{phase}_label"] == label]
            assert cell["fill_count"] == len(fills)
            assert Decimal(cell["fee"]) == sum((Decimal(r["fee"]) for r in fills), Decimal(0))
            assert cell["qty_lots"] == sum(r["qty_lots"] for r in fills)
            for horizon, stats in cell["markouts"].items():
                rows = [
                    r
                    for r in sink.rows
                    if r["event_type"] == "markout" and r[f"{phase}_label"] == label and r["horizon_ms"] == int(horizon)
                ]
                resolved = [r for r in rows if r["status"] == "resolved"]
                assert stats["resolved_samples"] == len(resolved)
                assert stats["unresolved_samples"] == len(fills) - len(rows)
                quantity = sum((Decimal(r["qty"]) for r in resolved), Decimal(0))
                weighted = sum((Decimal(r["qty"]) * Decimal(r["markout"]) for r in resolved), Decimal(0))
                assert stats["mean_signed_markout"] == (float(weighted / quantity) if quantity else None)


def test_execution_writer_failure_leaves_incomplete_bundle(tmp_path, monkeypatch):
    from lob_sim.sim.sinks import StreamingCsvSink

    original = StreamingCsvSink.write

    def fail_execution_write(self, row):
        if self.path.name == "regime_execution.csv":
            raise OSError("execution audit disk failure")
        original(self, row)

    monkeypatch.setattr(StreamingCsvSink, "write", fail_execution_write)
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(cfg(), record_dir=tmp_path / "runs", hmm=settings())
    with pytest.raises(OSError, match="execution audit disk"):
        run_bounded_simulation(config, path)
    incomplete = list(config.output_dir.rglob("_INCOMPLETE.json"))
    assert len(incomplete) == 1
    assert not (incomplete[0].parent / "manifest.json").exists()
    assert (incomplete[0].parent / "regime_execution.csv.partial").exists()
