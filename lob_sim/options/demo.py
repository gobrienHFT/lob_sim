from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from math import exp, log, sqrt
from pathlib import Path
from typing import Any
import csv
import json
import random
import shutil

import matplotlib.pyplot as plt

from .black_scholes import OptionContract, OptionGreeks, option_metrics
from .surface import SimpleVolSurface
from ..util import write_summary_csv


DEFAULT_OPTIONS_SCENARIO = "calm_market"
OPTIONS_SCENARIOS: dict[str, dict[str, Any]] = {
    "calm_market": {
        "description": "Lower-volatility quoting with modest toxic flow and lighter hedge pressure.",
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


def _summary_interpretation(summary: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if summary["total_pnl"] >= 0:
        notes.append("Quoted edge held up over the run after hedge costs and inventory marking.")
    else:
        notes.append("Inventory moves and adverse selection outweighed quoted edge in this run.")

    if summary["adverse_fill_rate_1_step"] >= 0.5:
        notes.append("Adverse selection was meaningful; toxic or underpriced flow hit the book too often.")
    else:
        notes.append("Post-trade markouts stayed reasonably contained.")

    if abs(summary["final_delta_exposure"]) > 50.0:
        notes.append("The book finished with material delta still warehoused.")
    else:
        notes.append("The book finished close to delta-flat after hedging.")

    if summary["max_abs_contract_position"] >= 8:
        notes.append("Inventory skew mattered because single-contract positions became meaningfully large.")
    else:
        notes.append("Inventory stayed controlled at the single-contract level.")
    return notes


def _format_metric_rows(rows: list[tuple[str, str]]) -> list[str]:
    width = max((len(label) for label, _ in rows), default=0)
    return [f"{label:<{width}} : {value}" for label, value in rows]


def format_terminal_wrapup(summary: dict[str, Any]) -> str:
    headline = _format_metric_rows(
        [
            ("Scenario", str(summary["scenario"])),
            ("Total PnL", f"{summary['total_pnl']:.2f}"),
            ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
            ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
            ("Average half-spread", f"{summary['avg_half_spread']:.4f}"),
        ]
    )
    inventory = _format_metric_rows(
        [
            ("Hedge trades", str(summary["hedge_trade_count"])),
            ("Toxic fills", str(summary["toxic_fill_count"])),
            ("Adverse fill rate", f"{summary['adverse_fill_rate_1_step']:.1%}"),
            ("Max contract position", str(summary["max_abs_contract_position"])),
            ("Final delta exposure", f"{summary['final_delta_exposure']:.2f}"),
        ]
    )
    lines = [
        "",
        "==============================================================",
        "OPTIONS MM RUN WRAP-UP",
        "==============================================================",
        *headline,
        "",
        "Inventory and hedging",
        *inventory,
        "",
        "Interpretation",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    return "\n".join(lines)


def format_interview_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Options MM interview mode",
        f"Scenario: {summary['scenario']} - {summary['scenario_description']}",
        "",
        *_format_metric_rows(
            [
                ("Total PnL", f"{summary['total_pnl']:.2f}"),
                ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
                ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
                ("Average half-spread", f"{summary['avg_half_spread']:.4f}"),
                ("Hedge trades", str(summary["hedge_trade_count"])),
                ("Toxic fills", str(summary["toxic_fill_count"])),
                ("Adverse fill rate", f"{summary['adverse_fill_rate_1_step']:.1%}"),
                ("Max contract position", str(summary["max_abs_contract_position"])),
                ("Final delta exposure", f"{summary['final_delta_exposure']:.2f}"),
            ]
        ),
        "",
        "Interpretation:",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    return "\n".join(lines)


def format_case_study_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Options market making case study",
        f"Scenario: {summary['scenario']}",
        f"Description: {summary['scenario_description']}",
        "",
        "Headline metrics",
        *_format_metric_rows(
            [
                ("Total PnL", f"{summary['total_pnl']:.2f}"),
                ("Realized PnL", f"{summary['realized_pnl']:.2f}"),
                ("Unrealized PnL", f"{summary['unrealized_pnl']:.2f}"),
                ("Spread capture PnL", f"{summary['spread_capture_pnl']:.2f}"),
                ("Hedge costs", f"{summary['hedge_costs']:.2f}"),
                ("Average half-spread", f"{summary['avg_half_spread']:.4f}"),
                ("Average full spread", f"{summary['avg_full_spread']:.4f}"),
            ]
        ),
        "",
        "Inventory and hedging",
        *_format_metric_rows(
            [
                ("Hedge trades", str(summary["hedge_trade_count"])),
                ("Toxic fills", str(summary["toxic_fill_count"])),
                ("Adverse fills (1-step)", str(summary["adverse_fill_count_1_step"])),
                ("Adverse fill rate", f"{summary['adverse_fill_rate_1_step']:.1%}"),
                ("Max contract position", str(summary["max_abs_contract_position"])),
                ("Max stock hedge position", f"{summary['max_abs_stock_position']:.2f}"),
                ("Final delta exposure", f"{summary['final_delta_exposure']:.2f}"),
            ]
        ),
        "",
        "Interpretation",
    ]
    lines.extend(f"- {note}" for note in _summary_interpretation(summary))
    lines.extend(
        [
            "",
            "Best files to open next:",
            f"- {summary['output_files']['latest_summary']}",
            f"- {summary['output_files']['latest_trades']}",
            f"- {summary['output_files']['latest_pnl']}",
            f"- {summary['output_files']['latest_report']}",
        ]
    )
    return "\n".join(lines) + "\n"


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
        self.toxic_fill_count = 0
        self.hedge_count = 0
        self.trade_count = 0
        self.half_spread_sum = 0.0
        self.full_spread_sum = 0.0
        self.max_abs_contract_position_seen = 0
        self.max_abs_stock_position_seen = 0.0
        self.path_rows: list[dict[str, float | int]] = []
        self.trade_rows: list[dict[str, float | int | str | bool]] = []
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

    def _write_plot(self, path: Path) -> None:
        steps = [int(row["step"]) for row in self.path_rows]
        total_pnl = [float(row["total_pnl"]) for row in self.path_rows]
        realized_pnl = [float(row["realized_pnl"]) for row in self.path_rows]
        delta = [float(row["portfolio_delta"]) for row in self.path_rows]
        inventory = [float(row["inventory_abs_contracts"]) for row in self.path_rows]
        spot = [float(row["spot"]) for row in self.path_rows]

        fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
        axes[0].plot(steps, total_pnl, color="tab:green", label="Total PnL")
        axes[0].plot(steps, realized_pnl, color="tab:blue", linestyle="--", label="Realized PnL")
        axes[0].set_ylabel("PnL")
        axes[0].set_title(f"Options MM case study: {self.cfg.scenario_name}")
        axes[0].legend(loc="upper left")

        axes[1].plot(steps, delta, color="tab:orange", label="Portfolio delta")
        axes[1].axhline(self.cfg.hedge_threshold_delta, color="tab:red", linestyle="--", linewidth=1)
        axes[1].axhline(-self.cfg.hedge_threshold_delta, color="tab:red", linestyle="--", linewidth=1)
        axes[1].set_ylabel("Delta")
        axes[1].legend(loc="upper left")

        axes[2].plot(steps, inventory, color="tab:purple", label="Abs option inventory")
        ax2b = axes[2].twinx()
        ax2b.plot(steps, spot, color="tab:brown", alpha=0.6, label="Spot")
        axes[2].set_ylabel("Contracts")
        ax2b.set_ylabel("Spot")
        axes[2].set_xlabel("Step")

        plt.tight_layout()
        plt.savefig(path)
        plt.close(fig)

    def _build_latest_trade_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.trade_rows:
            rows.append(
                {
                    "step": row["step"],
                    "contract": row["contract"],
                    "customer_side": row["customer_side"],
                    "mm_side": row["mm_side"],
                    "qty_contracts": row["qty_contracts"],
                    "option_position_after": row["option_position_after"],
                    "toxic_flow": row["toxic_flow"],
                    "fair_value": row["fair_value"],
                    "fill_price": row["fill_price"],
                    "half_spread": row["half_spread"],
                    "spread_capture_pnl": row["spread_capture_pnl"],
                    "portfolio_delta_before": row["portfolio_delta_before"],
                    "hedge_qty": row["hedge_qty"],
                    "hedge_cost": row["hedge_cost"],
                    "mm_markout_1_step": row["mm_markout_1_step"],
                    "portfolio_delta_after_hedge": row["portfolio_delta_after_hedge"],
                }
            )
        return rows

    def _build_latest_pnl_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.path_rows:
            rows.append(
                {
                    "step": row["step"],
                    "spot": row["spot"],
                    "realized_pnl": row["realized_pnl"],
                    "unrealized_pnl": row["unrealized_pnl"],
                    "total_pnl": row["total_pnl"],
                    "stock_position": row["stock_position"],
                    "portfolio_delta": row["portfolio_delta"],
                    "inventory_abs_contracts": row["inventory_abs_contracts"],
                    "max_abs_contract_position": row["max_abs_contract_position"],
                    "hedge_costs": row["hedge_costs"],
                    "trade_count": row["trade_count"],
                    "hedge_count": row["hedge_count"],
                }
            )
        return rows

    def _write_walkthrough(self, path: Path, summary: dict[str, Any]) -> None:
        output_files = summary["output_files"]
        config = asdict(self.cfg)
        lines = [
            "# Options MM case study walkthrough",
            "",
            f"Scenario: `{summary['scenario']}`",
            summary["scenario_description"],
            "",
            "## What this run demonstrates",
            "- Black-Scholes fair value on a skewed vol surface.",
            "- Reservation pricing driven by current delta and vega inventory.",
            "- Spread widening when realized vol and gamma risk increase.",
            "- Toxic flow and post-trade markout as an adverse-selection diagnostic.",
            "- Delta hedging in the underlying once risk crosses a threshold.",
            "",
            "## Core quote logic",
            "`bid = fair_value - half_spread - reservation`",
            "`ask = fair_value + half_spread - reservation`",
            "",
            "## Best files to open",
            f"- `latest_summary.txt`: {output_files['latest_summary']}",
            f"- `latest_trades.csv`: {output_files['latest_trades']}",
            f"- `latest_pnl.csv`: {output_files['latest_pnl']}",
            f"- `latest_report.png`: {output_files['latest_report']}",
            "",
            "## Current run configuration",
        ]
        for key, value in config.items():
            lines.append(f"- `{key}`: `{value}`")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_latest_outputs(self, out_dir: Path, summary: dict[str, Any], plot_path: Path) -> dict[str, str]:
        latest_summary_path = out_dir / "latest_summary.txt"
        latest_trades_path = out_dir / "latest_trades.csv"
        latest_pnl_path = out_dir / "latest_pnl.csv"
        latest_report_path = out_dir / "latest_report.png"
        latest_outputs = {
            "latest_summary": str(latest_summary_path),
            "latest_trades": str(latest_trades_path),
            "latest_pnl": str(latest_pnl_path),
            "latest_report": str(latest_report_path),
        }

        latest_trades = self._build_latest_trade_rows()
        latest_pnl = self._build_latest_pnl_rows()
        summary_for_text = dict(summary)
        summary_for_text["output_files"] = dict(summary["output_files"])
        summary_for_text["output_files"].update(latest_outputs)
        latest_summary_path.write_text(format_case_study_summary(summary_for_text), encoding="utf-8")
        self._write_csv(
            latest_trades_path,
            latest_trades,
            fieldnames=[
                "step",
                "contract",
                "customer_side",
                "mm_side",
                "qty_contracts",
                "option_position_after",
                "toxic_flow",
                "fair_value",
                "fill_price",
                "half_spread",
                "spread_capture_pnl",
                "portfolio_delta_before",
                "hedge_qty",
                "hedge_cost",
                "mm_markout_1_step",
                "portfolio_delta_after_hedge",
            ],
        )
        self._write_csv(
            latest_pnl_path,
            latest_pnl,
            fieldnames=[
                "step",
                "spot",
                "realized_pnl",
                "unrealized_pnl",
                "total_pnl",
                "stock_position",
                "portfolio_delta",
                "inventory_abs_contracts",
                "max_abs_contract_position",
                "hedge_costs",
                "trade_count",
                "hedge_count",
            ],
        )
        shutil.copyfile(plot_path, latest_report_path)
        return latest_outputs

    def run(
        self,
        out_dir: Path,
        verbose: bool = False,
        progress_every: int = 25,
    ) -> dict[str, Any]:
        rng = random.Random(self.cfg.seed)
        out_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(
                f"[options] scenario={self.cfg.scenario_name} steps={self.cfg.steps} seed={self.cfg.seed} "
                f"out_dir={out_dir}",
                flush=True,
            )
            print(f"[options] {self.cfg.scenario_description}", flush=True)
            print(
                "[options] quote = fair +/- spread - reservation; "
                "reservation reacts to delta/vega inventory; spread reacts to realized vol and gamma",
                flush=True,
            )
            print(
                f"[options] hedge when |delta| > {self.cfg.hedge_threshold_delta:.1f}; "
                "watch toxic fills, markouts, and max position growth",
                flush=True,
            )

        spot = self.cfg.spot0
        equity_peak = 0.0
        max_drawdown = 0.0
        abs_delta_sum = 0.0
        abs_gamma_sum = 0.0
        abs_vega_sum = 0.0

        for step in range(self.cfg.steps):
            risk_before = self._portfolio_risk(spot, step)
            directional_edge = 0.0

            if rng.random() < self.cfg.customer_arrival_prob:
                contract = self._select_contract(spot, step, rng)
                qty = rng.randint(self.cfg.min_trade_size, self.cfg.max_trade_size)
                customer_side = "buy" if rng.random() < 0.5 else "sell"
                toxic = rng.random() < self.cfg.toxic_flow_prob
                quote = self._quote(spot, step, contract, risk_before)
                option_position_before = self.option_positions[contract.symbol]
                stock_position_before = self.stock_position
                mm_position_change = -qty if customer_side == "buy" else qty
                fill_price = quote.ask if customer_side == "buy" else quote.bid
                self.option_positions[contract.symbol] += mm_position_change
                self.cash -= mm_position_change * fill_price * self.cfg.contract_size
                self.trade_count += 1
                self.half_spread_sum += quote.half_spread
                self.full_spread_sum += 2.0 * quote.half_spread
                if toxic:
                    self.toxic_fill_count += 1
                self._record_inventory_extremes()
                risk_after_trade = self._portfolio_risk(spot, step)

                spread_edge = (
                    fill_price - quote.fair_value
                    if customer_side == "buy"
                    else quote.fair_value - fill_price
                )
                spread_capture_pnl = spread_edge * qty * self.cfg.contract_size
                self.spread_capture_pnl += spread_capture_pnl
                if toxic:
                    directional_edge = self._economic_direction(customer_side, quote.greeks)

                hedge_qty, hedge_cost, risk_after_hedge = self._hedge(spot, step, risk_after_trade)
                self._record_inventory_extremes()
                next_spot = self._evolve_spot(spot, rng, directional_edge)
                next_remaining = self._remaining_years(contract, step + 1)
                next_vol = self.surface.implied_vol(next_spot, contract, next_remaining)
                next_greeks = option_metrics(
                    spot=next_spot,
                    strike=contract.strike,
                    time_to_expiry=next_remaining,
                    rate=self.cfg.rate,
                    vol=next_vol,
                    option_type=contract.option_type,
                )
                mm_markout = (
                    (fill_price - next_greeks.price)
                    if customer_side == "buy"
                    else (next_greeks.price - fill_price)
                ) * qty * self.cfg.contract_size
                self.markout_sum += mm_markout
                self.markout_count += 1
                if mm_markout < 0.0:
                    self.adverse_markout_count += 1

                self.trade_rows.append(
                    {
                        "step": step,
                        "contract": contract.symbol,
                        "option_type": contract.option_type,
                        "strike": contract.strike,
                        "expiry_days": round(next_remaining * 252.0, 2),
                        "customer_side": customer_side,
                        "mm_side": "sell" if customer_side == "buy" else "buy",
                        "qty_contracts": qty,
                        "mm_position_change_contracts": mm_position_change,
                        "option_position_before": option_position_before,
                        "option_position_after": self.option_positions[contract.symbol],
                        "toxic_flow": toxic,
                        "toxic_flow_direction": (
                            "up" if directional_edge > 0 else "down" if directional_edge < 0 else "flat"
                        ),
                        "spot_before": round(spot, 6),
                        "spot_after": round(next_spot, 6),
                        "fair_value": round(quote.fair_value, 6),
                        "bid": round(quote.bid, 6),
                        "ask": round(quote.ask, 6),
                        "fill_price": round(fill_price, 6),
                        "implied_vol": round(quote.implied_vol, 6),
                        "realized_vol": round(quote.realized_vol, 6),
                        "reservation": round(quote.reservation, 6),
                        "delta_reservation_component": round(quote.delta_reservation_component, 6),
                        "vega_reservation_component": round(quote.vega_reservation_component, 6),
                        "half_spread": round(quote.half_spread, 6),
                        "base_half_spread": round(quote.base_half_spread, 6),
                        "vol_half_spread_component": round(quote.vol_half_spread_component, 6),
                        "gamma_half_spread_component": round(quote.gamma_half_spread_component, 6),
                        "spread_edge_per_contract": round(spread_edge, 6),
                        "spread_capture_pnl": round(spread_capture_pnl, 6),
                        "option_delta": round(quote.greeks.delta, 6),
                        "option_gamma": round(quote.greeks.gamma, 6),
                        "option_vega": round(quote.greeks.vega, 6),
                        "portfolio_delta_before": round(risk_before.delta, 6),
                        "portfolio_gamma_before": round(risk_before.gamma, 6),
                        "portfolio_vega_before": round(risk_before.vega, 6),
                        "portfolio_delta_after_trade": round(risk_after_trade.delta, 6),
                        "portfolio_gamma_after_trade": round(risk_after_trade.gamma, 6),
                        "portfolio_vega_after_trade": round(risk_after_trade.vega, 6),
                        "stock_position_before": round(stock_position_before, 6),
                        "hedge_qty": round(hedge_qty, 6),
                        "hedge_cost": round(hedge_cost, 6),
                        "stock_position_after_hedge": round(self.stock_position, 6),
                        "portfolio_delta_after_hedge": round(risk_after_hedge.delta, 6),
                        "mm_markout_1_step": round(mm_markout, 6),
                        "mm_markout_1_step_per_contract": round(mm_markout / max(qty, 1), 6),
                    }
                )
                if verbose and (toxic or hedge_qty != 0.0):
                    print(
                        f"[options] event step={step + 1} contract={contract.symbol} side={customer_side} "
                        f"toxic={toxic} hedge={hedge_qty:.0f} markout={mm_markout:.2f}",
                        flush=True,
                    )
            else:
                next_spot = self._evolve_spot(spot, rng, directional_edge)

            if next_spot > 0.0 and spot > 0.0:
                self._returns.append(log(next_spot / spot))
            spot = next_spot
            risk_now = self._portfolio_risk(spot, step + 1)
            equity = self._mark_to_market(spot, step + 1)
            realized_pnl = self.spread_capture_pnl - self.hedge_costs
            unrealized_pnl = equity - realized_pnl
            equity_peak = max(equity_peak, equity)
            max_drawdown = max(max_drawdown, equity_peak - equity)
            abs_delta_sum += abs(risk_now.delta)
            abs_gamma_sum += abs(risk_now.gamma)
            abs_vega_sum += abs(risk_now.vega)
            current_max_contract_position = self._max_abs_option_position()
            inventory_abs_contracts = self._abs_option_inventory_contracts()
            self._record_inventory_extremes()
            self.path_rows.append(
                {
                    "step": step,
                    "spot": round(spot, 6),
                    "stock_position": round(self.stock_position, 6),
                    "option_value": round(risk_now.option_value, 6),
                    "portfolio_delta": round(risk_now.delta, 6),
                    "portfolio_gamma": round(risk_now.gamma, 6),
                    "portfolio_vega": round(risk_now.vega, 6),
                    "inventory_abs_contracts": inventory_abs_contracts,
                    "max_abs_contract_position": current_max_contract_position,
                    "realized_vol": round(self._realized_vol(), 6),
                    "cash": round(self.cash, 6),
                    "realized_pnl": round(realized_pnl, 6),
                    "unrealized_pnl": round(unrealized_pnl, 6),
                    "total_pnl": round(equity, 6),
                    "trade_count": self.trade_count,
                    "hedge_count": self.hedge_count,
                    "spread_capture_pnl": round(self.spread_capture_pnl, 6),
                    "hedge_costs": round(self.hedge_costs, 6),
                }
            )
            if verbose and progress_every > 0 and ((step + 1) % progress_every == 0 or step + 1 == self.cfg.steps):
                print(
                    f"[options] checkpoint {step + 1:>4}/{self.cfg.steps:<4} | "
                    f"pnl={equity:>8.2f} | realized={realized_pnl:>8.2f} | "
                    f"unrealized={unrealized_pnl:>8.2f} | delta={risk_now.delta:>7.1f} | "
                    f"inv={inventory_abs_contracts:>3} | hedges={self.hedge_count:>2}",
                    flush=True,
                )

        final_risk = self._portfolio_risk(spot, self.cfg.steps)
        total_pnl = self._mark_to_market(spot, self.cfg.steps)
        realized_pnl = self.spread_capture_pnl - self.hedge_costs
        unrealized_pnl = total_pnl - realized_pnl
        avg_markout = self.markout_sum / self.markout_count if self.markout_count else 0.0
        adverse_markout_rate = (
            float(self.adverse_markout_count) / float(self.markout_count)
            if self.markout_count
            else 0.0
        )
        avg_half_spread = self.half_spread_sum / self.trade_count if self.trade_count else 0.0
        avg_full_spread = self.full_spread_sum / self.trade_count if self.trade_count else 0.0
        active_positions = {
            symbol: position
            for symbol, position in self.option_positions.items()
            if position != 0
        }

        summary: dict[str, Any] = {
            "scenario": self.cfg.scenario_name,
            "scenario_description": self.cfg.scenario_description,
            "spot_final": round(spot, 6),
            "trade_count": self.trade_count,
            "hedge_count": self.hedge_count,
            "hedge_trade_count": self.hedge_count,
            "total_pnl": round(total_pnl, 6),
            "realized_pnl": round(realized_pnl, 6),
            "unrealized_pnl": round(unrealized_pnl, 6),
            "spread_capture_pnl": round(self.spread_capture_pnl, 6),
            "hedge_costs": round(self.hedge_costs, 6),
            "avg_mm_markout_1_step": round(avg_markout, 6),
            "adverse_fill_count_1_step": self.adverse_markout_count,
            "adverse_fill_rate_1_step": round(adverse_markout_rate, 6),
            "toxic_fill_count": self.toxic_fill_count,
            "toxic_fill_rate": round(float(self.toxic_fill_count) / float(self.trade_count), 6)
            if self.trade_count
            else 0.0,
            "avg_half_spread": round(avg_half_spread, 6),
            "avg_full_spread": round(avg_full_spread, 6),
            "avg_abs_delta": round(abs_delta_sum / max(self.cfg.steps, 1), 6),
            "avg_abs_gamma": round(abs_gamma_sum / max(self.cfg.steps, 1), 6),
            "avg_abs_vega": round(abs_vega_sum / max(self.cfg.steps, 1), 6),
            "max_drawdown": round(max_drawdown, 6),
            "max_abs_contract_position": self.max_abs_contract_position_seen,
            "max_abs_stock_position": round(self.max_abs_stock_position_seen, 6),
            "final_stock_position": round(self.stock_position, 6),
            "final_delta_exposure": round(final_risk.delta, 6),
            "final_option_inventory_contracts": self._abs_option_inventory_contracts(),
            "active_option_positions": active_positions,
        }

        summary_path = out_dir / "options_mm_summary.json"
        summary_csv_path = out_dir / "options_mm_summary.csv"
        config_path = out_dir / "options_mm_config.json"
        config_csv_path = out_dir / "options_mm_config.csv"
        path_path = out_dir / "options_mm_path.csv"
        trades_path = out_dir / "options_mm_trades.csv"
        plot_path = out_dir / "options_mm_report.png"
        walkthrough_path = out_dir / "options_mm_walkthrough.md"
        summary["output_files"] = {
            "summary": str(summary_path),
            "summary_csv": str(summary_csv_path),
            "config": str(config_path),
            "config_csv": str(config_csv_path),
            "path": str(path_path),
            "trades": str(trades_path),
            "plot": str(plot_path),
            "walkthrough": str(walkthrough_path),
        }

        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        with config_path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(self.cfg), handle, indent=2)
        write_summary_csv(summary_csv_path, summary)
        write_summary_csv(config_csv_path, asdict(self.cfg))
        self._write_csv(path_path, self.path_rows)
        self._write_csv(trades_path, self.trade_rows)
        self._write_plot(plot_path)

        latest_outputs = self._write_latest_outputs(out_dir, summary, plot_path)
        summary["output_files"].update(latest_outputs)
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        write_summary_csv(summary_csv_path, summary)
        self._write_walkthrough(walkthrough_path, summary)

        if verbose:
            print(format_terminal_wrapup(summary), flush=True)
            print(
                "[options] Clean outputs:",
                flush=True,
            )
            print(
                f"[options]   {summary['output_files']['latest_summary']}",
                flush=True,
            )
            print(
                f"[options]   {summary['output_files']['latest_trades']}",
                flush=True,
            )
            print(
                f"[options]   {summary['output_files']['latest_pnl']}",
                flush=True,
            )
            print(
                f"[options]   {summary['output_files']['latest_report']}",
                flush=True,
            )
        return summary
