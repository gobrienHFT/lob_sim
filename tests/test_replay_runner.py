from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.runner import replay
from lob_sim.config import load_config


def test_replay_returns_structured_symbol_diagnostics(tmp_path: Path, capsys) -> None:
    path = tmp_path / "replay.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    result = replay(path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result.events_processed == 3
    assert result.depth_events == 1
    assert result.gap_count == 0
    assert result.events_per_sec > 0
    assert set(result.symbols) == {"BTCUSDT"}
    assert result.symbols["BTCUSDT"].snapshot_seen is True
    assert result.symbols["BTCUSDT"].synced is True
    assert result.symbols["BTCUSDT"].total_levels == 2
    assert result.symbols["BTCUSDT"].last_update_id == 105


def test_replay_counts_non_resync_gap_without_advancing_book(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    path = tmp_path / "gap.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
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
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.008"]], "a": [["100.1", "0.009"]]},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 110, "pu": 999, "b": [["100.0", "0.001"]], "a": [["100.1", "0.001"]]},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    cfg = replace(load_config(".env.example"), resync_on_gap=False)

    result = replay(path, cfg)

    assert result.gap_count == 1
    assert result.symbols["BTCUSDT"].gap_count == 1
    assert result.symbols["BTCUSDT"].synced is False
    assert result.symbols["BTCUSDT"].last_update_id is None
    # Gap invalidation clears the previous epoch; no stale liquidity remains
    # available to fills or markouts while awaiting a fresh snapshot.
    assert result.symbols["BTCUSDT"].total_levels == 0
