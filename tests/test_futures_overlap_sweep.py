from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.sweep_futures_overlap import (
    DEFAULT_FILL_MODELS,
    DEFAULT_OVERLAP_WINDOWS_MS,
    _parse_fill_models,
    _parse_windows,
    run_overlap_sweep,
    write_overlap_outputs,
)
from lob_sim.record.format import NDJSONRecord, snapshot_payload


def _set_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = {
        "RECORD_DIR": str(tmp_path / "records"),
        "RECORD_GZIP": "0",
        "LOG_LEVEL": "ERROR",
        "RESYNC_ON_GAP": "1",
        "SIM_FILL_MODEL": "trade",
        "FILL_PROFILE": "base",
        "SIM_ORDER_LATENCY_MS": "0",
        "SIM_CANCEL_LATENCY_MS": "0",
        "MM_ENABLED": "0",
        "FEES_MAKER_BPS": "0",
        "FEES_TAKER_BPS": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _write_replay(path: Path) -> Path:
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
            data=snapshot_payload(100, [("100.0", "0.010")], [("100.1", "0.010")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.010"]], "a": [["100.1", "0.010"]]},
        ),
        # The trade is the selected economic signal. The following depth
        # reduction is corroborated only when its receipt-time age is inside
        # the configured overlap window.
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
        NDJSONRecord(
            ts_local=3.05,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.008"]], "a": []},
        ),
        # A second pair runs in the opposite arrival order so the depth-only
        # signal also exercises the overlap window.
        NDJSONRecord(
            ts_local=4.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 107, "u": 107, "pu": 106, "b": [], "a": [["100.1", "0.008"]]},
        ),
        NDJSONRecord(
            ts_local=4.05,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.1", "q": "0.002", "m": False},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def test_parse_windows_requires_unique_nonnegative_integer_ms() -> None:
    assert _parse_windows("0, 125,250") == DEFAULT_OVERLAP_WINDOWS_MS
    with pytest.raises(ValueError, match="non-negative"):
        _parse_windows("0,-1")
    with pytest.raises(ValueError, match="unique"):
        _parse_windows("125,125")


def test_parse_fill_models_requires_known_unique_signals() -> None:
    assert _parse_fill_models("trade, depth") == DEFAULT_FILL_MODELS
    with pytest.raises(ValueError, match="trade or depth"):
        _parse_fill_models("trade,other")
    with pytest.raises(ValueError, match="unique"):
        _parse_fill_models("depth,depth")


def test_overlap_sweep_is_frozen_and_window_changes_corroboration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, tmp_path)
    replay_path = _write_replay(tmp_path / "overlap.ndjson")

    first = run_overlap_sweep(replay_path, ".env.example")
    second = run_overlap_sweep(replay_path, ".env.example")

    assert first["schema_version"] == "lob_sim.futures_overlap_sensitivity.v2"
    assert first["fill_models"] == ["trade", "depth"]
    assert first["windows_ms"] == [0, 125, 250]
    assert first["audit"]["ok"] is True
    assert first["research_registry"]["frozen"] is True
    assert first["research_registry"] == second["research_registry"]
    assert [row["state_sha256"] for row in first["runs"]] == [row["state_sha256"] for row in second["runs"]]

    rows = {(row["fill_model"], row["overlap_window_ms"]): row for row in first["runs"]}
    assert len(first["runs"]) == 6
    assert rows[("trade", 0)]["uncorroborated_depth_reduction_lots"] == 4
    assert rows[("trade", 125)]["corroborated_depth_reduction_lots"] == 2
    assert rows[("trade", 250)]["corroborated_depth_reduction_lots"] == 2
    assert rows[("trade", 125)]["uncorroborated_depth_reduction_lots"] == 2
    assert rows[("trade", 250)]["uncorroborated_depth_reduction_lots"] == 2
    assert rows[("trade", 0)]["public_consumption_totals"]["total_overlap_netted_lots"] == 0
    assert rows[("trade", 125)]["public_consumption_totals"]["total_overlap_netted_lots"] == 2
    assert rows[("trade", 250)]["public_consumption_totals"]["total_overlap_netted_lots"] == 2
    assert rows[("depth", 0)]["public_consumption_totals"]["total_overlap_netted_lots"] == 0
    assert rows[("depth", 125)]["public_consumption_totals"]["total_overlap_netted_lots"] == 2
    assert rows[("depth", 250)]["public_consumption_totals"]["total_overlap_netted_lots"] == 2
    assert all(row["scenario_id"].startswith("public_l2:profile=base:signal=") for row in first["runs"])
    assert all(row["execution_claim_ready"] is False for row in first["runs"])

    outputs = write_overlap_outputs(first, tmp_path / "out")
    assert set(outputs) == {"json", "csv", "markdown", "registry"}
    assert json.loads(outputs["json"].read_text(encoding="utf-8"))["audit"]["ok"] is True
    report = outputs["markdown"].read_text(encoding="utf-8")
    assert "Public L2 cannot prove private fills" in report
    assert "125" in report
