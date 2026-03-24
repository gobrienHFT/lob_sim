from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from math import exp, log, sqrt
from pathlib import Path
from statistics import median
from typing import Any
import csv
import json
import random

import matplotlib.pyplot as plt

from .black_scholes import OptionContract, OptionGreeks, option_metrics
from .markout import markout_horizon_label, signed_markout
from .surface import SimpleVolSurface


DEFAULT_OPTIONS_SCENARIO = "calm_market"
SAMPLE_SCENARIO_MATRIX_ARTIFACT = "docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md"
SAMPLE_SENSITIVITY_ARTIFACT = (
    "docs/sample_outputs/toxicity_spread_sensitivity_seed7/toxicity_spread_sensitivity.md"
)
OPTIONS_SCENARIOS: dict[str, dict[str, Any]] = {
    "calm_market": {
        "description": "Lower-volatility quoting with modest toxic flow and lighter hedge pressure.",
        "volatility_regime": "Calm underlying path with smaller jumps and tighter option quote width.",
        "flow_characteristics": "Lower customer arrival intensity and mostly non-toxic flow.",
        "hedging_pressure": "Lower hedge pressure because delta excursions are less violent.",
        "intended_lesson": "Shows clean spread capture, inventory skew, and measured hedging.",
        "overrides": {
            "realized_vol": 0.16,
            "jump_prob": 0.01,
            "jump_std": 0.006,
            "customer_arrival_prob": 0.62,
            "toxic_flow_prob": 0.10,
            "toxic_flow_drift": 0.004,
            "base_half_spread": 0.06,
            "realized_vol_spread_mult": 0.22,
            "hedge_threshold_delta": 145.0,
        },
    },
    "volatile_market": {
        "description": "Higher realized volatility and jump risk, forcing wider quotes and faster hedging.",
        "volatility_regime": "Large spot swings and more jump risk push fair values around quickly.",
        "flow_characteristics": "Healthy flow, but the fast underlying path makes inventory harder to warehouse.",
        "hedging_pressure": "Higher hedge pressure because delta moves faster and wider.",
        "intended_lesson": "Shows why quote width and hedge discipline matter in faster tape.",
        "overrides": {
            "realized_vol": 0.34,
            "jump_prob": 0.08,
            "jump_std": 0.025,
            "customer_arrival_prob": 0.76,
            "toxic_flow_prob": 0.28,
            "toxic_flow_drift": 0.008,
            "base_half_spread": 0.11,
            "realized_vol_spread_mult": 0.48,
            "gamma_spread_mult": 0.00022,
            "hedge_threshold_delta": 105.0,
        },
    },
    "toxic_flow": {
        "description": "Flow is more informed, so markouts matter and toxic fills are easier to discuss.",
        "volatility_regime": "Moderate volatility, but adverse-selection drift is deliberately stronger.",
        "flow_characteristics": "A larger share of customer trades are informed against the quoted price.",
        "hedging_pressure": "Moderate hedge pressure, but post-trade markouts are the real story.",
        "intended_lesson": "Shows the difference between quoted edge and realized edge after markout.",
        "overrides": {
            "realized_vol": 0.24,
            "jump_prob": 0.04,
            "jump_std": 0.012,
            "customer_arrival_prob": 0.80,
            "toxic_flow_prob": 0.55,
            "toxic_flow_drift": 0.014,
            "base_half_spread": 0.09,
            "realized_vol_spread_mult": 0.40,
            "hedge_threshold_delta": 110.0,
        },
    },
    "inventory_stress": {
        "description": "Larger clips and looser hedge thresholds force more inventory warehousing.",
        "volatility_regime": "Normal volatility, but inventory accumulates quickly because clips are larger.",
        "flow_characteristics": "Faster flow and bigger trade sizes push single-contract positions harder.",
        "hedging_pressure": "Delta is allowed to run further before hedging, so inventory skew dominates.",
        "intended_lesson": "Shows how reservation price and inventory limits shape quoting decisions.",
        "overrides": {
            "realized_vol": 0.22,
            "customer_arrival_prob": 0.86,
            "min_trade_size": 2,
            "max_trade_size": 6,
            "base_half_spread": 0.09,
            "delta_reservation_mult": 0.0018,
            "vega_reservation_mult": 0.00005,
            "hedge_threshold_delta": 175.0,
        },
    },
}


@dataclass(frozen=True)
class OptionsMMConfig:
    steps: int = 450
    seed: int = 7
    scenario_name: str = "custom"
    scenario_description: str = "Custom parameter mix."
    spot0: float = 100.0
    rate: float = 0.0
    dt_years: float = 1.0 / (252.0 * 78.0)
    realized_vol: float = 0.22
    jump_prob: float = 0.03
    jump_std: float = 0.012
    customer_arrival_prob: float = 0.72
    toxic_flow_prob: float = 0.22
    toxic_flow_drift: float = 0.006
    contract_size: int = 100
    min_trade_size: int = 1
    max_trade_size: int = 4
    base_half_spread: float = 0.08
    min_half_spread: float = 0.03
    realized_vol_window: int = 30
    realized_vol_spread_mult: float = 0.35
    delta_reservation_mult: float = 0.0012
    gamma_spread_mult: float = 0.00015
    vega_reservation_mult: float = 0.00003
    hedge_threshold_delta: float = 125.0
    hedge_slippage_bps: float = 0.6
    markout_horizon_steps: int = 1


def options_scenarios() -> tuple[str, ...]:
    return tuple(OPTIONS_SCENARIOS.keys())


def build_options_config(
    steps: int = 450,
    seed: int = 7,
    scenario: str = DEFAULT_OPTIONS_SCENARIO,
) -> OptionsMMConfig:
    preset = OPTIONS_SCENARIOS.get(scenario)
    if preset is None:
        valid = ", ".join(options_scenarios())
        raise ValueError(f"Unknown options scenario '{scenario}'. Choose one of: {valid}")

    base = OptionsMMConfig(
        steps=steps,
        seed=seed,
        scenario_name=scenario,
        scenario_description=str(preset["description"]),
    )
    return replace(base, **dict(preset["overrides"]))


def scenario_card(scenario: str) -> dict[str, str]:
    preset = OPTIONS_SCENARIOS.get(scenario)
    if preset is None:
        valid = ", ".join(options_scenarios())
        raise ValueError(f"Unknown options scenario '{scenario}'. Choose one of: {valid}")
    return {
        "scenario": scenario,
        "description": str(preset["description"]),
        "volatility_regime": str(preset["volatility_regime"]),
        "flow_characteristics": str(preset["flow_characteristics"]),
        "hedging_pressure": str(preset["hedging_pressure"]),
        "intended_lesson": str(preset["intended_lesson"]),
    }


def format_scenario_card(card: dict[str, str]) -> str:
    lines = [
        "Scenario assumptions",
        f"- Scenario: {card['scenario']}",
        f"- Description: {card['description']}",
        f"- Volatility regime: {card['volatility_regime']}",
        f"- Flow characteristics: {card['flow_characteristics']}",
        f"- Hedging pressure: {card['hedging_pressure']}",
        f"- Intended lesson: {card['intended_lesson']}",
    ]
    return "\n".join(lines)


def format_run_intro(cfg: OptionsMMConfig, out_dir: Path, log_mode: str) -> str:
    return "\n".join(
        [
            "======================================================================",
            "Options Market Making Case Study",
            "Synthetic customer flow, inventory-aware quoting, hedging, and PnL",
            "======================================================================",
            f"Scenario: {cfg.scenario_name}",
            f"Seed: {cfg.seed}",
            f"Steps: {cfg.steps}",
            f"Output folder: {out_dir}",
            f"Log mode: {log_mode}",
            f"Markout horizon: {markout_horizon_label(cfg.markout_horizon_steps)}",
        ]
    )


def _format_metric_rows(rows: list[tuple[str, str]]) -> list[str]:
    width = max((len(label) for label, _ in rows), default=0)
    return [f"{label:<{width}} : {value}" for label, value in rows]


def _markdown_metric_rows(rows: list[tuple[str, str]]) -> list[str]:
    return [f"- **{label}**: {value}" for label, value in rows]


def _format_top_contracts(summary: dict[str, Any]) -> list[str]:
    top_contracts = summary.get("most_traded_contracts", [])
    if not top_contracts:
        return ["- none"]
    return [
        f"- {row['contract']}: count={row['trade_count']}, signed_qty={row['signed_contract_qty']}"
        for row in top_contracts
    ]


def _summary_interpretation(summary: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if summary["ending_pnl"] >= 0:
        notes.append("Quoted edge held up after hedge costs and inventory marking.")
    else:
        notes.append("Inventory moves and adverse selection outweighed quoted edge.")

    if summary["toxic_fill_rate"] >= 0.40:
        notes.append("A large share of fills were toxic, so post-trade selection pressure stayed elevated.")
    else:
        notes.append("Toxic flow stayed contained relative to total activity.")

    if summary["avg_abs_delta_before_hedge"] >= summary["hedge_trigger_delta"]:
        notes.append("Hedge triggers were reached with meaningful delta, so underlying hedging mattered.")
    else:
        notes.append("The book spent most of the run inside the configured hedge band.")

    if abs(summary["final_net_delta"]) > 50.0:
        notes.append("The book finished with material net delta still warehoused.")
    else:
        notes.append("The book finished close to delta-flat after hedging.")
    return notes


def _surface_risk_notes(summary: dict[str, Any]) -> list[str]:
    surface = summary["surface_risk"]
    largest_position = surface["largest_position_bucket"]
    largest_vega = surface["largest_vega_bucket"]
    notes = [
        (
            f"Largest signed contract inventory sat in strike `{largest_position['strike']:.0f}` / "
            f"`{largest_position['expiry_days']}` day expiry at `{largest_position['contracts']:+.0f}` contracts."
        ),
        (
            f"Largest net vega sat in strike `{largest_vega['strike']:.0f}` / "
            f"`{largest_vega['expiry_days']}` day expiry at `{largest_vega['net_vega']:+.0f}` vega."
        ),
        (
            f"Risk was spread across `{surface['active_cells']}` non-zero strike/expiry buckets, so the book is "
            "not just one contract position."
        ),
    ]
    if abs(summary["final_net_delta"]) <= 50.0 and abs(summary["final_net_vega"]) > 0.0:
        notes.append("Underlying hedges flattened delta, but the volatility surface exposure remained warehoused.")
    return notes


def format_terminal_summary(summary: dict[str, Any]) -> str:
    headline = _format_metric_rows(
        [
            ("Scenario", str(summary["scenario"])),
            ("Seed", str(summary["seed"])),
            ("Steps", str(summary["steps"])),
            ("Spot path", f"{summary['spot_start']:.2f} -> {summary['spot_final']:.2f}"),
            ("Ending PnL", f"{summary['ending_pnl']:.2f}"),
            ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
            ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
            ("Gross spread captured", f"{summary['gross_spread_captured']:.2f}"),
        ]
    )
    flow = _format_metric_rows(
        [
            (
                f"Signed markout ({summary['markout_horizon_label']})",
                f"{summary['total_signed_markout']:.2f}",
            ),
            ("Average signed markout", f"{summary['average_signed_markout']:.2f}"),
            ("Avg toxic markout", f"{summary['avg_toxic_markout']:.2f}"),
            ("Avg non-toxic markout", f"{summary['avg_non_toxic_markout']:.2f}"),
            ("Toxic fills", f"{summary['toxic_fill_count']} ({summary['toxic_fill_rate']:.1%})"),
            ("Adverse fills", f"{summary['adverse_fill_count']} ({summary['adverse_fill_rate']:.1%})"),
            ("Best single markout", f"{summary['best_single_trade_markout']:.2f}"),
            ("Worst single markout", f"{summary['worst_single_trade_markout']:.2f}"),
        ]
    )
    inventory = _format_metric_rows(
        [
            ("Hedge trades", str(summary["hedge_trade_count"])),
            ("Average |delta| before hedge", f"{summary['avg_abs_delta_before_hedge']:.2f}"),
            ("Max inventory", str(summary["max_inventory_contracts"])),
            ("Max single-contract position", str(summary["max_single_contract_position"])),
            ("Worst drawdown", f"{summary['worst_drawdown']:.2f}"),
            ("Final net delta", f"{summary['final_net_delta']:.2f}"),
            ("Final net vega", f"{summary['final_net_vega']:.2f}"),
        ]
    )
    lines = [
        "",
        "======================================================================",
        "RUN SUMMARY",
        "======================================================================",
        "Headline",
        *headline,
        "",
        "Flow and markout",
        *flow,
        "",
        "Inventory and hedging",
        *inventory,
        "",
        "Most traded contracts",
        *_format_top_contracts(summary),
        "",
        "Interpretation",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    lines.extend(
        [
            "",
            "Markout definition",
            (
                f"- Signed markout ({summary['markout_horizon_label']}) compares fill price with the option fair "
                "value at a fixed future step, in contract dollars after quantity and contract size; positive is "
                "good for the dealer, negative indicates adverse selection."
            ),
        ]
    )
    return "\n".join(lines)


def format_brief_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Options MM quick summary",
        f"Scenario: {summary['scenario']} - {summary['scenario_description']}",
        "",
        *_format_metric_rows(
            [
                ("Ending PnL", f"{summary['ending_pnl']:.2f}"),
                ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
                ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
                ("Gross spread captured", f"{summary['gross_spread_captured']:.2f}"),
                ("Signed markout", f"{summary['total_signed_markout']:.2f}"),
                ("Toxic fills", f"{summary['toxic_fill_count']} ({summary['toxic_fill_rate']:.1%})"),
                ("Adverse fills", f"{summary['adverse_fill_count']} ({summary['adverse_fill_rate']:.1%})"),
                ("Hedge trades", str(summary["hedge_trade_count"])),
                ("Max inventory", str(summary["max_inventory_contracts"])),
                ("Final net delta", f"{summary['final_net_delta']:.2f}"),
            ]
        ),
        "",
        "Interpretation",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    return "\n".join(lines)


def _format_metric_table(rows: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(f"| {label} | {value} |" for label, value in rows)
    return lines


def _hedged_fills(trade_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in trade_rows if abs(float(row.get("hedge_qty", 0.0))) > 0.0]


def _representative_hedged_fill(trade_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    hedged = _hedged_fills(trade_rows)
    if not hedged:
        return None
    target = median(abs(float(row.get("signed_markout", 0.0))) for row in hedged)
    return min(
        hedged,
        key=lambda row: (
            abs(abs(float(row.get("signed_markout", 0.0))) - target),
            int(row.get("step", 0)),
            str(row.get("contract", "")),
        ),
    )


def _stress_toxic_fill(trade_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    toxic_hedged = [row for row in _hedged_fills(trade_rows) if bool(row.get("toxic_flow"))]
    if toxic_hedged:
        return min(
            toxic_hedged,
            key=lambda row: (
                float(row.get("signed_markout", 0.0)),
                int(row.get("step", 0)),
                str(row.get("contract", "")),
            ),
        )
    hedged = _hedged_fills(trade_rows)
    if hedged:
        return min(
            hedged,
            key=lambda row: (
                float(row.get("signed_markout", 0.0)),
                int(row.get("step", 0)),
                str(row.get("contract", "")),
            ),
        )
    return None


def select_worked_fill_examples(trade_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    return {
        "representative": _representative_hedged_fill(trade_rows),
        "stress": _stress_toxic_fill(trade_rows),
    }


def _worked_fill_rules() -> dict[str, str]:
    return {
        "representative": (
            "Representative Fill = the hedged fill whose absolute signed markout is closest to the median "
            "absolute signed markout across all hedged fills."
        ),
        "stress": "Stress-case toxic fill = the toxic hedged fill with the worst signed markout.",
    }


def _example_short_interpretation(fill: dict[str, Any]) -> str:
    reservation = float(fill["reservation_price"])
    half_spread = float(fill["base_half_spread"]) + float(fill["vol_half_spread_component"]) + float(
        fill["gamma_half_spread_component"]
    )
    premium_value = float(fill["fill_price"]) * int(fill["qty_contracts"]) * int(fill["contract_size"])
    if abs(reservation) > abs(float(fill["fair_value"])):
        skew_note = (
            f"Reservation pressure of {reservation:+.3f} premium per option dominated fair value, so the dealer "
            f"{fill['mm_side']} side was skewed far from model mid."
        )
    else:
        skew_note = (
            f"Reservation pressure of {reservation:+.3f} and half-spread {half_spread:.3f} kept the quote close "
            "to model fair value."
        )
    return (
        f"{skew_note} The fill transacted {premium_value:.2f} contract dollars of premium before the 1-step "
        f"signed markout of {float(fill['signed_markout']):+.2f} contract dollars."
    )


def _stress_fill_follow_up(fill: dict[str, Any]) -> list[str]:
    reservation = float(fill["reservation_price"])
    fair_value = float(fill["fair_value"])
    if abs(reservation) <= abs(fair_value):
        return []
    return [
        (
            "This is not a units mismatch: the per-option fair value is low, but a large negative vega reservation "
            "shifted the dealer bid above model mid to attract offsetting flow."
        ),
        (
            "The trader read is that inventory transfer pricing became aggressive here, and the subsequent negative "
            "markout is evidence to question whether that skew should have been capped or hedged earlier."
        ),
    ]


def _format_worked_fill_example(
    heading: str,
    selection_rule: str,
    fill: dict[str, Any] | None,
    follow_up_lines: list[str] | None = None,
) -> list[str]:
    lines = [f"### {heading}", "", selection_rule]
    if fill is None:
        lines.extend(["", "- No fill matched this rule in the run."])
        return lines
    lines.extend(
        [
            "",
            "| Field | Value |",
            "|---|---|",
            f"| step | {fill['step']} |",
            f"| contract | {fill['contract']} |",
            f"| option_type | {fill['option_type']} |",
            f"| strike | {float(fill['strike']):.2f} |",
            f"| expiry_days | {float(fill['expiry_days']):.2f} |",
            f"| customer_side | {fill['customer_side']} |",
            f"| dealer_side | {fill['mm_side']} |",
            f"| quantity | {fill['qty_contracts']} |",
            f"| contract_size | {fill['contract_size']} |",
            f"| spot_before | {float(fill['spot_before']):.2f} spot units |",
            f"| fair_value | {float(fill['fair_value']):.3f} premium per option |",
            f"| base_half_spread | {float(fill['base_half_spread']):.3f} premium per option |",
            (
                f"| vol_half_spread_component | {float(fill['vol_half_spread_component']):.3f} "
                "premium per option |"
            ),
            (
                f"| gamma_half_spread_component | {float(fill['gamma_half_spread_component']):.3f} "
                "premium per option |"
            ),
            f"| reservation_price | {float(fill['reservation_price']):+.3f} premium per option |",
            (
                f"| delta_reservation_component | {float(fill['delta_reservation_component']):+.3f} "
                "premium per option |"
            ),
            (
                f"| vega_reservation_component | {float(fill['vega_reservation_component']):+.3f} "
                "premium per option |"
            ),
            f"| final bid | {float(fill['bid']):.3f} premium per option |",
            f"| final ask | {float(fill['ask']):.3f} premium per option |",
            f"| fill_price | {float(fill['fill_price']):.3f} premium per option |",
            f"| toxic_flow | {fill['toxic_flow']} |",
            f"| signed_markout | {float(fill['signed_markout']):+.2f} contract dollars |",
            f"| portfolio_delta_before | {float(fill['portfolio_delta_before']):+.1f} |",
            f"| portfolio_delta_after_trade | {float(fill['portfolio_delta_after_trade']):+.1f} |",
            f"| hedge_qty | {float(fill['hedge_qty']):+.0f} underlying units |",
            f"| portfolio_delta_after_hedge | {float(fill['portfolio_delta_after_hedge']):+.1f} |",
            f"| option_position_after | {fill['option_position_after']} contracts |",
            f"| short_interpretation | {_example_short_interpretation(fill)} |",
        ]
    )
    if follow_up_lines:
        lines.extend(["", *(f"- {line}" for line in follow_up_lines)])
    return lines


def _economics_notes(summary: dict[str, Any]) -> list[str]:
    notes = [
        "Gross spread capture can stay positive while signed markout is negative because the dealer still earns quoted edge at the fill even when the next fair-value move goes against the position.",
        "Ending PnL can still finish positive when quoted edge and subsequent inventory moves outweigh hedge costs, even if post-trade markouts are poor on average.",
        "Signed markout is a diagnostic in contract dollars, not a separate PnL line item that is added mechanically into ending PnL in this toy accounting.",
    ]
    if summary["ending_pnl"] > 0.0 and summary["total_signed_markout"] < 0.0:
        notes.append("That combination means the strategy earned enough spread and inventory carry to survive adverse selection, but the fill quality still deserves skepticism.")
    return notes


def _pricing_surface_notes(summary: dict[str, Any]) -> list[str]:
    snapshot = summary["pricing_surface"]
    return [
        (
            f"The demo uses an {snapshot['snapshot_label']} implied-vol surface at spot `{snapshot['spot']:.2f}`; "
            "that surface feeds Black-Scholes fair value for every quoted option."
        ),
        "Vega exposure should be read alongside this surface because the book can be close to delta-flat while still carrying large strike/expiry volatility risk.",
        "The surface shape is synthetic and parametric here; real calibration would need live option quotes or trades across strike and expiry.",
    ]


def _representative_fill_reference(summary: dict[str, Any]) -> str:
    case_brief = summary["output_files"].get("case_brief")
    if case_brief:
        return f"{case_brief}#representative-fill"
    return summary["output_files"]["fills"]


def format_case_brief(summary: dict[str, Any], worked_examples: dict[str, dict[str, Any] | None]) -> str:
    takeaways = [
        (
            f"Gross spread capture was {summary['gross_spread_captured']:.2f} while signed markout was "
            f"{summary['total_signed_markout']:.2f}, so the run makes quoted edge versus adverse selection explicit."
        ),
        (
            f"Inventory peaked at {summary['max_inventory_contracts']} contracts and triggered "
            f"{summary['hedge_trade_count']} hedge trades, so warehousing risk is visible rather than hidden."
        ),
        (
            f"The book finished with net delta {summary['final_net_delta']:.2f} and net vega "
            f"{summary['final_net_vega']:.2f}, which shows delta control without pretending the book is fully hedged."
        ),
    ]
    limitations = [
        "This is a synthetic dealer study, not a replay of exchange options order-book data.",
        "The strategy only hedges delta in the underlying; gamma and vega are intentionally warehoused.",
        "The volatility surface and customer flow are transparent approximations, not venue-calibrated models.",
    ]
    next_steps = [
        "Calibrate the implied-vol surface and customer-flow assumptions from real market data.",
        "Add cross-option hedging so gamma and vega can be managed with listed options, not just underlying delta hedges.",
        "Build a separate live options market-data recorder if venue microstructure realism becomes the primary goal.",
    ]
    executive_summary = [
        f"- Scenario `{summary['scenario']}` with seed `{summary['seed']}` over `{summary['steps']}` steps.",
        (
            f"- Ending PnL `{summary['ending_pnl']:.2f}` with realized `{summary['realized_pnl']:.2f}` "
            f"and unrealized `{summary['unrealized_pnl']:.2f}`."
        ),
        (
            f"- Toxic fills were `{summary['toxic_fill_count']}` (`{summary['toxic_fill_rate']:.1%}`) and total "
            f"signed markout at `{summary['markout_horizon_label']}` was `{summary['total_signed_markout']:.2f}`."
        ),
        (
            f"- Hedge trades were `{summary['hedge_trade_count']}` with max inventory `{summary['max_inventory_contracts']}` "
            f"and final net delta `{summary['final_net_delta']:.2f}`."
        ),
        f"- Readout: {_summary_interpretation(summary)[0]}",
    ]
    metric_rows = _format_metric_table(
        [
            ("Ending PnL", f"{summary['ending_pnl']:.2f}"),
            ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
            ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
            ("Gross spread captured", f"{summary['gross_spread_captured']:.2f}"),
            ("Signed markout", f"{summary['total_signed_markout']:.2f}"),
            ("Toxic fill rate", f"{summary['toxic_fill_rate']:.1%}"),
            ("Hedge trades", str(summary["hedge_trade_count"])),
            ("Max inventory", str(summary["max_inventory_contracts"])),
            ("Worst drawdown", f"{summary['worst_drawdown']:.2f}"),
            ("Final net delta", f"{summary['final_net_delta']:.2f}"),
            ("Final net vega", f"{summary['final_net_vega']:.2f}"),
        ]
    )
    lines = [
        "# Options MM case brief",
        "",
        "## Executive summary",
        *executive_summary,
        "",
        "## Metrics",
        *metric_rows,
        "",
        "## Strongest takeaways",
        *(f"- {item}" for item in takeaways),
        "",
        "## Key limitations",
        *(f"- {item}" for item in limitations),
        "",
        "## Warehoused risk across the surface",
        *(f"- {item}" for item in _surface_risk_notes(summary)),
        "",
        "## Pricing surface used by the demo",
        *(f"- {item}" for item in _pricing_surface_notes(summary)),
        "",
        "## Economics of the run",
        *(
            _format_metric_table(
                [
                    ("Gross spread captured", f"{summary['gross_spread_captured']:.2f} contract dollars"),
                    ("Hedge costs", f"{summary['hedge_costs']:.2f} contract dollars"),
                    ("Total signed markout", f"{summary['total_signed_markout']:.2f} contract dollars"),
                    ("Ending PnL", f"{summary['ending_pnl']:.2f} contract dollars"),
                    ("Realized PnL", f"{summary['realized_pnl']:.2f} contract dollars"),
                    ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f} contract dollars"),
                ]
            )
        ),
        "",
        "Signed markout is reported here as a contract-dollar fill-quality diagnostic. It is not treated as a separate additive PnL line item in this demo.",
        "",
        *(f"- {item}" for item in _economics_notes(summary)),
        "",
        "## Worked fill examples",
        "Quoted prices below are per-option premium. `signed_markout`, `gross_spread_captured`, and `hedge_costs` are shown in contract dollars after multiplying by `qty_contracts * contract_size`.",
        "",
    ]
    rules = _worked_fill_rules()
    lines.extend(
        _format_worked_fill_example(
            "Representative Fill",
            rules["representative"],
            worked_examples.get("representative"),
        )
    )
    lines.extend(
        [
            "",
            *(
                _format_worked_fill_example(
                    "Stress-case toxic fill",
                    rules["stress"],
                    worked_examples.get("stress"),
                    _stress_fill_follow_up(worked_examples.get("stress"))
                    if worked_examples.get("stress") is not None
                    else None,
                )
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## What I would build next",
            *(f"- {item}" for item in next_steps),
            "",
            "## Files to open next",
            f"- `case_brief.md`: {summary['output_files']['case_brief']}",
            f"- `overview_dashboard.png`: {summary['output_files']['overview_dashboard_plot']}",
            f"- `implied_vol_surface_snapshot.png`: {summary['output_files']['implied_vol_surface_snapshot_plot']}",
            f"- `position_surface_heatmap.png`: {summary['output_files']['position_surface_heatmap_plot']}",
            f"- `vega_surface_heatmap.png`: {summary['output_files']['vega_surface_heatmap_plot']}",
            "- representative fill: see the `Representative Fill` section in this file",
            f"- `scenario_matrix.md`: {SAMPLE_SCENARIO_MATRIX_ARTIFACT}",
            f"- `toxicity_spread_sensitivity.md`: {SAMPLE_SENSITIVITY_ARTIFACT}",
        ]
    )
    return "\n".join(lines) + "\n"


def format_demo_report(
    summary: dict[str, Any],
    worked_examples: dict[str, dict[str, Any] | None] | None = None,
) -> str:
    worked_examples = worked_examples or {"representative": None, "stress": None}
    lines = [
        "# Options market making case study",
        "",
        "## What this project demonstrates",
        "- A dealer quoting options around Black-Scholes fair value on a simple skewed surface.",
        "- Inventory-aware reservation pricing that shifts quotes when delta and vega risk build.",
        "- Synthetic customer flow with a configurable toxic-flow share.",
        "- Delta hedging in the underlying, plus realized and unrealized PnL decomposition.",
        "",
        "## What is synthetic vs real",
        "- The option chain, customer arrivals, toxicity, and fills are synthetic and scenario-driven.",
        "- The demo does not replay a live options venue order book.",
        "- The point is transparency: every quote, hedge, markout, and PnL component is inspectable.",
        "",
        "## Scenario overview",
        f"- **Scenario**: `{summary['scenario']}`",
        f"- **Description**: {summary['scenario_description']}",
        f"- **Volatility regime**: {summary['scenario_card']['volatility_regime']}",
        f"- **Flow characteristics**: {summary['scenario_card']['flow_characteristics']}",
        f"- **Hedging pressure**: {summary['scenario_card']['hedging_pressure']}",
        f"- **Intended lesson**: {summary['scenario_card']['intended_lesson']}",
        "",
        "## Simulation loop",
        "1. Build a quote around fair value for one option contract from the synthetic chain.",
        "2. Sample customer flow side, size, and toxicity from the chosen scenario.",
        "3. Apply the fill, update option inventory and cash, then recalculate portfolio Greeks.",
        "4. Hedge net delta in the underlying when the configured trigger is breached.",
        "5. Evolve underlying spot, mark the portfolio, and record path, fills, and checkpoints.",
        "",
        "## Markout definition",
        (
            f"- Signed markout is measured at a fixed future horizon of `{summary['markout_horizon_label']}` "
            "against the option fair value at that future step."
        ),
        "- Formula: `signed_markout = direction * (future_fair_value - fill_price) * qty * contract_size`.",
        "- `direction = +1` for a market-maker buy fill and `direction = -1` for a market-maker sell fill.",
        "- Positive signed markout means the fill aged well for the dealer; negative means adverse selection.",
        "",
        "## Parameter choices",
        *_markdown_metric_rows(
            [
                ("Seed", str(summary["seed"])),
                ("Steps", str(summary["steps"])),
                ("Underlying spot start", f"{summary['spot_start']:.4f}"),
                ("Underlying spot final", f"{summary['spot_final']:.4f}"),
                ("Markout horizon", summary["markout_horizon_label"]),
                ("Hedge trigger", f"|delta| > {summary['hedge_trigger_delta']:.2f}"),
                ("Customer arrival probability", f"{summary['parameters']['customer_arrival_prob']:.2f}"),
                ("Toxic flow probability", f"{summary['parameters']['toxic_flow_prob']:.2f}"),
            ]
        ),
        "",
        "## Key metrics",
        *_markdown_metric_rows(
            [
                ("Ending PnL", f"{summary['ending_pnl']:.2f}"),
                ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
                ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
                ("Gross spread captured", f"{summary['gross_spread_captured']:.2f}"),
                ("Hedge costs", f"{summary['hedge_costs']:.2f}"),
                ("Total signed markout", f"{summary['total_signed_markout']:.2f}"),
                ("Average signed markout", f"{summary['average_signed_markout']:.2f}"),
                ("Average toxic markout", f"{summary['avg_toxic_markout']:.2f}"),
                ("Average non-toxic markout", f"{summary['avg_non_toxic_markout']:.2f}"),
                ("Toxic fill rate", f"{summary['toxic_fill_rate']:.1%}"),
                ("Adverse fill rate", f"{summary['adverse_fill_rate']:.1%}"),
                ("Average quote width", f"{summary['avg_quote_width']:.4f}"),
                ("Average half-width", f"{summary['avg_half_spread']:.4f}"),
            ]
        ),
        "",
        "## Inventory and hedging",
        *_markdown_metric_rows(
            [
                ("Hedge trades", str(summary["hedge_trade_count"])),
                ("Average |delta| before hedge", f"{summary['avg_abs_delta_before_hedge']:.2f}"),
                ("Max inventory", str(summary["max_inventory_contracts"])),
                ("Max single-contract position", str(summary["max_single_contract_position"])),
                ("Max underlying hedge position", f"{summary['max_abs_stock_position']:.2f}"),
                ("Final net delta", f"{summary['final_net_delta']:.2f}"),
                ("Final net vega", f"{summary['final_net_vega']:.2f}"),
                ("Worst drawdown", f"{summary['worst_drawdown']:.2f}"),
            ]
        ),
        "",
        "## Interpretation",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    lines.extend(
        [
            "",
        "## Warehoused risk across the surface",
        *(f"- {note}" for note in _surface_risk_notes(summary)),
        "",
        "## Pricing surface used by the demo",
        *(f"- {note}" for note in _pricing_surface_notes(summary)),
        "",
        "## Economics of the run",
        *_markdown_metric_rows(
            [
                ("Gross spread captured", f"{summary['gross_spread_captured']:.2f} contract dollars"),
                ("Hedge costs", f"{summary['hedge_costs']:.2f} contract dollars"),
                ("Total signed markout", f"{summary['total_signed_markout']:.2f} contract dollars"),
                ("Ending PnL", f"{summary['ending_pnl']:.2f} contract dollars"),
                ("Realized PnL", f"{summary['realized_pnl']:.2f} contract dollars"),
                ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f} contract dollars"),
            ]
        ),
        "",
        "Signed markout is a contract-dollar diagnostic of fill quality. It is not used here as a separate additive PnL line item.",
        *(f"- {note}" for note in _economics_notes(summary)),
        "",
        "## Worked fill examples",
        "Quoted prices below are per-option premium. `signed_markout`, `gross_spread_captured`, and `hedge_costs` are shown in contract dollars after multiplying by `qty_contracts * contract_size`.",
        "",
        *(
            _format_worked_fill_example(
                "Representative Fill",
                _worked_fill_rules()["representative"],
                worked_examples.get("representative"),
            )
        ),
        "",
        *(
            _format_worked_fill_example(
                "Stress-case toxic fill",
                _worked_fill_rules()["stress"],
                worked_examples.get("stress"),
                _stress_fill_follow_up(worked_examples.get("stress"))
                if worked_examples.get("stress") is not None
                else None,
            )
        ),
        "",
        "## Most traded contracts",
        *_format_top_contracts(summary),
        "",
        "## Suggested artifact reading order",
            *(
                [f"- `case_brief.md`: {summary['output_files']['case_brief']}"]
                if "case_brief" in summary["output_files"]
                else []
            ),
            f"- `overview_dashboard.png`: {summary['output_files']['overview_dashboard_plot']}",
            f"- `implied_vol_surface_snapshot.png`: {summary['output_files']['implied_vol_surface_snapshot_plot']}",
            f"- `position_surface_heatmap.png`: {summary['output_files']['position_surface_heatmap_plot']}",
            f"- `vega_surface_heatmap.png`: {summary['output_files']['vega_surface_heatmap_plot']}",
            f"- representative fill: {_representative_fill_reference(summary)}",
            f"- `scenario_matrix.md`: {SAMPLE_SCENARIO_MATRIX_ARTIFACT}",
            f"- `toxicity_spread_sensitivity.md`: {SAMPLE_SENSITIVITY_ARTIFACT}",
            "",
            "## Glossary",
            "- **Underlying spot**: the simulated price of the underlying used for option fair value and delta hedging.",
            "- **Fair value**: Black-Scholes option value per option, quoted in premium units from current spot, time to expiry, and implied vol.",
            "- **Reservation price**: inventory-driven quote adjustment that discourages more unwanted risk.",
            "- **Quote skew**: the directional shift in bid and ask caused by reservation price.",
            "- **Signed markout**: future fair-value edge relative to fill price, reported in contract dollars after multiplying by quantity and contract size.",
            "- **Toxic flow**: customer flow more likely to be informed against the current quote.",
            "- **Realized PnL**: contract-dollar gross spread capture less hedge slippage costs.",
            "- **Unrealized PnL**: residual contract-dollar mark-to-market of the option inventory and hedge book.",
            "- **Delta hedge**: underlying trade used to reduce net delta after option fills.",
            "",
            "## Output files",
            f"- Summary JSON: `{summary['output_files']['summary']}`",
            f"- Fills CSV: `{summary['output_files']['fills']}`",
            f"- Checkpoints CSV: `{summary['output_files']['checkpoints']}`",
            f"- PnL timeseries CSV: `{summary['output_files']['pnl_timeseries']}`",
            f"- Final positions CSV: `{summary['output_files']['positions_final']}`",
            f"- Report Markdown: `{summary['output_files']['report']}`",
            f"- Overview dashboard: `{summary['output_files']['overview_dashboard_plot']}`",
            f"- Implied-vol surface snapshot: `{summary['output_files']['implied_vol_surface_snapshot_plot']}`",
            f"- Position surface heatmap: `{summary['output_files']['position_surface_heatmap_plot']}`",
            f"- Vega surface heatmap: `{summary['output_files']['vega_surface_heatmap_plot']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def format_artifact_paths(summary: dict[str, Any]) -> str:
    lines = ["Artifacts"]
    if "case_brief" in summary["output_files"]:
        lines.append(f"- Case brief: {summary['output_files']['case_brief']}")
    lines.extend(
        [
            f"- Report: {summary['output_files']['report']}",
            f"- Summary JSON: {summary['output_files']['summary']}",
            f"- Fills CSV: {summary['output_files']['fills']}",
            f"- Checkpoints CSV: {summary['output_files']['checkpoints']}",
            f"- PnL timeseries CSV: {summary['output_files']['pnl_timeseries']}",
            f"- Final positions CSV: {summary['output_files']['positions_final']}",
            f"- Overview dashboard: {summary['output_files']['overview_dashboard_plot']}",
            f"- Implied-vol surface snapshot: {summary['output_files']['implied_vol_surface_snapshot_plot']}",
            f"- Position surface heatmap: {summary['output_files']['position_surface_heatmap_plot']}",
            f"- Vega surface heatmap: {summary['output_files']['vega_surface_heatmap_plot']}",
            f"- PnL chart: {summary['output_files']['pnl_over_time_plot']}",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class PortfolioRisk:
    delta: float
    gamma: float
    vega: float
    option_value: float


@dataclass(frozen=True)
class Quote:
    fair_value: float
    bid: float
    ask: float
    implied_vol: float
    reservation: float
    half_spread: float
    realized_vol: float
    delta_reservation_component: float
    vega_reservation_component: float
    base_half_spread: float
    vol_half_spread_component: float
    gamma_half_spread_component: float
    greeks: OptionGreeks


class OptionsMarketMakerDemo:
    _TITLE_FONT_SIZE = 14
    _AXIS_LABEL_FONT_SIZE = 11
    _TICK_LABEL_FONT_SIZE = 9
    _LEGEND_FONT_SIZE = 9

    def __init__(self, cfg: OptionsMMConfig) -> None:
        self.cfg = cfg
        self.surface = SimpleVolSurface()
        self.contracts = self._build_contracts()
        self.option_positions = {contract.symbol: 0 for contract in self.contracts}
        self.stock_position = 0.0
        self.cash = 0.0
        self.spread_capture_pnl = 0.0
        self.hedge_costs = 0.0
        self.markout_sum = 0.0
        self.markout_count = 0
        self.adverse_markout_count = 0
        self.toxic_markout_sum = 0.0
        self.toxic_markout_count = 0
        self.non_toxic_markout_sum = 0.0
        self.non_toxic_markout_count = 0
        self.toxic_fill_count = 0
        self.hedge_count = 0
        self.trade_count = 0
        self.abs_delta_before_hedge_sum = 0.0
        self.abs_delta_before_hedge_count = 0
        self.half_spread_sum = 0.0
        self.full_spread_sum = 0.0
        self.max_abs_contract_position_seen = 0
        self.max_abs_stock_position_seen = 0.0
        self.best_single_trade_markout = float("-inf")
        self.worst_single_trade_markout = float("inf")
        self.contract_trade_counts: Counter[str] = Counter()
        self.contract_signed_qty: Counter[str] = Counter()
        self.path_rows: list[dict[str, Any]] = []
        self.checkpoint_rows: list[dict[str, Any]] = []
        self.trade_rows: list[dict[str, Any]] = []
        self._returns: deque[float] = deque(maxlen=cfg.realized_vol_window)

    def _build_contracts(self) -> list[OptionContract]:
        contracts: list[OptionContract] = []
        tenors = [14.0 / 252.0, 45.0 / 252.0, 90.0 / 252.0]
        strike_multipliers = [0.9, 0.95, 1.0, 1.05, 1.1]
        for tenor in tenors:
            tenor_days = int(round(tenor * 252.0))
            for multiplier in strike_multipliers:
                strike = round(self.cfg.spot0 * multiplier, 2)
                for option_type in ("call", "put"):
                    contracts.append(
                        OptionContract(
                            symbol=f"{option_type.upper()}_{strike:.2f}_{tenor_days}D",
                            option_type=option_type,
                            strike=strike,
                            expiry_years=tenor,
                        )
                    )
        return contracts

    def _remaining_years(self, contract: OptionContract, step: int) -> float:
        return max(contract.expiry_years - (step * self.cfg.dt_years), 1e-6)

    def _realized_vol(self) -> float:
        if len(self._returns) < 2:
            return self.cfg.realized_vol
        mean = sum(self._returns) / len(self._returns)
        variance = sum((value - mean) ** 2 for value in self._returns) / (len(self._returns) - 1)
        annualizer = sqrt(1.0 / self.cfg.dt_years)
        return max(0.01, sqrt(max(variance, 0.0)) * annualizer)

    def _abs_option_inventory_contracts(self) -> int:
        return sum(abs(position) for position in self.option_positions.values())

    def _max_abs_option_position(self) -> int:
        return max((abs(position) for position in self.option_positions.values()), default=0)

    def _record_inventory_extremes(self) -> None:
        self.max_abs_contract_position_seen = max(self.max_abs_contract_position_seen, self._max_abs_option_position())
        self.max_abs_stock_position_seen = max(self.max_abs_stock_position_seen, abs(self.stock_position))

    def _average(self, total: float, count: int) -> float:
        return 0.0 if count <= 0 else total / float(count)

    def _comment_flag(self, mm_side: str, signed_markout_value: float, toxic: bool, hedge_qty: float) -> str:
        if signed_markout_value < 0.0 and toxic:
            base = "picked off"
        elif signed_markout_value < 0.0:
            base = "adverse selection"
        elif mm_side == "buy":
            base = "good buy"
        else:
            base = "sold rich"

        if hedge_qty > 0.0:
            return f"{base}; hedged short delta"
        if hedge_qty < 0.0:
            return f"{base}; hedged long delta"
        return base

    def _should_log_fill(self, log_mode: str, toxic: bool, hedge_qty: float, signed_markout_value: float) -> bool:
        if log_mode == "verbose":
            return True
        if toxic or hedge_qty != 0.0:
            return True
        return abs(signed_markout_value) >= 500.0

    def _format_fill_log(self, row: dict[str, Any], log_mode: str) -> str:
        toxic_flag = "Y" if row["toxic_flow"] else "N"
        label = row["markout_horizon_label"]
        if log_mode == "verbose":
            return (
                f"[fill] step={row['step']} spot={row['spot_before']:.3f} contract={row['contract']} "
                f"cust={row['customer_side']} mm={row['mm_side']} qty={row['qty_contracts']} "
                f"fair={row['fair_value']:.3f} bid={row['bid']:.3f} ask={row['ask']:.3f} "
                f"fill={row['fill_price']:.3f} toxic={toxic_flag} markout@{label}={row['signed_markout_preview']:+.2f} "
                f"delta={row['portfolio_delta_after_trade']:.1f}->{row['portfolio_delta_after_hedge']:.1f} "
                f"hedge={row['hedge_qty']:+.0f} pos={row['option_position_after']} "
                f"inv={row['running_inventory_contracts']} tag={row['comment_flag']}"
            )
        return (
            f"[fill] s={row['step']} spot={row['spot_before']:.2f} ctr={row['contract']} "
            f"{row['customer_side']}/{row['qty_contracts']} mm={row['mm_side']} "
            f"fv={row['fair_value']:.2f} q=[{row['bid']:.2f}/{row['ask']:.2f}] fill={row['fill_price']:.2f} "
            f"tox={toxic_flag} mk@{label}={row['signed_markout_preview']:+.0f} "
            f"d={row['portfolio_delta_after_trade']:.0f}->{row['portfolio_delta_after_hedge']:.0f} "
            f"h={row['hedge_qty']:+.0f} pos={row['option_position_after']} inv={row['running_inventory_contracts']} "
            f"tag={row['comment_flag']}"
        )

    def _format_checkpoint(self, row: dict[str, Any]) -> str:
        return (
            f"[checkpoint] {row['step']:>4}/{self.cfg.steps:<4} "
            f"spot={row['spot']:>7.2f} pnl={row['ending_pnl']:>9.2f} "
            f"realized={row['realized_pnl']:>9.2f} unrealized={row['unrealized_pnl']:>9.2f} "
            f"delta={row['net_delta']:>8.1f} inv={row['inventory_contracts']:>3} "
            f"hedges={row['hedge_trade_count']:>3} toxic={row['toxic_fill_count']:>3} "
            f"markout={row['total_signed_markout']:>9.2f}"
        )

    def _portfolio_risk(self, spot: float, step: int) -> PortfolioRisk:
        delta = self.stock_position
        gamma = 0.0
        vega = 0.0
        option_value = 0.0
        for contract in self.contracts:
            position = self.option_positions[contract.symbol]
            if position == 0:
                continue
            remaining = self._remaining_years(contract, step)
            implied_vol = self.surface.implied_vol(spot, contract, remaining)
            greeks = option_metrics(
                spot=spot,
                strike=contract.strike,
                time_to_expiry=remaining,
                rate=self.cfg.rate,
                vol=implied_vol,
                option_type=contract.option_type,
            )
            multiplier = position * self.cfg.contract_size
            delta += multiplier * greeks.delta
            gamma += multiplier * greeks.gamma
            vega += multiplier * greeks.vega
            option_value += multiplier * greeks.price
        return PortfolioRisk(delta=delta, gamma=gamma, vega=vega, option_value=option_value)

    def _mark_to_market(self, spot: float, step: int) -> float:
        risk = self._portfolio_risk(spot, step)
        return self.cash + (self.stock_position * spot) + risk.option_value

    def _select_contract(self, spot: float, step: int, rng: random.Random) -> OptionContract:
        weights: list[float] = []
        for contract in self.contracts:
            remaining = self._remaining_years(contract, step)
            log_moneyness = abs(log(contract.strike / max(spot, 1e-9)))
            moneyness_score = 1.0 / (0.2 + log_moneyness)
            tenor_score = 1.0 / (0.05 + remaining)
            weights.append(moneyness_score * tenor_score)
        return rng.choices(self.contracts, weights=weights, k=1)[0]

    def _quote(self, spot: float, step: int, contract: OptionContract, risk: PortfolioRisk) -> Quote:
        remaining = self._remaining_years(contract, step)
        implied_vol = self.surface.implied_vol(spot, contract, remaining)
        greeks = option_metrics(
            spot=spot,
            strike=contract.strike,
            time_to_expiry=remaining,
            rate=self.cfg.rate,
            vol=implied_vol,
            option_type=contract.option_type,
        )
        realized_vol = self._realized_vol()
        delta_reservation_component = risk.delta * greeks.delta * self.cfg.delta_reservation_mult
        vega_reservation_component = risk.vega * greeks.vega * self.cfg.vega_reservation_mult
        reservation = delta_reservation_component + vega_reservation_component
        vol_half_spread_component = realized_vol * self.cfg.realized_vol_spread_mult
        gamma_half_spread_component = abs(risk.gamma) * self.cfg.gamma_spread_mult
        half_spread = max(
            self.cfg.min_half_spread,
            self.cfg.base_half_spread + vol_half_spread_component + gamma_half_spread_component,
        )
        bid = max(0.01, greeks.price - half_spread - reservation)
        ask = max(bid + 0.01, greeks.price + half_spread - reservation)
        return Quote(
            fair_value=greeks.price,
            bid=bid,
            ask=ask,
            implied_vol=implied_vol,
            reservation=reservation,
            half_spread=half_spread,
            realized_vol=realized_vol,
            delta_reservation_component=delta_reservation_component,
            vega_reservation_component=vega_reservation_component,
            base_half_spread=self.cfg.base_half_spread,
            vol_half_spread_component=vol_half_spread_component,
            gamma_half_spread_component=gamma_half_spread_component,
            greeks=greeks,
        )

    def _economic_direction(self, customer_side: str, greeks: OptionGreeks) -> float:
        customer_delta = greeks.delta if customer_side == "buy" else -greeks.delta
        if customer_delta > 0:
            return 1.0
        if customer_delta < 0:
            return -1.0
        return 0.0

    def _evolve_spot(self, spot: float, rng: random.Random, directional_edge: float) -> float:
        dt = self.cfg.dt_years
        diffusion = self.cfg.realized_vol * sqrt(dt) * rng.gauss(0.0, 1.0)
        jump = 0.0
        if rng.random() < self.cfg.jump_prob:
            jump = rng.gauss(0.0, self.cfg.jump_std)
        log_return = diffusion + jump + (directional_edge * self.cfg.toxic_flow_drift)
        return max(1.0, spot * exp(log_return))

    def _hedge(self, spot: float, step: int, risk: PortfolioRisk) -> tuple[float, float, PortfolioRisk]:
        hedge_qty = 0.0
        hedge_cost = 0.0
        if abs(risk.delta) > self.cfg.hedge_threshold_delta:
            hedge_qty = -float(round(risk.delta))
            if hedge_qty != 0.0:
                slippage = self.cfg.hedge_slippage_bps / 10000.0
                hedge_price = spot * (1.0 + slippage if hedge_qty > 0 else 1.0 - slippage)
                self.stock_position += hedge_qty
                self.cash -= hedge_qty * hedge_price
                hedge_cost = abs(hedge_qty) * spot * slippage
                self.hedge_costs += hedge_cost
                self.hedge_count += 1
                risk = self._portfolio_risk(spot, step)
        return hedge_qty, hedge_cost, risk

    def _write_csv(self, path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
        resolved_fieldnames = fieldnames or (list(rows[0].keys()) if rows else [])
        if not resolved_fieldnames:
            return
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=resolved_fieldnames)
            writer.writeheader()
            if rows:
                writer.writerows(rows)

    def _style_axes(
        self,
        ax: plt.Axes,
        *,
        title: str,
        xlabel: str,
        ylabel: str,
        legend: bool = False,
        legend_loc: str = "best",
        zero_line: bool = False,
    ) -> None:
        ax.set_title(title, fontsize=self._TITLE_FONT_SIZE, pad=10)
        ax.set_xlabel(xlabel, fontsize=self._AXIS_LABEL_FONT_SIZE)
        ax.set_ylabel(ylabel, fontsize=self._AXIS_LABEL_FONT_SIZE)
        ax.tick_params(axis="both", labelsize=self._TICK_LABEL_FONT_SIZE)
        ax.grid(True, alpha=0.25)
        if zero_line:
            ax.axhline(0.0, color="black", linewidth=1)
        if legend:
            ax.legend(loc=legend_loc, fontsize=self._LEGEND_FONT_SIZE, frameon=False)

    def _save_line_plot(
        self,
        path: Path,
        x: list[int],
        series: list[tuple[str, list[float], str]],
        ylabel: str,
        title: str,
    ) -> None:
        fig, ax = plt.subplots(figsize=(10, 4.8))
        for label, values, color in series:
            ax.plot(x, values, label=label, color=color, linewidth=2)
        self._style_axes(
            ax,
            title=title,
            xlabel="Step",
            ylabel=ylabel,
            legend=len(series) > 1,
            zero_line=any(any(value < 0.0 for value in values) for _, values, _ in series),
        )
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_histogram(self, path: Path, values: list[float], title: str, xlabel: str) -> None:
        fig, ax = plt.subplots(figsize=(8.5, 4.8))
        data = values or [0.0]
        ax.hist(data, bins=min(20, max(5, len(data))), color="tab:blue", alpha=0.8, edgecolor="white")
        self._style_axes(ax, title=title, xlabel=xlabel, ylabel="Count", zero_line=False)
        ax.grid(True, axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_bar_chart(
        self,
        path: Path,
        labels: list[str],
        values: list[float],
        title: str,
        ylabel: str,
        color: str = "tab:blue",
    ) -> None:
        fig, ax = plt.subplots(figsize=(9, 4.8))
        if not labels:
            labels = ["none"]
            values = [0.0]
        ax.bar(labels, values, color=color, alpha=0.85)
        self._style_axes(ax, title=title, xlabel="", ylabel=ylabel)
        ax.grid(True, axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=20, labelsize=self._TICK_LABEL_FONT_SIZE)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_markout_comparison(self, path: Path) -> None:
        fig, ax = plt.subplots(figsize=(7.5, 4.8))
        self._render_markout_comparison_axis(ax)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _render_markout_comparison_axis(self, ax: plt.Axes) -> None:
        labels = ["toxic", "non-toxic"]
        values = [
            self._average(self.toxic_markout_sum, self.toxic_markout_count),
            self._average(self.non_toxic_markout_sum, self.non_toxic_markout_count),
        ]
        colors = ["tab:red", "tab:green"]
        ax.bar(labels, values, color=colors, alpha=0.85)
        self._style_axes(
            ax,
            title="Average Signed Markout by Flow Type",
            xlabel="Flow type",
            ylabel="Signed markout",
            zero_line=True,
        )
        ax.grid(True, axis="y", alpha=0.25)

    def _save_overview_dashboard(
        self,
        path: Path,
        steps: list[int],
        ending_pnl: list[float],
        inventory: list[float],
        net_delta: list[float],
    ) -> None:
        fig, axes = plt.subplots(2, 2, figsize=(14, 8))
        fig.suptitle(
            f"Options MM overview dashboard ({self.cfg.scenario_name})",
            fontsize=self._TITLE_FONT_SIZE + 2,
        )

        pnl_ax = axes[0, 0]
        pnl_ax.plot(steps, ending_pnl, color="tab:green", linewidth=2, label="Ending PnL")
        self._style_axes(
            pnl_ax,
            title="Ending PnL Over Time",
            xlabel="Step",
            ylabel="PnL",
            legend=True,
            zero_line=any(value < 0.0 for value in ending_pnl),
        )

        inventory_ax = axes[0, 1]
        inventory_ax.plot(steps, inventory, color="tab:purple", linewidth=2, label="Abs option inventory")
        self._style_axes(
            inventory_ax,
            title="Inventory Over Time",
            xlabel="Step",
            ylabel="Contracts",
            legend=True,
        )

        delta_ax = axes[1, 0]
        delta_ax.plot(steps, net_delta, color="tab:red", linewidth=2, label="Net delta")
        self._style_axes(
            delta_ax,
            title="Net Delta Over Time",
            xlabel="Step",
            ylabel="Delta",
            legend=True,
            zero_line=True,
        )

        markout_ax = axes[1, 1]
        self._render_markout_comparison_axis(markout_ax)

        fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _surface_snapshot(self, spot: float, step: int) -> dict[str, Any]:
        strikes = sorted({float(contract.strike) for contract in self.contracts})
        expiry_days = sorted({int(round(contract.expiry_years * 252.0)) for contract in self.contracts})
        strike_index = {strike: idx for idx, strike in enumerate(strikes)}
        expiry_index = {expiry: idx for idx, expiry in enumerate(expiry_days)}

        position_matrix = [[0.0 for _ in strikes] for _ in expiry_days]
        vega_matrix = [[0.0 for _ in strikes] for _ in expiry_days]

        for contract in self.contracts:
            position = self.option_positions[contract.symbol]
            if position == 0:
                continue
            expiry = int(round(contract.expiry_years * 252.0))
            row_idx = expiry_index[expiry]
            col_idx = strike_index[float(contract.strike)]
            position_matrix[row_idx][col_idx] += float(position)

            remaining = self._remaining_years(contract, step)
            implied_vol = self.surface.implied_vol(spot, contract, remaining)
            greeks = option_metrics(
                spot=spot,
                strike=contract.strike,
                time_to_expiry=remaining,
                rate=self.cfg.rate,
                vol=implied_vol,
                option_type=contract.option_type,
            )
            vega_matrix[row_idx][col_idx] += float(position) * self.cfg.contract_size * greeks.vega

        active_cells = sum(
            1
            for row_idx in range(len(expiry_days))
            for col_idx in range(len(strikes))
            if position_matrix[row_idx][col_idx] != 0.0 or vega_matrix[row_idx][col_idx] != 0.0
        )
        largest_position = max(
            (
                {
                    "strike": strike,
                    "expiry_days": expiry,
                    "contracts": position_matrix[expiry_index[expiry]][strike_index[strike]],
                }
                for expiry in expiry_days
                for strike in strikes
            ),
            key=lambda bucket: abs(bucket["contracts"]),
            default={"strike": strikes[0], "expiry_days": expiry_days[0], "contracts": 0.0},
        )
        largest_vega = max(
            (
                {
                    "strike": strike,
                    "expiry_days": expiry,
                    "net_vega": vega_matrix[expiry_index[expiry]][strike_index[strike]],
                }
                for expiry in expiry_days
                for strike in strikes
            ),
            key=lambda bucket: abs(bucket["net_vega"]),
            default={"strike": strikes[0], "expiry_days": expiry_days[0], "net_vega": 0.0},
        )
        return {
            "strikes": strikes,
            "expiry_days": expiry_days,
            "position_contracts": [[round(value, 6) for value in row] for row in position_matrix],
            "net_vega": [[round(value, 6) for value in row] for row in vega_matrix],
            "active_cells": active_cells,
            "largest_position_bucket": {
                "strike": round(float(largest_position["strike"]), 6),
                "expiry_days": int(largest_position["expiry_days"]),
                "contracts": round(float(largest_position["contracts"]), 6),
            },
            "largest_vega_bucket": {
                "strike": round(float(largest_vega["strike"]), 6),
                "expiry_days": int(largest_vega["expiry_days"]),
                "net_vega": round(float(largest_vega["net_vega"]), 6),
            },
        }

    def _pricing_surface_snapshot(self) -> dict[str, Any]:
        strikes = sorted({float(contract.strike) for contract in self.contracts})
        expiry_days = sorted({int(round(contract.expiry_years * 252.0)) for contract in self.contracts})
        vol_matrix: list[list[float]] = []
        for expiry in expiry_days:
            row: list[float] = []
            for strike in strikes:
                contract = next(
                    item
                    for item in self.contracts
                    if int(round(item.expiry_years * 252.0)) == expiry and float(item.strike) == strike
                )
                implied_vol = self.surface.implied_vol(self.cfg.spot0, contract, contract.expiry_years)
                row.append(round(implied_vol, 6))
            vol_matrix.append(row)
        return {
            "snapshot_label": "initial snapshot",
            "spot": round(self.cfg.spot0, 6),
            "strikes": strikes,
            "expiry_days": expiry_days,
            "implied_vols": vol_matrix,
        }

    def _save_surface_heatmap(
        self,
        path: Path,
        matrix: list[list[float]],
        strikes: list[float],
        expiry_days: list[int],
        *,
        title: str,
        colorbar_label: str,
        annotation_fmt: str,
    ) -> None:
        fig, ax = plt.subplots(figsize=(10, 4.8))
        max_abs = max((abs(value) for row in matrix for value in row), default=0.0)
        if max_abs == 0.0:
            max_abs = 1.0
        image = ax.imshow(matrix, cmap="RdBu_r", vmin=-max_abs, vmax=max_abs, aspect="auto")
        self._style_axes(ax, title=title, xlabel="Strike", ylabel="Expiry (days)")
        ax.set_xticks(range(len(strikes)), [f"{strike:.0f}" for strike in strikes])
        ax.set_yticks(range(len(expiry_days)), [str(expiry) for expiry in expiry_days])
        for row_idx, row in enumerate(matrix):
            for col_idx, value in enumerate(row):
                text_color = "white" if abs(value) > max_abs * 0.55 else "black"
                ax.text(
                    col_idx,
                    row_idx,
                    format(value, annotation_fmt),
                    ha="center",
                    va="center",
                    fontsize=self._TICK_LABEL_FONT_SIZE,
                    color=text_color,
                )
        colorbar = fig.colorbar(image, ax=ax)
        colorbar.ax.set_ylabel(colorbar_label, fontsize=self._AXIS_LABEL_FONT_SIZE)
        colorbar.ax.tick_params(labelsize=self._TICK_LABEL_FONT_SIZE)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_implied_vol_surface_snapshot(self, path: Path, snapshot: dict[str, Any]) -> None:
        matrix = snapshot["implied_vols"]
        strikes = snapshot["strikes"]
        expiry_days = snapshot["expiry_days"]
        fig, ax = plt.subplots(figsize=(10, 4.8))
        image = ax.imshow(matrix, cmap="viridis", aspect="auto")
        self._style_axes(
            ax,
            title=f"Implied Vol Surface Snapshot ({snapshot['snapshot_label']})",
            xlabel="Strike",
            ylabel="Expiry (days)",
        )
        ax.set_xticks(range(len(strikes)), [f"{strike:.0f}" for strike in strikes])
        ax.set_yticks(range(len(expiry_days)), [str(expiry) for expiry in expiry_days])
        max_vol = max((value for row in matrix for value in row), default=0.0)
        for row_idx, row in enumerate(matrix):
            for col_idx, value in enumerate(row):
                text_color = "white" if max_vol and value > max_vol * 0.6 else "black"
                ax.text(
                    col_idx,
                    row_idx,
                    f"{value:.2%}",
                    ha="center",
                    va="center",
                    fontsize=self._TICK_LABEL_FONT_SIZE,
                    color=text_color,
                )
        colorbar = fig.colorbar(image, ax=ax)
        colorbar.ax.set_ylabel("Implied vol", fontsize=self._AXIS_LABEL_FONT_SIZE)
        colorbar.ax.tick_params(labelsize=self._TICK_LABEL_FONT_SIZE)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _write_plots(self, out_dir: Path) -> dict[str, str]:
        steps = [int(row["step"]) for row in self.path_rows]
        ending_pnl = [float(row["ending_pnl"]) for row in self.path_rows]
        realized_pnl = [float(row["realized_pnl"]) for row in self.path_rows]
        unrealized_pnl = [float(row["unrealized_pnl"]) for row in self.path_rows]
        spot = [float(row["spot"]) for row in self.path_rows]
        inventory = [float(row["inventory_contracts"]) for row in self.path_rows]
        net_delta = [float(row["net_delta"]) for row in self.path_rows]
        markouts = [float(row["signed_markout"]) for row in self.trade_rows]
        top_contracts = self.contract_trade_counts.most_common(6)
        surface_risk = self._surface_snapshot(spot[-1] if spot else self.cfg.spot0, self.cfg.steps)
        pricing_surface = self._pricing_surface_snapshot()

        paths = {
            "pnl_over_time_plot": out_dir / "pnl_over_time.png",
            "realized_vs_unrealized_plot": out_dir / "realized_vs_unrealized.png",
            "spot_path_plot": out_dir / "spot_path.png",
            "inventory_over_time_plot": out_dir / "inventory_over_time.png",
            "net_delta_over_time_plot": out_dir / "net_delta_over_time.png",
            "markout_distribution_plot": out_dir / "markout_distribution.png",
            "toxic_vs_nontoxic_plot": out_dir / "toxic_vs_nontoxic_markout.png",
            "top_traded_contracts_plot": out_dir / "top_traded_contracts.png",
            "overview_dashboard_plot": out_dir / "overview_dashboard.png",
            "implied_vol_surface_snapshot_plot": out_dir / "implied_vol_surface_snapshot.png",
            "position_surface_heatmap_plot": out_dir / "position_surface_heatmap.png",
            "vega_surface_heatmap_plot": out_dir / "vega_surface_heatmap.png",
        }

        self._save_line_plot(
            paths["pnl_over_time_plot"],
            steps,
            [("Ending PnL", ending_pnl, "tab:green")],
            ylabel="PnL",
            title=f"Ending PnL Over Time ({self.cfg.scenario_name})",
        )
        self._save_line_plot(
            paths["realized_vs_unrealized_plot"],
            steps,
            [
                ("Realized PnL", realized_pnl, "tab:blue"),
                ("Unrealized PnL", unrealized_pnl, "tab:orange"),
            ],
            ylabel="PnL",
            title="Realized and Unrealized PnL Over Time",
        )
        self._save_line_plot(
            paths["spot_path_plot"],
            steps,
            [("Underlying spot", spot, "tab:brown")],
            ylabel="Spot",
            title="Underlying Spot Over Time",
        )
        self._save_line_plot(
            paths["inventory_over_time_plot"],
            steps,
            [("Abs option inventory", inventory, "tab:purple")],
            ylabel="Contracts",
            title="Inventory Over Time",
        )
        self._save_line_plot(
            paths["net_delta_over_time_plot"],
            steps,
            [("Net delta", net_delta, "tab:red")],
            ylabel="Delta",
            title="Net Delta Over Time",
        )
        self._save_histogram(
            paths["markout_distribution_plot"],
            markouts,
            title=f"Signed Markout Distribution ({markout_horizon_label(self.cfg.markout_horizon_steps)})",
            xlabel="Signed markout",
        )
        self._save_markout_comparison(paths["toxic_vs_nontoxic_plot"])
        self._save_bar_chart(
            paths["top_traded_contracts_plot"],
            [item[0] for item in top_contracts],
            [float(item[1]) for item in top_contracts],
            title="Top Traded Contracts by Fill Count",
            ylabel="Fills",
            color="tab:cyan",
        )
        self._save_overview_dashboard(
            paths["overview_dashboard_plot"],
            steps,
            ending_pnl,
            inventory,
            net_delta,
        )
        self._save_surface_heatmap(
            paths["position_surface_heatmap_plot"],
            surface_risk["position_contracts"],
            surface_risk["strikes"],
            surface_risk["expiry_days"],
            title="Final Position Surface",
            colorbar_label="Signed contracts",
            annotation_fmt="+.0f",
        )
        self._save_surface_heatmap(
            paths["vega_surface_heatmap_plot"],
            surface_risk["net_vega"],
            surface_risk["strikes"],
            surface_risk["expiry_days"],
            title="Final Vega Surface",
            colorbar_label="Net vega",
            annotation_fmt="+.0f",
        )
        self._save_implied_vol_surface_snapshot(paths["implied_vol_surface_snapshot_plot"], pricing_surface)

        return {key: str(value) for key, value in paths.items()}

    def _checkpoint_row(
        self,
        step: int,
        spot: float,
        risk: PortfolioRisk,
        ending_pnl: float,
        realized_pnl: float,
        unrealized_pnl: float,
        max_drawdown: float,
    ) -> dict[str, Any]:
        return {
            "step": step,
            "spot": round(spot, 6),
            "ending_pnl": round(ending_pnl, 6),
            "realized_pnl": round(realized_pnl, 6),
            "unrealized_pnl": round(unrealized_pnl, 6),
            "gross_spread_captured": round(self.spread_capture_pnl, 6),
            "hedge_costs": round(self.hedge_costs, 6),
            "total_signed_markout": round(self.markout_sum, 6),
            "net_delta": round(risk.delta, 6),
            "net_gamma": round(risk.gamma, 6),
            "net_vega": round(risk.vega, 6),
            "inventory_contracts": self._abs_option_inventory_contracts(),
            "max_single_contract_position": self._max_abs_option_position(),
            "hedge_trade_count": self.hedge_count,
            "trade_count": self.trade_count,
            "toxic_fill_count": self.toxic_fill_count,
            "toxic_fill_rate": round(float(self.toxic_fill_count) / float(self.trade_count), 6)
            if self.trade_count
            else 0.0,
            "worst_drawdown": round(max_drawdown, 6),
        }

    def _finalize_markouts(self) -> None:
        self.markout_sum = 0.0
        self.markout_count = 0
        self.adverse_markout_count = 0
        self.toxic_markout_sum = 0.0
        self.toxic_markout_count = 0
        self.non_toxic_markout_sum = 0.0
        self.non_toxic_markout_count = 0
        self.best_single_trade_markout = float("-inf")
        self.worst_single_trade_markout = float("inf")

        last_index = max(len(self.path_rows) - 1, 0)
        requested_horizon = self.cfg.markout_horizon_steps

        for row in self.trade_rows:
            fill_step = int(row["step"])
            reference_index = min(fill_step + requested_horizon - 2, last_index)
            reference_row = self.path_rows[reference_index]
            reference_spot = float(reference_row["spot"])
            remaining = self._remaining_years(row["_contract_obj"], reference_index + 1)
            reference_vol = self.surface.implied_vol(reference_spot, row["_contract_obj"], remaining)
            reference_greeks = option_metrics(
                spot=reference_spot,
                strike=row["_contract_obj"].strike,
                time_to_expiry=remaining,
                rate=self.cfg.rate,
                vol=reference_vol,
                option_type=row["_contract_obj"].option_type,
            )
            effective_horizon_steps = reference_index - (fill_step - 1) + 1
            markout_value = signed_markout(
                mm_side=str(row["mm_side"]),
                fill_price=float(row["fill_price"]),
                reference_fair_value=reference_greeks.price,
                qty_contracts=int(row["qty_contracts"]),
                contract_size=self.cfg.contract_size,
            )
            row["markout_reference_step"] = reference_index + 1
            row["markout_reference_spot"] = round(reference_spot, 6)
            row["markout_reference_fair_value"] = round(reference_greeks.price, 6)
            row["effective_markout_horizon_steps"] = effective_horizon_steps
            row["effective_markout_horizon_label"] = markout_horizon_label(effective_horizon_steps)
            row["signed_markout"] = round(markout_value, 6)
            row["comment_flag"] = self._comment_flag(
                str(row["mm_side"]),
                markout_value,
                bool(row["toxic_flow"]),
                float(row["hedge_qty"]),
            )

            self.markout_sum += markout_value
            self.markout_count += 1
            if markout_value < 0.0:
                self.adverse_markout_count += 1
            if bool(row["toxic_flow"]):
                self.toxic_markout_sum += markout_value
                self.toxic_markout_count += 1
            else:
                self.non_toxic_markout_sum += markout_value
                self.non_toxic_markout_count += 1
            self.best_single_trade_markout = max(self.best_single_trade_markout, markout_value)
            self.worst_single_trade_markout = min(self.worst_single_trade_markout, markout_value)

        if self.markout_count == 0:
            self.best_single_trade_markout = 0.0
            self.worst_single_trade_markout = 0.0

        running_markout = 0.0
        by_step: dict[int, float] = {}
        for row in self.trade_rows:
            step = int(row["step"])
            by_step[step] = by_step.get(step, 0.0) + float(row["signed_markout"])
        for path_row in self.path_rows:
            running_markout += by_step.get(int(path_row["step"]), 0.0)
            path_row["total_signed_markout"] = round(running_markout, 6)

        checkpoint_by_step = {int(row["step"]): row for row in self.checkpoint_rows}
        for step, checkpoint in checkpoint_by_step.items():
            checkpoint["total_signed_markout"] = round(
                sum(float(row["signed_markout"]) for row in self.trade_rows if int(row["step"]) <= step),
                6,
            )

    def _build_positions_rows(self, spot: float, step: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.stock_position != 0.0:
            rows.append(
                {
                    "instrument_type": "underlying",
                    "symbol": "UNDERLYING",
                    "option_type": "",
                    "strike": "",
                    "expiry_days": "",
                    "quantity": round(self.stock_position, 6),
                    "mark_price": round(spot, 6),
                    "mark_value": round(self.stock_position * spot, 6),
                    "net_delta_contribution": round(self.stock_position, 6),
                    "net_vega_contribution": 0.0,
                }
            )
        for contract in self.contracts:
            position = self.option_positions[contract.symbol]
            if position == 0:
                continue
            remaining = self._remaining_years(contract, step)
            implied_vol = self.surface.implied_vol(spot, contract, remaining)
            greeks = option_metrics(
                spot=spot,
                strike=contract.strike,
                time_to_expiry=remaining,
                rate=self.cfg.rate,
                vol=implied_vol,
                option_type=contract.option_type,
            )
            mark_value = position * self.cfg.contract_size * greeks.price
            net_delta = position * self.cfg.contract_size * greeks.delta
            net_vega = position * self.cfg.contract_size * greeks.vega
            rows.append(
                {
                    "instrument_type": "option",
                    "symbol": contract.symbol,
                    "option_type": contract.option_type,
                    "strike": round(contract.strike, 6),
                    "expiry_days": int(round(contract.expiry_years * 252.0)),
                    "quantity": position,
                    "mark_price": round(greeks.price, 6),
                    "mark_value": round(mark_value, 6),
                    "net_delta_contribution": round(net_delta, 6),
                    "net_vega_contribution": round(net_vega, 6),
                }
            )
        return rows

    def _build_summary(
        self,
        spot: float,
        final_risk: PortfolioRisk,
        ending_pnl: float,
        realized_pnl: float,
        unrealized_pnl: float,
        max_drawdown: float,
    ) -> dict[str, Any]:
        avg_markout = self._average(self.markout_sum, self.markout_count)
        avg_half_spread = self._average(self.half_spread_sum, self.trade_count)
        surface_risk = self._surface_snapshot(spot, self.cfg.steps)
        pricing_surface = self._pricing_surface_snapshot()
        summary: dict[str, Any] = {
            "scenario": self.cfg.scenario_name,
            "scenario_description": self.cfg.scenario_description,
            "scenario_card": scenario_card(self.cfg.scenario_name),
            "seed": self.cfg.seed,
            "steps": self.cfg.steps,
            "spot_start": round(self.cfg.spot0, 6),
            "spot_final": round(spot, 6),
            "markout_horizon_steps": self.cfg.markout_horizon_steps,
            "markout_horizon_label": markout_horizon_label(self.cfg.markout_horizon_steps),
            "markout_definition": (
                "Signed markout compares fill price with future option fair value at a fixed horizon, in contract "
                "dollars after multiplying by quantity and contract size. Positive is good for the dealer; "
                "negative is adverse selection."
            ),
            "quote_price_units": "Per-option premium in underlying price units.",
            "pnl_units": "Contract dollars unless otherwise stated.",
            "hedge_trigger_delta": round(self.cfg.hedge_threshold_delta, 6),
            "trade_count": self.trade_count,
            "fill_count": self.trade_count,
            "hedge_trade_count": self.hedge_count,
            "ending_pnl": round(ending_pnl, 6),
            "realized_pnl": round(realized_pnl, 6),
            "unrealized_pnl": round(unrealized_pnl, 6),
            "gross_spread_captured": round(self.spread_capture_pnl, 6),
            "hedge_costs": round(self.hedge_costs, 6),
            "total_signed_markout": round(self.markout_sum, 6),
            "average_signed_markout": round(avg_markout, 6),
            "avg_toxic_markout": round(self._average(self.toxic_markout_sum, self.toxic_markout_count), 6),
            "avg_non_toxic_markout": round(
                self._average(self.non_toxic_markout_sum, self.non_toxic_markout_count),
                6,
            ),
            "adverse_fill_count": self.adverse_markout_count,
            "adverse_fill_rate": round(float(self.adverse_markout_count) / float(self.trade_count), 6)
            if self.trade_count
            else 0.0,
            "toxic_fill_count": self.toxic_fill_count,
            "toxic_fill_rate": round(float(self.toxic_fill_count) / float(self.trade_count), 6)
            if self.trade_count
            else 0.0,
            "avg_half_spread": round(avg_half_spread, 6),
            "avg_quote_width": round(2.0 * avg_half_spread, 6),
            "avg_abs_delta_before_hedge": round(
                self._average(self.abs_delta_before_hedge_sum, self.abs_delta_before_hedge_count),
                6,
            ),
            "max_inventory_contracts": self._abs_option_inventory_contracts()
            if not self.path_rows
            else max(int(row["inventory_contracts"]) for row in self.path_rows),
            "max_single_contract_position": self.max_abs_contract_position_seen,
            "max_abs_stock_position": round(self.max_abs_stock_position_seen, 6),
            "worst_drawdown": round(max_drawdown, 6),
            "best_single_trade_markout": round(self.best_single_trade_markout, 6),
            "worst_single_trade_markout": round(self.worst_single_trade_markout, 6),
            "final_net_delta": round(final_risk.delta, 6),
            "final_net_vega": round(final_risk.vega, 6),
            "final_stock_position": round(self.stock_position, 6),
            "final_option_inventory_contracts": self._abs_option_inventory_contracts(),
            "most_traded_contracts": [
                {
                    "contract": contract,
                    "trade_count": trade_count,
                    "signed_contract_qty": int(self.contract_signed_qty[contract]),
                }
                for contract, trade_count in self.contract_trade_counts.most_common(5)
            ],
            "parameters": asdict(self.cfg),
            "simulation_scope": "Synthetic customer-flow dealer study; not a venue order-book replay.",
            "surface_risk": surface_risk,
            "pricing_surface": pricing_surface,
        }
        return summary

    def run(
        self,
        out_dir: Path,
        verbose: bool = False,
        progress_every: int = 25,
        log_mode: str = "compact",
        walkthrough_mode: bool = False,
        write_artifacts: bool = True,
    ) -> dict[str, Any]:
        rng = random.Random(self.cfg.seed)
        if write_artifacts:
            out_dir.mkdir(parents=True, exist_ok=True)

        spot = self.cfg.spot0
        equity_peak = 0.0
        max_drawdown = 0.0

        for step_index in range(self.cfg.steps):
            step = step_index + 1
            risk_before = self._portfolio_risk(spot, step_index)
            directional_edge = 0.0

            if rng.random() < self.cfg.customer_arrival_prob:
                contract = self._select_contract(spot, step_index, rng)
                qty = rng.randint(self.cfg.min_trade_size, self.cfg.max_trade_size)
                customer_side = "buy" if rng.random() < 0.5 else "sell"
                mm_side = "sell" if customer_side == "buy" else "buy"
                toxic = rng.random() < self.cfg.toxic_flow_prob
                quote = self._quote(spot, step_index, contract, risk_before)

                option_position_before = self.option_positions[contract.symbol]
                mm_position_change = -qty if customer_side == "buy" else qty
                fill_price = quote.ask if customer_side == "buy" else quote.bid
                self.option_positions[contract.symbol] += mm_position_change
                self.cash -= mm_position_change * fill_price * self.cfg.contract_size
                self.trade_count += 1
                self.contract_trade_counts[contract.symbol] += 1
                self.contract_signed_qty[contract.symbol] += mm_position_change
                self.half_spread_sum += quote.half_spread
                self.full_spread_sum += 2.0 * quote.half_spread
                if toxic:
                    self.toxic_fill_count += 1

                spread_edge = fill_price - quote.fair_value if customer_side == "buy" else quote.fair_value - fill_price
                spread_capture_pnl = spread_edge * qty * self.cfg.contract_size
                self.spread_capture_pnl += spread_capture_pnl

                risk_after_trade = self._portfolio_risk(spot, step_index)
                self.abs_delta_before_hedge_sum += abs(risk_after_trade.delta)
                self.abs_delta_before_hedge_count += 1

                if toxic:
                    directional_edge = self._economic_direction(customer_side, quote.greeks)

                hedge_qty, hedge_cost, risk_after_hedge = self._hedge(spot, step_index, risk_after_trade)
                self._record_inventory_extremes()
                next_spot = self._evolve_spot(spot, rng, directional_edge)

                preview_remaining = self._remaining_years(contract, step_index + 1)
                preview_vol = self.surface.implied_vol(next_spot, contract, preview_remaining)
                preview_greeks = option_metrics(
                    spot=next_spot,
                    strike=contract.strike,
                    time_to_expiry=preview_remaining,
                    rate=self.cfg.rate,
                    vol=preview_vol,
                    option_type=contract.option_type,
                )
                signed_markout_preview = signed_markout(
                    mm_side=mm_side,
                    fill_price=fill_price,
                    reference_fair_value=preview_greeks.price,
                    qty_contracts=qty,
                    contract_size=self.cfg.contract_size,
                )
                fill_row = {
                    "step": step,
                    "spot_before": round(spot, 6),
                    "spot_after_fill_move": round(next_spot, 6),
                    "contract": contract.symbol,
                    "option_type": contract.option_type,
                    "strike": round(contract.strike, 6),
                    "expiry_days": round(self._remaining_years(contract, step_index) * 252.0, 6),
                    "customer_side": customer_side,
                    "mm_side": mm_side,
                    "qty_contracts": qty,
                    "contract_size": self.cfg.contract_size,
                    "fair_value": round(quote.fair_value, 6),
                    "bid": round(quote.bid, 6),
                    "ask": round(quote.ask, 6),
                    "fill_price": round(fill_price, 6),
                    "toxic_flow": toxic,
                    "markout_horizon_steps": self.cfg.markout_horizon_steps,
                    "markout_horizon_label": markout_horizon_label(self.cfg.markout_horizon_steps),
                    "signed_markout_preview": round(signed_markout_preview, 6),
                    "portfolio_delta_before": round(risk_before.delta, 6),
                    "portfolio_delta_after_trade": round(risk_after_trade.delta, 6),
                    "hedge_qty": round(hedge_qty, 6),
                    "hedge_cost": round(hedge_cost, 6),
                    "portfolio_delta_after_hedge": round(risk_after_hedge.delta, 6),
                    "portfolio_vega_after_hedge": round(risk_after_hedge.vega, 6),
                    "option_position_before": option_position_before,
                    "option_position_after": self.option_positions[contract.symbol],
                    "running_inventory_contracts": self._abs_option_inventory_contracts(),
                    "spread_capture_pnl": round(spread_capture_pnl, 6),
                    "reservation_price": round(quote.reservation, 6),
                    "delta_reservation_component": round(quote.delta_reservation_component, 6),
                    "vega_reservation_component": round(quote.vega_reservation_component, 6),
                    "half_spread": round(quote.half_spread, 6),
                    "quote_width": round(2.0 * quote.half_spread, 6),
                    "base_half_spread": round(quote.base_half_spread, 6),
                    "vol_half_spread_component": round(quote.vol_half_spread_component, 6),
                    "gamma_half_spread_component": round(quote.gamma_half_spread_component, 6),
                    "implied_vol": round(quote.implied_vol, 6),
                    "realized_vol": round(quote.realized_vol, 6),
                    "option_delta": round(quote.greeks.delta, 6),
                    "option_gamma": round(quote.greeks.gamma, 6),
                    "option_vega": round(quote.greeks.vega, 6),
                    "comment_flag": self._comment_flag(mm_side, signed_markout_preview, toxic, hedge_qty),
                    "_contract_obj": contract,
                }
                self.trade_rows.append(fill_row)
                self.markout_sum += signed_markout_preview
                if verbose and self._should_log_fill(log_mode, toxic, hedge_qty, signed_markout_preview):
                    print(self._format_fill_log(fill_row, log_mode), flush=True)
            else:
                next_spot = self._evolve_spot(spot, rng, directional_edge)

            if next_spot > 0.0 and spot > 0.0:
                self._returns.append(log(next_spot / spot))
            spot = next_spot

            risk_now = self._portfolio_risk(spot, step)
            ending_pnl = self._mark_to_market(spot, step)
            realized_pnl = self.spread_capture_pnl - self.hedge_costs
            unrealized_pnl = ending_pnl - realized_pnl
            equity_peak = max(equity_peak, ending_pnl)
            max_drawdown = max(max_drawdown, equity_peak - ending_pnl)
            self._record_inventory_extremes()

            self.path_rows.append(
                {
                    "step": step,
                    "spot": round(spot, 6),
                    "stock_position": round(self.stock_position, 6),
                    "option_value": round(risk_now.option_value, 6),
                    "net_delta": round(risk_now.delta, 6),
                    "net_gamma": round(risk_now.gamma, 6),
                    "net_vega": round(risk_now.vega, 6),
                    "inventory_contracts": self._abs_option_inventory_contracts(),
                    "max_single_contract_position": self._max_abs_option_position(),
                    "realized_vol": round(self._realized_vol(), 6),
                    "cash": round(self.cash, 6),
                    "realized_pnl": round(realized_pnl, 6),
                    "unrealized_pnl": round(unrealized_pnl, 6),
                    "ending_pnl": round(ending_pnl, 6),
                    "gross_spread_captured": round(self.spread_capture_pnl, 6),
                    "hedge_costs": round(self.hedge_costs, 6),
                    "trade_count": self.trade_count,
                    "hedge_trade_count": self.hedge_count,
                    "toxic_fill_count": self.toxic_fill_count,
                    "total_signed_markout": round(self.markout_sum, 6),
                }
            )

            if progress_every > 0 and (step % progress_every == 0 or step == self.cfg.steps):
                checkpoint = self._checkpoint_row(
                    step=step,
                    spot=spot,
                    risk=risk_now,
                    ending_pnl=ending_pnl,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    max_drawdown=max_drawdown,
                )
                self.checkpoint_rows.append(checkpoint)
                if verbose:
                    print(self._format_checkpoint(checkpoint), flush=True)

        self._finalize_markouts()

        for row in self.trade_rows:
            row.pop("_contract_obj", None)

        final_risk = self._portfolio_risk(spot, self.cfg.steps)
        ending_pnl = self._mark_to_market(spot, self.cfg.steps)
        realized_pnl = self.spread_capture_pnl - self.hedge_costs
        unrealized_pnl = ending_pnl - realized_pnl
        summary = self._build_summary(
            spot=spot,
            final_risk=final_risk,
            ending_pnl=ending_pnl,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            max_drawdown=max_drawdown,
        )

        positions_rows = self._build_positions_rows(spot, self.cfg.steps)
        output_files: dict[str, str] = {}
        if write_artifacts:
            output_files = {
                "summary": str(out_dir / "summary.json"),
                "fills": str(out_dir / "fills.csv"),
                "checkpoints": str(out_dir / "checkpoints.csv"),
                "pnl_timeseries": str(out_dir / "pnl_timeseries.csv"),
                "positions_final": str(out_dir / "positions_final.csv"),
                "report": str(out_dir / "demo_report.md"),
            }
            if walkthrough_mode:
                output_files["case_brief"] = str(out_dir / "case_brief.md")
            output_files.update(self._write_plots(out_dir))
        summary["output_files"] = output_files

        fills_fields = [
            "step",
            "spot_before",
            "spot_after_fill_move",
            "contract",
            "option_type",
            "strike",
            "expiry_days",
            "customer_side",
            "mm_side",
            "qty_contracts",
            "contract_size",
            "fair_value",
            "bid",
            "ask",
            "fill_price",
            "toxic_flow",
            "markout_horizon_steps",
            "markout_horizon_label",
            "signed_markout_preview",
            "markout_reference_step",
            "markout_reference_spot",
            "markout_reference_fair_value",
            "effective_markout_horizon_steps",
            "effective_markout_horizon_label",
            "signed_markout",
            "portfolio_delta_before",
            "portfolio_delta_after_trade",
            "hedge_qty",
            "hedge_cost",
            "portfolio_delta_after_hedge",
            "portfolio_vega_after_hedge",
            "option_position_before",
            "option_position_after",
            "running_inventory_contracts",
            "spread_capture_pnl",
            "reservation_price",
            "delta_reservation_component",
            "vega_reservation_component",
            "half_spread",
            "quote_width",
            "base_half_spread",
            "vol_half_spread_component",
            "gamma_half_spread_component",
            "implied_vol",
            "realized_vol",
            "option_delta",
            "option_gamma",
            "option_vega",
            "comment_flag",
        ]
        checkpoint_fields = [
            "step",
            "spot",
            "ending_pnl",
            "realized_pnl",
            "unrealized_pnl",
            "gross_spread_captured",
            "hedge_costs",
            "total_signed_markout",
            "net_delta",
            "net_gamma",
            "net_vega",
            "inventory_contracts",
            "max_single_contract_position",
            "hedge_trade_count",
            "trade_count",
            "toxic_fill_count",
            "toxic_fill_rate",
            "worst_drawdown",
        ]
        pnl_fields = [
            "step",
            "spot",
            "stock_position",
            "option_value",
            "net_delta",
            "net_gamma",
            "net_vega",
            "inventory_contracts",
            "max_single_contract_position",
            "realized_vol",
            "cash",
            "realized_pnl",
            "unrealized_pnl",
            "ending_pnl",
            "gross_spread_captured",
            "hedge_costs",
            "trade_count",
            "hedge_trade_count",
            "toxic_fill_count",
            "total_signed_markout",
        ]
        positions_fields = [
            "instrument_type",
            "symbol",
            "option_type",
            "strike",
            "expiry_days",
            "quantity",
            "mark_price",
            "mark_value",
            "net_delta_contribution",
            "net_vega_contribution",
        ]
        if write_artifacts:
            worked_examples = select_worked_fill_examples(self.trade_rows)
            self._write_csv(Path(output_files["fills"]), self.trade_rows, fieldnames=fills_fields)
            self._write_csv(Path(output_files["checkpoints"]), self.checkpoint_rows, fieldnames=checkpoint_fields)
            self._write_csv(Path(output_files["pnl_timeseries"]), self.path_rows, fieldnames=pnl_fields)
            self._write_csv(Path(output_files["positions_final"]), positions_rows, fieldnames=positions_fields)
            Path(output_files["report"]).write_text(
                format_demo_report(summary, worked_examples),
                encoding="utf-8",
            )
            if walkthrough_mode:
                Path(output_files["case_brief"]).write_text(
                    format_case_brief(summary, worked_examples),
                    encoding="utf-8",
                )
            with Path(output_files["summary"]).open("w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)

        return summary
