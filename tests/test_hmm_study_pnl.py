from __future__ import annotations

import random
from dataclasses import replace
from fractions import Fraction

import pytest

from lob_sim.regime.economics import EconomicPoint
from lob_sim.regime.study_pnl import EconomicClockSampler, compare_pnl_periods
from lob_sim.regime.study_periods import PERIOD_NS, MAX_PERIODS
from test_hmm_dataset import SECOND, WALL


def periods(count=3):
    return [
        {
            "utc_start_ns": WALL + (i + 1) * PERIOD_NS,
            "period_ns": PERIOD_NS,
            "epoch": [0, 0, 0],
            "excluded_reason": None,
        }
        for i in range(count)
    ]


def sampler(period_table=None):
    return EconomicClockSampler(
        periods() if period_table is None else period_table,
        {
            "first_wall_ns": WALL,
            "last_wall_ns": WALL + 240 * SECOND,
            "wall_offset_ns": WALL,
            "clock_basis": "receive_nanoseconds",
        },
    )


def point(seconds, cash=0, inventory=0, *, fee=0, mid=200, until=1000):
    return EconomicPoint(
        seconds * SECOND,
        Fraction(cash),
        Fraction(fee),
        inventory,
        Fraction(1),
        mid,
        until * SECOND if mid is not None else None,
    )


def test_exact_time_fill_and_price_update_belong_to_starting_period_not_prior_endpoint():
    subject = sampler()
    for item in (
        point(0),
        point(60),  # A before-market row at the same time as the fill.
        point(60, -100, 1, fee="0.1"),
        point(70, -100, 1, fee="0.1", mid=220),
        point(120, 20, 0, fee="0.3", mid=999),
        point(180, 20, 0, fee="0.3"),
        point(240, 20, 0, fee="0.3"),
    ):
        subject.observe(item)
    actual = subject.periods_result()
    assert [p["net_marked_delta_quote_rational"] for p in actual] == ["99/10", "49/5", "0"]
    assert [p["gross_marked_delta_quote_rational"] for p in actual] == ["10", "10", "0"]
    assert [p["fees_delta_quote_rational"] for p in actual] == ["1/10", "1/5", "0"]
    assert actual[0]["start_anchor_logical_ns"] == 0
    assert actual[0]["end_anchor_logical_ns"] == 70 * SECOND


@pytest.mark.parametrize("seed", range(10))
def test_clock_equity_deltas_match_independent_batch_transaction_oracle(seed):
    rng = random.Random(seed)
    records = [(0, 0, 100, Fraction(0))]  # Known opening state, no fill.
    for time in range(1, 241):
        # Multiple events at exact boundary times expose same-time accounting
        # errors that a single evenly spaced sample cannot catch.
        for _ in range(3 if time % 60 == 0 else 1):
            records.append(
                (time, rng.choice([-2, -1, 0, 1, 2]), rng.randrange(80, 121), Fraction(rng.randrange(-5, 11), 100))
            )
    subject = sampler()
    cash, inventory, fees = Fraction(0), 0, Fraction(0)
    for seconds, signed_lots, price, fee in records:
        cash -= signed_lots * price
        inventory += signed_lots
        fees += fee if signed_lots else 0
        subject.observe(point(seconds, cash, inventory, fee=fees, mid=2 * price))
    actual = subject.periods_result()

    def batch_equity(endpoint):
        prefix = [r for r in records if r[0] < endpoint]
        transaction_cash = sum(-q * price for _, q, price, _ in prefix)
        position = sum(q for _, q, _, _ in prefix)
        total_fees = sum((f for _, q, _, f in prefix if q), Fraction(0))
        gross = transaction_cash + position * prefix[-1][2]
        return gross, gross - total_fees, total_fees

    for index, period in enumerate(actual):
        a, b = batch_equity((index + 1) * 60), batch_equity((index + 2) * 60)
        assert period["excluded_reason"] is None
        assert Fraction(period["gross_marked_delta_quote_rational"]) == b[0] - a[0]
        assert Fraction(period["net_marked_delta_quote_rational"]) == b[1] - a[1]
        assert Fraction(period["fees_delta_quote_rational"]) == b[2] - a[2]


@pytest.mark.parametrize("until,expected", [(120, "10"), (119, None)])
def test_expiry_is_a_left_limit_not_stale_price_extrapolation(until, expected):
    subject = sampler(periods(1))
    subject.observe(point(0, -100, 1))
    subject.observe(point(70, -100, 1, mid=220, until=until))
    subject.observe(point(120, -100, 1, mid=1000))  # Later price never rescues a stale left limit.
    actual = subject.periods_result()[0]
    assert actual["net_marked_delta_quote_rational"] == expected
    assert (actual["end_valuation_reason"] is not None) == (expected is None)


def test_flat_equity_needs_no_mark_but_risk_invalidity_cannot_bridge_returns():
    table = periods(1)
    table[0]["excluded_reason"] = "epoch crossing"
    subject = sampler(table)
    subject.observe(point(0, 10, mid=None))
    subject.observe(point(90, 20, mid=None, fee="0.2"))
    subject.observe(point(120, 30, mid=None))
    actual = subject.periods_result()[0]
    assert actual["start_valuation_reason"] is actual["end_valuation_reason"] is None
    assert actual["gross_marked_delta_quote_rational"] is actual["net_marked_delta_quote_rational"] is None
    assert actual["fees_delta_quote_rational"] == "1/5"
    assert actual["excluded_reason"] == "epoch crossing"


def test_missing_endpoint_and_capture_tail_do_not_project_another_period():
    subject = sampler(periods(1))
    subject.observe(point(60))  # No strictly-earlier boundary for the start.
    subject.observe(point(119))  # No observation at or beyond the end.
    actual = subject.periods_result()[0]
    assert actual["start_anchor_logical_ns"] is actual["end_anchor_logical_ns"] is None
    assert actual["net_marked_delta_quote_rational"] is None
    assert actual["start_valuation_reason"] and actual["end_valuation_reason"]


def test_future_point_mutation_cannot_rewrite_completed_period_and_regression_rejected():
    subject = sampler(periods(1))
    subject.observe(point(0))
    subject.observe(point(90, 5))
    subject.observe(point(120, 900))
    before = subject.periods_result()
    subject.observe(point(120, -900))
    subject.observe(point(121, 1000))
    assert subject.periods_result() == before
    with pytest.raises(ValueError, match="regressing"):
        subject.observe(point(119))


def test_pairing_and_short_block_uncertainty_are_explicit():
    subject = sampler(periods(1))
    subject.observe(point(0))
    subject.observe(point(90, 5))
    subject.observe(point(120))
    left = subject.periods_result()
    right = ({**left[0], "net_marked_delta_quote_rational": "2", "gross_marked_delta_quote_rational": "2"},)
    report = compare_pnl_periods(left, right, "a" * 64, replicates=10)
    assert report["metrics"]["net_marked_delta_quote_rational"]["30"]["estimate"] == 3
    assert all(r["interval"] is None for values in report["metrics"].values() for r in values.values())
    assert "not whole-path drawdown" in report["contract"]["estimand"]
    with pytest.raises(ValueError, match="same UTC"):
        compare_pnl_periods(left, (), "a" * 64)


def test_large_integer_clock_is_not_float_retimed():
    subject = sampler(periods(1))
    # The most recent pre-boundary point differs by only one nanosecond.
    subject.observe(replace(point(60), logical_ns=60 * SECOND - 1, cash_quote=Fraction(7)))
    subject.observe(point(60, 99))
    subject.observe(point(120, 999))
    assert subject.periods_result()[0]["net_marked_delta_quote_rational"] == "92"


@pytest.mark.parametrize("fault", ["duplicate", "reorder", "off_grid", "partial", "boolean", "non_numeric"])
def test_malformed_or_uncaptured_clock_grid_cannot_be_sampled(fault):
    table = periods()
    if fault == "duplicate":
        table[1] = dict(table[0])
    elif fault == "reorder":
        table.reverse()
    elif fault == "off_grid":
        table[0]["utc_start_ns"] += 1
    elif fault == "partial":
        table[-1]["utc_start_ns"] += 2 * PERIOD_NS
    elif fault == "boolean":
        table[0]["utc_start_ns"] = True
    else:
        table[0]["utc_start_ns"] = "not a clock"
    with pytest.raises(ValueError, match="unique ordered complete"):
        sampler(table)


def test_offline_cap_and_moving_clock_fail_closed_and_metadata_cannot_be_mutated():
    with pytest.raises(ValueError, match="cap"):
        sampler(periods(1) * (MAX_PERIODS + 1))
    with pytest.raises(ValueError, match="stable"):
        EconomicClockSampler(periods(1), {"wall_offset_ns": None})
    table = periods(1)
    subject = sampler(table)
    table[0]["epoch"][0] = 123
    for item in (point(0), point(90, 10), point(120)):
        subject.observe(item)
    before = subject.periods_result()
    assert before[0]["epoch"] == [0, 0, 0]
    before[0]["epoch"][1] = 456
    assert subject.periods_result()[0]["epoch"] == [0, 0, 0]


def test_long_valid_pnl_has_repeatable_clock_intervals_but_invalid_minutes_split_blocks():
    table = periods(180)
    subject = EconomicClockSampler(
        table,
        {
            "first_wall_ns": WALL,
            "last_wall_ns": WALL + 181 * PERIOD_NS,
            "wall_offset_ns": WALL,
            "clock_basis": "receive_nanoseconds",
        },
    )
    subject.observe(point(0))
    cash = Fraction(0)
    for index in range(1, 181):
        # Quiet flat minutes still belong to the eligible clock sample.
        cash += 0 if index % 5 == 0 else Fraction((index % 7) - 3, 10)
        subject.observe(point(index * 60 + 30, cash))
    subject.observe(point(181 * 60))
    left = subject.periods_result()
    right = tuple({**p, "net_marked_delta_quote_rational": "0", "gross_marked_delta_quote_rational": "0"} for p in left)
    first = compare_pnl_periods(left, right, "a" * 64, replicates=100)
    assert first == compare_pnl_periods(left, right, "a" * 64, replicates=100)
    for metric in first["metrics"].values():
        for value in metric.values():
            assert value["interval"] is not None
            assert value["eligible_period_count"] == 180
            assert value["sequence_lengths"] == [180]
        assert metric["30"]["estimate"] == pytest.approx(float(cash / 180))
    gapped = list(left)
    gapped[25] = {
        **gapped[25],
        "excluded_reason": "gap",
        "net_marked_delta_quote_rational": None,
        "gross_marked_delta_quote_rational": None,
    }
    report = compare_pnl_periods(gapped, right, "a" * 64, replicates=100)
    for metric in report["metrics"].values():
        assert metric["30"]["interval"] is None
        assert metric["30"]["sequence_lengths"] == [25, 154]
        assert metric["30"]["excluded_period_counts"] == {"gap": 1}
