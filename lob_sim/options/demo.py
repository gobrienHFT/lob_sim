from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import exp, log, sqrt
from pathlib import Path
import csv
import json
import random

import matplotlib.pyplot as plt

from .black_scholes import OptionContract, OptionGreeks, option_metrics
from .surface import SimpleVolSurface


@dataclass(frozen=True)
class OptionsMMConfig:
    steps: int = 450
    seed: int = 7
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
        self.hedge_count = 0
        self.trade_count = 0
        self.path_rows: list[dict[str, float]] = []
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
        reservation = (
            (risk.delta * greeks.delta * self.cfg.delta_reservation_mult)
            + (risk.vega * greeks.vega * self.cfg.vega_reservation_mult)
        )
        half_spread = max(
            self.cfg.min_half_spread,
            self.cfg.base_half_spread
            + (realized_vol * self.cfg.realized_vol_spread_mult)
            + (abs(risk.gamma) * self.cfg.gamma_spread_mult),
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

    def _hedge(self, spot: float, step: int) -> tuple[float, PortfolioRisk]:
        risk = self._portfolio_risk(spot, step)
        hedge_qty = 0.0
        if abs(risk.delta) > self.cfg.hedge_threshold_delta:
            hedge_qty = -float(round(risk.delta))
            if hedge_qty != 0.0:
                slippage = self.cfg.hedge_slippage_bps / 10000.0
                hedge_price = spot * (1.0 + slippage if hedge_qty > 0 else 1.0 - slippage)
                self.stock_position += hedge_qty
                self.cash -= hedge_qty * hedge_price
                self.hedge_costs += abs(hedge_qty) * spot * slippage
                self.hedge_count += 1
                risk = self._portfolio_risk(spot, step)
        return hedge_qty, risk

    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        if not rows:
            return
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _write_plot(self, path: Path) -> None:
        steps = [int(row["step"]) for row in self.path_rows]
        spots = [float(row["spot"]) for row in self.path_rows]
        pnl = [float(row["total_pnl"]) for row in self.path_rows]
        delta = [float(row["portfolio_delta"]) for row in self.path_rows]
        gamma = [float(row["portfolio_gamma"]) for row in self.path_rows]
        vega = [float(row["portfolio_vega"]) for row in self.path_rows]

        fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
        axes[0].plot(steps, spots, label="Underlying spot", color="tab:blue")
        axes[0].set_ylabel("Spot")
        ax0b = axes[0].twinx()
        ax0b.plot(steps, pnl, label="Total PnL", color="tab:green")
        ax0b.set_ylabel("PnL")
        axes[0].set_title("Options market-maker case study")

        axes[1].plot(steps, delta, color="tab:orange", label="Delta")
        axes[1].axhline(self.cfg.hedge_threshold_delta, color="tab:red", linestyle="--", linewidth=1)
        axes[1].axhline(-self.cfg.hedge_threshold_delta, color="tab:red", linestyle="--", linewidth=1)
        axes[1].set_ylabel("Delta")

        axes[2].plot(steps, gamma, color="tab:purple", label="Gamma")
        ax2b = axes[2].twinx()
        ax2b.plot(steps, vega, color="tab:brown", label="Vega")
        axes[2].set_ylabel("Gamma")
        ax2b.set_ylabel("Vega")
        axes[2].set_xlabel("Step")

        plt.tight_layout()
        plt.savefig(path)
        plt.close(fig)

    def run(
        self,
        out_dir: Path,
        verbose: bool = False,
        progress_every: int = 25,
    ) -> dict:
        rng = random.Random(self.cfg.seed)
        out_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(
                f"[options] starting case study steps={self.cfg.steps} contracts={len(self.contracts)} "
                f"seed={self.cfg.seed} out_dir={out_dir}",
                flush=True,
            )

        spot = self.cfg.spot0
        equity_peak = 0.0
        max_drawdown = 0.0
        abs_delta_sum = 0.0
        abs_gamma_sum = 0.0
        abs_vega_sum = 0.0
        max_abs_delta = 0.0

        for step in range(self.cfg.steps):
            risk_before = self._portfolio_risk(spot, step)
            hedge_qty = 0.0
            directional_edge = 0.0

            if rng.random() < self.cfg.customer_arrival_prob:
                contract = self._select_contract(spot, step, rng)
                qty = rng.randint(self.cfg.min_trade_size, self.cfg.max_trade_size)
                customer_side = "buy" if rng.random() < 0.5 else "sell"
                toxic = rng.random() < self.cfg.toxic_flow_prob
                quote = self._quote(spot, step, contract, risk_before)
                mm_position_change = -qty if customer_side == "buy" else qty
                fill_price = quote.ask if customer_side == "buy" else quote.bid
                self.option_positions[contract.symbol] += mm_position_change
                self.cash -= mm_position_change * fill_price * self.cfg.contract_size
                self.trade_count += 1

                spread_edge = (
                    fill_price - quote.fair_value
                    if customer_side == "buy"
                    else quote.fair_value - fill_price
                )
                self.spread_capture_pnl += spread_edge * qty * self.cfg.contract_size
                if toxic:
                    directional_edge = self._economic_direction(customer_side, quote.greeks)

                hedge_qty, risk_after_trade = self._hedge(spot, step)
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
                        "qty_contracts": qty,
                        "toxic_flow": toxic,
                        "spot_before": round(spot, 6),
                        "spot_after": round(next_spot, 6),
                        "fair_value": round(quote.fair_value, 6),
                        "bid": round(quote.bid, 6),
                        "ask": round(quote.ask, 6),
                        "fill_price": round(fill_price, 6),
                        "implied_vol": round(quote.implied_vol, 6),
                        "reservation": round(quote.reservation, 6),
                        "half_spread": round(quote.half_spread, 6),
                        "portfolio_delta_before": round(risk_before.delta, 6),
                        "portfolio_delta_after_trade": round(risk_after_trade.delta, 6),
                        "hedge_qty": round(hedge_qty, 6),
                        "mm_markout_1_step": round(mm_markout, 6),
                    }
                )
                if verbose:
                    print(
                        f"[options] step={step + 1} trade {customer_side} {qty}x {contract.symbol} "
                        f"toxic={toxic} fair={quote.fair_value:.3f} bid={quote.bid:.3f} ask={quote.ask:.3f} "
                        f"fill={fill_price:.3f} hedge={hedge_qty:.0f}",
                        flush=True,
                    )
            else:
                next_spot = self._evolve_spot(spot, rng, directional_edge)

            if next_spot > 0.0 and spot > 0.0:
                self._returns.append(log(next_spot / spot))
            spot = next_spot
            risk_now = self._portfolio_risk(spot, step + 1)
            equity = self._mark_to_market(spot, step + 1)
            equity_peak = max(equity_peak, equity)
            max_drawdown = max(max_drawdown, equity_peak - equity)
            abs_delta_sum += abs(risk_now.delta)
            abs_gamma_sum += abs(risk_now.gamma)
            abs_vega_sum += abs(risk_now.vega)
            max_abs_delta = max(max_abs_delta, abs(risk_now.delta))
            self.path_rows.append(
                {
                    "step": step,
                    "spot": round(spot, 6),
                    "stock_position": round(self.stock_position, 6),
                    "option_value": round(risk_now.option_value, 6),
                    "portfolio_delta": round(risk_now.delta, 6),
                    "portfolio_gamma": round(risk_now.gamma, 6),
                    "portfolio_vega": round(risk_now.vega, 6),
                    "cash": round(self.cash, 6),
                    "total_pnl": round(equity, 6),
                }
            )
            if verbose and progress_every > 0 and ((step + 1) % progress_every == 0 or step + 1 == self.cfg.steps):
                print(
                    f"[options] step={step + 1}/{self.cfg.steps} spot={spot:.3f} pnl={equity:.2f} "
                    f"delta={risk_now.delta:.1f} gamma={risk_now.gamma:.3f} vega={risk_now.vega:.1f} "
                    f"trades={self.trade_count} hedges={self.hedge_count}",
                    flush=True,
                )

        total_pnl = self._mark_to_market(spot, self.cfg.steps)
        adverse_markout_rate = (
            float(self.adverse_markout_count) / float(self.markout_count)
            if self.markout_count
            else 0.0
        )
        avg_markout = self.markout_sum / self.markout_count if self.markout_count else 0.0
        residual_inventory_pnl = total_pnl - self.spread_capture_pnl + self.hedge_costs
        active_positions = {
            symbol: position
            for symbol, position in self.option_positions.items()
            if position != 0
        }
        largest_position = max((abs(position) for position in active_positions.values()), default=0)

        summary = {
            "spot_final": round(spot, 6),
            "trade_count": self.trade_count,
            "hedge_count": self.hedge_count,
            "total_pnl": round(total_pnl, 6),
            "spread_capture_pnl": round(self.spread_capture_pnl, 6),
            "hedge_costs": round(self.hedge_costs, 6),
            "residual_inventory_pnl": round(residual_inventory_pnl, 6),
            "avg_mm_markout_1_step": round(avg_markout, 6),
            "adverse_markout_rate_1_step": round(adverse_markout_rate, 6),
            "avg_abs_delta": round(abs_delta_sum / max(self.cfg.steps, 1), 6),
            "avg_abs_gamma": round(abs_gamma_sum / max(self.cfg.steps, 1), 6),
            "avg_abs_vega": round(abs_vega_sum / max(self.cfg.steps, 1), 6),
            "max_abs_delta": round(max_abs_delta, 6),
            "max_drawdown": round(max_drawdown, 6),
            "final_stock_position": round(self.stock_position, 6),
            "largest_option_position_contracts": largest_position,
            "active_option_positions": active_positions,
        }

        summary_path = out_dir / "options_mm_summary.json"
        path_path = out_dir / "options_mm_path.csv"
        trades_path = out_dir / "options_mm_trades.csv"
        plot_path = out_dir / "options_mm_report.png"
        summary["output_files"] = {
            "summary": str(summary_path),
            "path": str(path_path),
            "trades": str(trades_path),
            "plot": str(plot_path),
        }

        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        self._write_csv(path_path, self.path_rows)
        self._write_csv(trades_path, self.trade_rows)
        self._write_plot(plot_path)
        if verbose:
            print(
                f"[options] completed total_pnl={total_pnl:.2f} trades={self.trade_count} hedges={self.hedge_count}",
                flush=True,
            )
        return summary
