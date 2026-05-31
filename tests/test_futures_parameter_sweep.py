from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from experiments.sweep_futures_parameters import run_sweep, write_sweep_outputs
from lob_sim.record.format import NDJSONRecord, snapshot_payload


def _write_fixture(path: Path) -> Path:
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
            ts_local=2.4,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def test_parameter_sweep_writes_ranked_csv_and_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "data"))
    fixture = _write_fixture(tmp_path / "fixture.ndjson")

    rows = run_sweep(
        input_file=fixture,
        env_path=".env.example",
        profiles=["baseline"],
        half_spreads_bps=[Decimal("0.05")],
        queue_repost_lots=[0],
    )
    paths = write_sweep_outputs(rows, tmp_path / "sweep", fixture)

    assert len(rows) == 1
    assert rows[0]["rank"] == 1
    assert "diagnostic_score" in rows[0]
    assert paths["csv"].exists()
    assert paths["markdown"].exists()
    assert "not an alpha or profitability claim" in paths["markdown"].read_text(encoding="utf-8")
