"""Malformed tapes must not turn validation diagnostics into retained traces."""

import json

import pytest

from lob_sim.record.schema import RecordValidationError
from lob_sim.record.segmented import validate_segment, recover_valid_envelopes
from lob_sim.replay.reader import iter_records
from test_segment_recovery_boundaries import _segment


def test_invalid_tape_issue_count_is_bounded_and_omissions_are_explicit(tmp_path):
    segment, lines = _segment(tmp_path)
    lines[2:2] = [json.dumps({"record": "unknown"}) for _ in range(1000)]
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = validate_segment(segment)
    issue_count = len(report.issues)
    assert not report.ok
    assert issue_count <= 129
    assert "872 additional issues omitted" in report.issues[-1]
    assert [row.recv_seq for row in recover_valid_envelopes(segment)] == [1]
    with pytest.raises(RecordValidationError):
        list(iter_records(segment))


def test_single_retained_issue_has_a_fixed_visible_size_budget(tmp_path):
    segment, lines = _segment(tmp_path)
    lines.insert(2, json.dumps({"record": "x" * 20_000}))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = validate_segment(segment)
    maximum = max(map(len, report.issues))
    assert not report.ok
    assert maximum <= 512
    assert any("diagnostic truncated" in issue for issue in report.issues)
