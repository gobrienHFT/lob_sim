from __future__ import annotations

import csv
import json
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path

import pytest

from lob_sim.config import Config, load_config
from lob_sim.options.demo import (
    OptionsMarketMakerDemo,
    build_options_config,
    format_brief_summary,
    format_terminal_summary,
)
from lob_sim.options.markout import signed_markout
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


def test_options_demo_writes_demo_artifacts(tmp_path: Path, monkeypatch):
    def _write_plots_stub(self, out_dir: Path) -> dict[str, str]:
        names = {
            "pnl_over_time_plot": "pnl_over_time.png",
            "realized_vs_unrealized_plot": "realized_vs_unrealized.png",
            "spot_path_plot": "spot_path.png",
            "inventory_over_time_plot": "inventory_over_time.png",
            "net_delta_over_time_plot": "net_delta_over_time.png",
            "markout_distribution_plot": "markout_distribution.png",
            "toxic_vs_nontoxic_plot": "toxic_vs_nontoxic_markout.png",
            "top_traded_contracts_plot": "top_traded_contracts.png",
        }
        paths: dict[str, str] = {}
        for key, name in names.items():
            path = out_dir / name
            path.write_bytes(b"")
            paths[key] = str(path)
        return paths

    monkeypatch.setattr(OptionsMarketMakerDemo, "_write_plots", _write_plots_stub)
    summary = OptionsMarketMakerDemo(build_options_config(steps=8, seed=3, scenario="toxic_flow")).run(
        tmp_path,
        progress_every=4,
    )

    summary_json = tmp_path / "summary.json"
    report_md = tmp_path / "demo_report.md"
    fills_csv = tmp_path / "fills.csv"
    checkpoints_csv = tmp_path / "checkpoints.csv"
    pnl_timeseries_csv = tmp_path / "pnl_timeseries.csv"
    positions_csv = tmp_path / "positions_final.csv"
    pnl_plot = tmp_path / "pnl_over_time.png"

    assert summary_json.exists()
    assert report_md.exists()
    assert fills_csv.exists()
    assert checkpoints_csv.exists()
    assert pnl_timeseries_csv.exists()
    assert positions_csv.exists()
    assert pnl_plot.exists()

    with summary_json.open("r", encoding="utf-8") as handle:
        summary_on_disk = json.load(handle)

    assert summary_on_disk["scenario"] == "toxic_flow"
    assert summary_on_disk["output_files"]["fills"] == str(fills_csv)
    assert summary["output_files"]["report"] == str(report_md)
    assert summary_on_disk["markout_definition"].startswith("Signed markout")

    with fills_csv.open("r", encoding="utf-8", newline="") as handle:
        fill_row = next(csv.DictReader(handle))

    assert "spot_before" in fill_row
    assert "signed_markout" in fill_row
    assert "markout_reference_fair_value" in fill_row
    assert "comment_flag" in fill_row

    with checkpoints_csv.open("r", encoding="utf-8", newline="") as handle:
        checkpoint_row = next(csv.DictReader(handle))

    assert "ending_pnl" in checkpoint_row
    assert "net_delta" in checkpoint_row
    assert "total_signed_markout" in checkpoint_row

    with pnl_timeseries_csv.open("r", encoding="utf-8", newline="") as handle:
        pnl_row = next(csv.DictReader(handle))

    assert "spot" in pnl_row
    assert "ending_pnl" in pnl_row
    assert "inventory_contracts" in pnl_row
    assert "net_delta" in pnl_row

    report_text = report_md.read_text(encoding="utf-8")
    assert "Options market making case study" in report_text
    assert "Markout definition" in report_text
    assert "Glossary" in report_text


def test_options_presets_and_summary_helpers():
    calm = build_options_config(steps=12, seed=1, scenario="calm_market")
    toxic = build_options_config(steps=12, seed=1, scenario="toxic_flow")

    assert toxic.toxic_flow_prob > calm.toxic_flow_prob
    assert toxic.scenario_name == "toxic_flow"

    summary = {
        "scenario": toxic.scenario_name,
        "scenario_description": toxic.scenario_description,
        "seed": toxic.seed,
        "steps": toxic.steps,
        "spot_start": 100.0,
        "spot_final": 101.5,
        "ending_pnl": 12.5,
        "realized_pnl": 9.0,
        "unrealized_pnl": 3.5,
        "gross_spread_captured": 14.2,
        "hedge_costs": 1.2,
        "total_signed_markout": -4.0,
        "average_signed_markout": -0.5,
        "avg_toxic_markout": -1.4,
        "avg_non_toxic_markout": 0.6,
        "toxic_fill_count": 6,
        "toxic_fill_rate": 0.55,
        "adverse_fill_count": 4,
        "adverse_fill_rate": 0.36,
        "avg_abs_delta_before_hedge": 110.0,
        "hedge_trigger_delta": 100.0,
        "hedge_trade_count": 4,
        "max_inventory_contracts": 9,
        "max_single_contract_position": 5,
        "worst_drawdown": 18.5,
        "best_single_trade_markout": 6.0,
        "worst_single_trade_markout": -8.5,
        "final_net_delta": 18.0,
        "final_net_vega": 4.5,
        "markout_horizon_label": "1-step",
        "most_traded_contracts": [
            {"contract": "CALL_100.00_14D", "trade_count": 4, "signed_contract_qty": -2}
        ],
    }
    brief = format_brief_summary(summary)
    assert "Options MM quick summary" in brief
    assert "Adverse fills" in brief
    assert "Interpretation" in brief

    terminal = format_terminal_summary(summary)
    assert "RUN SUMMARY" in terminal
    assert "Markout definition" in terminal
    assert "Most traded contracts" in terminal


def test_signed_markout_sign_convention():
    assert signed_markout("buy", 4.0, 4.2, 2, 100) == pytest.approx(40.0)
    assert signed_markout("sell", 4.2, 4.4, 2, 100) == pytest.approx(-40.0)
