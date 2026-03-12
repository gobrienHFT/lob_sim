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
    format_interview_brief,
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
            "overview_dashboard_plot": "overview_dashboard.png",
            "implied_vol_surface_snapshot_plot": "implied_vol_surface_snapshot.png",
            "position_surface_heatmap_plot": "position_surface_heatmap.png",
            "vega_surface_heatmap_plot": "vega_surface_heatmap.png",
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
    dashboard_plot = tmp_path / "overview_dashboard.png"
    vol_surface_plot = tmp_path / "implied_vol_surface_snapshot.png"
    position_surface_plot = tmp_path / "position_surface_heatmap.png"
    vega_surface_plot = tmp_path / "vega_surface_heatmap.png"

    assert summary_json.exists()
    assert report_md.exists()
    assert fills_csv.exists()
    assert checkpoints_csv.exists()
    assert pnl_timeseries_csv.exists()
    assert positions_csv.exists()
    assert pnl_plot.exists()
    assert dashboard_plot.exists()
    assert vol_surface_plot.exists()
    assert position_surface_plot.exists()
    assert vega_surface_plot.exists()

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
    assert "Warehoused risk across the surface" in report_text
    assert "Pricing surface used by the demo" in report_text
    assert "Economics of the run" in report_text


def test_options_demo_writes_interview_brief(tmp_path: Path, monkeypatch):
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
            "overview_dashboard_plot": "overview_dashboard.png",
            "implied_vol_surface_snapshot_plot": "implied_vol_surface_snapshot.png",
            "position_surface_heatmap_plot": "position_surface_heatmap.png",
            "vega_surface_heatmap_plot": "vega_surface_heatmap.png",
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
        interview_mode=True,
    )

    interview_brief = tmp_path / "interview_brief.md"
    assert interview_brief.exists()
    assert summary["output_files"]["interview_brief"] == str(interview_brief)

    interview_text = interview_brief.read_text(encoding="utf-8")
    assert "Options MM interview brief" in interview_text
    assert "Executive summary" in interview_text
    assert "Strongest takeaways" in interview_text
    assert "Key limitations" in interview_text
    assert "Worked fill examples" in interview_text
    assert "Representative Fill" in interview_text
    assert "Stress-case toxic fill" in interview_text
    assert "What I would build next" in interview_text
    assert "overview_dashboard.png" in interview_text
    assert "implied_vol_surface_snapshot.png" in interview_text
    assert "position_surface_heatmap.png" in interview_text
    assert "vega_surface_heatmap.png" in interview_text
    assert "Warehoused risk across the surface" in interview_text
    assert "Economics of the run" in interview_text
    assert "Pricing surface used by the demo" in interview_text


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
        "quote_price_units": "Per-option premium in underlying price units.",
        "pnl_units": "Contract dollars unless otherwise stated.",
        "surface_risk": {
            "active_cells": 4,
            "largest_position_bucket": {"strike": 95.0, "expiry_days": 45, "contracts": -6.0},
            "largest_vega_bucket": {"strike": 100.0, "expiry_days": 45, "net_vega": 320.0},
        },
        "pricing_surface": {
            "snapshot_label": "initial snapshot",
            "spot": 100.0,
            "strikes": [95.0, 100.0],
            "expiry_days": [14, 45],
            "implied_vols": [[0.22, 0.24], [0.23, 0.25]],
        },
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

    summary["output_files"] = {
        "interview_brief": "outputs/interview_brief.md",
        "report": "outputs/demo_report.md",
        "fills": "outputs/fills.csv",
        "pnl_timeseries": "outputs/pnl_timeseries.csv",
        "pnl_over_time_plot": "outputs/pnl_over_time.png",
        "overview_dashboard_plot": "outputs/overview_dashboard.png",
        "implied_vol_surface_snapshot_plot": "outputs/implied_vol_surface_snapshot.png",
        "position_surface_heatmap_plot": "outputs/position_surface_heatmap.png",
        "vega_surface_heatmap_plot": "outputs/vega_surface_heatmap.png",
    }
    worked_examples = {
        "representative": {
            "step": 7,
            "contract": "CALL_95.00_45D",
            "option_type": "call",
            "strike": 95.0,
            "expiry_days": 45.0,
            "customer_side": "buy",
            "mm_side": "sell",
            "qty_contracts": 2,
            "contract_size": 100,
            "spot_before": 102.51,
            "fair_value": 9.27,
            "base_half_spread": 0.09,
            "vol_half_spread_component": 0.21,
            "gamma_half_spread_component": 0.01,
            "reservation_price": 0.12,
            "delta_reservation_component": 0.05,
            "vega_reservation_component": 0.07,
            "bid": 8.20,
            "ask": 8.46,
            "fill_price": 8.46,
            "toxic_flow": False,
            "signed_markout": 31.37,
            "portfolio_delta_before": -20.0,
            "portfolio_delta_after_trade": -133.8,
            "hedge_qty": 134.0,
            "portfolio_delta_after_hedge": 0.2,
            "option_position_after": -2,
        },
        "stress": {
            "step": 19,
            "contract": "PUT_100.00_14D",
            "option_type": "put",
            "strike": 100.0,
            "expiry_days": 14.0,
            "customer_side": "buy",
            "mm_side": "sell",
            "qty_contracts": 4,
                "contract_size": 100,
                "spot_before": 90.0,
                "fair_value": 2.0,
                "base_half_spread": 0.09,
                "vol_half_spread_component": 0.50,
                "gamma_half_spread_component": 0.01,
                "reservation_price": -12.5,
                "delta_reservation_component": -0.20,
                "vega_reservation_component": -12.30,
                "bid": 13.89,
                "ask": 15.09,
                "fill_price": 13.89,
                "toxic_flow": True,
                "signed_markout": -331.37,
                "portfolio_delta_before": 70.0,
                "portfolio_delta_after_trade": 190.0,
                "hedge_qty": -190.0,
            "portfolio_delta_after_hedge": 0.0,
            "option_position_after": -6,
        },
    }
    interview_brief = format_interview_brief(summary, worked_examples)
    assert "Options MM interview brief" in interview_brief
    assert "Worked fill examples" in interview_brief
    assert "Representative Fill" in interview_brief
    assert "Stress-case toxic fill" in interview_brief
    assert "What I would build next" in interview_brief
    assert "implied_vol_surface_snapshot.png" in interview_brief
    assert "contract dollars" in interview_brief
    assert "This is not a units mismatch" in interview_brief


def test_signed_markout_sign_convention():
    assert signed_markout("buy", 4.0, 4.2, 2, 100) == pytest.approx(40.0)
    assert signed_markout("sell", 4.2, 4.4, 2, 100) == pytest.approx(-40.0)
