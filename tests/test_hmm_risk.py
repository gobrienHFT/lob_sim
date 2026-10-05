"""Independent interval arithmetic and native causal-risk boundary regressions."""

from __future__ import annotations

import copy
import math
import random
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from lob_sim.regime.risk import BASES, COUNTERS, RISK_FIELDS, RegimeRiskAudit, verify_risk_trace
from lob_sim.regime.observation import TRACE_FIELDS
from lob_sim.regime.diagnostics import inspect_run
from lob_sim.regime.execution import capture_stage
from lob_sim.regime.validation import canonical_json
from lob_sim.oracle import Checkpoint, read_checkpoint, write_checkpoint
from lob_sim.sim.checkpoint import encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.sinks import StreamingCsvSink
from test_hmm_dataset import SECOND, cfg, tape
from test_hmm_execution import execution_tape
from test_hmm_observation import Recorder, settings, without_diagnostics


def row(time, *, k=2, state=0, active=0, inventory=2, regime_life=100, mark_life=100):
    p = [0.1 / (k - 1)] * k
    p[state] = 0.9
    signal = {
        "model_sha256": "a" * 64,
        "status": "VALID" if regime_life else "INVALID_BOOK",
        "raw_map_state": state,
        "active_state": active,
        "posterior": p,
        "sample_ns": max(0, time - 2),
        "available_at_ns": time,
        "receive_seq": time,
        "confidence": 0.9,
        "normalized_entropy": -sum(value * math.log(value) for value in p) / math.log(k),
        "epochs": [1, 2, 3],
    }
    return {
        "schema_version": "lob_sim.hmm_risk_boundary.v1",
        "symbol": "BTCUSDT",
        "model_sha256": "a" * 64,
        "logical_ns": time,
        "reason": "after_record",
        "clock_basis": "receive_nanoseconds",
        "stage": capture_stage(signal, time),
        "regime_until_ns": time + regime_life if regime_life else None,
        "mark_until_ns": time + mark_life if mark_life else None,
        "mid_twice_tick": 200 if mark_life else None,
        "inventory_lots": inventory,
        "live_bid_lots": 3,
        "live_ask_lots": 2,
        "pending_bid_lots": 4,
        "pending_ask_lots": 1,
        "order_notional_tick_lots": 1000,
        "halted": False,
        "tick_size": "0.1",
        "step_size": "0.001",
        "contract_multiplier": "2",
        "regime_trace_count": 0,
        "regime_trace_sha256": "b" * 64,
    }


def write_rows(path, rows):
    with StreamingCsvSink(path, RISK_FIELDS) as sink:
        for item in rows:
            sink.write(item)


def independent_batch(rows, k):
    """Separate batch oracle: intersect each state/mark window with an interval."""
    labels = [f"STATE_{i}" for i in range(k)] + ["UNCONFIRMED", "UNAVAILABLE"]
    result = {basis: {label: dict.fromkeys(COUNTERS, 0) for label in labels} for basis in BASES}
    for previous, following in zip(rows, rows[1:]):
        start, end = previous["logical_ns"], following["logical_ns"]
        inventory = previous["inventory_lots"]
        valid_end = min(end, previous["regime_until_ns"] or start)
        for basis, key in (("raw_map", "raw_map_state"), ("active", "active_state")):
            state = previous["stage"][key]
            segments = [(f"STATE_{state}" if state is not None else "UNCONFIRMED", start, valid_end)]
            segments += [("UNAVAILABLE", valid_end, end)]
            for label, left, right in segments:
                if left >= right:
                    continue
                duration = right - left
                cell = result[basis][label]
                cell["duration_ns"] += duration
                cell["inventory_lots_ns"] += inventory * duration
                cell["absolute_inventory_lots_ns"] += abs(inventory) * duration
                cell["squared_inventory_lots_ns"] += inventory**2 * duration
                cell["max_absolute_inventory_lots"] = max(cell["max_absolute_inventory_lots"], abs(inventory))
                cell["halted_ns"] += duration if previous["halted"] else 0
                for field in ("live_bid_lots", "live_ask_lots", "pending_bid_lots", "pending_ask_lots"):
                    cell[field + "_ns"] += previous[field] * duration
                covered = max(0, min(right, previous["mark_until_ns"] or left) - left)
                if covered:
                    inv_notional = abs(inventory) * previous["mid_twice_tick"]
                    reservation = inv_notional + previous["order_notional_tick_lots"] * 2
                    cell["marked_duration_ns"] += covered
                    cell["absolute_inventory_notional_twice_tick_lots_ns"] += inv_notional * covered
                    cell["reserved_notional_twice_tick_lots_ns"] += reservation * covered
                    cell["max_reserved_notional_twice_tick_lots"] = max(
                        cell["max_reserved_notional_twice_tick_lots"], reservation
                    )
    return result


@pytest.mark.parametrize("k", [2, 3, 4, 5])
@pytest.mark.parametrize("seed", [11, 42, 1001])
def test_integer_risk_cells_and_derived_values_match_independent_batch(k, seed, tmp_path):
    rng, sink = random.Random(seed), Recorder()
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", k, sink)
    time = 2**60  # Adjacent nanoseconds cannot be represented by binary64 seconds.
    for ordinal in range(100):
        time += rng.randrange(0, 10)
        item = row(
            time,
            k=k,
            state=rng.randrange(k),
            active=None if ordinal % 4 == 0 else rng.randrange(k),
            inventory=rng.randrange(-5, 6),
            regime_life=rng.randrange(0, 10),
            mark_life=rng.randrange(0, 10),
        )
        item["halted"] = ordinal % 13 == 0
        item["mid_twice_tick"] = rng.randrange(100, 301) if item["mark_until_ns"] else None
        item["order_notional_tick_lots"] = rng.randrange(0, 1000)
        subject.observe(item)
    expected = independent_batch(sink.rows, k)
    summary = subject.summary()
    for basis in BASES:
        for label, raw in expected[basis].items():
            actual = summary["conditioned"][basis][label]
            assert {key: actual[key] for key in COUNTERS} == raw
            d, n, s = raw["duration_ns"], raw["inventory_lots_ns"], raw["squared_inventory_lots_ns"]
            assert actual["mean_inventory_lots"] == (str(Fraction(n, d)) if d else None)
            assert actual["inventory_variance_lots"] == (str(Fraction(s * d - n * n, d * d)) if d else None)
            assert actual["marked_coverage"] == (str(Fraction(raw["marked_duration_ns"], d)) if d else None)
    clone = subject.validated_copy(subject.checkpoint())
    assert clone.summary() == summary
    path = tmp_path / "risk.csv"
    write_rows(path, sink.rows)
    verify_risk_trace(path, summary)


def test_staleness_splits_exactly_and_does_not_erase_inventory_or_pending_cancels():
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject.observe(row(0, regime_life=5, mark_life=8))
    subject.observe(row(10, state=1, active=1, inventory=0, regime_life=0, mark_life=0))
    summary = subject.summary()
    valid, unavailable = (summary["conditioned"]["active"][name] for name in ("STATE_0", "UNAVAILABLE"))
    assert valid["duration_ns"] == unavailable["duration_ns"] == 5
    assert valid["marked_coverage"] == "1" and unavailable["marked_coverage"] == "3/5"
    assert valid["mean_absolute_inventory_qty"] == unavailable["mean_absolute_inventory_qty"] == "0.002"
    assert valid["live_bid_lots_ns"] == unavailable["live_bid_lots_ns"] == 15
    assert valid["pending_bid_lots_ns"] == unavailable["pending_bid_lots_ns"] == 20
    assert valid["mean_absolute_inventory_notional"] == "0.0400"
    assert valid["mean_reserved_notional"] == "0.2400"
    assert subject.summary()["last_ns"] == 10  # Querying summary never extends EOF.


def test_same_timestamp_label_changes_have_zero_weight_and_variance_is_population_time_weighted():
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    for item in (
        row(0, inventory=1),
        row(3, inventory=-1),
        row(3, state=1, active=1, inventory=100),
        row(3, inventory=-1),
        row(6, inventory=0),
    ):
        subject.observe(item)
    states = subject.summary()["conditioned"]["active"]
    assert states["STATE_1"]["duration_ns"] == 0 and states["STATE_1"]["mean_inventory_lots"] is None
    assert states["STATE_0"]["mean_inventory_lots"] == "0"
    assert states["STATE_0"]["inventory_variance_lots"] == "1"
    assert states["STATE_0"]["max_absolute_inventory_lots"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"inventory_lots": True},
        {"pending_bid_lots": -1},
        {"mid_twice_tick": None},
        {"mark_until_ns": 0},
        {"regime_until_ns": None},
        {"logical_ns": 0},
        {"step_size": "NaN"},
        {"step_size": "0"},
        {"symbol": "ETHUSDT"},
        {"halted": 1},
        {"regime_trace_sha256": "bad"},
        {"regime_trace_count": True},
    ],
)
def test_invalid_boundary_rejection_is_atomic(mutation):
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject.observe(row(1))
    before = subject.checkpoint()
    with pytest.raises(ValueError):
        subject.observe({**row(2), **mutation})
    assert subject.checkpoint() == before


@pytest.mark.parametrize(
    "mutation", ["bool_counter", "negative_variance", "duration", "margin", "mark", "schema", "future_stage"]
)
def test_checkpoint_corruption_rejected_without_mutation(mutation):
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject.observe(row(0))
    subject.observe(row(10))
    before, changed = subject.checkpoint(), copy.deepcopy(subject.checkpoint())
    cell = changed["cells"]["active"]["STATE_0"]
    if mutation == "bool_counter":
        cell["duration_ns"] = True
    elif mutation == "negative_variance":
        cell["squared_inventory_lots_ns"] = 0
    elif mutation == "duration":
        changed["first_ns"] = 1
    elif mutation == "margin":
        cell["pending_bid_lots_ns"] += 1
    elif mutation == "mark":
        cell["marked_duration_ns"] = 0
    elif mutation == "schema":
        changed["schema_version"] = "older"
    elif mutation == "future_stage":
        changed["anchor"]["stage"]["available_at_ns"] = 11
    with pytest.raises(ValueError):
        subject.validated_copy(changed)
    assert subject.checkpoint() == before


def test_sink_failure_cannot_advance_risk_hash_or_integrals():
    class Failing(Recorder):
        def write(self, value):
            if self.rows:
                raise OSError("disk full")
            super().write(value)

    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2, Failing())
    subject.observe(row(0))
    before = subject.checkpoint()
    with pytest.raises(OSError):
        subject.observe(row(1))
    assert subject.checkpoint() == before


def test_memory_retains_one_boundary_and_fixed_cells_not_tape():
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 5)
    for time in range(2000):
        subject.observe(row(time, k=5, state=time % 5, active=time % 5))
    assert subject.summary()["trace_count"] == 2000
    assert len(canonical_json(subject.checkpoint())) < 16_000
    assert len(subject._cells["active"]) == 7 and subject.sink.memory_bounded


def test_native_audit_preserves_actions_accounting_rng_and_cutoff_and_matches_batch(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(
        cfg(),
        mm_half_spread_bps=Decimal(0),
        mm_skew_bps_per_unit=Decimal(0),
        sim_markout_horizons_ms=(100, 1000),
    )
    base = SimulationEngine(configuration)
    base.run(path)
    sink = Recorder()
    observed = SimulationEngine(replace(configuration, hmm=settings()), regime_risk_sink=sink)
    observed.run(path)
    assert observed.metrics.fill_count > 0
    assert without_diagnostics(observed.event_trace) == base.event_trace
    assert observed.metrics.get_summary(observed._books, observed._specs) == base.metrics.get_summary(
        base._books, base._specs
    )
    assert (
        observed._id_counter == base._id_counter
        and observed.latency_model.sampler_state() == base.latency_model.sampler_state()
    )
    summary = observed.hmm_risk.summary()
    expected = independent_batch(sink.rows, 2)
    for basis in BASES:
        assert sum(cell["duration_ns"] for cell in summary["conditioned"][basis].values()) == summary["duration_ns"]
        for label, values in expected[basis].items():
            assert {key: summary["conditioned"][basis][label][key] for key in COUNTERS} == values
    assert summary["last_ns"] == observed._last_logical_ns == 14 * SECOND
    assert sink.rows[-1]["inventory_lots"] == observed.metrics.inventory_lots("BTCUSDT")
    assert any(item["reason"] == "fill_accounted" for item in sink.rows)
    assert any(item["pending_bid_lots"] for item in sink.rows)


@pytest.mark.parametrize("cut", [2, 7, 10, 14, 16])
def test_native_checkpoint_resume_identical_risk_and_full_hash(tmp_path, cut):
    path = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings(), mm_half_spread_bps=Decimal(0))
    full, partial, resumed = (SimulationEngine(configuration) for _ in range(3))
    full.run(path)
    state = tmp_path / "checkpoint.json"
    partial.run(path, checkpoint_path=state, stop_after_records=cut)
    resumed.run(path, resume_from=state)
    assert resumed.hmm_risk.checkpoint() == full.hmm_risk.checkpoint()
    assert resumed.state_sha256() == full.state_sha256()


@pytest.mark.parametrize(
    "key,value",
    [
        ("inventory_lots", 999),
        ("pending_bid_lots", 999),
        ("mid_twice_tick", 999),
        ("halted", True),
        ("regime_trace_count", 0),
    ],
)
def test_rehashed_native_checkpoint_wrong_core_anchor_rejected_before_core_mutation(tmp_path, key, value):
    path = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    paused = SimulationEngine(configuration)
    checkpoint = tmp_path / "state.json"
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=10)
    original = read_checkpoint(checkpoint)
    changed = copy.deepcopy(original.state)
    changed["hmm_risk"]["anchor"][key] = value
    altered = tmp_path / "altered.json"
    write_checkpoint(
        altered,
        Checkpoint.create(original.event_index, original.logical_time, changed, schema_version=original.schema_version),
    )
    subject = SimulationEngine(configuration)
    before = encode(subject._checkpoint_mutable_state())
    with pytest.raises(ValueError, match="risk checkpoint"):
        subject.run(path, resume_from=altered)
    assert encode(subject._checkpoint_mutable_state()) == before


@pytest.mark.parametrize(
    "control", [("disconnect", "market", "BTCUSDT"), ("disconnect", "public", "BTCUSDT"), ("overflow", "control", "*")]
)
def test_validity_and_mark_coverage_remain_independent_across_route_faults(tmp_path, control):
    path = tape(tmp_path / "input.ndjson", control=control)
    subject = SimulationEngine(replace(cfg(), hmm=settings()))
    subject.run(path)
    invalid = subject.hmm_risk.summary()["conditioned"]["active"]["UNAVAILABLE"]
    assert invalid["duration_ns"] > 0
    if control[1] == "market":
        assert invalid["marked_duration_ns"] > 0
    else:
        assert invalid["marked_duration_ns"] < invalid["duration_ns"]


def test_streamed_bundle_verifies_risk_before_reporting_and_detects_rehashed_summary(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    files, summary = run_bounded_simulation(replace(cfg(), hmm=settings(), record_dir=tmp_path / "runs"), path)
    verify_risk_trace(files["regime_risk"], summary["hmm_risk"])
    assert "Causal time-weighted risk" in inspect_run(files["manifest"].parent)
    wrong = copy.deepcopy(summary["hmm_risk"])
    wrong["conditioned"]["active"]["UNAVAILABLE"]["duration_ns"] += 1
    with pytest.raises(ValueError, match="time-weighted summary"):
        verify_risk_trace(files["regime_risk"], wrong)
    wrong = copy.deepcopy(summary["hmm_risk"])
    wrong["claim_ready"] = 0
    with pytest.raises(ValueError):
        verify_risk_trace(files["regime_risk"], wrong)


def test_future_tape_mutation_cannot_relabel_prior_risk_intervals(tmp_path):
    histories = []
    for name, qty in (("left", "0.015"), ("right", "0.5")):
        sink = Recorder()
        subject = SimulationEngine(replace(cfg(), hmm=settings()), regime_risk_sink=sink)
        subject.run(tape(tmp_path / f"{name}.ndjson", future_qty=qty))
        histories.append(
            [
                {key: value for key, value in item.items() if key != "regime_trace_sha256"}
                for item in sink.rows
                if item["logical_ns"] < 10 * SECOND
            ]
        )
    assert histories[0] == histories[1]


@pytest.mark.parametrize("mutation", ["stage", "prefix_count", "prefix_digest"])
def test_self_consistent_but_false_regime_link_is_rejected(tmp_path, mutation):
    risk_sink, regime_sink = Recorder(), Recorder()
    subject = SimulationEngine(replace(cfg(), hmm=settings()), regime_risk_sink=risk_sink, regime_sink=regime_sink)
    subject.run(tape(tmp_path / "input.ndjson"))
    changed = copy.deepcopy(risk_sink.rows)
    selected = next(item for item in changed if item["stage"]["status"] == "VALID")
    if mutation == "stage":
        selected["stage"]["posterior"].reverse()
        selected["stage"]["active_state"] = 1 - selected["stage"]["raw_map_state"]
        selected["stage"]["raw_map_state"] = 1 - selected["stage"]["raw_map_state"]
    elif mutation == "prefix_count":
        selected["regime_trace_count"] -= 1
    else:
        selected["regime_trace_sha256"] = "c" * 64
    # Deliberately produce a matching self-hash AND matching aggregate summary.
    # Only the separate causal-regime stream can expose this false information set.
    forged = RegimeRiskAudit(subject.cfg.hmm.model.model_sha256, "BTCUSDT", 2)
    for item in changed:
        forged.observe(item)
    risk_path, regime_path = tmp_path / "risk.csv", tmp_path / "regime.csv"
    write_rows(risk_path, changed)
    with StreamingCsvSink(regime_path, TRACE_FIELDS) as sink:
        for item in regime_sink.rows:
            sink.write(item)
    verify_risk_trace(risk_path, forged.summary())
    with pytest.raises(ValueError, match="risk (regime|stage)"):
        verify_risk_trace(risk_path, forged.summary(), regime_path=regime_path, regime_summary=subject.regime.summary())


def test_stale_gap_with_internal_actions_measures_until_exact_book_expiry(tmp_path):
    import json

    path = tape(tmp_path / "input.ndjson")
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records = [
        record
        for record in records
        if not (record["type"] == "depthUpdate" and record["data"]["_capture"]["recvMonotonicNs"] > 6 * SECOND)
    ]
    # Preserve receive-sequence continuity: this fixture is an idle interval,
    # not a missing-record corruption detected before staleness can be tested.
    for seq, record in enumerate(records):
        record["data"]["_capture"]["recvSeq"] = seq
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    sink = Recorder()
    subject = SimulationEngine(replace(cfg(), hmm=settings(), sim_order_latency_ms=8000), regime_risk_sink=sink)
    subject.run(path)
    expiry = 6 * SECOND + subject.regime.spec.stale_after_ns + 1
    assert any(item["reason"] == "after_action" and 6 * SECOND < item["logical_ns"] < expiry for item in sink.rows)
    assert any(item["logical_ns"] >= expiry and item["stage"]["status"] == "STALE" for item in sink.rows)
    final = subject.hmm_risk.summary()
    assert final["last_ns"] == 14 * SECOND
    assert sum(cell["marked_duration_ns"] for cell in final["conditioned"]["active"].values()) == expiry - 2 * SECOND


def test_risk_writer_failure_leaves_visibly_incomplete_bundle(tmp_path, monkeypatch):
    original = StreamingCsvSink.write

    def failing(self, event):
        if self.path.name == "regime_risk.csv":
            raise OSError("risk writer failure")
        original(self, event)

    monkeypatch.setattr(StreamingCsvSink, "write", failing)
    with pytest.raises(OSError, match="risk writer failure"):
        run_bounded_simulation(
            replace(cfg(), hmm=settings(), record_dir=tmp_path / "runs"), tape(tmp_path / "input.ndjson")
        )
    sentinels = list((tmp_path / "runs").rglob("_INCOMPLETE.json"))
    assert len(sentinels) == 1
    assert (sentinels[0].parent / "regime_risk.csv.partial").exists()
    assert not (sentinels[0].parent / "manifest.json").exists()


def test_quantity_presentation_ignores_callers_decimal_context():
    from decimal import localcontext

    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject.observe(row(0, inventory=7))
    subject.observe(row(3, inventory=-4))
    subject.observe(row(10, inventory=0))
    expected = subject.summary()
    with localcontext() as context:
        context.prec = 2
        assert subject.summary() == expected


def test_explicit_checkpoint_after_schema_v3_finish_preserves_exact_risk_anchor(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    subject = SimulationEngine(configuration)
    subject.run(path)
    checkpoint = tmp_path / "final_state.json"
    subject.write_state_checkpoint(path, checkpoint)
    restored = SimulationEngine(configuration)
    restored._load_state_checkpoint(path, checkpoint)
    assert restored.hmm_risk.checkpoint() == subject.hmm_risk.checkpoint()
    assert restored.hmm_risk._anchor["reason"] == "finish"
