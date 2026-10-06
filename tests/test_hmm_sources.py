"""Independent batch census and adversarial storage checks for source diagnostics."""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

import pytest

from lob_sim.regime.execution import (
    CHAIN_DOMAIN,
    EXECUTION_FIELDS,
    FILL_SOURCE_GROUPS,
    PHASES,
    QUEUE_FIELDS,
    RegimeExecutionAudit,
    capture_stage,
    format_execution_report,
    verify_execution_trace,
)
from lob_sim.regime.validation import canonical_json
from lob_sim.sim.checkpoint import decode, encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.sinks import StreamingCsvSink
from lob_sim.oracle import Checkpoint, read_checkpoint, write_checkpoint
from test_hmm_dataset import cfg
from test_hmm_execution import execution_tape, fill_row, stage
from test_hmm_observation import Recorder, settings, without_diagnostics


def many_state_stage(k, state, clock):
    signal = stage(0, clock)
    posterior = [0.1 / (k - 1)] * k
    posterior[state] = 0.9
    signal.update(
        raw_map_state=state,
        active_state=state,
        posterior=posterior,
        normalized_entropy=-sum(p * math.log(p) for p in posterior) / math.log(k),
    )
    return capture_stage(signal, clock)


def horizon_entry(frozen, fill, horizon):
    return {
        "hmm_attribution": frozen,
        **{key: fill[key] for key in ("symbol", "side", "order_id", "qty", "qty_lots", "fill_source", "ts_local")},
        "deadline_ts": fill["ts_local"] + horizon / 1000,
    }


def serialized(path, rows):
    sink = StreamingCsvSink(path, EXECUTION_FIELDS)
    for row in rows:
        sink.write(row)
    sink.close()


def rehash(rows, summary):
    changed = copy.deepcopy(summary)
    digest = sha256(CHAIN_DOMAIN).hexdigest()
    for row in rows:
        digest = sha256(bytes.fromhex(digest) + canonical_json(row).encode()).hexdigest()
    changed["trace_sha256"] = digest
    changed["trace_count"] = len(rows)
    return changed


@pytest.mark.parametrize("k", [2, 3, 4, 5])
@pytest.mark.parametrize("seed", [11, 42, 1001])
def test_all_source_cells_match_independent_batch_reduction(k, seed, tmp_path):
    rng, sink = random.Random(seed), Recorder()
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", k, (100, 1000), 2, sink)
    for ordinal in range(1, 61):
        states = [rng.randrange(k) for _ in PHASES]
        order_id = f"order-{ordinal}"
        subject.accept_order(
            order_id, many_state_stage(k, states[0], ordinal * 3), many_state_stage(k, states[1], ordinal * 3 + 1)
        )
        source = ("agg_trade", "depth_update", "taker_order", "future_source", None)[ordinal % 5]
        qty_lots = rng.randrange(1, 5)
        fill = {
            **fill_row(
                qty=str(qty_lots), fee="-0.01" if ordinal % 3 else "0.02", spread=None if ordinal % 4 else "-0.4"
            ),
            "order_id": order_id,
            "fill_source": source,
            "maker": source != "taker_order",
            "queue_ahead_lots": 0 if source == "taker_order" else ordinal % 4,
            "queue_trajectory": {},
        }
        if source == "taker_order":
            fill["queue_trajectory"] = {
                "visible_level_before_lots": qty_lots + 2,
                "visible_level_after_lots": 2,
                "fill_lots": qty_lots,
                "remaining_order_lots_after_fill": 0,
            }
        elif ordinal % 2:
            fill["queue_trajectory"] = {
                "queue_ahead_before_trigger_lots": fill["queue_ahead_lots"] + 5,
                "queue_ahead_at_fill_lots": fill["queue_ahead_lots"],
                "queue_consumed_before_fill_lots": 5,
                "public_consumption_trigger_lots": qty_lots + 5,
                "fill_lots": qty_lots,
                "remaining_order_lots_after_fill": 0,
            }
        if ordinal % 7 == 0:
            fill.pop("queue_ahead_lots")
            fill.pop("queue_trajectory")
        frozen = subject.on_fill(
            ordinal, fill, subject.at_fill(order_id, many_state_stage(k, states[2], ordinal * 3 + 2))
        )
        for horizon in subject.horizons:
            if ordinal % 6 == 0:
                continue  # Right-censored, not a zero-valued markout.
            invalid = ordinal % 5 == 0
            subject.on_markout(
                horizon_entry(frozen, fill, horizon),
                horizon,
                markout=None if invalid else Decimal(str(ordinal % 9 - 4)),
                observed_ts=1.0 + horizon / 1000 + 0.125,
                invalid_reason="book_gap" if invalid else None,
            )
        subject.release_order(order_id)
    summary = subject.summary()
    for phase in PHASES:
        for label, sources in summary["conditioned_by_source"][phase].items():
            for source, cell in sources.items():
                selected = [
                    row
                    for row in sink.rows
                    if row[f"{phase}_label"] == label
                    and (row["fill_source"] if row["fill_source"] in FILL_SOURCE_GROUPS else "OTHER") == source
                ]
                fills = [row for row in selected if row["event_type"] == "fill"]
                assert cell["fill_count"] == len(fills)
                assert cell["qty_lots"] == sum(row["qty_lots"] for row in fills)
                for field in ("qty", "fee", "spread_capture_value"):
                    assert Decimal(cell[field]) == sum(
                        (Decimal(row[field]) for row in fills if row[field] is not None), Decimal(0)
                    )
                ahead = [row["queue_ahead_lots"] for row in fills if row["queue_ahead_lots"] is not None]
                assert cell["queue_ahead_samples"] == len(ahead)
                assert cell["mean_modeled_queue_ahead_lots"] == (sum(ahead) / len(ahead) if ahead else None)
                assert cell["queue_trajectory_samples"] == sum(bool(row["queue_trajectory"]) for row in fills)
                for field in QUEUE_FIELDS:
                    assert cell["queue_trajectory_lots"][field] == sum(
                        (row["queue_trajectory"] or {}).get(field, 0) for row in fills
                    )
                    assert cell["queue_trajectory_field_samples"][field] == sum(
                        field in (row["queue_trajectory"] or {}) for row in fills
                    )
                for horizon, stats in cell["markouts"].items():
                    rows = [
                        row for row in selected if row["event_type"] == "markout" and row["horizon_ms"] == int(horizon)
                    ]
                    resolved = [row for row in rows if row["status"] == "resolved"]
                    qty = sum((Decimal(row["qty"]) for row in resolved), Decimal(0))
                    weighted = sum((Decimal(row["qty"]) * Decimal(row["markout"]) for row in resolved), Decimal(0))
                    assert stats["resolved_samples"] == len(resolved)
                    assert stats["invalidated_samples"] == len(rows) - len(resolved)
                    assert stats["unresolved_samples"] == len(fills) - len(rows)
                    assert stats["mean_signed_markout"] == (float(weighted / qty) if qty else None)
                    assert stats["adverse_rate"] == (
                        sum(Decimal(row["markout"]) < 0 for row in resolved) / len(resolved) if resolved else None
                    )
                    assert stats["coverage"] == (len(resolved) / len(fills) if fills else None)
    assert subject.validated_copy(subject.checkpoint()).checkpoint() == subject.checkpoint()
    path = tmp_path / "audit.csv"
    serialized(path, sink.rows)
    verify_execution_trace(path, summary)


@pytest.mark.parametrize("source", ["agg_trade", "depth_update", "taker_order", "future_source", None])
def test_source_frozen_partial_fills_and_missing_queue_are_not_backfilled(source):
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100,), 2)
    subject.accept_order("order-1", stage(0, 1), stage(1, 2))
    frozen = subject.on_fill(1, {**fill_row(), "fill_source": source}, subject.at_fill("order-1", stage(1, 3)))
    changed = {**fill_row(), "fill_source": "depth_update" if source != "depth_update" else "agg_trade"}
    before = subject.checkpoint()
    with pytest.raises(ValueError, match="frozen fill source"):
        subject.on_markout(horizon_entry(frozen, changed, 100), 100, markout=Decimal(-1), observed_ts=1.1)
    assert subject.checkpoint() == before
    cell = subject.summary()["conditioned_by_source"]["decision"]["STATE_0"][
        source if source in FILL_SOURCE_GROUPS else "OTHER"
    ]
    assert cell["queue_ahead_samples"] == 0 and cell["mean_modeled_queue_ahead_lots"] is None
    assert cell["queue_trajectory_coverage"] == 0.0


@pytest.mark.parametrize(
    "trajectory",
    [
        {"fill_lots": 3},
        {"queue_ahead_at_fill_lots": 1},
        {"queue_ahead_before_trigger_lots": 5, "queue_ahead_at_fill_lots": 0, "queue_consumed_before_fill_lots": 4},
        {"visible_level_before_lots": 10, "visible_level_after_lots": 7},
        {"private_fifo": 1},
        {"fill_lots": True},
        {"fill_lots": -1},
    ],
)
def test_corrupt_queue_trajectory_rejected_before_audit_mutation(trajectory):
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 2)
    before = subject.checkpoint()
    with pytest.raises(ValueError):
        subject.on_fill(
            1, {**fill_row(), "queue_ahead_lots": 0, "queue_trajectory": trajectory}, subject.at_fill(None, stage(1, 3))
        )
    assert subject.checkpoint() == before


@pytest.mark.parametrize(
    "corruption",
    [
        "fill_count",
        "fee",
        "queue_sum",
        "field_count",
        "resolved",
        "source_key",
        "bool_count",
        "cross_phase",
        "old_schema",
    ],
)
def test_source_checkpoint_marginals_fail_closed(corruption):
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100,), 2)
    subject.on_fill(1, fill_row(), subject.at_fill(None, stage(1, 3)))
    original = subject.checkpoint()
    changed = copy.deepcopy(original)
    cell = changed["by_source"]["decision"]["UNAVAILABLE"]["agg_trade"]
    if corruption == "fill_count":
        cell["fill_count"] += 1
    elif corruption == "fee":
        cell["fee"] = "1"
    elif corruption == "queue_sum":
        cell["queue_ahead_lots_sum"] = 100
    elif corruption == "field_count":
        cell["queue_trajectory_field_samples"]["fill_lots"] = 1
    elif corruption == "resolved":
        cell["markouts"]["100"]["resolved_samples"] = 1
    elif corruption == "source_key":
        changed["by_source"]["decision"]["UNAVAILABLE"]["made_up"] = cell
    elif corruption == "bool_count":
        cell["fill_count"] = True
    elif corruption == "cross_phase":
        changed["conditioned"]["decision"]["UNAVAILABLE"]["fee"] = "1"
        cell["fee"] = "1"
    else:
        changed["schema_version"] = "lob_sim.hmm_execution_checkpoint.v2"
    with pytest.raises(ValueError):
        subject.validated_copy(changed)
    assert subject.checkpoint() == original


@pytest.mark.parametrize(
    "corruption",
    ["source_swap", "duplicate_horizon", "frozen_stage", "too_early", "queue_rewrite", "summary_only", "pending_cap"],
)
def test_internally_rehashed_execution_rows_cannot_forge_source_or_horizon_proof(tmp_path, corruption):
    sink = Recorder()
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100,), 2, sink, max_pending_markouts=1)
    fill = {**fill_row(), "queue_ahead_lots": 0, "queue_trajectory": {"fill_lots": 2}}
    frozen = subject.on_fill(1, fill, subject.at_fill(None, stage(1, 3)))
    subject.on_markout(horizon_entry(frozen, fill, 100), 100, markout=Decimal(-2), observed_ts=1.125)
    rows, summary = copy.deepcopy(sink.rows), subject.summary()
    if corruption == "source_swap":
        rows[1]["fill_source"] = "depth_update"
    elif corruption == "duplicate_horizon":
        rows.append(copy.deepcopy(rows[1]))
    elif corruption == "frozen_stage":
        rows[1]["pre_fill"] = stage(0, 3)
        rows[1]["pre_fill_label"] = "STATE_0"
    elif corruption == "too_early":
        rows[1]["observed_ts"] = 1.05
        rows[1]["actual_lag_seconds"] = 0.05
    elif corruption == "queue_rewrite":
        rows[0]["queue_trajectory"]["fill_lots"] = 999
    elif corruption == "summary_only":
        summary["conditioned_by_source"]["pre_fill"]["STATE_1"]["agg_trade"]["fee"] = "999"
    else:
        rows.insert(1, {**copy.deepcopy(rows[0]), "fill_id": 2})
    serialized(tmp_path / "audit.csv", rows)
    with pytest.raises(ValueError):
        verify_execution_trace(tmp_path / "audit.csv", rehash(rows, summary))


@pytest.mark.parametrize("corruption", ["source", "missing_source", "source_pending_census"])
def test_native_source_checkpoint_rejected_before_core_mutation(tmp_path, corruption):
    tape = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    checkpoint = tmp_path / "checkpoint.json"
    SimulationEngine(configuration).run(tape, checkpoint_path=checkpoint, stop_after_records=14)
    original = read_checkpoint(checkpoint)
    state = copy.deepcopy(original.state)
    core = decode(state["engine"])
    pending = core["metrics"]["_pending_markout_horizons"]
    assert pending
    if corruption == "source":
        pending[0]["hmm_attribution"]["fill_source"] = "depth_update"
    elif corruption == "missing_source":
        del pending[0]["hmm_attribution"]["fill_source"]
    else:
        pending[0]["fill_source"] = pending[0]["hmm_attribution"]["fill_source"] = "depth_update"
    state["engine"] = encode(core)
    write_checkpoint(
        checkpoint,
        Checkpoint.create(original.event_index, original.logical_time, state, schema_version=original.schema_version),
    )
    resumed = SimulationEngine(configuration)
    before = encode(resumed._checkpoint_mutable_state())
    with pytest.raises(ValueError):
        resumed.run(tape, resume_from=checkpoint)
    assert encode(resumed._checkpoint_mutable_state()) == before


def test_native_queue_trace_and_report_leave_core_outputs_unchanged(tmp_path):
    tape = execution_tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), record_dir=tmp_path / "runs")
    base = SimulationEngine(configuration)
    base.run(tape)
    sink = Recorder()
    observed = SimulationEngine(replace(configuration, hmm=settings()), regime_execution_sink=sink)
    observed.run(tape)
    assert without_diagnostics(observed.event_trace) == base.event_trace
    assert observed.metrics.get_summary(observed._books, observed._specs) == base.metrics.get_summary(
        base._books, base._specs
    )
    rows = [row for row in sink.rows if row["event_type"] == "fill"]
    assert len(rows) == len(observed.metrics.fills_log)
    for row, original in zip(rows, observed.metrics.fills_log, strict=True):
        assert row["queue_trajectory"] == original["queue_trajectory"]
        assert row["queue_ahead_lots"] == original["queue_ahead_lots"]
    files, summary = run_bounded_simulation(replace(configuration, hmm=settings()), tape)
    verify_execution_trace(files["regime_execution"], summary["hmm_execution"])
    report = format_execution_report(summary["hmm_execution"])
    assert "not quote acceptance" in report and "never private participant FIFO" in report
    assert "agg_trade" in report and "actual lag mean=" in report


def test_source_cardinality_is_fixed_for_unknown_source_names():
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 2)
    for ordinal in range(1, 2001):
        subject.on_fill(
            ordinal, {**fill_row(), "fill_source": f"unrecognized-{ordinal}"}, subject.at_fill(None, stage(1, 3))
        )
    assert set(subject.checkpoint()["by_source"]["pre_fill"]["STATE_1"]) == set(FILL_SOURCE_GROUPS)
    assert subject.summary()["conditioned_by_source"]["pre_fill"]["STATE_1"]["OTHER"]["fill_count"] == 2000
    assert len(json.dumps(subject.checkpoint())) < 100000


def test_verifier_preserves_submillisecond_primary_deadline(tmp_path):
    sink = Recorder()
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100,), 2, sink)
    fill = fill_row()
    frozen = subject.on_fill(1, fill, subject.at_fill(None, stage(1, 3)))
    entry = {**horizon_entry(frozen, fill, 100), "deadline_ts": 1.1004}
    subject.on_markout(entry, 100, markout=Decimal(1), observed_ts=1.1005)
    serialized(tmp_path / "audit.csv", sink.rows)
    verify_execution_trace(tmp_path / "audit.csv", subject.summary())


def test_verifier_union_of_pending_horizons_uses_all_core_capacity(tmp_path):
    sink = Recorder()
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100, 1000, 5000), 2, sink, max_pending_markouts=1)
    fill = fill_row()
    first = subject.on_fill(1, fill, subject.at_fill(None, stage(1, 3)))
    for horizon in (100, 1000):
        subject.on_markout(horizon_entry(first, fill, horizon), horizon, markout=Decimal(1), observed_ts=2.0)
    later = {**fill, "ts_local": 2.0}
    subject.on_fill(2, later, subject.at_fill(None, stage(1, 4)))
    serialized(tmp_path / "audit.csv", sink.rows)
    verify_execution_trace(tmp_path / "audit.csv", subject.summary())


@pytest.mark.parametrize("corruption", ["cell_count", "rate", "global_count", "transition_count", "note", "claim"])
def test_summary_boolean_values_cannot_pass_as_integer_counts_or_rates(tmp_path, corruption):
    sink = Recorder()
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 2, sink)
    subject.on_fill(1, fill_row(), subject.at_fill(None, stage(1, 3)))
    summary = subject.summary()
    if corruption == "cell_count":
        summary["conditioned_by_source"]["pre_fill"]["STATE_1"]["agg_trade"]["fill_count"] = True
    elif corruption == "rate":
        summary["conditioned_by_source"]["pre_fill"]["STATE_1"]["agg_trade"]["spread_capture_coverage"] = True
    elif corruption == "global_count":
        summary["fill_count"] = True
    elif corruption == "transition_count":
        summary["fill_transition_counts"]["UNAVAILABLE"]["STATE_1"] = True
    elif corruption == "note":
        summary["queue_note"] = "Private FIFO measured exactly."
    else:
        summary["claim_ready"] = True
    serialized(tmp_path / "audit.csv", sink.rows)
    with pytest.raises(ValueError):
        verify_execution_trace(tmp_path / "audit.csv", summary)
