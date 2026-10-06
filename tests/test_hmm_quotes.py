"""Independent lifecycle census and native observe-only non-interference tests."""

from __future__ import annotations

import copy
import csv
import json
import random
from hashlib import sha256
from collections import Counter
from dataclasses import replace

import pytest

from lob_sim.regime.quotes import COUNTS, DOMAIN, QUOTE_FIELDS, RegimeQuoteAudit, verify_quote_trace
from lob_sim.regime.validation import canonical_json
from lob_sim.regime.execution import RegimeExecutionAudit
from lob_sim.regime.diagnostics import inspect_run
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.sinks import StreamingCsvSink
from test_hmm_dataset import cfg, tape, SECOND
from test_hmm_execution import execution_tape, fill_row, stage
from test_hmm_observation import Recorder, settings, without_diagnostics
from lob_sim.oracle import Checkpoint, read_checkpoint, write_checkpoint
from lob_sim.sim.checkpoint import decode, encode


def quote_audit(k=2, sink=None):
    return RegimeQuoteAudit("BTCUSDT", tuple(f"STATE_{i}" for i in range(k)) + ("UNCONFIRMED", "UNAVAILABLE"), 4, sink)


def test_partial_fills_count_one_order_and_keep_original_two_cohorts():
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 4)
    decision, arrival = stage(0, 1), stage(1, 2)
    request = subject.schedule_quote(decision, "bid", "base", 3)
    subject.accept_order("order-1", decision, arrival, request)
    first = subject.at_fill("order-1", stage(1, 3), 2)
    second = subject.at_fill("order-1", stage(0, 4), 1)
    assert first["quote"] == {"request_id": request, "first_fill": True}
    assert second["quote"] == {"request_id": request, "first_fill": False}
    # Matching may terminate before delayed legacy accounting consumes the batch.
    subject.release_order("order-1", 4, "filled")
    subject.on_fill(1, fill_row(qty="2"), first)
    subject.on_fill(2, fill_row(qty="1"), second)
    report = subject.summary()["quote_lifecycles"]
    for basis, label in (("decision", "STATE_0"), ("arrival", "STATE_1")):
        cell = report["cohorts"][basis][label]
        assert cell["accepted_orders"] == cell["unique_filled_orders"] == 1
        assert cell["fill_events"] == 2 and cell["filled_qty_lots"] == 3
        assert cell["unique_filled_fraction_of_accepted"] == 1.0
        assert cell["terminal_orders"]["filled"] == 1 and cell["live_orders"] == 0
    assert report["cohorts"]["arrival"]["STATE_0"]["accepted_orders"] == 0
    assert report["cohorts"]["arrival"]["STATE_0"]["unique_filled_fraction_of_accepted"] is None
    assert report["cohorts"]["arrival"]["STATE_1"]["unique_filled_fraction_of_scheduled"] is None
    assert subject.validated_copy(subject.checkpoint()).checkpoint() == subject.checkpoint()


def test_rejections_discards_censoring_and_no_fabricated_accepted_denominator():
    subject = quote_audit()
    requests = [subject.schedule("STATE_0", 1, "bid", str(i), 3) for i in range(4)]
    subject.arrive(requests[0], "STATE_1", 2, order_id="accepted")
    subject.arrive(requests[1], "UNAVAILABLE", 2, reason="post_only_would_cross")
    subject.discard(requests[2], 2, "epoch_invalidated")
    cell = subject.summary()["cohorts"]["decision"]["STATE_0"]
    assert cell["scheduled_requests"] == 4
    assert cell["arrived_requests"] == 2 and cell["accepted_orders"] == cell["rejected_requests"] == 1
    assert cell["discarded_before_arrival"] == cell["pending_requests"] == cell["live_orders"] == 1
    assert cell["unique_filled_fraction_of_accepted"] == 0.0
    assert cell["acceptance_fraction_of_arrived"] == 0.5
    assert subject.summary()["cohorts"]["arrival"]["UNAVAILABLE"]["unique_filled_fraction_of_accepted"] is None
    subject.clear(3, "halted", discard_pending=False)
    assert subject.summary()["retained_pending_requests"] == 1
    subject.discard(requests[3], 4, "halted")
    assert subject.validated_copy(subject.checkpoint()).checkpoint() == subject.checkpoint()


@pytest.mark.parametrize("k", [2, 3, 4, 5])
@pytest.mark.parametrize("seed", [11, 37, 103])
def test_generated_cohorts_agree_with_independent_batch_census(k, seed):
    sink = Recorder()
    subject = quote_audit(k, sink)
    rng = random.Random(seed)
    expected: dict[str, dict[str, Counter]] = {
        basis: {label: Counter() for label in subject.labels} for basis in ("decision", "arrival")
    }
    filled_orders: dict[tuple[str, str], set[str]] = {
        (basis, label): set() for basis in expected for label in subject.labels
    }
    ordinal = 0
    for index in range(120):
        decision, arrival = rng.choice(subject.labels), rng.choice(subject.labels)
        time = index * 10
        request = subject.schedule(decision, time, "bid", "base", 5)
        expected["decision"][decision]["scheduled_requests"] += 1
        expected["decision"][decision]["scheduled_qty_lots"] += 5
        outcome = rng.randrange(4)
        if outcome == 0:
            subject.discard(request, time + 1, "epoch_invalidated")
            expected["decision"][decision]["discarded_before_arrival"] += 1
            continue
        for basis, label in (("decision", decision), ("arrival", arrival)):
            expected[basis][label]["arrived_requests"] += 1
        if outcome == 1:
            subject.arrive(request, arrival, time + 1, reason="post_only_would_cross")
            for basis, label in (("decision", decision), ("arrival", arrival)):
                expected[basis][label]["rejected_requests"] += 1
            continue
        order = f"order-{index}"
        subject.arrive(request, arrival, time + 1, order_id=order)
        for basis, label in (("decision", decision), ("arrival", arrival)):
            expected[basis][label]["accepted_orders"] += 1
            expected[basis][label]["accepted_qty_lots"] += 5
        quantities = (2, 3) if outcome == 2 else ()
        for lots in quantities:
            ordinal += 1
            frozen = subject.freeze_fill(order, lots)
            subject.on_fill(ordinal, frozen, time + 2, order, "bid", lots, decision, arrival)
            for basis, label in (("decision", decision), ("arrival", arrival)):
                expected[basis][label]["fill_events"] += 1
                expected[basis][label]["filled_qty_lots"] += lots
                filled_orders[basis, label].add(order)
        subject.terminate(order, time + 3, "filled" if quantities else "cancelled")
        # Serialized clones must preserve first-fill flags and all cohort counts.
        if index % 17 == 0:
            subject = subject.validated_copy(json.loads(json.dumps(subject.checkpoint())))
    for basis, groups in subject.summary()["cohorts"].items():
        for label, cell in groups.items():
            for name in COUNTS:
                assert cell[name] == (
                    len(filled_orders[basis, label]) if name == "unique_filled_orders" else expected[basis][label][name]
                )
    assert subject.validated_copy(subject.checkpoint()).checkpoint() == subject.checkpoint()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_pending",
        "missing_live",
        "first_fill",
        "matched_lots",
        "future_arrival",
        "duplicate_request",
        "accepted_count",
        "scheduled_count",
        "terminal_count",
        "trace_count",
        "empty_digest",
        "arrival_denominator",
        "bool_lots",
    ],
)
def test_checkpoint_corruption_rejected_without_mutation(corruption):
    subject = quote_audit()
    request = subject.schedule("STATE_0", 1, "bid", "base", 5)
    subject.arrive(request, "STATE_1", 2, order_id="order-1")
    frozen = subject.freeze_fill("order-1", 2)
    subject.on_fill(1, frozen, 3, "order-1", "bid", 2, "STATE_0", "STATE_1")
    subject.schedule("UNAVAILABLE", 4, "ask", "base", 5)
    original = subject.checkpoint()
    changed = copy.deepcopy(original)
    live = changed["live"]["order-1"]
    cell = changed["cohorts"]["decision"]["STATE_0"]
    if corruption == "missing_pending":
        changed["pending"].clear()
    elif corruption == "missing_live":
        changed["live"].clear()
    elif corruption == "first_fill":
        live["first_fill_stamped"] = False
    elif corruption == "matched_lots":
        live["matched_lots"] = 6
    elif corruption == "future_arrival":
        live["arrival_ns"] = 0
    elif corruption == "duplicate_request":
        changed["pending"]["2"]["request_id"] = 1
    elif corruption == "accepted_count":
        cell["accepted_orders"] += 1
    elif corruption == "scheduled_count":
        cell["scheduled_requests"] += 1
    elif corruption == "terminal_count":
        cell["terminal_orders"]["filled"] += 1
    elif corruption == "trace_count":
        changed["trace_count"] += 1
    elif corruption == "arrival_denominator":
        changed["cohorts"]["arrival"]["STATE_1"]["scheduled_requests"] += 1
    elif corruption == "bool_lots":
        live["qty_lots"] = True
    else:
        empty = quote_audit()
        changed = empty.checkpoint()
        changed["trace_sha256"] = "a" * 64
        with pytest.raises(ValueError):
            empty.validated_copy(changed)
        return
    with pytest.raises(ValueError):
        subject.validated_copy(changed)
    assert subject.checkpoint() == original


def test_pending_and_partial_fill_checkpoint_resume_preserves_unique_count():
    subject = quote_audit()
    request = subject.schedule("STATE_0", 1, "bid", "base", 3)
    subject = subject.validated_copy(subject.checkpoint())
    subject.arrive(request, "STATE_1", 2, order_id="order-1")
    first = subject.freeze_fill("order-1", 2)
    subject.on_fill(1, first, 3, "order-1", "bid", 2, "STATE_0", "STATE_1")
    subject = subject.validated_copy(subject.checkpoint())
    second = subject.freeze_fill("order-1", 1)
    assert not second["first_fill"]
    subject.terminate("order-1", 4, "filled")
    subject.on_fill(2, second, 4, "order-1", "bid", 1, "STATE_0", "STATE_1")
    assert subject.summary()["cohorts"]["decision"]["STATE_0"]["unique_filled_orders"] == 1


@pytest.mark.parametrize("fill_mode", ["trade", "depth"])
def test_native_quote_counts_match_core_events_and_independent_unique_order_ids(tmp_path, fill_mode):
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(cfg(), hmm=settings(), sim_fill_model=fill_mode)
    quotes, executions = Recorder(), Recorder()
    engine = SimulationEngine(config, regime_quote_sink=quotes, regime_execution_sink=executions)
    engine.run(path)
    base = SimulationEngine(replace(config, hmm=None))
    base.run(path)
    assert without_diagnostics(engine.event_trace) == base.event_trace
    assert engine._id_counter == base._id_counter
    assert engine.latency_model.sampler_state() == base.latency_model.sampler_state()
    assert engine.metrics.get_summary(engine._books, engine._specs) == base.metrics.get_summary(
        base._books, base._specs
    )
    summary = engine.hmm_execution.quotes.summary()
    totals = {name: sum(cell[name] for cell in summary["cohorts"]["decision"].values()) for name in COUNTS}
    assert totals["scheduled_requests"] == engine.metrics.order_arrival_scheduled_count
    assert totals["arrived_requests"] == engine.metrics.order_arrival_count
    assert totals["rejected_requests"] == engine.metrics.order_rejected_count
    fills = [r for r in executions.rows if r["event_type"] == "fill"]
    assert (
        totals["unique_filled_orders"] == len({row["order_id"] for row in fills}) == engine.metrics.filled_order_count
    )
    for basis, groups in summary["cohorts"].items():
        for label, cell in groups.items():
            accepted = [r for r in quotes.rows if r["event_type"] == "accepted" and r[f"{basis}_label"] == label]
            cohort_fills = [r for r in fills if r[f"{basis}_label"] == label]
            assert cell["accepted_orders"] == len(accepted)
            assert cell["fill_events"] == len(cohort_fills)
            assert cell["unique_filled_orders"] == len({r["order_id"] for r in cohort_fills})
            assert cell["filled_qty_lots"] == sum(r["qty_lots"] for r in cohort_fills)


def test_quote_stream_and_summary_verified_before_cli_report(tmp_path):
    files, summary = run_bounded_simulation(
        replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), execution_tape(tmp_path / "input.ndjson")
    )
    quotes = summary["hmm_execution"]["quote_lifecycles"]
    labels = tuple(quotes["cohorts"]["decision"])
    verify_quote_trace(files["regime_quotes"], quotes, labels, execution_path=files["regime_execution"])
    assert "Quote lifecycle cohorts" in inspect_run(files["summary"].parent)
    changed = copy.deepcopy(quotes)
    changed["cohorts"]["decision"]["UNAVAILABLE"]["scheduled_requests"] += 1
    with pytest.raises(ValueError, match="statistics"):
        verify_quote_trace(files["regime_quotes"], changed, labels)
    changed = copy.deepcopy(quotes)
    changed["retained_live_bindings"] += 1
    with pytest.raises(ValueError, match="retained"):
        verify_quote_trace(files["regime_quotes"], changed, labels)
    content = files["regime_quotes"].read_text()
    files["regime_quotes"].write_text(content.replace("BTCUSDT", "ETHUSDT"))
    with pytest.raises(ValueError):
        inspect_run(files["summary"].parent)


def test_quote_writer_failure_preserves_incomplete_evidence(tmp_path, monkeypatch):
    original = StreamingCsvSink.write

    def fail(self, row):
        if self.path.name == "regime_quotes.csv":
            raise OSError("quote writer failed")
        original(self, row)

    monkeypatch.setattr(StreamingCsvSink, "write", fail)
    config = replace(cfg(), record_dir=tmp_path / "out", hmm=settings())
    with pytest.raises(OSError, match="quote writer failed"):
        run_bounded_simulation(config, execution_tape(tmp_path / "input.ndjson"))
    incomplete = list(config.output_dir.rglob("_INCOMPLETE.json"))
    assert len(incomplete) == 1 and not (incomplete[0].parent / "manifest.json").exists()
    assert (incomplete[0].parent / "regime_quotes.csv.partial").exists()


def test_consistent_quote_rehash_cannot_break_accounted_fill_link(tmp_path):
    files, summary = run_bounded_simulation(
        replace(cfg(), record_dir=tmp_path / "out", hmm=settings()), execution_tape(tmp_path / "input.ndjson")
    )
    path = files["regime_quotes"]
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    fill = next(row for row in rows if row["event_type"] == "fill" and row["first_fill"] == "True")
    fill["first_fill"] = "False"
    claimed = copy.deepcopy(summary["hmm_execution"]["quote_lifecycles"])
    for basis in ("decision", "arrival"):
        cell = claimed["cohorts"][basis][fill[f"{basis}_label"]]
        cell["unique_filled_orders"] -= 1
        cell["unique_filled_fraction_of_accepted"] = cell["unique_filled_orders"] / cell["accepted_orders"]
        if basis == "decision":
            cell["unique_filled_fraction_of_scheduled"] = cell["unique_filled_orders"] / cell["scheduled_requests"]
    digest = sha256(DOMAIN).hexdigest()
    for row in rows:
        parsed = {}
        for key, value in row.items():
            if value == "":
                parsed[key] = None
            elif key in {"logical_ns", "request_id", "fill_id", "qty_lots"}:
                parsed[key] = int(value)
            elif key == "first_fill":
                parsed[key] = value == "True"
            else:
                parsed[key] = value
        digest = sha256(bytes.fromhex(digest) + canonical_json(parsed).encode()).hexdigest()
    claimed["trace_sha256"] = digest
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUOTE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    labels = tuple(claimed["cohorts"]["decision"])
    verify_quote_trace(path, claimed, labels)  # Deliberately self-consistent rewritten sidecar.
    with pytest.raises(ValueError, match="accounted execution"):
        verify_quote_trace(path, claimed, labels, execution_path=files["regime_execution"])


def test_old_execution_checkpoint_cannot_fabricate_missing_quote_denominators():
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 4)
    old = subject.checkpoint()
    del old["quotes"]
    old["schema_version"] = "lob_sim.hmm_execution_checkpoint.v1"
    with pytest.raises(ValueError):
        subject.validated_copy(old)


def test_constant_cardinality_across_many_quote_lifetimes():
    subject = quote_audit()
    sizes = []
    for index in range(2000):
        request = subject.schedule("UNCONFIRMED", index * 3, "bid", "base", 1)
        subject.arrive(request, "STATE_0", index * 3 + 1, order_id=str(index))
        frozen = subject.freeze_fill(str(index), 1)
        subject.terminate(str(index), index * 3 + 2, "filled")
        subject.on_fill(index + 1, frozen, index * 3 + 2, str(index), "bid", 1, "UNCONFIRMED", "STATE_0")
        if index in {9, 99, 1999}:
            sizes.append(len(json.dumps(subject.checkpoint())))
    assert max(sizes) - min(sizes) < 200  # Only integer counters grow; no ID history.
    assert subject.checkpoint()["pending"] == subject.checkpoint()["live"] == {}
    assert subject.validated_copy(subject.checkpoint()).checkpoint() == subject.checkpoint()


def test_pending_capacity_overfill_and_future_arrival_fail_closed():
    subject = quote_audit()
    ids = [subject.schedule("STATE_0", 10, "bid", str(i), 2) for i in range(4)]
    original = subject.checkpoint()
    with pytest.raises(ValueError):
        subject.schedule("STATE_0", 10, "ask", "base", 2)
    with pytest.raises(ValueError):
        subject.arrive(ids[0], "STATE_1", 9, order_id="early")
    assert subject.checkpoint() == original
    subject.arrive(ids[0], "STATE_1", 11, order_id="order-1")
    with pytest.raises(ValueError):
        subject.freeze_fill("order-1", 3)
    assert subject.checkpoint()["live"]["order-1"]["matched_lots"] == 0


def test_quote_only_sink_cannot_resume_into_truncated_audit(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    config = replace(cfg(), hmm=settings())
    checkpoint = tmp_path / "checkpoint.json"
    SimulationEngine(config).run(path, checkpoint_path=checkpoint, stop_after_records=7)
    with pytest.raises(ValueError, match="NullSink"):
        SimulationEngine(config, regime_quote_sink=Recorder()).run(path, resume_from=checkpoint)


def test_native_gap_discards_outbound_requests_without_inventing_rejections(tmp_path):
    path = tape(tmp_path / "input.ndjson", control=("disconnect", "public", "BTCUSDT"))
    configuration = replace(cfg(), hmm=settings(), sim_order_latency_ms=5000)
    sink = Recorder()
    engine = SimulationEngine(configuration, regime_quote_sink=sink)
    engine.run(path)
    baseline = SimulationEngine(replace(configuration, hmm=None))
    baseline.run(path)
    assert without_diagnostics(engine.event_trace) == baseline.event_trace
    discarded = [row for row in sink.rows if row["event_type"] == "discarded"]
    assert len(discarded) == 2 and {row["reason"] for row in discarded} == {"epoch_invalidated"}
    assert not [row for row in sink.rows if row["event_type"] in {"accepted", "rejected", "fill"}]
    assert engine.hmm_execution.quotes.summary()["retained_pending_requests"] == 0
    assert (
        engine.hmm_execution.quotes.validated_copy(engine.hmm_execution.quotes.checkpoint()).checkpoint()
        == engine.hmm_execution.quotes.checkpoint()
    )


def test_native_halt_keeps_outbound_state_until_arrival_discards_it(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings(), sim_order_latency_ms=5000)
    engine = SimulationEngine(configuration)
    engine.run(path, checkpoint_path=tmp_path / "pause.json", stop_after_records=7)
    assert engine.hmm_execution.quotes.summary()["retained_pending_requests"] == 2
    engine._disable_trading()
    assert engine.hmm_execution.quotes.summary()["retained_pending_requests"] == 2
    engine._drain_events(engine._last_ts + 10, logical_ns=engine._last_logical_ns + 10 * SECOND)
    summary = engine.hmm_execution.quotes.summary()
    assert summary["retained_pending_requests"] == 0
    assert sum(cell["discarded_before_arrival"] for cell in summary["cohorts"]["decision"].values()) == 2
    assert engine.metrics.order_rejected_count == 0


def test_native_post_only_rejection_preserves_request_denominator(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    sink = Recorder()
    engine = SimulationEngine(replace(cfg(), hmm=settings()), regime_quote_sink=sink)
    engine.run(path)
    now = engine._last_ts
    for order in engine.fill_model.get_orders("BTCUSDT", "ask"):
        engine._handle_cancel({"order_id": order.order_id}, now, "BTCUSDT")
    creation = engine._hmm_stage("BTCUSDT", now)
    request = engine.hmm_execution.schedule_quote(creation, "bid", "crossing-test", 1)
    best_bid, best_ask = engine._books["BTCUSDT"].best_ticks()
    before = engine.metrics.order_rejected_count
    engine._handle_arrival(
        "BTCUSDT",
        {
            "side": "bid",
            "quote_slot": "crossing-test",
            "price_tick": best_ask,
            "qty_lots": 1,
            "hmm_decision": creation,
            "hmm_request_id": request,
        },
        now,
    )
    row = sink.rows[-1]
    assert row["event_type"] == "rejected" and row["reason"] == "post_only_would_cross"
    assert row["request_id"] == request
    assert engine.metrics.order_rejected_count == before + 1
    assert request not in engine.hmm_execution.quotes._pending


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_pending",
        "pending_quantity",
        "missing_live",
        "live_quantity",
        "live_label",
        "missing_markout_quote",
        "fill_flag_type",
        "fill_census",
    ],
)
def test_native_corrupted_quote_checkpoint_refused_before_core_mutation(tmp_path, corruption):
    path = execution_tape(tmp_path / "input.ndjson")
    pending_case = corruption in {"missing_pending", "pending_quantity"}
    configuration = replace(cfg(), hmm=settings(), sim_order_latency_ms=5000 if pending_case else 10)
    checkpoint = tmp_path / "state.json"
    paused = SimulationEngine(configuration)
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=7 if pending_case else 14)
    original = read_checkpoint(checkpoint)
    state = copy.deepcopy(original.state)
    core = decode(state["engine"])
    quotes = state["hmm_execution"]["quotes"]
    if corruption.startswith("pending") or corruption == "missing_pending":
        # This checkpoint precedes the first delayed arrival.
        assert quotes["pending"]
        if corruption == "missing_pending":
            quotes["pending"].clear()
        else:
            next(iter(quotes["pending"].values()))["qty_lots"] += 1
    elif corruption in {"missing_live", "live_quantity", "live_label"}:
        assert quotes["live"]
        binding = next(iter(quotes["live"].values()))
        if corruption == "missing_live":
            quotes["live"].clear()
        elif corruption == "live_quantity":
            binding["qty_lots"] += 1
        else:
            binding["decision_label"] = "STATE_0" if binding["decision_label"] != "STATE_0" else "STATE_1"
    elif corruption in {"missing_markout_quote", "fill_flag_type"}:
        pending = core["metrics"]["_pending_markout_horizons"]
        assert pending
        if corruption == "missing_markout_quote":
            pending[0]["hmm_attribution"]["quote"] = None
        else:
            pending[0]["hmm_attribution"]["quote"]["first_fill"] = 1
    else:
        quotes["last_fill"] += 1
    state["engine"] = encode(core)
    write_checkpoint(
        checkpoint,
        Checkpoint.create(original.event_index, original.logical_time, state, schema_version=original.schema_version),
    )
    resumed = SimulationEngine(configuration)
    before = encode(resumed._checkpoint_mutable_state())
    with pytest.raises(ValueError):
        resumed.run(path, resume_from=checkpoint)
    assert encode(resumed._checkpoint_mutable_state()) == before
