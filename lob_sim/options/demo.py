from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from math import exp, log, sqrt
from pathlib import Path
from typing import Any
import csv
import json
import random

import matplotlib.pyplot as plt

from .black_scholes import OptionContract, OptionGreeks, option_metrics
from .markout import markout_horizon_label, signed_markout
from .surface import SimpleVolSurface


DEFAULT_OPTIONS_SCENARIO = "calm_market"
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
                "value at a fixed future step; positive is good for the dealer, negative indicates adverse selection."
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


def format_demo_report(summary: dict[str, Any]) -> str:
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
            "## Most traded contracts",
            *_format_top_contracts(summary),
            "",
            "## Suggested artifact reading order",
            f"- `demo_report.md`: {summary['output_files']['report']}",
            f"- `fills.csv`: {summary['output_files']['fills']}",
            f"- `pnl_timeseries.csv`: {summary['output_files']['pnl_timeseries']}",
            f"- `checkpoints.csv`: {summary['output_files']['checkpoints']}",
            f"- `pnl_over_time.png`: {summary['output_files']['pnl_over_time_plot']}",
            "",
            "## Glossary",
            "- **Underlying spot**: the simulated price of the underlying used for option fair value and delta hedging.",
            "- **Fair value**: Black-Scholes option value from current spot, time to expiry, and implied vol.",
            "- **Reservation price**: inventory-driven quote adjustment that discourages more unwanted risk.",
            "- **Quote skew**: the directional shift in bid and ask caused by reservation price.",
            "- **Signed markout**: future fair-value edge relative to fill price, positive when the fill ages well for the dealer.",
            "- **Toxic flow**: customer flow more likely to be informed against the current quote.",
            "- **Realized PnL**: gross spread capture less hedge slippage costs.",
            "- **Unrealized PnL**: residual mark-to-market of the option inventory and hedge book.",
            "- **Delta hedge**: underlying trade used to reduce net delta after option fills.",
            "",
            "## Output files",
            f"- Summary JSON: `{summary['output_files']['summary']}`",
            f"- Fills CSV: `{summary['output_files']['fills']}`",
            f"- Checkpoints CSV: `{summary['output_files']['checkpoints']}`",
            f"- PnL timeseries CSV: `{summary['output_files']['pnl_timeseries']}`",
            f"- Final positions CSV: `{summary['output_files']['positions_final']}`",
            f"- Report Markdown: `{summary['output_files']['report']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def format_artifact_paths(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Artifacts",
            f"- Report: {summary['output_files']['report']}",
            f"- Summary JSON: {summary['output_files']['summary']}",
            f"- Fills CSV: {summary['output_files']['fills']}",
            f"- Checkpoints CSV: {summary['output_files']['checkpoints']}",
            f"- PnL timeseries CSV: {summary['output_files']['pnl_timeseries']}",
            f"- Final positions CSV: {summary['output_files']['positions_final']}",
            f"- PnL chart: {summary['output_files']['pnl_over_time_plot']}",
        ]
    )


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
        ax.set_title(title)
        ax.set_xlabel("Step")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        if len(series) > 1:
            ax.legend(loc="best")
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_histogram(self, path: Path, values: list[float], title: str, xlabel: str) -> None:
        fig, ax = plt.subplots(figsize=(8.5, 4.8))
        data = values or [0.0]
        ax.hist(data, bins=min(20, max(5, len(data))), color="tab:blue", alpha=0.8, edgecolor="white")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Count")
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
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=20)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

    def _save_markout_comparison(self, path: Path) -> None:
        labels = ["toxic", "non-toxic"]
        values = [
            self._average(self.toxic_markout_sum, self.toxic_markout_count),
            self._average(self.non_toxic_markout_sum, self.non_toxic_markout_count),
        ]
        colors = ["tab:red", "tab:green"]
        fig, ax = plt.subplots(figsize=(7.5, 4.8))
        ax.bar(labels, values, color=colors, alpha=0.85)
        ax.axhline(0.0, color="black", linewidth=1)
        ax.set_title("Average signed markout by flow type")
        ax.set_ylabel("Signed markout")
        ax.grid(True, axis="y", alpha=0.25)
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

        paths = {
            "pnl_over_time_plot": out_dir / "pnl_over_time.png",
            "realized_vs_unrealized_plot": out_dir / "realized_vs_unrealized.png",
            "spot_path_plot": out_dir / "spot_path.png",
            "inventory_over_time_plot": out_dir / "inventory_over_time.png",
            "net_delta_over_time_plot": out_dir / "net_delta_over_time.png",
            "markout_distribution_plot": out_dir / "markout_distribution.png",
            "toxic_vs_nontoxic_plot": out_dir / "toxic_vs_nontoxic_markout.png",
            "top_traded_contracts_plot": out_dir / "top_traded_contracts.png",
        }

        self._save_line_plot(
            paths["pnl_over_time_plot"],
            steps,
            [("Ending PnL", ending_pnl, "tab:green")],
            ylabel="PnL",
            title=f"Ending PnL over time ({self.cfg.scenario_name})",
        )
        self._save_line_plot(
            paths["realized_vs_unrealized_plot"],
            steps,
            [
                ("Realized PnL", realized_pnl, "tab:blue"),
                ("Unrealized PnL", unrealized_pnl, "tab:orange"),
            ],
            ylabel="PnL",
            title="Realized vs unrealized PnL",
        )
        self._save_line_plot(
            paths["spot_path_plot"],
            steps,
            [("Underlying spot", spot, "tab:brown")],
            ylabel="Spot",
            title="Underlying spot path",
        )
        self._save_line_plot(
            paths["inventory_over_time_plot"],
            steps,
            [("Abs option inventory", inventory, "tab:purple")],
            ylabel="Contracts",
            title="Inventory over time",
        )
        self._save_line_plot(
            paths["net_delta_over_time_plot"],
            steps,
            [("Net delta", net_delta, "tab:red")],
            ylabel="Delta",
            title="Net delta over time",
        )
        self._save_histogram(
            paths["markout_distribution_plot"],
            markouts,
            title=f"Signed markout distribution ({markout_horizon_label(self.cfg.markout_horizon_steps)})",
            xlabel="Signed markout",
        )
        self._save_markout_comparison(paths["toxic_vs_nontoxic_plot"])
        self._save_bar_chart(
            paths["top_traded_contracts_plot"],
            [item[0] for item in top_contracts],
            [float(item[1]) for item in top_contracts],
            title="Top traded contracts by fill count",
            ylabel="Fills",
            color="tab:cyan",
        )

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
                "Signed markout compares fill price with future option fair value at a fixed horizon. "
                "Positive is good for the dealer; negative is adverse selection."
            ),
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
        }
        return summary

    def run(
        self,
        out_dir: Path,
        verbose: bool = False,
        progress_every: int = 25,
        log_mode: str = "compact",
    ) -> dict[str, Any]:
        rng = random.Random(self.cfg.seed)
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

        output_files: dict[str, str] = {
            "summary": str(out_dir / "summary.json"),
            "fills": str(out_dir / "fills.csv"),
            "checkpoints": str(out_dir / "checkpoints.csv"),
            "pnl_timeseries": str(out_dir / "pnl_timeseries.csv"),
            "positions_final": str(out_dir / "positions_final.csv"),
            "report": str(out_dir / "demo_report.md"),
        }
        output_files.update(self._write_plots(out_dir))
        summary["output_files"] = output_files

        positions_rows = self._build_positions_rows(spot, self.cfg.steps)
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
            "quantity",
            "mark_price",
            "mark_value",
            "net_delta_contribution",
            "net_vega_contribution",
        ]
        self._write_csv(Path(output_files["fills"]), self.trade_rows, fieldnames=fills_fields)
        self._write_csv(Path(output_files["checkpoints"]), self.checkpoint_rows, fieldnames=checkpoint_fields)
        self._write_csv(Path(output_files["pnl_timeseries"]), self.path_rows, fieldnames=pnl_fields)
        self._write_csv(Path(output_files["positions_final"]), positions_rows, fieldnames=positions_fields)
        Path(output_files["report"]).write_text(format_demo_report(summary), encoding="utf-8")
        with Path(output_files["summary"]).open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)

        return summary
