from __future__ import annotations

from pathlib import Path

from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.runner import replay


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
