from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.sweep_futures_latency import (
    _validate_registry_rows,
    build_latency_sweep_metadata,
    run_latency_sweep,
    write_latency_sweep_outputs,
)
from lob_sim.record.format import NDJSONRecord, snapshot_payload


def _write_fixture(path: Path) -> Path:
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
            ts_local=2.3,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def test_latency_sweep_writes_caveated_csv_and_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "data"))
    fixture = _write_fixture(tmp_path / "fixture.ndjson")

    rows = run_latency_sweep(
        input_file=fixture,
        env_path=".env.example",
        profile="baseline",
        order_latencies_ms=[0.0, 10.0],
        cancel_latencies_ms=[0.0],
    )
    metadata = build_latency_sweep_metadata(
        input_file=fixture,
        env_path=".env.example",
        profile="baseline",
        order_latencies_ms=[0.0, 10.0],
        cancel_latencies_ms=[0.0],
    )
    paths = write_latency_sweep_outputs(
        rows,
        tmp_path / "latency",
        fixture,
        metadata=metadata,
        command="python latency",
    )

    assert len(rows) == 2
    assert [row["rank"] for row in rows] == [1, 2]
    assert {row["order_latency_ms"] for row in rows} == {0.0, 10.0}
    assert {row["cancel_latency_ms"] for row in rows} == {0.0}
    assert "diagnostic_score" in rows[0]
    assert all(row["registry_variant_id"] for row in rows)
    assert "fill_source_counts" in rows[0]
    assert "markout_by_fill_source" in rows[0]
    assert "order_lifecycle_counts" in rows[0]
    assert metadata["input_sha256"]
    assert metadata["base_config_digest"]
    assert metadata["feed_adapter"] == {
        "name": "binance_usdm",
        "venue_label": "BINANCE_USDM",
        "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
    }
    assert metadata["fill_model"] == "trade"
    assert metadata["research_registry"]["frozen"] is True
    assert len(metadata["research_registry"]["variants"]) == 2
    assert {row["registry_variant_id"] for row in rows} == {
        variant["variant_id"] for variant in metadata["research_registry"]["variants"]
    }
    assert paths["csv"].exists()
    assert paths["markdown"].exists()
    assert paths["registry"].exists()
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    assert registry["research_registry"] == metadata["research_registry"]
    assert registry["row_registry_variant_ids"] == sorted(row["registry_variant_id"] for row in rows)

    csv_text = paths["csv"].read_text(encoding="utf-8")
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "order_latency_ms,cancel_latency_ms" in csv_text
    assert "modeled order-arrival and cancel-ack delays" in markdown
    assert "not a latency-arbitrage, alpha, or profitability claim" in markdown
    assert "Feed adapter: `binance_usdm` (`BINANCE_USDM`)" in markdown
    assert "Public-L2 fill model: `trade`" in markdown
    assert "Frozen research registry SHA-256" in markdown
    assert metadata["research_registry"]["registry_sha256"] in markdown
    tampered = [dict(rows[0])]
    tampered[0]["registry_variant_id"] = "tampered"
    with pytest.raises(ValueError, match="one-to-one"):
        _validate_registry_rows(tampered, metadata["research_registry"])
    if all(row["fill_count"] == 0 for row in rows):
        assert "zero-fill diagnostic, not economic evidence" in markdown
    assert "python latency" in markdown
