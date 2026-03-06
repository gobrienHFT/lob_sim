from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path

from lob_sim.config import Config, load_config
from lob_sim.options.demo import OptionsMMConfig, OptionsMarketMakerDemo
from lob_sim.sim.engine import SimulationEngine
from lob_sim.util import write_summary_csv


def _build_config(tmp_path: Path) -> Config:
    values = {
        "BINANCE_FAPI_BASE": "https://fapi.binance.com",
        "BINANCE_FWS_BASE": "wss://fstream.binance.com",
        "DEPTH_STREAM_SUFFIX": "@depth@100ms",
        "TRADE_STREAM_SUFFIX": "@aggTrade",
        "SYMBOLS": "BTCUSDT",
        "SNAPSHOT_LIMIT": "1000",
        "BOOK_TOP_N": "50",
        "COLLECT_SECONDS": "10",
        "RECORD_DIR": str(tmp_path),
        "RECORD_FORMAT": "ndjson",
        "RECORD_GZIP": "0",
        "RECORD_FLUSH_EVERY": "2000",
        "HTTP_TIMEOUT": "10",
        "HTTP_RETRIES": "2",
        "RATE_LIMIT_REQ_PER_SEC": "8",
        "WS_PING_INTERVAL": "180",
        "WS_PING_TIMEOUT": "600",
        "WS_RECONNECT_MAX_SEC": "30",
        "RESYNC_ON_GAP": "1",
        "SIM_SEED": "1",
        "SIM_ORDER_LATENCY_MS": "25",
        "SIM_CANCEL_LATENCY_MS": "25",
        "MM_ENABLED": "1",
        "MM_REQUOTE_MS": "250",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "0.01",
        "MM_HALF_SPREAD_BPS": "2.0",
        "MM_SKEW_BPS_PER_UNIT": "10.0",
        "FEES_MAKER_BPS": "-0.2",
        "FEES_TAKER_BPS": "4.0",
        "LOG_LEVEL": "INFO",
    }
    for key, value in values.items():
        os.environ[key] = value
    return load_config(".env")


def test_write_summary_csv_serializes_nested_values(tmp_path: Path):
    path = tmp_path / "summary.csv"
    write_summary_csv(
        path,
        {
            "total_pnl": 12.5,
            "position": {"BTCUSDT": 0.01},
            "fills": [{"symbol": "BTCUSDT", "qty": 1}],
            "fee_bps": Decimal("1.5"),
        },
        exclude_keys={"fills"},
    )

    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["total_pnl"] == "12.5"
    assert row["position"] == '{"BTCUSDT": 0.01}'
    assert row["fee_bps"] == "1.5"
    assert "fills" not in row


def test_engine_write_outputs_writes_excel_friendly_csvs(tmp_path: Path):
    cfg = replace(_build_config(tmp_path), record_dir=tmp_path)
    engine = SimulationEngine(cfg)

    class StubMetrics:
        def get_summary(self, _books):
            return {
                "total_pnl": 7.25,
                "fill_count": 1,
                "inventory_by_symbol": {"BTCUSDT": 0.001},
                "fills": [
                    {
                        "ts_local": 1.0,
                        "symbol": "BTCUSDT",
                        "side": "bid",
                        "price": 100000.0,
                        "qty": 0.001,
                        "maker": True,
                        "order_id": "o1",
                        "mid_at_fill": 100000.5,
                        "regime": "neutral",
                        "queue_ahead_lots": 0,
                        "time_in_book_ms": 10.0,
                        "markout_horizon": 1.0,
                        "book_bid_tick": 1000000,
                        "book_ask_tick": 1000010,
                    }
                ],
                "markout_events": [{"side": "bid", "markout": -0.5}],
            }

    output_files, summary = engine.write_outputs("raw_test.ndjson", StubMetrics())

    assert output_files["summary"].exists()
    assert output_files["summary_csv"].exists()
    assert output_files["trades"].exists()
    assert summary["output_files"]["summary_csv"] == str(output_files["summary_csv"])

    with output_files["summary_csv"].open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["total_pnl"] == "7.25"
    assert row["inventory_by_symbol"] == '{"BTCUSDT": 0.001}'
    assert "fills" not in row
    assert "markout_events" not in row

    with output_files["trades"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"


def test_options_demo_writes_summary_csv(tmp_path: Path, monkeypatch):
    def _write_plot_stub(self, path: Path) -> None:
        path.write_bytes(b"")

    monkeypatch.setattr(OptionsMarketMakerDemo, "_write_plot", _write_plot_stub)
    summary = OptionsMarketMakerDemo(OptionsMMConfig(steps=8, seed=3)).run(tmp_path)

    summary_csv = tmp_path / "options_mm_summary.csv"
    config_csv = tmp_path / "options_mm_config.csv"
    walkthrough = tmp_path / "options_mm_walkthrough.md"
    trades_csv = tmp_path / "options_mm_trades.csv"
    assert summary_csv.exists()
    assert config_csv.exists()
    assert walkthrough.exists()
    assert trades_csv.exists()
    assert summary["output_files"]["summary_csv"] == str(summary_csv)
    assert summary["output_files"]["config_csv"] == str(config_csv)
    assert summary["output_files"]["walkthrough"] == str(walkthrough)

    with summary_csv.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["trade_count"] == str(summary["trade_count"])
    assert "summary_csv" in row["output_files"]

    with trades_csv.open("r", encoding="utf-8", newline="") as handle:
        trade_row = next(csv.DictReader(handle))

    assert "delta_reservation_component" in trade_row
    assert "gamma_half_spread_component" in trade_row
    assert "portfolio_delta_after_hedge" in trade_row

    assert "Core quote logic" in walkthrough.read_text(encoding="utf-8")
