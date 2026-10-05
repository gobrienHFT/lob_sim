from __future__ import annotations

import json
import math
import tracemalloc
from dataclasses import replace
from hashlib import sha256

import pytest

import experiments.benchmark_hmm_overhead as module

from lob_sim.regime.artifact import save_model
from lob_sim.regime.dataset import instrument_grid_identity
from lob_sim.regime.validation import canonical_json
from lob_sim.book.types import InstrumentSpec
from lob_sim.record.schema import RecordValidationError
from test_hmm_dataset import cfg, tape
from test_hmm_policy import policy_settings


def inputs(tmp_path):
    path = tape(tmp_path / "market.ndjson", same_time_trades=True)
    model = tmp_path / "model.json"
    save_model(model, policy_settings().model)
    config = replace(cfg(), mm_strategy_profile="research_mm", mm_requote_ms=500)
    return path, model, config


def test_quantiles_against_independent_sorted_linear_interpolation():
    assert module.percentiles([10, 0, 20]) == {"median": 10.0, "p95": 19.0, "p99": 19.8}
    assert module.percentiles([7]) == {"median": 7.0, "p95": 7.0, "p99": 7.0}
    with pytest.raises(ValueError):
        module.percentiles([])


def test_real_three_mode_measurements_are_separate_bounded_deterministic_and_paired(tmp_path, monkeypatch):
    path, model, configuration = inputs(tmp_path)
    calls = []
    original = module.SimulationEngine

    def engine(config, **kwargs):
        calls.append((config.hmm.mode if config.hmm else "baseline", tracemalloc.is_tracing(), kwargs))
        return original(config, **kwargs)

    monkeypatch.setattr(module, "SimulationEngine", engine)
    report = module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=2)
    assert len(calls) == 12
    assert all(not traced for _, traced, _ in calls[:9])
    assert all(traced for _, traced, _ in calls[9:])
    assert not tracemalloc.is_tracing()
    assert all(kwargs == {"retain_event_trace": False, "retain_audit_rows": False} for _, _, kwargs in calls)
    assert report["observation_core_summary_book_fill_markout_latency_parity"]
    assert not report["claim_ready"]
    assert (
        report["report_sha256"]
        == sha256(
            json.dumps(
                {key: value for key, value in report.items() if key != "report_sha256"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    assert report["modes"]["baseline"]["probe"]["core_sha256"] == report["modes"]["observe"]["probe"]["core_sha256"]
    for name, result in report["modes"].items():
        assert len(result["raw_wall_ns"]) == 2
        assert all(value > 0 for value in result["raw_wall_ns"])
        assert result["peak_traced_bytes"] > 0
        assert result["probe"]["event_trace_retention"]["rows_retained"] == 0
        if name != "baseline":
            assert result["probe"]["hmm"]["status_counts"]["VALID"] > 0
        ratios = [
            value / baseline
            for value, baseline in zip(result["raw_wall_ns"], report["modes"]["baseline"]["raw_wall_ns"])
        ]
        expected_median = sum(sorted(ratios)) / 2
        # Addition and linear interpolation can round one bit differently.
        # This is an arithmetic oracle, not a bitwise timing identity.
        assert result["matched_round_relative_runtime"]["median"] == pytest.approx(
            expected_median, rel=0, abs=math.ulp(expected_median)
        )


def test_matched_round_median_oracle_handles_one_ulp_roundoff(tmp_path, monkeypatch):
    # Synthetic clock values reproduce the original intermittent assertion.
    # They are arithmetic test data, never a benchmark or speedup result.
    durations = [100000] * 3 + [77387, 77387, 48931, 8602, 67510, 67510] + [100000] * 3
    clock = iter(value for duration in durations for value in (0, duration))
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: next(clock))
    test_real_three_mode_measurements_are_separate_bounded_deterministic_and_paired(tmp_path, monkeypatch)


@pytest.mark.parametrize("control", [{"warmups": 0}, {"repetitions": True}, {"repetitions": 1001}, {"memory_runs": 11}])
def test_bad_protocol_rejected_before_replay(tmp_path, control):
    path, model, configuration = inputs(tmp_path)
    with pytest.raises(ValueError):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", **control)


def test_existing_tracer_rejected_without_stopping_callers_tracer(tmp_path):
    path, model, configuration = inputs(tmp_path)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="tracemalloc"):
            module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT")
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("missing_metadata", [False, True])
def test_native_instrument_preflight_aborts_before_engine_construction(tmp_path, monkeypatch, missing_metadata):
    from decimal import Decimal

    path, model, configuration = inputs(tmp_path)
    if missing_metadata:
        path.write_text(
            "\n".join(line for line in path.read_text().splitlines() if '"type": "exchangeInfo"' not in line) + "\n",
            encoding="utf-8",
        )
    else:
        original = policy_settings().model
        wrong_grid = InstrumentSpec("BTCUSDT", Decimal("0.2"), Decimal("0.001"), venue="BINANCE_USDM")
        provenance = json.loads(original.provenance_json)
        provenance["training"]["instrument_sha256"] = instrument_grid_identity(wrong_grid)
        save_model(
            tmp_path / "other_model.json",
            replace(
                original,
                provenance_json=canonical_json(provenance),
            ),
        )
        model = tmp_path / "other_model.json"
    monkeypatch.setattr(module, "SimulationEngine", lambda *args, **kwargs: pytest.fail("constructed a timed engine"))
    with pytest.raises(ValueError, match="benchmark preflight"):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1)


def test_no_clobber_before_expensive_work(tmp_path):
    out = tmp_path / "already.json"
    out.write_text("preserved")
    with pytest.raises(FileExistsError):
        module.main(["--file", "missing", "--model", "missing", "--json-out", str(out)])
    assert out.read_text() == "preserved"


def test_native_preflight_drains_corrupt_tail_before_any_timed_engine(tmp_path, monkeypatch):
    path, model, configuration = inputs(tmp_path)
    path.write_text(path.read_text() + "{broken_tail\n", encoding="utf-8")
    monkeypatch.setattr(module, "SimulationEngine", lambda *args, **kwargs: pytest.fail("constructed a timed engine"))
    with pytest.raises(RecordValidationError, match="invalid JSON"):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1)


def test_invalid_config_rejected_before_model_load(tmp_path):
    path, model, configuration = inputs(tmp_path)
    with pytest.raises(ValueError, match="fixed latency"):
        module.benchmark_hmm(path, model, replace(configuration, sim_latency_mode="empirical"), symbol="BTCUSDT")


@pytest.mark.parametrize("value", [1, None, "true"])
def test_active_policy_requirement_is_strict_before_model_access(tmp_path, value):
    with pytest.raises(ValueError, match="boolean"):
        module.benchmark_hmm(
            tmp_path / "missing", tmp_path / "missing_model", cfg(), symbol="BTCUSDT", require_active_policy=value
        )


@pytest.mark.parametrize("quotes,rested", [(0, 0), (12, 0)])
def test_active_policy_gate_rejects_zero_or_rejected_only_quoting(tmp_path, monkeypatch, quotes, rested):
    path, model, configuration = inputs(tmp_path)
    original = module.replay_probe

    def no_active_work(engine):
        probe = original(engine)
        if engine.regime is not None and engine.regime.settings.mode == "policy":
            probe["quote_count"] = quotes
            probe["order_lifecycle_counts"] = {"rested_after_arrival": rested}
        return probe

    monkeypatch.setattr(module, "replay_probe", no_active_work)
    with pytest.raises(ValueError, match="accepted resting quotes"):
        module.benchmark_hmm(
            path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1, require_active_policy=True
        )


def test_real_multilot_policy_can_be_active_without_requiring_fills(tmp_path):
    from decimal import Decimal

    path, model, configuration = inputs(tmp_path)
    report = module.benchmark_hmm(
        path,
        model,
        replace(configuration, mm_order_qty=Decimal("0.010"), mm_half_spread_bps=Decimal("100")),
        symbol="BTCUSDT",
        warmups=1,
        repetitions=1,
        require_active_policy=True,
    )
    assert report["schema_version"] == "lob_sim.hmm_overhead_benchmark.v2"
    assert report["protocol"]["active_policy_required"]
    probe = report["modes"]["policy"]["probe"]
    assert probe["quote_count"] > 0
    assert probe["order_lifecycle_counts"]["rested_after_arrival"] > 0
    assert probe["fill_count"] == 0  # Live quoting is work even when no trade reaches it.


def test_no_valid_inference_is_not_published_as_hmm_overhead(tmp_path, monkeypatch):
    path, model, configuration = inputs(tmp_path)
    original = module.replay_probe

    def invalid(engine):
        value = original(engine)
        if value["hmm"] is not None:
            value["hmm"]["status_counts"]["VALID"] = 0
        return value

    monkeypatch.setattr(module, "replay_probe", invalid)
    with pytest.raises(ValueError, match="never had valid inference"):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1)


def test_loading_race_cannot_bind_model_to_different_file_bytes(tmp_path, monkeypatch):
    path, model, configuration = inputs(tmp_path)
    original = module.load_model

    def changed(file):
        result = original(file)
        file.write_text(file.read_text() + "\n")
        return result

    monkeypatch.setattr(module, "load_model", changed)
    with pytest.raises(ValueError, match="changed during loading"):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1)


def test_observation_parity_failure_is_not_a_valid_benchmark(tmp_path, monkeypatch):
    path, model, configuration = inputs(tmp_path)
    original = module.replay_probe

    def changed(engine):
        value = original(engine)
        if engine.regime is not None and engine.regime.settings.mode == "observe":
            value["core_sha256"] = "a" * 64
        return value

    monkeypatch.setattr(module, "replay_probe", changed)
    with pytest.raises(ValueError, match="baseline/observe"):
        module.benchmark_hmm(path, model, configuration, symbol="BTCUSDT", warmups=1, repetitions=1)


@pytest.mark.parametrize("policy_quotes", [0, 12])
@pytest.mark.parametrize("require_active", [False, True])
def test_cli_reports_workload_and_warns_only_for_inactive_quoting(
    tmp_path, monkeypatch, capsys, policy_quotes, require_active
):
    report = {
        "modes": {
            name: {
                "wall_ns": {"median": 1_000_000_000},
                "median_relative_overhead_percent": 0,
                "peak_traced_bytes": 123,
                "probe": {
                    "quote_count": policy_quotes if name == "policy" else 12,
                    "cancel_count": 3,
                    "fill_count": 4,
                },
            }
            for name in module.MODES
        }
    }
    published = []
    options = []

    def benchmark(*args, **kwargs):
        options.append(kwargs)
        return report

    monkeypatch.setattr(module, "benchmark_hmm", benchmark)
    monkeypatch.setattr(module, "publish_json", lambda path, value: published.append((path, value)))
    output = tmp_path / "new.json"
    arguments = ["--file", "unused", "--model", "unused", "--json-out", str(output)]
    if require_active:
        arguments.append("--require-active-policy")
    assert module.main(arguments) == 0
    assert options[0]["require_active_policy"] is require_active
    assert published == [(output, report)]
    text = capsys.readouterr().out
    assert f"quotes={policy_quotes}; cancels=3; fills=4" in text
    assert ("WARNING: policy issued no quotes" in text) is (policy_quotes == 0)
    assert "not exchange latency or a policy-benefit claim" in text
