from __future__ import annotations

from pathlib import Path

import pytest

from experiments.run_fill_assumption_envelope import run_envelope
from lob_sim.record.format import NDJSONRecord, snapshot_payload


def _set_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = {
        "RECORD_DIR": str(tmp_path / "records"),
        "RECORD_GZIP": "0",
        "LOG_LEVEL": "ERROR",
        "RESYNC_ON_GAP": "1",
        "SIM_ORDER_LATENCY_MS": "0",
        "SIM_CANCEL_LATENCY_MS": "0",
        "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
        "SIM_KILL_SWITCH_ENABLED": "0",
        "SIM_KILL_MAX_DRAWDOWN": "0",
        "SIM_KILL_MAX_CONSECUTIVE_LOSSES": "0",
        "MM_ENABLED": "1",
        "MM_REQUOTE_MS": "1000",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "0.01",
        "MM_HALF_SPREAD_BPS": "0.05",
        "MM_LAYERED_INNER_SPREAD_BPS": "0.05",
        "MM_LAYERED_OUTER_SPREAD_BPS": "0.15",
        "MM_SKEW_BPS_PER_UNIT": "0",
        "MM_VOLATILITY_WINDOW": "30",
        "MM_VOLATILITY_SPREAD_FACTOR": "0",
        "MM_QUEUE_REPOST_LOTS": "99",
        "MM_TRADE_IMBALANCE_WINDOW": "12",
        "MM_MICROSTRUCTURE_GATE_THRESHOLD": "0.20",
        "MM_MICROSTRUCTURE_GATE_BPS": "0.10",
        "MM_FEE_FLOOR_BUFFER_BPS": "0.02",
        "MM_TOXICITY_SPREAD_FACTOR": "0",
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
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.1", "0.003")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.002"]], "a": [["100.1", "0.003"]]},
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [["100.0", "0.001"]], "a": []},
        ),
        NDJSONRecord(
            ts_local=2.25,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def _stable_run(row: dict) -> dict:
    return {
        key: row[key]
        for key in [
            "profile",
            "input_digest",
            "config_digest",
            "normalized_config_digest",
            "fill_count",
            "realized_pnl",
            "unrealized_pnl",
            "total_fees",
            "avg_spread_captured",
            "adverse_fill_rate_1s",
            "fill_source_counts",
            "public_consumption_totals",
            "max_inventory",
            "kill_switch_triggered",
            "kill_switch_reason",
            "fill_assumption",
        ]
    }


def test_fill_assumption_envelope_is_deterministic_and_digest_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, tmp_path)
    replay_path = _write_replay(tmp_path / "fixture.ndjson")

    first = run_envelope(replay_path, ".env.example", tmp_path / "first")
    second = run_envelope(replay_path, ".env.example", tmp_path / "second")

    assert first["audit"]["ok"] is True
    assert first["profiles"] == ["conservative", "base", "aggressive"]
    assert {row["input_digest"] for row in first["runs"]} == {first["audit"]["input_digest"]}
    assert {row["normalized_config_digest"] for row in first["runs"]} == {first["audit"]["normalized_config_digest"]}
    assert [_stable_run(row) for row in first["runs"]] == [_stable_run(row) for row in second["runs"]]
    assert (tmp_path / "first" / "fill_envelope_summary.json").exists()
    assert (tmp_path / "first" / "fill_envelope_summary.csv").exists()
    assert (tmp_path / "first" / "fill_envelope_report.md").exists()
