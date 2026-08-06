from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim import benchmark
from lob_sim.config import Config
from lob_sim.provenance import config_provenance


def _config(tmp_path: Path) -> Config:
    return Config(
        binance_api_key="super-secret-api-key",
        binance_api_secret="super-secret-api-secret",
        binance_fapi_base="https://fapi.binance.com",
        binance_fws_base="wss://fstream.binance.com",
        symbols=("BTCUSDT",),
        depth_stream_suffix="@depth@100ms",
        trade_stream_suffix="@aggTrade",
        snapshot_limit=1000,
        book_top_n=50,
        collect_seconds=10,
        record_dir=tmp_path,
        record_format="ndjson",
        record_gzip=False,
        record_flush_every=100,
        http_timeout=10.0,
        http_retries=2,
        rate_limit_req_per_sec=8.0,
        ws_ping_interval=180.0,
        ws_ping_timeout=600.0,
        ws_reconnect_max_sec=30.0,
        resync_on_gap=True,
        sim_order_latency_ms=25.0,
        sim_cancel_latency_ms=25.0,
        mm_enabled=True,
        mm_requote_ms=250.0,
        mm_order_qty=Decimal("0.001"),
        mm_max_position=Decimal("0.01"),
        mm_half_spread_bps=Decimal("2.0"),
        mm_skew_bps_per_unit=Decimal("10.0"),
        fees_maker_bps=Decimal("-0.2"),
        fees_taker_bps=Decimal("4.0"),
        log_level="INFO",
    )


def _fixture(tmp_path: Path) -> Path:
    rows = [
        {
            "ts_local": 1.0,
            "symbol": "BTCUSDT",
            "type": "exchangeInfo",
            "data": {"tickSize": "0.1", "stepSize": "0.001"},
        },
        {
            "ts_local": 1.1,
            "symbol": "BTCUSDT",
            "type": "snapshot",
            "data": {"lastUpdateId": 10, "bids": [], "asks": []},
        },
        {
            "ts_local": 1.2,
            "symbol": "BTCUSDT",
            "type": "aggTrade",
            "data": {"p": "100.0", "q": "0.001", "m": False},
        },
    ]
    path = tmp_path / "tiny.ndjson"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_benchmark_reports_provenance_and_uses_fresh_engines(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    config = _config(tmp_path)
    instances = []

    class FakeEngine:
        def __init__(self, received_config):
            assert received_config is config
            instances.append(self)

        def run(self, received_path):
            assert Path(received_path) == fixture.resolve()

    clock_values = iter((0, 1_000_000_000, 2_000_000_000, 4_000_000_000, 5_000_000_000, 8_000_000_000))
    traced_peaks = iter((101, 202, 303))
    monkeypatch.setattr(benchmark, "SimulationEngine", FakeEngine)
    monkeypatch.setattr(benchmark, "perf_counter_ns", lambda: next(clock_values))
    monkeypatch.setattr(benchmark.tracemalloc, "is_tracing", lambda: False)
    monkeypatch.setattr(benchmark.tracemalloc, "start", lambda: None)
    monkeypatch.setattr(benchmark.tracemalloc, "reset_peak", lambda: None)
    monkeypatch.setattr(benchmark.tracemalloc, "get_traced_memory", lambda: (0, next(traced_peaks)))
    monkeypatch.setattr(benchmark.tracemalloc, "stop", lambda: None)

    report = benchmark.benchmark_simulation(
        fixture,
        config,
        warmups=2,
        repetitions=3,
        known_limitations=("Synthetic test engine; no simulator work measured.",),
        reproduction_command="python -m benchmark-the-fixture --exact",
    )

    assert len(instances) == 8
    assert len({id(instance) for instance in instances}) == 8
    assert report["fixture"] == {
        "path": str(fixture.resolve()),
        "size_bytes": fixture.stat().st_size,
        "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "record_count": 3,
    }
    assert report["protocol"]["warmup_runs"] == 2
    assert report["protocol"]["measured_runs"] == 3
    assert [run["duration_seconds"] for run in report["runs"]] == [1.0, 2.0, 3.0]
    assert [run["events_per_second"] for run in report["runs"]] == [3.0, 1.5, 1.0]
    assert [run["peak_bytes"] for run in report["memory_runs"]] == [101, 202, 303]
    assert report["summary"]["duration_seconds"]["median"] == 2.0
    assert report["summary"]["duration_seconds"]["p95"] == pytest.approx(2.9)
    assert report["summary"]["duration_seconds"]["stdev"] == pytest.approx(0.8164965809)
    assert report["summary"]["peak_bytes"]["median"] == 202
    assert report["summary"]["peak_bytes"]["p95"] == pytest.approx(292.9)
    assert report["metric_definitions"]["events_per_second"]["unit"] == "records/second"
    assert report["metric_definitions"]["peak_bytes"]["unit"] == "bytes"
    assert report["protocol"]["timing_instrumentation"] == ("perf_counter_ns with tracemalloc disabled")
    assert report["environment"]["python"]["version"]
    assert report["environment"]["platform"]["platform_string"]
    assert "model" in report["environment"]["cpu"]
    assert report["created_at_utc"].endswith("Z")
    assert report["known_limitations"][-1] == "Synthetic test engine; no simulator work measured."
    assert report["reproduction_command"] == "python -m benchmark-the-fixture --exact"
    assert report["comparator"]["baseline"]["type"] == "first_measured_run"
    assert report["comparator"]["baseline"]["run"] == 1
    assert report["comparator"]["external_comparator"] is None
    assert report["comparator"]["comparison_scope"]["fixture_sha256"] == report["fixture"]["sha256"]
    assert (
        report["comparator"]["comparison_scope"]["code_fingerprint_sha256"]
        == report["code"]["fingerprint_sha256"]
    )
    assert (
        report["comparator"]["comparison_scope"]["configuration_fingerprint_sha256"]
        == report["configuration"]["fingerprint_sha256"]
    )
    assert len(report["code"]["fingerprint_sha256"]) == 64
    assert report["code"]["python_file_count"] > 0

    serialized = json.dumps(report, sort_keys=True)
    assert "super-secret-api-key" not in serialized
    assert "super-secret-api-secret" not in serialized
    assert "binance_api_key" not in report["configuration"]["values"]
    assert "binance_api_secret" not in report["configuration"]["values"]


def test_config_fingerprint_excludes_secrets_but_tracks_public_parameters(tmp_path):
    config = _config(tmp_path)
    original = config_provenance(config)
    changed_secrets = config_provenance(
        replace(config, binance_api_key="different-key", binance_api_secret="different-secret")
    )
    changed_public_value = config_provenance(replace(config, mm_requote_ms=500.0))

    assert original["fingerprint_sha256"] == changed_secrets["fingerprint_sha256"]
    assert original["fingerprint_sha256"] != changed_public_value["fingerprint_sha256"]
    assert original["excluded_fields"] == ["binance_api_key", "binance_api_secret"]
    assert original["values"]["mm_order_qty"] == "0.001"
    assert original["values"]["symbols"] == ["BTCUSDT"]


def test_benchmark_builds_guarded_canonical_reproduction_command(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)

    class FakeEngine:
        def __init__(self, _config):
            pass

        def run(self, _path):
            pass

    monkeypatch.setattr(benchmark, "SimulationEngine", FakeEngine)
    report = benchmark.benchmark_simulation(fixture, _config(tmp_path), warmups=0, repetitions=1)

    command = report["reproduction_command"]
    assert "-m lob_sim.benchmark" in command
    assert str(fixture.resolve()) in command
    assert "--warmups 0" in command
    assert "--repetitions 1" in command
    assert report["fixture"]["sha256"] in command
    assert report["configuration"]["fingerprint_sha256"] in command
    assert report["code"]["fingerprint_sha256"] in command


@pytest.mark.parametrize(
    ("warmups", "repetitions", "message"),
    [(-1, 1, "warmups must be >= 0"), (0, 0, "repetitions must be > 0")],
)
def test_benchmark_rejects_invalid_protocol(tmp_path, warmups, repetitions, message):
    with pytest.raises(ValueError, match=message):
        benchmark.benchmark_simulation(
            _fixture(tmp_path),
            _config(tmp_path),
            warmups=warmups,
            repetitions=repetitions,
        )


def test_cli_fingerprint_mismatch_fails_before_running_simulation(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)

    class MustNotRun:
        def __init__(self, _config):
            raise AssertionError("fingerprint preflight must happen before simulation")

    monkeypatch.setattr(benchmark, "SimulationEngine", MustNotRun)

    with pytest.raises(SystemExit):
        benchmark.main(
            [
                "--input",
                str(fixture),
                "--warmups",
                "0",
                "--repetitions",
                "1",
                "--expect-fixture-sha256",
                "0" * 64,
            ]
        )
