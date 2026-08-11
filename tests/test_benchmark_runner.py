from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.benchmark_futures_replay import (
    BENCHMARK_SCHEMA_VERSION,
    REVIEWER_BENCHMARK_SCHEMA_VERSION,
    benchmark_replay,
    benchmark_reviewer_modes,
    write_benchmark_json,
)
from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.engine import SimulationEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_benchmark_fixture(path: Path) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={
                "symbol": "BTCUSDT",
                "tickSize": "0.1",
                "stepSize": "0.001",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "contractMultiplier": "1",
            },
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.1", "0.003")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.003"]]},
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def test_benchmark_replay_returns_machine_readable_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    input_path = _write_benchmark_fixture(tmp_path / "benchmark_fixture.ndjson")

    result = benchmark_replay(input_path, str(REPO_ROOT / ".env.example"))

    assert result["schema_version"] == BENCHMARK_SCHEMA_VERSION
    assert result["metadata"]["input_file"] == input_path.as_posix()
    assert result["metadata"]["input_sha256"] == file_sha256(input_path)
    assert result["metadata"]["config_digest"]
    assert result["metadata"]["config"]["book_top_n"] > 0
    assert result["metadata"]["feed_adapter"] == {
        "name": "binance_usdm",
        "venue_label": "BINANCE_USDM",
        "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
    }
    assert result["metadata"]["instrument_specs"] == {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "venue": "BINANCE_USDM",
            "price_currency": "USDT",
            "quantity_unit": "BTC",
            "tick_size": "0.1",
            "step_size": "0.001",
            "contract_multiplier": "1",
        }
    }
    assert set(result["metadata"]["source"]) == {"git_commit", "git_branch", "git_dirty"}
    assert result["event_counts"] == {
        "total_events": 4,
        "exchange_info_events": 1,
        "snapshot_events": 1,
        "depth_events": 1,
        "agg_trade_events": 1,
        "gap_count": 0,
    }
    assert result["timing"]["wall_time_seconds"] > 0
    assert result["timing"]["events_per_second"] > 0
    assert result["timing"]["loop_latency_p50_us"] >= 0
    assert result["timing"]["loop_latency_p99_us"] >= result["timing"]["loop_latency_p50_us"]
    assert result["memory"]["peak_traced_bytes"] >= 0
    assert result["memory"]["peak_traced_mib"] >= 0


def test_write_benchmark_json_creates_parseable_artifact(tmp_path: Path) -> None:
    result = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "metadata": {"input_sha256": "abc"},
        "event_counts": {"total_events": 1},
        "timing": {"events_per_second": 1.0},
        "memory": {"peak_traced_bytes": 1},
    }
    out_path = tmp_path / "nested" / "benchmark.json"

    write_benchmark_json(result, out_path)

    assert json.loads(out_path.read_text(encoding="utf-8")) == result


def test_reviewer_benchmark_modes_time_simulation_export_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    input_path = _write_benchmark_fixture(tmp_path / "benchmark_fixture.ndjson")

    result = benchmark_reviewer_modes(
        input_path,
        str(REPO_ROOT / ".env.example"),
        runs=1,
        pack_dir=REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough",
    )

    assert result["schema_version"] == REVIEWER_BENCHMARK_SCHEMA_VERSION
    assert result["metadata"]["input_sha256"] == file_sha256(input_path)
    assert set(result["modes"]) == {
        "replay_only",
        "simulation_no_export",
        "simulation_with_streaming_audit_export",
        "pack_audit",
    }
    assert result["modes"]["replay_only"]["event_counts"]["total_events"] == 4
    assert result["modes"]["simulation_no_export"]["event_counts"]["records_processed"] == 4
    no_export = result["modes"]["simulation_no_export"]
    assert no_export["event_trace_retention"]["memory_bounded_by_tape_duration"] is True
    assert no_export["event_trace_retention"]["rows_retained"] == 0
    assert no_export["audit_retention"]["memory_bounded_by_tape_duration"] is True
    assert no_export["audit_retention"]["fill_rows_retained"] == 0
    streaming_export = result["modes"]["simulation_with_streaming_audit_export"]
    assert streaming_export["artifact_labels"] == [
        "event_trace",
        "manifest",
        "markouts",
        "summary",
        "summary_csv",
        "trades",
    ]
    assert streaming_export["event_trace_retention"]["memory_bounded_by_tape_duration"] is True
    assert streaming_export["audit_retention"]["memory_bounded_by_tape_duration"] is True
    assert streaming_export["simulation_export"]["mode"] == "bounded_streaming"
    assert result["modes"]["pack_audit"]["audit_ok"] is True
    for mode in result["modes"].values():
        assert mode["timing"]["wall_time_seconds"] >= 0
        assert mode["memory"]["peak_traced_bytes"] >= 0


def test_engine_state_hash_is_independent_of_audit_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_ORDER_LATENCY_MS", "0")
    monkeypatch.setenv("SIM_CANCEL_LATENCY_MS", "0")
    input_path = _write_benchmark_fixture(tmp_path / "retention_fixture.ndjson")
    cfg = load_config(str(REPO_ROOT / ".env.example"))

    full = SimulationEngine(cfg)
    full.run(input_path)
    bounded = SimulationEngine(cfg, retain_event_trace=False, retain_audit_rows=False)
    bounded.run(input_path)

    assert bounded.event_trace == []
    assert bounded.metrics.fills_log == []
    assert bounded.metrics.fill_audit_sha256 == full.metrics.fill_audit_sha256
    assert bounded.metrics.markout_audit_sha256 == full.metrics.markout_audit_sha256
    assert bounded.state_sha256() == full.state_sha256()
