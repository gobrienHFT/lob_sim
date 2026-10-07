"""Hand-derived clock contracts, not another implementation of replay time."""

from dataclasses import replace

import pytest

from lob_sim.research.capture_clock import CaptureClock

SECOND = 1_000_000_000
WALL = 1_735_689_600 * SECOND


def test_projection_uses_only_the_first_anchor_and_integer_elapsed_time():
    clock = CaptureClock(123_456_789, WALL)
    assert clock.project(123_456_790) == WALL + 1
    assert clock.project(123_456_789 + SECOND) == WALL + SECOND
    # Later host-wall jitter is checked, not fitted or used to move old events.
    assert clock.deviation_ns(123_456_789 + SECOND, WALL + SECOND + 378_000) == 378_000
    assert clock.project(123_456_789 + SECOND) == WALL + SECOND
    assert CaptureClock.from_dict(clock.as_dict()) == clock
    assert clock.digest == CaptureClock.from_dict(clock.as_dict()).digest


def test_wall_deviation_limit_is_an_explicit_assumption_not_a_clock_accuracy_claim():
    clock = CaptureClock(0, WALL)
    assert clock.within_tolerance(SECOND, WALL + SECOND + 50_000_000)
    assert not clock.within_tolerance(SECOND, WALL + SECOND + 50_000_001)
    assert clock.within_tolerance(SECOND, WALL + SECOND - 50_000_000)
    assert not clock.within_tolerance(SECOND, WALL + SECOND - 50_000_001)
    assert "unmeasured" in clock.as_dict()["absolute_utc_accuracy"]
    assert "first" in clock.as_dict()["projection"]


@pytest.mark.parametrize("value", [True, 1.5, "1", -1, None])
def test_receipt_clocks_never_coerce_wire_types(value):
    with pytest.raises(ValueError):
        CaptureClock(value, WALL)
    with pytest.raises(ValueError):
        CaptureClock(0, value)
    with pytest.raises(ValueError):
        CaptureClock(0, WALL).project(value)
    with pytest.raises(ValueError):
        CaptureClock(0, WALL).deviation_ns(0, value)


def test_projection_rejects_pre_origin_and_unrepresentable_utc_time():
    clock = CaptureClock(10, WALL)
    with pytest.raises(ValueError, match="origin"):
        clock.project(9)
    with pytest.raises(ValueError):
        clock.project(2**63)
    with pytest.raises(ValueError):
        CaptureClock(0, 2**63)


def test_serialized_clock_cannot_silently_change_projection_or_add_fields():
    clock = CaptureClock(0, WALL)
    for changed in (
        {**clock.as_dict(), "projection": "future_receipt_interpolation"},
        {**clock.as_dict(), "extra": 1},
        {**clock.as_dict(), "origin_logical_ns": True},
        {**clock.as_dict(), "absolute_utc_accuracy": "certified"},
    ):
        with pytest.raises(ValueError):
            CaptureClock.from_dict(changed)
    assert replace(clock, origin_wall_ns=WALL + 1).digest != clock.digest
