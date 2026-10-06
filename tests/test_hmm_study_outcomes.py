from __future__ import annotations

import copy
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256

import pytest

from lob_sim.regime.execution import EXECUTION_FIELDS, RegimeExecutionAudit, iter_execution_rows
from lob_sim.regime.study_outcomes import read_execution_periods, compare_execution_periods
from lob_sim.regime.study_periods import PERIOD_NS
from lob_sim.sim.export import (
    TRADE_AUDIT_FIELDS,
    FILL_FLOAT_FIELDS,
    FILL_INT_FIELDS,
    FILL_JSON_FIELDS,
    FILL_OPTIONAL_FIELDS,
    iter_fill_audit_rows,
)
from lob_sim.sim.metrics import FILL_AUDIT_CHAIN_DOMAIN, advance_audit_digest
from lob_sim.sim.sinks import StreamingCsvSink
from test_hmm_execution import stage, fill_row
from test_hmm_dataset import SECOND, WALL


@pytest.fixture
def bundle(tmp_path):
    execution_path, trades_path = tmp_path / "execution.csv", tmp_path / "trades.csv"
    execution_sink = StreamingCsvSink(execution_path, EXECUTION_FIELDS)
    trade_sink = StreamingCsvSink(trades_path, TRADE_AUDIT_FIELDS)
    subject = RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (100, 1000), 4, execution_sink)
    entries = []
    for ordinal, (seconds, qty, fee) in enumerate(((5, "1", "0.1"), (60, "9", "-0.2"), (119, "2", "0.3")), 1):
        fill = fill_row(qty=qty, fee=fee)
        fill.update(ts_local=WALL / SECOND + seconds, queue_ahead_lots=0 if ordinal == 1 else None)
        attribution = subject.on_fill(ordinal, fill, subject.at_fill("order-1", stage(0, seconds * SECOND)))
        entry = {**fill, "hmm_attribution": attribution, "deadline_ts": fill["ts_local"] + 0.1}
        entries.append(entry)
        trade = {}
        for key in TRADE_AUDIT_FIELDS:
            trade[key] = (
                None
                if key in FILL_OPTIONAL_FIELDS
                else {}
                if key in FILL_JSON_FIELDS
                else 0.0
                if key in FILL_FLOAT_FIELDS
                else 0
                if key in FILL_INT_FIELDS
                else "0"
            )
        trade.update(
            fill,
            price="100",
            notional=str(100 * int(qty)),
            contract_multiplier="1",
            fee_currency="USDT",
            queue_ahead_lots=0,
        )
        trade_sink.write(trade)
    # A resolution in a later minute belongs to its original integer fill
    # minute, not to the observation minute or a rounded float fill timestamp.
    subject.on_markout(entries[0], 100, markout=Decimal("-2"), observed_ts=WALL / SECOND + 65)
    subject.on_markout(entries[0], 1000, markout=None, observed_ts=WALL / SECOND + 65, invalid_reason="gap")
    subject.on_markout(entries[1], 100, markout=Decimal("4"), observed_ts=WALL / SECOND + 65)
    subject.on_markout(
        {**entries[1], "deadline_ts": entries[1]["ts_local"] + 1},
        1000,
        markout=Decimal("-1"),
        observed_ts=WALL / SECOND + 65,
    )
    execution_sink.close()
    trade_sink.close()
    execution = subject.summary()
    digest = sha256(FILL_AUDIT_CHAIN_DOMAIN.encode()).digest()
    for fill in iter_fill_audit_rows(trades_path):
        digest = advance_audit_digest(digest, fill)
    economics = {
        "symbol": "BTCUSDT",
        "model_sha256": "a" * 64,
        "grid": ["1", "1", "1"],
        "clock_basis": "receive_nanoseconds",
        "parents": {
            "execution_trace_count": execution["trace_count"],
            "execution_trace_sha256": execution["trace_sha256"],
            "global_fill_count": 3,
            "global_fill_sha256": digest.hex(),
        },
    }
    periods = [
        {"utc_start_ns": WALL + i * PERIOD_NS, "period_ns": PERIOD_NS, "epoch": [0, 0, 0], "excluded_reason": None}
        for i in range(3)
    ]
    return execution_path, trades_path, execution, economics, periods


def reduce(bundle, **changes):
    execution_path, trades_path, execution, economics, periods = bundle
    return read_execution_periods(
        execution_path,
        trades_path,
        **(
            {
                "execution_summary": execution,
                "economic_summary": economics,
                "risk_periods": periods,
                "span": {"wall_offset_ns": WALL, "clock_basis": "receive_nanoseconds"},
            }
            | changes
        ),
    )


def test_native_audit_reducer_matches_independent_batch_sufficient_statistics(bundle):
    periods = reduce(bundle)
    assert [int(p["additive"]["fill_count"]) for p in periods] == [1, 2, 0]
    assert [Fraction(p["additive"]["fees_quote"]) for p in periods] == [Fraction("0.1"), Fraction("0.1"), 0]
    assert [int(p["additive"]["turnover_quote"]) for p in periods] == [100, 1100, 0]
    assert periods[0]["ratios"]["signed_markout_100ms"] == {"numerator_rational": "-2", "denominator_rational": "1"}
    assert periods[1]["ratios"]["signed_markout_100ms"] == {"numerator_rational": "36", "denominator_rational": "9"}
    assert periods[1]["markout_census"]["100"] == {
        "resolved": 1,
        "invalidated": 0,
        "unresolved": 1,
        "resolved_quantity_rational": "9",
    }
    assert periods[0]["markout_census"]["1000"]["invalidated"] == 1
    assert periods[0]["ratios"]["signed_markout_1000ms"] == {"numerator_rational": "0", "denominator_rational": "0"}
    assert periods[2]["excluded_reason"] is None  # No fills is not invalid data.
    assert periods[2]["ratios"]["coverage_100ms"] == {"numerator_rational": "0", "denominator_rational": "0"}
    assert periods[0]["ratios"]["mean_queue_ahead_lots"] == {"numerator_rational": "0", "denominator_rational": "1"}
    assert periods[1]["ratios"]["mean_queue_ahead_lots"]["denominator_rational"] == "0"
    with localcontext() as context:
        context.prec = 2
        assert sum(Fraction(p["additive"]["fees_quote"]) for p in periods) == Fraction("0.2")


def test_fill_minute_uses_integer_clock_not_legacy_float_timestamp(bundle, monkeypatch):
    import lob_sim.regime.study_outcomes as module

    original = module.iter_execution_rows

    def mutated(path):
        for row in original(path):
            # Even a semantically relevant field not used for minute assignment
            # must stay bound to the chain; do not accept a changed consumed row.
            row["fill_ts_local"] += 1000
            yield row

    monkeypatch.setattr(module, "iter_execution_rows", mutated)
    with pytest.raises(ValueError, match="consumed audit"):
        reduce(bundle)


def test_verified_stream_mutation_cannot_change_cash_flow(bundle, monkeypatch):
    import lob_sim.regime.study_outcomes as module

    original = module.iter_fill_audit_rows

    def mutated(path):
        for fill in original(path):
            fill["price"] = "101"
            fill["notional"] = str(Fraction(fill["qty"]) * 101)
            yield fill

    monkeypatch.setattr(module, "iter_fill_audit_rows", mutated)
    with pytest.raises(ValueError, match="consumed audit"):
        reduce(bundle)


def test_empty_period_table_still_verifies_entire_audit_and_trade_stream(bundle, monkeypatch):
    assert reduce(bundle, risk_periods=[]) == ()
    import lob_sim.regime.study_outcomes as module

    original = module.iter_execution_rows

    def missing_tail(path):
        rows = list(original(path))
        yield from rows[:-1]

    monkeypatch.setattr(module, "iter_execution_rows", missing_tail)
    with pytest.raises(ValueError, match="consumed audit"):
        reduce(bundle, risk_periods=[])


@pytest.mark.parametrize(
    "fault", ["model", "global_count", "execution_chain", "offset", "clock", "duplicate", "width", "cap"]
)
def test_parent_or_clock_mismatch_fails_closed(bundle, monkeypatch, fault):
    economics, periods = copy.deepcopy(bundle[3]), copy.deepcopy(bundle[4])
    kwargs = {}
    if fault == "model":
        economics["model_sha256"] = "b" * 64
    if fault == "global_count":
        economics["parents"]["global_fill_count"] = 4
    if fault == "execution_chain":
        economics["parents"]["execution_trace_sha256"] = "b" * 64
    if fault == "offset":
        kwargs["span"] = {"wall_offset_ns": None, "clock_basis": "receive_nanoseconds"}
    if fault == "clock":
        economics["clock_basis"] = "legacy_compatibility_nanoseconds"
    if fault == "duplicate":
        periods.append(periods[0])
    if fault == "width":
        periods[0]["period_ns"] = SECOND
    if fault == "cap":
        monkeypatch.setattr("lob_sim.regime.study_outcomes.MAX_PERIODS", 2)
    with pytest.raises(ValueError):
        reduce(bundle, economic_summary=economics, risk_periods=periods, **kwargs)


def test_comparison_has_pooled_quantity_estimand_and_separate_coverage(bundle):
    left, right = reduce(bundle), list(copy.deepcopy(reduce(bundle)))
    for period in right:
        for ratio in period["ratios"].values():
            ratio["numerator_rational"] = "0"
    report = compare_execution_periods(left, right, "b" * 64, horizons=(100, 1000), replicates=10)
    metrics = report["metrics"]
    signed = metrics["signed_markout_100ms"]["sensitivities"]["30"]
    assert signed["estimate"] == pytest.approx(3.4)  # (-2*1 + 4*9)/(1+9), not (-2+4)/2.
    assert signed["left_denominator_sum"] == 10
    assert signed["zero_activity_periods"] == {"left": 1, "right": 1}
    assert metrics["coverage_100ms"]["sensitivities"]["30"]["estimate"] == pytest.approx(2 / 3)
    assert all(value["interval"] is None for metric in metrics.values() for value in metric["sensitivities"].values())
    assert "not marked net PnL" in report["contract"]["scope"]
    assert "not quote fill rate" in metrics["fill_count"]["unit"]
    assert not report["claim_ready"]
    with pytest.raises(ValueError, match="same UTC"):
        compare_execution_periods(left, right[:-1], "b" * 64, horizons=(100, 1000))


def test_common_invalid_minute_is_excluded_without_hiding_outcome_census(bundle):
    bundle[4][1]["excluded_reason"] = "invalid trade stream"
    periods = reduce(bundle)
    assert periods[1]["additive"]["fill_count"] == "2"
    assert periods[1]["markout_census"]["100"]["unresolved"] == 1
    report = compare_execution_periods(periods, periods, "b" * 64, horizons=(100, 1000), replicates=10)
    metric = report["metrics"]["signed_markout_100ms"]["sensitivities"]["30"]
    assert metric["excluded_period_counts"] == {"invalid trade stream": 1}
    assert metric["eligible_period_count"] == 2
    assert metric["left_denominator_sum"] == 1


def test_parser_iterator_reproduces_every_verified_execution_row(bundle):
    assert len(list(iter_execution_rows(bundle[0]))) == bundle[2]["trace_count"]
