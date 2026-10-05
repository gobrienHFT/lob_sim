from __future__ import annotations

from dataclasses import replace

import pytest

from lob_sim.research.clock_bootstrap import DAY, SECOND, PairedClockPeriod, paired_clock_bootstrap
from lob_sim.research.protocol import moving_block_bootstrap_mean, paired_moving_block_bootstrap_mean_delta

MINUTE = 60 * SECOND
WALL = 1_735_689_600 * SECOND


def periods(count=120, *, width=MINUTE):
    return [PairedClockPeriod("a" * 64, WALL + i * width, width, 0, float(i + 1), float(i)) for i in range(count)]


def test_minutes_are_clock_periods_not_event_counts_and_constant_pair_is_exact():
    result = paired_clock_bootstrap(periods(), block_minutes=30, replicates=25)
    assert result["eligible_duration_ns"] == 120 * MINUTE
    assert result["block_size"] == 30
    assert result["interval"]["estimate"] == result["interval"]["lower"] == result["interval"]["upper"] == 1
    assert result["sequence_lengths"] == [120]
    assert not result["claim_ready"]
    # Identical periods with second-resolution grid need 1800 rows, not 30.
    short = paired_clock_bootstrap(periods(count=120, width=SECOND), block_minutes=30)
    assert short["block_size"] == 1800
    assert short["interval"] is None and "shorter" in short["unavailable_reason"]


def test_segmented_resampling_preserves_weights_and_never_crosses_source_boundary():
    left = [1.0] * 60 + [101.0] * 60
    result = moving_block_bootstrap_mean(left, block_size=30, lengths=(60, 60), replicates=100)
    assert result.lower == result.upper == result.estimate == 51
    # A whole-array block bootstrap DOES create different weights: independent
    # strata are a substantive sampling contract, not just a relabeled output.
    whole = moving_block_bootstrap_mean(left, block_size=30, replicates=100)
    assert whole.lower < whole.upper
    values = periods()
    values = [
        replace(p, source_sha256=("a" if i < 60 else "b") * 64, left=left[i], right=0) for i, p in enumerate(values)
    ]
    actual = paired_clock_bootstrap(values, block_minutes=30, replicates=100)
    assert actual["sequence_lengths"] == [60, 60]
    assert (
        actual["interval"]
        == paired_moving_block_bootstrap_mean_delta(
            left, [0] * 120, block_size=30, lengths=(60, 60), replicates=100, seed=7
        ).as_dict()
    )


def test_legacy_rng_path_is_preserved_for_single_stratum():
    values = [float(i * i) for i in range(20)]
    original = moving_block_bootstrap_mean(values, block_size=4, replicates=100, seed=19)
    new = moving_block_bootstrap_mean(values, block_size=4, lengths=(20,), replicates=100, seed=19)
    assert (original.estimate, original.lower, original.upper) == (new.estimate, new.lower, new.upper)
    assert original.algorithm == "splitmix64_moving_blocks_v1"
    assert new.algorithm == "splitmix64_stratified_moving_blocks_v1"


@pytest.mark.parametrize("boundary", ["epoch", "gap", "missing", "source", "day"])
def test_clock_boundaries_cannot_be_used_as_one_long_block(boundary):
    values = periods(count=40)
    if boundary == "day":
        values = [replace(p, utc_start_ns=WALL + DAY - 20 * MINUTE + i * MINUTE) for i, p in enumerate(values)]
    elif boundary == "missing":
        values[20] = replace(values[20], right=None, excluded_reason="missing mark")
    else:
        for i in range(20, len(values)):
            if boundary == "epoch":
                values[i] = replace(values[i], validity_epoch=1)
            if boundary == "gap":
                values[i] = replace(values[i], utc_start_ns=values[i].utc_start_ns + MINUTE)
            if boundary == "source":
                values[i] = replace(values[i], source_sha256="b" * 64)
    report = paired_clock_bootstrap(values, block_minutes=30, replicates=20)
    assert len(report["sequence_lengths"]) == 2
    assert report["interval"] is None
    assert report["estimate"] == 1
    assert report["unavailable_reason"]
    if boundary == "missing":
        assert report["eligible_period_count"] == 39
        assert report["excluded_period_counts"] == {"missing mark": 1}


def test_unavailable_short_sensitivities_are_not_silently_resized():
    values = periods(count=60)
    reports = {minutes: paired_clock_bootstrap(values, block_minutes=minutes, replicates=25) for minutes in (5, 30, 60)}
    assert reports[5]["interval"] is not None and reports[30]["interval"] is not None
    assert reports[60]["interval"] is None
    assert "two complete" in reports[60]["unavailable_reason"]


def test_all_excluded_has_null_estimate_and_coverage_reason():
    values = [replace(p, left=None, right=None, excluded_reason="invalid trade stream") for p in periods(count=3)]
    report = paired_clock_bootstrap(values)
    assert report["estimate"] is report["interval"] is None
    assert report["eligible_period_count"] == report["eligible_duration_ns"] == 0
    assert report["excluded_period_counts"] == {"invalid trade stream": 3}


@pytest.mark.parametrize(
    "changes",
    [
        {"source_sha256": "short"},
        {"utc_start_ns": WALL + 1},
        {"period_ns": True},
        {"validity_epoch": -1},
        {"left": float("nan")},
        {"right": True},
        {"left": None},
        {"excluded_reason": ""},
        {"period_ns": DAY + 1},
    ],
)
def test_period_metadata_and_missingness_are_strict(changes):
    with pytest.raises(ValueError):
        replace(periods(count=1)[0], **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"block_minutes": False},
        {"replicates": 0},
        {"seed": True},
        {"confidence": float("inf")},
        {"confidence": True},
    ],
)
def test_statistical_controls_reject_ambiguous_values(changes):
    with pytest.raises(ValueError):
        paired_clock_bootstrap(periods(), **changes)


@pytest.mark.parametrize("lengths", [(1, 19), (20, 1), (True, 19), (), (10.0, 10)])
def test_segment_lengths_cannot_drop_rows_or_shrink_blocks(lengths):
    with pytest.raises(ValueError, match="sequence lengths"):
        moving_block_bootstrap_mean(range(20), block_size=4, lengths=lengths)


def test_duplicate_periods_and_mixed_width_fail_closed():
    values = periods()
    with pytest.raises(ValueError, match="duplicate"):
        paired_clock_bootstrap([values[0], values[0]])
    with pytest.raises(ValueError, match="width"):
        paired_clock_bootstrap([values[0], replace(values[1], period_ns=SECOND)])


def test_paired_boolean_inputs_are_not_converted_to_favorable_numeric_outcomes():
    with pytest.raises(TypeError, match="bool"):
        paired_moving_block_bootstrap_mean_delta([True, 1], [0, 0], block_size=1)
