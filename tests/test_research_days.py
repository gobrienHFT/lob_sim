"""Admission/census mechanics only. These fixtures are NOT empirical evidence."""

from copy import deepcopy
import json

import pytest

from lob_sim.regime.validation import identity
from lob_sim.research.capture_audit import audit_capture
from lob_sim.research.day_eligibility import DAY_NS, eligibility_rule, summarize_days, verify_day_report
from lob_sim.research.interval_reader import iter_verified_intervals, load_audit_report
from lob_sim.research.day_decision import coverage_exclusions
from test_research_capture_intervals import capture, cfg, SECOND


def bundle(tmp_path):
    output = tmp_path / "audit"
    audit_capture(capture(tmp_path), output, cfg())
    return output


def rehash_report(root, mutate):
    path = root / "report.json"
    data = json.loads(path.read_text())
    mutate(data)
    data["report_sha256"] = identity({k: v for k, v in data.items() if k != "report_sha256"})
    path.write_text(json.dumps(data))


def test_independent_interval_reader_recomputes_hand_counted_durations(tmp_path):
    root = bundle(tmp_path)
    report = load_audit_report(root)
    rows = list(iter_verified_intervals(root, report))
    assert sum(row["end_ns"] - row["start_ns"] for row in rows) == 11 * SECOND
    assert sum(row["end_ns"] - row["start_ns"] for row in rows if row["joint_valid"]) == 7 * SECOND
    assert report["native_book_gap_count"] == 1


def test_short_valid_snippet_is_not_a_full_research_day(tmp_path):
    root = bundle(tmp_path)
    report = summarize_days((root,))
    day = report["days"][0]
    assert day["total_utc_ns"] == DAY_NS
    assert day["covered_ns"] == 11 * SECOND
    assert day["joint_valid_ns"] == 7 * SECOND
    assert day["uncovered_ns"] == DAY_NS - 11 * SECOND
    assert not day["eligible"] and not report["ready_for_registration"]
    assert report["eligible_days"] == []
    assert "synthetic_source" in day["exclusions"]
    assert "coverage_below_99pct_of_full_utc_day" in day["exclusions"]
    assert day["invalid_reason_ns"]["ETHUSDT:trade_stream_invalid"] >= SECOND
    verify_day_report((root,), report)
    assert report == summarize_days((root,))


def test_rule_is_fixed_and_returned_metadata_cannot_mutate_it():
    rule = eligibility_rule()
    assert rule["minimum_coverage_ppm"] == 990_000
    assert rule["minimum_joint_valid_ppm"] == 950_000
    assert rule["minimum_joint_mark_ppm"] == 950_000
    rule["minimum_coverage_ppm"] = 0
    assert eligibility_rule()["minimum_coverage_ppm"] == 990_000


def test_full_utc_thresholds_use_exact_nanoseconds_not_rounded_percentages():
    coverage, valid = DAY_NS * 99 // 100, DAY_NS * 95 // 100
    assert coverage_exclusions(coverage, valid, valid) == []
    assert coverage_exclusions(coverage - 1, valid, valid) == ["coverage_below_99pct_of_full_utc_day"]
    assert coverage_exclusions(coverage, valid - 1, valid) == ["joint_valid_below_95pct_of_full_utc_day"]
    assert "joint_mark_below_95pct_of_full_utc_day" in coverage_exclusions(coverage, valid - 1, valid - 1)


@pytest.mark.parametrize("counts", [(True, 0, 0), (DAY_NS + 1, 0, 0), (10, 11, 10), (10, 10, 9), (10, 0, 11)])
def test_day_predicate_rejects_coercion_and_duration_nonconservation(counts):
    with pytest.raises(ValueError):
        coverage_exclusions(*counts)


def test_duplicate_sources_never_count_time_twice(tmp_path):
    root = bundle(tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        summarize_days((root, root))


@pytest.mark.parametrize("field", ["duration_ns", "joint_valid_ns", "joint_mark_available_ns", "joint_valid_fraction"])
def test_reader_rejects_forged_rehashed_totals(tmp_path, field):
    root = bundle(tmp_path)
    rehash_report(root, lambda data: data.update({field: data[field] + 1}))
    with pytest.raises(ValueError, match="census|coverage|fraction"):
        summarize_days((root,))


def test_rehashed_day_report_cannot_change_the_independent_admission_decision(tmp_path):
    root = bundle(tmp_path)
    report = deepcopy(summarize_days((root,)))
    report["days"][0]["eligible"] = True
    report["days"][0]["exclusions"] = []
    report["report_sha256"] = identity({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError, match="independently"):
        verify_day_report((root,), report)


def test_failure_or_partial_interval_file_prevents_admission(tmp_path):
    root = bundle(tmp_path)
    (root / "failure.json").write_text("{}")
    with pytest.raises(ValueError, match="incomplete"):
        summarize_days((root,))


def test_truncated_or_changed_interval_bytes_never_publish_day_statistics(tmp_path):
    root = bundle(tmp_path)
    path = root / "intervals.jsonl"
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="tail"):
        summarize_days((root,))
