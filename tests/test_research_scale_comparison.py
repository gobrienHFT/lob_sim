"""Reported-workload consistency controls, not authenticated timing evidence."""

from copy import deepcopy
import json

import pytest

from experiments.compare_research_scale import compare


def report():
    return {
        "schema_version": "lob_sim.research_scale_worker.v1",
        "mode": "observe",
        "workload": "null",
        "configuration": {"latency_ms": 0.0},
        "warmups": 0,
        "repetitions": 1,
        "profiled": False,
        "memory_traced": False,
        "host": {"platform": "fixture"},
        "identities": {"script_sha256": "a" * 64, "input": {"sha256": "b" * 64}, "code_identity": {"sha256": "c" * 64}},
        "probe": {"records_processed": 10, "fill_hash": "d" * 64},
        "rows": [
            {
                "index": 0,
                "warmup": False,
                "wall_ns": 1000,
                "cpu_ns": 900,
                "records": 10,
                "records_per_second": 10_000_000.0,
                "os_process_peak_rss_bytes": 100,
                "output_bytes": 0,
            }
        ],
    }


def run(tmp_path, left, right):
    before, after, target = [tmp_path / name for name in ("before.json", "after.json", "comparison.json")]
    before.write_text(json.dumps(left))
    after.write_text(json.dumps(right))
    return compare(before, after, target)


def test_comparison_allows_changed_processing_revision_but_not_changed_workload(tmp_path):
    left = report()
    right = deepcopy(left)
    right["identities"]["code_identity"]["sha256"] = "e" * 64
    right["rows"][0].update(wall_ns=500, cpu_ns=450, records_per_second=20_000_000.0)
    result = run(tmp_path, left, right)
    assert result["complete"] and result["median_wall_change_percent"] == -50
    assert result["before"]["confidence_interval"] is None


@pytest.mark.parametrize("mutation", ["typed_configuration", "worker", "probe", "host", "typed_trial", "schema"])
def test_comparison_rejects_changed_controls_or_business_probe(tmp_path, mutation):
    left = report()
    right = deepcopy(left)
    if mutation == "typed_configuration":
        right["configuration"]["latency_ms"] = False
    elif mutation == "worker":
        right["identities"]["script_sha256"] = "0" * 64
    elif mutation == "probe":
        right["probe"]["fill_hash"] = "0" * 64
    elif mutation == "host":
        right["host"]["platform"] = "different"
    elif mutation == "typed_trial":
        right["rows"][0]["index"] = False
    else:
        right["schema_version"] = "unknown"
    with pytest.raises(ValueError):
        run(tmp_path, left, right)
    assert not (tmp_path / "comparison.json").exists()
