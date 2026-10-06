from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import AggTradeEvent, LevelChange, SymbolSpec
from lob_sim.regime.features import BookView, CausalFeatureSampler, FeatureSpec, FeatureValidity


SECOND = 1_000_000_000
SPEC = FeatureSpec(window_steps=2, depth_levels=2)
BOOK = BookView(((99, 10), (98, 5)), ((101, 10), (102, 5)))


def _observe(sampler: CausalFeatureSampler, time: float, seq: int, **kwargs) -> list:
    ns = round(time * SECOND)
    result = list(sampler.advance_before(ns))
    sampler.observe(
        ns, seq, book=kwargs.pop("book", BOOK), validity=kwargs.pop("validity", FeatureValidity()), **kwargs
    )
    return result


def test_feature_formulas_are_independently_computable() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    samples = _observe(sampler, 0, 0, depth_event=True)
    book = BookView(((99, 12), (98, 5)), ((101, 8), (102, 5)))
    samples += _observe(
        sampler,
        1,
        1,
        book=book,
        depth_event=True,
        changes=(LevelChange("bids", 99, 10, 12), LevelChange("asks", 101, 10, 8)),
    )
    samples += _observe(sampler, 1.5, 2, book=book, trade=AggTradeEvent("BTCUSDT", 101, 3, False, 1.5))
    samples += _observe(sampler, 1.75, 3, book=book, trade=AggTradeEvent("BTCUSDT", 99, 1, True, 1.75))
    samples += _observe(sampler, 2, 4, book=book, depth_event=True)
    samples += list(sampler.finish(2 * SECOND))
    assert [sample.status for sample in samples] == ["WARMING_UP", "VALID"]
    row = samples[-1].values
    assert row is not None
    assert row == pytest.approx(
        (
            200,
            0,
            0,
            0.2,
            (17 - 13) / 30,
            (3 - 1) / 4,
            math.log1p(30),
            0,
            math.log1p(4 / 2),
            math.log1p(2 / 2),
            20,
            0,
        )
    )


def test_sampling_counts_elapsed_grid_points_not_feed_records() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    samples = []
    for seq in range(1, 201):
        samples += _observe(sampler, seq / 100, seq, depth_event=True)
    samples += list(sampler.finish(2 * SECOND))
    assert [row.sample_ns for row in samples] == [SECOND, 2 * SECOND]
    assert samples[-1].status == "VALID"
    assert samples[-1].values[8] == pytest.approx(math.log1p(200 / 2))
    assert sampler.retained_bins == 2


def test_same_time_receive_ties_enter_one_right_closed_sample() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    _observe(sampler, 1, 1, depth_event=True)
    _observe(sampler, 2, 2, trade=AggTradeEvent("BTCUSDT", 101, 1, False, 2))
    _observe(sampler, 2, 3, trade=AggTradeEvent("BTCUSDT", 99, 3, True, 2))
    rows = list(sampler.finish(2 * SECOND))
    assert len(rows) == 1
    assert rows[0].receive_seq == 3
    assert rows[0].values[5] == -0.5
    assert not list(sampler.finish(2 * SECOND))


def test_future_book_and_trade_mutations_leave_historical_feature_samples_identical() -> None:
    def run(future_book: BookView, buyer_is_maker: bool) -> list:
        sampler = CausalFeatureSampler("BTCUSDT", SPEC)
        rows = _observe(sampler, 0, 0, depth_event=True)
        rows += _observe(sampler, 1, 1, depth_event=True)
        rows += _observe(sampler, 2, 2, depth_event=True)
        rows += _observe(sampler, 2.5, 3, book=future_book, trade=AggTradeEvent("BTCUSDT", 101, 2, buyer_is_maker, 2.5))
        rows += _observe(sampler, 3, 4, book=future_book, depth_event=True)
        rows += list(sampler.finish(3 * SECOND))
        return rows

    left = run(BookView(((99, 100),), ((101, 1),)), True)
    right = run(BookView(((199, 1),), ((201, 100),)), False)
    assert left[:2] == right[:2]
    assert left[-1] != right[-1]


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("book", "INVALID_BOOK"),
        ("trade", "INVALID_TRADE_STREAM"),
        ("clock", "INVALID_CLOCK"),
        ("capture", "INVALID_CAPTURE"),
    ],
)
def test_invalid_required_feed_resets_window_instead_of_zero_imputing(flag: str, expected: str) -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    _observe(sampler, 1, 1, depth_event=True)
    _observe(sampler, 2, 2, depth_event=True)
    assert list(sampler.finish(2 * SECOND))[-1].status == "VALID"
    _observe(sampler, 2.5, 3, validity=replace(FeatureValidity(), **{flag: False}))
    assert sampler.status == expected
    invalid = _observe(sampler, 3.1, 4, depth_event=True)
    assert invalid[-1].status == expected
    assert invalid[-1].values is None
    assert invalid[-1].reset_reason == expected
    assert sampler.retained_bins == 0
    _observe(sampler, 4, 5, depth_event=True)
    assert list(sampler.finish(4 * SECOND))[-1].status == "WARMING_UP"
    _observe(sampler, 5, 6, depth_event=True)
    assert list(sampler.finish(5 * SECOND))[-1].status == "WARMING_UP"
    _observe(sampler, 6, 7, depth_event=True)
    assert list(sampler.finish(6 * SECOND))[-1].status == "VALID"


def test_valid_zero_trades_are_not_a_broken_trade_stream() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    for seq in range(3):
        _observe(sampler, seq, seq, depth_event=True)
    row = list(sampler.finish(2 * SECOND))[-1]
    assert row.status == "VALID"
    assert row.values[5] == row.values[9] == 0


def test_epoch_jump_clears_history_and_preserves_sampling_phase() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    for seq in range(3):
        _observe(sampler, seq, seq, depth_event=True)
    list(sampler.finish(2 * SECOND))
    _observe(sampler, 2.25, 3, validity=FeatureValidity(epochs=(1, 1, 2)), depth_event=True)
    assert sampler.retained_bins == 0
    row = list(sampler.advance_before(3 * SECOND + 1))[-1]
    assert row.sample_ns == 3 * SECOND
    assert row.status == "WARMING_UP"
    assert row.reset_reason == "epoch_changed"


def test_stale_book_is_not_retroactively_recovered_from_future_update() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", replace(SPEC, stale_after_ns=SECOND))
    _observe(sampler, 0, 0, depth_event=True)
    historical = _observe(sampler, 3, 1, depth_event=True)
    assert historical[-1].sample_ns == 2 * SECOND
    assert historical[-1].status == "STALE"
    assert historical[-1].values is None
    assert list(sampler.finish(3 * SECOND))[-1].status == "WARMING_UP"


def test_visible_level_depletion_is_not_named_cancellation_or_counted_outside_top_n() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    _observe(
        sampler, 1, 1, depth_event=True, changes=(LevelChange("bids", 99, 15, 10), LevelChange("bids", 90, 1000, 0))
    )
    _observe(sampler, 2, 2, depth_event=True)
    sample = list(sampler.finish(2 * SECOND))[-1]
    assert sample.values[7] == pytest.approx(-5 / 30)
    assert "not_cancel_rate" in SPEC.as_dict()["visible_change"]


def test_nonfinite_features_fail_closed_and_long_windows_remain_bounded() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    for index in range(1001):
        _observe(sampler, index, index, depth_event=True)
        assert sampler.retained_bins <= SPEC.window_steps
    # Integers are valid book coordinates but may be unrepresentable as floats.
    huge = 10**400
    row_book = BookView(((huge, 10),), ((huge + 2, 10),))
    _observe(sampler, 1001, 1001, book=row_book, depth_event=True)
    assert sampler.status == "NONFINITE_FEATURE"
    row = list(sampler.finish(1001 * SECOND))[-1]
    assert row.values is None


def test_clock_and_receive_contracts_fail_before_mutation() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 1, 1, depth_event=True)
    with pytest.raises(ValueError, match="regressing"):
        sampler.observe(0, 2, book=BOOK, validity=FeatureValidity())
    with pytest.raises(ValueError, match="sequence"):
        sampler.observe(SECOND, 1, book=BOOK, validity=FeatureValidity())
    with pytest.raises(RuntimeError, match="advance_before"):
        sampler.observe(3 * SECOND, 2, book=BOOK, validity=FeatureValidity())
    with pytest.raises(ValueError, match="tail"):
        list(sampler.finish(2 * SECOND))


def test_book_view_does_not_mutate_authoritative_book() -> None:
    book = LocalOrderBook("BTCUSDT", SymbolSpec("BTCUSDT", Decimal("1"), Decimal("1")))
    book.reset_from_snapshot(1, {99: 10, 98: 5}, {101: 10, 102: 5})
    view = BookView.from_book(book, 1)
    book.apply_depth_update([(99, 1)], [])
    assert view.bids == ((99, 10),)
    assert len(view.asks) == 1


def test_sampler_rejects_event_before_closed_watermark() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    list(sampler.advance_before(3 * SECOND))
    with pytest.raises(ValueError, match="watermark"):
        sampler.observe(2 * SECOND, 1, book=BOOK, validity=FeatureValidity())
    with pytest.raises(ValueError, match="watermark"):
        list(sampler.advance_before(2 * SECOND))


def test_finish_prevents_new_receipts_reopening_a_closed_grid_point() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    _observe(sampler, 1, 1, depth_event=True)
    list(sampler.finish(SECOND))
    with pytest.raises(ValueError, match="closed"):
        sampler.observe(SECOND, 2, book=BOOK, validity=FeatureValidity())


@pytest.mark.parametrize(
    "kwargs", [{"interval_ns": 0}, {"window_steps": False}, {"depth_levels": 0}, {"stale_after_ns": 1}]
)
def test_invalid_feature_configuration_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        FeatureSpec(**kwargs)


@pytest.mark.parametrize(
    "bids,asks",
    [(((101, 1),), ((100, 1),)), ((), ((101, 1),)), (((99, True),), ((101, 1),)), (((98, 1), (99, 1)), ((101, 1),))],
)
def test_invalid_book_views_are_rejected(bids, asks) -> None:
    with pytest.raises(ValueError):
        BookView(bids, asks)


def test_partially_consumed_iterators_never_emit_the_same_sampling_point_twice() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    _observe(sampler, 0, 0, depth_event=True)
    first = sampler.advance_before(3 * SECOND)
    assert next(first).sample_ns == SECOND
    assert [row.sample_ns for row in sampler.advance_before(3 * SECOND)] == [2 * SECOND]
    assert list(first) == []


def test_trade_only_receipt_cannot_recover_a_stale_book() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", replace(SPEC, stale_after_ns=SECOND))
    _observe(sampler, 0, 0, depth_event=True)
    _observe(sampler, 2, 1, trade=AggTradeEvent("BTCUSDT", 101, 2, False, 2))
    assert sampler.status == "STALE"
    assert list(sampler.finish(2 * SECOND))[-1].values is None


def test_sampling_specification_cannot_be_replaced_in_place() -> None:
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    with pytest.raises(AttributeError):
        sampler.spec = replace(SPEC, interval_ns=250_000_000)
