from __future__ import annotations

import math
from dataclasses import replace

import pytest

from lob_sim.research.clock_bootstrap import (
    DAY,
    SECOND,
    PairedClockPeriod,
    PairedClockRatioPeriod,
    paired_clock_ratio_bootstrap,
)
from lob_sim.research.protocol import paired_moving_block_bootstrap_ratio_delta

MINUTE = 60 * SECOND
WALL = 1_735_689_600 * SECOND


def independent_oracle(components, lengths, block, replicates, seed):
    # Separate integer RNG and batch-index resampler; no production helpers.
    mask = 2**64 - 1
    state = seed

    def draw(upper):
        nonlocal state
        state = (state + 0x9E3779B97F4A7C15) & mask
        x = state
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & mask
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & mask
        return (x ^ (x >> 31)) % upper

    samples, undefined = [], 0
    for _ in range(replicates):
        indices, offset = [], 0
        for length in lengths:
            selected = []
            for _ in range((length + block - 1) // block):
                start = offset + draw(length - block + 1)
                selected += list(range(start, start + block))
            indices += selected[:length]
            offset += length
        ln, ld, rn, rd = [sum(values[i] for i in indices) for values in components]
        if ld == 0 or rd == 0:
            undefined += 1
        else:
            samples.append(ln / ld - rn / rd)
    samples.sort()

    def quantile(p):
        location = (len(samples) - 1) * p
        i, j = math.floor(location), math.ceil(location)
        return samples[i] + (samples[j] - samples[i]) * (location - i)

    return undefined, None if undefined else (quantile(0.025), quantile(0.975))


@pytest.mark.parametrize("seed", [0, 7, 19, 2**64 + 1])
@pytest.mark.parametrize("lengths", [(12,), (5, 7)])
def test_paired_ratio_matches_independent_stratified_rng_oracle(seed, lengths):
    components = ([i * i - 9 for i in range(12)], list(range(1, 13)), [2 * i for i in range(12)], [2] * 12)
    report = paired_moving_block_bootstrap_ratio_delta(
        *components, block_size=3, lengths=lengths, replicates=101, seed=seed
    )
    undefined, interval = independent_oracle(components, lengths, 3, 101, seed)
    assert undefined == report["undefined_replicates"] == 0
    assert (report["interval"]["lower"], report["interval"]["upper"]) == pytest.approx(interval)
    assert report["estimate"] == pytest.approx(
        sum(components[0]) / sum(components[1]) - sum(components[2]) / sum(components[3])
    )
    assert report["evaluated_replicates"] == 101


def test_unequal_activity_pools_quantities_not_mean_of_minute_means():
    report = paired_moving_block_bootstrap_ratio_delta([10, 0], [1, 9], [0, 0], [1, 1], block_size=1, replicates=30)
    assert report["estimate"] == 1  # NOT (10/1 + 0/9)/2 = 5.
    assert report["left_denominator_sum"] == 10


def test_sparse_resamples_are_counted_not_redrawn_or_conditionally_discarded():
    components = ([3, 0, 0, 0], [1, 0, 0, 0], [1] * 4, [1] * 4)
    report = paired_moving_block_bootstrap_ratio_delta(*components, block_size=1, replicates=101, seed=19)
    undefined, interval = independent_oracle(components, (4,), 1, 101, 19)
    assert undefined > 0 and interval is None
    assert report["undefined_replicates"] == undefined
    assert report["interval"] is None and report["estimate"] == 2
    assert report["evaluated_replicates"] == 101
    assert "discard" in report["unavailable_reason"]
    assert report["zero_activity_periods"] == {"left": 3, "right": 0}


def test_zero_total_is_missing_not_a_zero_quality_or_negative_baseline_delta():
    report = paired_moving_block_bootstrap_ratio_delta([0, 0], [0, 0], [3, 3], [1, 1], block_size=1)
    assert report["estimate"] is report["interval"] is None
    assert report["evaluated_replicates"] == report["undefined_replicates"] == 0
    assert "aggregate denominator" in report["unavailable_reason"]


@pytest.mark.parametrize(
    "changes",
    [
        {"left_numerators": [True, 0]},
        {"left_numerators": ["1", 0]},
        {"left_numerators": [float("nan"), 0]},
        {"left_denominators": [-1, 1]},
        {"left_denominators": [0, 0]},
        {"right_numerators": [0]},
        {"block_size": True},
        {"seed": True},
        {"seed": -1},
        {"replicates": False},
        {"confidence": True},
        {"lengths": (1, 2)},
    ],
)
def test_ratio_controls_and_denominators_are_strict(changes):
    args = dict(
        left_numerators=[1, 0],
        left_denominators=[1, 1],
        right_numerators=[0, 0],
        right_denominators=[1, 1],
        block_size=1,
    )
    with pytest.raises((ValueError, TypeError)):
        paired_moving_block_bootstrap_ratio_delta(**(args | changes))


def periods(count=120):
    return [
        PairedClockRatioPeriod(PairedClockPeriod("a" * 64, WALL + i * MINUTE, MINUTE, 0, 0, 0), 2, 1, 1, 1)
        for i in range(count)
    ]


def test_clock_ratio_reports_real_duration_and_source_weights():
    values = periods()
    values[60:] = [
        replace(p, clock=replace(p.clock, source_sha256="b" * 64), left_numerator=90, left_denominator=30)
        for p in values[60:]
    ]
    report = paired_clock_ratio_bootstrap(values, block_minutes=30, replicates=30)
    assert report["sequence_lengths"] == [60, 60]
    assert report["eligible_duration_ns"] == 120 * MINUTE
    assert report["estimate"] == pytest.approx((60 * 2 + 60 * 90) / (60 + 60 * 30) - 1)
    assert report["interval"]["lower"] == report["interval"]["upper"] == report["estimate"]
    assert not report["claim_ready"]


@pytest.mark.parametrize("boundary", ["epoch", "gap", "source", "day", "invalid"])
def test_ratio_clock_does_not_cross_independent_boundaries(boundary):
    values = periods(40)
    for i, p in enumerate(values):
        clock = p.clock
        if boundary == "day":
            clock = replace(clock, utc_start_ns=WALL + DAY - 20 * MINUTE + i * MINUTE)
        elif i >= 20:
            if boundary == "epoch":
                clock = replace(clock, validity_epoch=1)
            if boundary == "gap":
                clock = replace(clock, utc_start_ns=clock.utc_start_ns + MINUTE)
            if boundary == "source":
                clock = replace(clock, source_sha256="b" * 64)
            if boundary == "invalid" and i == 20:
                clock = replace(clock, excluded_reason="invalid feed")
        values[i] = replace(p, clock=clock)
    result = paired_clock_ratio_bootstrap(values, block_minutes=30, replicates=20)
    assert len(result["sequence_lengths"]) == 2
    assert result["interval"] is None and result["estimate"] == 1
    assert "shorter" in result["unavailable_reason"]


def test_zero_activity_period_keeps_clock_sequence_but_missing_interval_excludes():
    values = periods()
    values[50] = replace(values[50], left_numerator=0, left_denominator=0)
    result = paired_clock_ratio_bootstrap(values, block_minutes=30, replicates=30)
    assert result["sequence_lengths"] == [120] and result["eligible_period_count"] == 120
    assert result["zero_activity_periods"]["left"] == 1
    assert result["interval"] is not None
    values[50] = replace(values[50], clock=replace(values[50].clock, excluded_reason="stale mark"))
    result = paired_clock_ratio_bootstrap(values, block_minutes=30, replicates=30)
    assert result["sequence_lengths"] == [50, 69]
    assert result["excluded_period_counts"] == {"stale mark": 1}


@pytest.mark.parametrize(
    "changes",
    [{"left_denominator": -1}, {"left_denominator": 0}, {"left_numerator": True}, {"right_numerator": float("inf")}],
)
def test_clock_components_are_strict(changes):
    with pytest.raises(ValueError):
        replace(periods(1)[0], **changes)
