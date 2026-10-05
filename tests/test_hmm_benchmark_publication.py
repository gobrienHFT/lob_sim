import itertools
import json
from collections import Counter
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from statistics import median

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs/benchmark_results/hmm_active_public_reference.json"
INPUT_ROOT = ROOT / "docs/benchmark_inputs/hmm_public_btcusdt_20261005"


def report():
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_published_native_report_hash_and_historical_source_are_not_rewritten():
    data = report()
    digest = data.pop("report_sha256")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert sha256(encoded).hexdigest() == digest == "b2bb3fe11e8530afed71de63dc82d2d4846587eef865a494d06f85ab8797303b"
    assert data["schema_version"] == "lob_sim.hmm_overhead_benchmark.v2"
    assert data["claim_ready"] is False
    assert data["environment"]["source"] == {
        "git_branch": "codex/hmm-regime-layer",
        "git_commit": "8c2a0040e1bfb007c5b681d9081bc4391b0b351f",
        "git_dirty": False,
    }
    assert data["parents"]["package"]["sha256"] == "cde2eacaa0ad7db95abe190b413fcd4b5ed4e31623b84716704f7f083fc30eee"
    assert (
        data["parents"]["benchmark_script_sha256"] == "8f16400f61c379f2f6b0dadbf649dce74ac1e649e520c2dd1843a1419c0d53c8"
    )


def test_portable_input_and_model_bytes_match_recorded_evidence():
    data = report()
    for relative, expected in (
        (data["input"]["path"], data["parents"]["input_sha256"]),
        (data["model_file"], data["parents"]["model_file_sha256"]),
    ):
        assert sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    segment = INPUT_ROOT / "capture_1791203092_f912172cf0594437a728d2eb9160d85e_000000.ndjson.zst"
    assert (
        sha256(segment.read_bytes()).hexdigest() == "514d464398c4ce2d1951a542c62be71681815935bdf68744921658ca11a0ce0a"
    )
    model = json.loads((INPUT_ROOT / "synthetic_frozen_model.json").read_text(encoding="utf-8"))
    assert model["model_sha256"] == data["model_sha256"]
    assert model["provenance"]["purpose"]  # Original synthetic-training provenance stays attached.


def test_common_workload_protocol_and_native_activity_are_explicit():
    data = report()
    protocol = data["protocol"]
    assert (
        protocol["warmups_per_mode"],
        protocol["measured_repetitions_per_mode"],
        protocol["memory_runs_per_mode"],
    ) == (3, 30, 1)
    assert protocol["active_policy_required"] is True
    assert Counter(map(tuple, protocol["measured_order"])) == Counter(
        {order: 5 for order in itertools.permutations(("baseline", "observe", "policy"))}
    )
    common = []
    for name, mode in data["modes"].items():
        config = dict(mode["config"])
        hmm = config.pop("hmm", None)
        config.pop("mm_strategy_profile")
        assert config["mm_order_qty"] == "0.010"
        assert config["mm_max_position"] == "0.05"
        assert config["mm_requote_ms"] == 500
        common.append(config)
        probe = mode["probe"]
        assert probe["records_processed"] == 2364
        assert probe["quote_count"] > 0
        assert probe["order_lifecycle_counts"]["rested_after_arrival"] > 0
        assert probe["event_trace_retention"]["rows_retained"] == 0
        assert probe["audit_retention"]["memory_bounded_by_tape_duration"]
        if name == "baseline":
            assert hmm is None and probe["hmm"] is None
        else:
            assert hmm["mode"] == name
            assert probe["hmm"]["status_counts"] == {"INVALID_BOOK": 2, "STALE": 6, "VALID": 112, "WARMING_UP": 10}
    assert common[0] == common[1] == common[2]
    assert data["observation_core_summary_book_fill_markout_latency_parity"]
    for field in ("core_sha256", "fill_audit_sha256", "markout_audit_sha256", "order_lifecycle_counts"):
        assert data["modes"]["baseline"]["probe"][field] == data["modes"]["observe"]["probe"][field]


def test_all_raw_timings_and_matched_round_quantiles_independently_reconcile():
    data = report()
    baseline = data["modes"]["baseline"]["raw_wall_ns"]
    for mode in data["modes"].values():
        samples = mode["raw_wall_ns"]
        assert len(samples) == 30 and all(type(value) is int and value > 0 for value in samples)
        ordered = sorted(samples)
        for name, probability in (("median", Fraction(1, 2)), ("p95", Fraction(19, 20)), ("p99", Fraction(99, 100))):
            position = probability * (len(ordered) - 1)
            lower = position.numerator // position.denominator
            fraction = position - lower
            expected = (1 - fraction) * ordered[lower] + fraction * ordered[min(lower + 1, len(ordered) - 1)]
            assert mode["wall_ns"][name] == pytest.approx(float(expected), rel=0, abs=1)
        ratios = [value / reference for value, reference in zip(samples, baseline)]
        assert mode["matched_round_relative_runtime"]["median"] == median(ratios)
        assert mode["median_relative_overhead_percent"] == (median(ratios) - 1) * 100
        assert len(mode["raw_peak_traced_bytes"]) == 1
        assert mode["peak_traced_bytes"] == mode["raw_peak_traced_bytes"][0] > 0
