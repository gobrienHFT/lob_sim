from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.options.demo import (
    OptionsMarketMakerDemo,
    build_options_config,
    options_scenarios,
    scenario_card,
)


PLOT_FILE_NAMES = {
    "pnl_over_time_plot": "pnl_over_time.png",
    "realized_vs_unrealized_plot": "realized_vs_unrealized.png",
    "spot_path_plot": "spot_path.png",
    "inventory_over_time_plot": "inventory_over_time.png",
    "net_delta_over_time_plot": "net_delta_over_time.png",
    "markout_distribution_plot": "markout_distribution.png",
    "toxic_vs_nontoxic_plot": "toxic_vs_nontoxic_markout.png",
    "top_traded_contracts_plot": "top_traded_contracts.png",
    "overview_dashboard_plot": "overview_dashboard.png",
}


def _write_plots_stub(self: OptionsMarketMakerDemo, out_dir: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for key, name in PLOT_FILE_NAMES.items():
        path = out_dir / name
        path.write_bytes(b"")
        paths[key] = str(path)
    return paths


def test_option_scenario_presets_are_valid() -> None:
    expected = {"calm_market", "volatile_market", "toxic_flow", "inventory_stress"}
    assert set(options_scenarios()) == expected

    configs = {name: build_options_config(steps=12, seed=7, scenario=name) for name in expected}

    for name, config in configs.items():
        card = scenario_card(name)
        assert config.scenario_name == name
        assert config.steps == 12
        assert config.seed == 7
        assert 0.0 <= config.customer_arrival_prob <= 1.0
        assert 0.0 <= config.toxic_flow_prob <= 1.0
        assert config.realized_vol > 0.0
        assert config.base_half_spread > 0.0
        assert config.hedge_threshold_delta > 0.0
        assert config.max_trade_size >= config.min_trade_size
        assert card["description"]
        assert card["intended_lesson"]

    assert configs["volatile_market"].realized_vol > configs["calm_market"].realized_vol
    assert configs["toxic_flow"].toxic_flow_prob > configs["calm_market"].toxic_flow_prob
    assert configs["inventory_stress"].max_trade_size > configs["calm_market"].max_trade_size


def test_options_demo_smoke_run_is_deterministic_and_writes_required_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OptionsMarketMakerDemo, "_write_plots", _write_plots_stub)
    config = build_options_config(steps=12, seed=7, scenario="toxic_flow")

    first_dir = tmp_path / "run_one"
    second_dir = tmp_path / "run_two"
    summary_one = OptionsMarketMakerDemo(config).run(first_dir, progress_every=4)
    summary_two = OptionsMarketMakerDemo(config).run(second_dir, progress_every=4)

    required_paths = [
        first_dir / "summary.json",
        first_dir / "demo_report.md",
        first_dir / "fills.csv",
        first_dir / "checkpoints.csv",
        first_dir / "pnl_timeseries.csv",
        first_dir / "positions_final.csv",
        first_dir / "pnl_over_time.png",
        first_dir / "overview_dashboard.png",
    ]
    for path in required_paths:
        assert path.exists(), f"expected artifact at {path}"

    with (first_dir / "summary.json").open("r", encoding="utf-8") as handle:
        summary_on_disk = json.load(handle)

    assert summary_on_disk["scenario"] == "toxic_flow"
    assert summary_on_disk["markout_definition"].startswith("Signed markout")

    for key in (
        "trade_count",
        "ending_pnl",
        "realized_pnl",
        "gross_spread_captured",
        "total_signed_markout",
        "hedge_trade_count",
        "final_net_delta",
    ):
        assert summary_one[key] == summary_two[key]


def test_hedge_trigger_behavior_respects_threshold() -> None:
    config = build_options_config(steps=4, seed=7, scenario="calm_market")
    spot = 100.0

    below_trigger = OptionsMarketMakerDemo(config)
    below_trigger.stock_position = config.hedge_threshold_delta - 1.0
    risk_below = below_trigger._portfolio_risk(spot, 0)
    hedge_qty, hedge_cost, risk_after = below_trigger._hedge(spot, 0, risk_below)

    assert hedge_qty == 0.0
    assert hedge_cost == 0.0
    assert risk_after.delta == pytest.approx(risk_below.delta)
    assert below_trigger.hedge_count == 0

    above_trigger = OptionsMarketMakerDemo(config)
    above_trigger.stock_position = config.hedge_threshold_delta + 2.0
    risk_above = above_trigger._portfolio_risk(spot, 0)
    hedge_qty, hedge_cost, risk_after = above_trigger._hedge(spot, 0, risk_above)

    assert hedge_qty == pytest.approx(-round(risk_above.delta))
    assert hedge_cost > 0.0
    assert above_trigger.hedge_count == 1
    assert abs(risk_after.delta) < abs(risk_above.delta)
    assert risk_after.delta == pytest.approx(0.0)
