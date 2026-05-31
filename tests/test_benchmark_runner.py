from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.benchmark_futures_replay import (
    BENCHMARK_SCHEMA_VERSION,
    benchmark_replay,
    write_benchmark_json,
)
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.inspection import file_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_benchmark_fixture(path: Path) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
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
    assert result["metadata"]["feed_adapter"] == {
        "name": "binance_usdm",
        "venue_label": "BINANCE_USDM",
        "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
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
