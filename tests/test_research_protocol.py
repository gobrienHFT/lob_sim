from __future__ import annotations

from datetime import date

import pytest

from lob_sim.research.protocol import (
    ResearchRegistry,
    chronological_day_split,
    moving_block_bootstrap_mean,
    paired_moving_block_bootstrap_mean_delta,
)


def test_chronological_split_is_disjoint_and_marks_short_study_diagnostic() -> None:
    split = chronological_day_split(
        [date(2026, 1, day) for day in range(1, 10)],
    )

    assert split.calibration_days == (
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
    )
    assert split.validation_days == ("2026-01-06",)
    assert split.test_days == ("2026-01-07", "2026-01-08", "2026-01-09")
    assert set(split.calibration_days).isdisjoint(split.validation_days)
    assert set(split.validation_days).isdisjoint(split.test_days)
    assert split.claim_ready is False
    assert "need 10" in (split.reason or "")
    assert split.digest == chronological_day_split(split.all_days).digest


def test_ten_day_split_is_claim_ready_and_preserves_chronology() -> None:
    split = chronological_day_split([f"2026-02-{day:02d}" for day in range(1, 11)])

    assert split.claim_ready is True
    assert len(split.calibration_days) == 6
    assert len(split.validation_days) == 2
    assert len(split.test_days) == 2
    assert split.all_days == tuple(sorted(split.all_days))


def test_registry_is_content_addressed_and_freezes_before_test() -> None:
    registry = ResearchRegistry()
    config = {"half_spread_bps": "0.05", "seed": 7, "nested": {"gate": 1}}
    first = registry.register("baseline", config)
    config["nested"]["gate"] = 999
    assert first == registry.register("baseline", {"half_spread_bps": "0.05", "seed": 7, "nested": {"gate": 1}})
    second = registry.register("inventory", {"skew_bps": "10"})
    snapshot = registry.freeze()

    assert snapshot["frozen"] is True
    assert [row["variant_id"] for row in snapshot["variants"]] == sorted((first, second))
    assert snapshot["registry_sha256"]
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register("late_variant", {})


def test_moving_block_bootstrap_is_reproducible_and_has_mean_in_interval() -> None:
    values = [float(index) for index in range(20)]
    first = moving_block_bootstrap_mean(values, block_size=4, replicates=200, seed=19)
    second = moving_block_bootstrap_mean(values, block_size=4, replicates=200, seed=19)

    assert first == second
    assert first.estimate == pytest.approx(9.5)
    assert first.lower <= first.estimate <= first.upper
    assert first.algorithm == "splitmix64_moving_blocks_v1"
    assert first.as_dict()["schema_version"] == "lob_sim.bootstrap_interval.v1"


def test_paired_bootstrap_uses_eventwise_deltas() -> None:
    result = paired_moving_block_bootstrap_mean_delta(
        [10, 12, 14, 16],
        [9, 10, 11, 12],
        block_size=2,
        replicates=100,
        seed=3,
    )

    assert result.estimate == pytest.approx(2.5)
    assert result.lower <= result.estimate <= result.upper


@pytest.mark.parametrize(
    ("values", "kwargs", "message"),
    [
        ([], {"block_size": 1}, "at least one"),
        ([1, 2], {"block_size": 3}, "block_size"),
        ([1, 2], {"block_size": 1, "confidence": 1.0}, "confidence"),
        ([1, float("nan")], {"block_size": 1}, "finite"),
    ],
)
def test_bootstrap_rejects_invalid_inputs(values, kwargs, message: str) -> None:
    with pytest.raises((ValueError, TypeError), match=message):
        moving_block_bootstrap_mean(values, **kwargs)


def test_day_split_requires_three_distinct_days() -> None:
    with pytest.raises(ValueError, match="three distinct"):
        chronological_day_split(["2026-01-01", "2026-01-01"])
