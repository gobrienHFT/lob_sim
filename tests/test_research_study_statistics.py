"""Hand-counted clock/ratio oracles, never real market observations."""

from copy import deepcopy

import pytest

from lob_sim.regime.study_outcomes import outcome_contract
from lob_sim.regime.study_periods import METRICS, PERIOD_NS
from lob_sim.research.study_primary import ADDITIVE, RATIOS
from lob_sim.research.study_statistics import compare_units
from lob_sim.research.study_tables import publish_tables, iter_tables
from test_research_capture_intervals import WALL

STATISTICS = {
    "primary_block_minutes": 30,
    "sensitivity_block_minutes": [5, 60],
    "replications": 20,
    "confidence_level": 0.95,
    "seed": 7,
}


def frame(t, value=0, *, missing=False, quiet=False):
    common = {
        "utc_start_ns": t,
        "period_ns": PERIOD_NS,
        "epoch": [1, 1, 1],
        "excluded_reason": "unmarked" if missing else None,
    }
    contract = outcome_contract((1000,))
    ratio = {"numerator_rational": "0" if quiet else str(value), "denominator_rational": "0" if quiet else "1"}
    return {
        "risk": {**common, **{k: float(value) for k in METRICS}},
        "outcomes": {
            **common,
            "additive": {k: str(value) for k in contract["additive_units"]},
            "ratios": {k: deepcopy(ratio) for k in contract["ratio_units"]},
        },
        "pnl": {
            **common,
            "gross_marked_delta_quote_rational": None if missing else str(value),
            "net_marked_delta_quote_rational": None if missing else str(value),
        },
        "primary": {
            **common,
            "additive": {k: str(value) for k in ADDITIVE},
            "ratios": {k: deepcopy(ratio) for k in RATIOS},
        },
    }


def test_constant_difference_has_exact_clock_block_interval_and_no_gap_crossing():
    left = [frame(WALL + i * PERIOD_NS, 3) for i in range(120)]
    right = [frame(WALL + i * PERIOD_NS, 1) for i in range(120)]
    report = compare_units([("a" * 64, left, right)], STATISTICS, (1000,))
    for name in (
        "risk:mean_absolute_inventory_lots",
        "primary:maker_signed_markout_1s_bps",
        "pnl:net_marked_delta_quote_rational",
    ):
        for minutes in (5, 30, 60):
            result = report["metrics"][name]["sensitivities"][str(minutes)]
            assert result["estimate"] == 2.0
            assert result["interval"]["lower"] == result["interval"]["upper"] == 2.0
            assert result["eligible_period_count"] == 120
    assert report["claim_ready"] is False


def test_zero_activity_stays_on_clock_but_zero_ratio_denominator_is_unknown():
    left = [frame(WALL + i * PERIOD_NS, quiet=True) for i in range(120)]
    right = deepcopy(left)
    report = compare_units([("a" * 64, left, right)], STATISTICS, (1000,))
    ratio = report["metrics"]["primary:maker_signed_markout_1s_bps"]["sensitivities"]["30"]
    assert ratio["eligible_period_count"] == 120 and ratio["sequence_lengths"] == [120]
    assert ratio["estimate"] is None and ratio["interval"] is None
    assert report["metrics"]["outcomes:fill_count"]["sensitivities"]["30"]["estimate"] == 0.0


def test_missing_marks_are_not_zero_returns_and_short_strata_are_not_discarded():
    left = [frame(WALL + i * PERIOD_NS, 3, missing=i == 61) for i in range(120)]
    right = [frame(WALL + i * PERIOD_NS, 1) for i in range(120)]
    result = compare_units([("a" * 64, left, right)], STATISTICS, (1000,))["metrics"][
        "pnl:net_marked_delta_quote_rational"
    ]["sensitivities"]["60"]
    assert result["estimate"] == 2.0 and result["interval"] is None
    assert result["eligible_period_count"] == 119 and result["excluded_period_counts"] == {"unmarked": 1}
    assert result["sequence_lengths"] == [61, 58]


def test_source_and_day_boundaries_form_distinct_blocks_even_when_adjacent():
    a = [frame(WALL + i * PERIOD_NS, 3) for i in range(30)]
    b = [frame(WALL + i * PERIOD_NS, 1) for i in range(30)]
    c = [frame(WALL + (i + 30) * PERIOD_NS, 3) for i in range(30)]
    d = [frame(WALL + (i + 30) * PERIOD_NS, 1) for i in range(30)]
    report = compare_units([("a" * 64, a, b), ("b" * 64, c, d)], STATISTICS, (1000,))
    result = report["metrics"]["risk:mean_absolute_inventory_lots"]["sensitivities"]["30"]
    assert result["sequence_lengths"] == [30, 30]
    assert result["interval"]["lower"] == result["interval"]["upper"] == 2.0
    assert report["metrics"]["risk:mean_absolute_inventory_lots"]["sensitivities"]["60"]["interval"] is None


def test_pair_cannot_change_utc_clock_grid():
    with pytest.raises(ValueError, match="clock grid"):
        compare_units([("a" * 64, [frame(WALL)], [frame(WALL + PERIOD_NS)])], STATISTICS, (1000,))


def test_period_artifacts_verify_full_tail_and_cannot_clobber(tmp_path):
    path = tmp_path / "periods.jsonl"
    rows = (frame(WALL), frame(WALL + PERIOD_NS))
    binding = publish_tables(path, rows)
    assert tuple(iter_tables(path, binding)) == rows
    original = path.read_bytes()
    with pytest.raises(ValueError, match="exists"):
        publish_tables(path, rows)
    assert path.read_bytes() == original
    path.write_bytes(original + b"truncated")
    with pytest.raises(ValueError, match="truncated"):
        tuple(iter_tables(path, binding))
