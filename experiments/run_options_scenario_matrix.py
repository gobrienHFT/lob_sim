from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from lob_sim.options.demo import OptionsMarketMakerDemo, build_options_config, scenario_card


SCENARIOS = (
    "calm_market",
    "volatile_market",
    "toxic_flow",
    "inventory_stress",
)

METRICS: list[tuple[str, str]] = [
    ("ending_pnl", "Ending PnL"),
    ("realized_pnl", "Realized PnL"),
    ("gross_spread_captured", "Gross spread"),
    ("total_signed_markout", "Signed markout"),
    ("toxic_fill_rate", "Toxic fill rate"),
    ("adverse_fill_rate", "Adverse fill rate"),
    ("hedge_trade_count", "Hedge trades"),
    ("max_inventory_contracts", "Max inventory"),
    ("final_net_delta", "Final net delta"),
]

SCENARIO_COLORS = {
    "calm_market": "tab:blue",
    "volatile_market": "tab:orange",
    "toxic_flow": "tab:red",
    "inventory_stress": "tab:purple",
}


def _run_scenario(scenario: str, steps: int, seed: int, tmp_root: Path) -> dict[str, float | int | str]:
    config = build_options_config(steps=steps, seed=seed, scenario=scenario)
    summary = OptionsMarketMakerDemo(config).run(tmp_root / scenario, progress_every=0, write_artifacts=False)
    return {
        "scenario": scenario,
        "seed": seed,
        "steps": steps,
        "ending_pnl": summary["ending_pnl"],
        "realized_pnl": summary["realized_pnl"],
        "gross_spread_captured": summary["gross_spread_captured"],
        "total_signed_markout": summary["total_signed_markout"],
        "toxic_fill_rate": summary["toxic_fill_rate"],
        "adverse_fill_rate": summary["adverse_fill_rate"],
        "hedge_trade_count": summary["hedge_trade_count"],
        "max_inventory_contracts": summary["max_inventory_contracts"],
        "final_net_delta": summary["final_net_delta"],
    }


def _write_csv(path: Path, rows: list[dict[str, float | int | str]]) -> None:
    fieldnames = [
        "scenario",
        "seed",
        "steps",
        "ending_pnl",
        "realized_pnl",
        "gross_spread_captured",
        "total_signed_markout",
        "toxic_fill_rate",
        "adverse_fill_rate",
        "hedge_trade_count",
        "max_inventory_contracts",
        "final_net_delta",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fmt_value(key: str, value: float | int | str) -> str:
    if key.endswith("_rate"):
        return f"{float(value):.1%}"
    if key in {"hedge_trade_count", "max_inventory_contracts", "seed", "steps"}:
        return str(int(value))
    return f"{float(value):.2f}"


def _scenario_section(row: dict[str, float | int | str]) -> list[str]:
    card = scenario_card(str(row["scenario"]))
    return [
        f"### {row['scenario']}",
        "",
        f"- {card['description']}",
        f"- {card['volatility_regime']}",
        f"- {card['flow_characteristics']}",
        f"- {card['hedging_pressure']}",
        (
            f"- In this run it finished with ending PnL `{float(row['ending_pnl']):.2f}`, signed markout "
            f"`{float(row['total_signed_markout']):.2f}`, toxic fill rate `{float(row['toxic_fill_rate']):.1%}`, "
            f"hedge trades `{int(row['hedge_trade_count'])}`, and max inventory "
            f"`{int(row['max_inventory_contracts'])}` contracts."
        ),
        "",
    ]


def _write_markdown(path: Path, rows: list[dict[str, float | int | str]], steps: int, seed: int) -> None:
    headers = [
        "Scenario",
        "Ending PnL",
        "Realized PnL",
        "Gross spread",
        "Signed markout",
        "Toxic fill rate",
        "Adverse fill rate",
        "Hedge trades",
        "Max inventory",
        "Final net delta",
    ]
    lines = [
        "# Options scenario matrix",
        "",
        (
            f"Same-seed comparison across all current options presets. Each run uses seed `{seed}` and "
            f"`{steps}` steps so the scenario parameters, not a hand-picked path, drive the differences."
        ),
        "",
        "| " + " | ".join(headers) + " |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["scenario"]),
                    _fmt_value("ending_pnl", row["ending_pnl"]),
                    _fmt_value("realized_pnl", row["realized_pnl"]),
                    _fmt_value("gross_spread_captured", row["gross_spread_captured"]),
                    _fmt_value("total_signed_markout", row["total_signed_markout"]),
                    _fmt_value("toxic_fill_rate", row["toxic_fill_rate"]),
                    _fmt_value("adverse_fill_rate", row["adverse_fill_rate"]),
                    _fmt_value("hedge_trade_count", row["hedge_trade_count"]),
                    _fmt_value("max_inventory_contracts", row["max_inventory_contracts"]),
                    _fmt_value("final_net_delta", row["final_net_delta"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Scenario notes",
            "",
        ]
    )
    for row in rows:
        lines.extend(_scenario_section(row))

    lines.extend(
        [
            "## What this proves",
            "",
            "- The options case study is regime-sensitive: the same seed produces meaningfully different PnL, markout, hedging, and inventory outcomes when the scenario parameters change.",
            "- Toxic flow, realized volatility, and hedge thresholds move the outputs in visible ways, which makes the quoting and risk logic falsifiable rather than decorative.",
            "- The demo is not relying on one flattering path; it shows how the same dealer logic behaves across calmer, faster, more toxic, and more inventory-heavy conditions.",
            "",
            "## What this does not prove",
            "",
            "- It does not prove exchange realism for options, because the options side is still a synthetic dealer simulation rather than a venue order-book replay.",
            "- It does not prove statistical robustness across many seeds, long samples, or calibrated market regimes; this is a compact case-study comparison, not a full backtest study.",
            "- It does not prove production readiness. The value here is transparent market-making logic and interpretable outputs, not infrastructure scale.",
            "",
            f"Plot: [scenario_comparison.png]({path.with_name('scenario_comparison.png').name})",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _annotate_bars(ax: plt.Axes, values: list[float], metric_key: str) -> None:
    span = max(abs(value) for value in values) if values else 0.0
    pad = max(span * 0.04, 0.02)
    for idx, value in enumerate(values):
        if metric_key.endswith("_rate"):
            label = f"{value:.0%}"
        elif metric_key in {"hedge_trade_count", "max_inventory_contracts"}:
            label = f"{value:.0f}"
        else:
            label = f"{value:.1f}"
        va = "bottom" if value >= 0 else "top"
        y = value + pad if value >= 0 else value - pad
        ax.text(idx, y, label, ha="center", va=va, fontsize=8)


def _write_plot(path: Path, rows: list[dict[str, float | int | str]]) -> None:
    scenario_names = [str(row["scenario"]) for row in rows]
    colors = [SCENARIO_COLORS[name] for name in scenario_names]
    fig, axes = plt.subplots(3, 3, figsize=(16, 10))

    for ax, (metric_key, label) in zip(axes.flat, METRICS, strict=True):
        values = [float(row[metric_key]) for row in rows]
        ax.bar(scenario_names, values, color=colors, alpha=0.85)
        ax.set_title(label)
        ax.grid(True, axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=20)
        if metric_key.endswith("_rate"):
            ax.set_ylim(0.0, max(max(values) * 1.2, 0.05))
            ax.set_ylabel("Rate")
        elif any(value < 0.0 for value in values):
            ax.axhline(0.0, color="black", linewidth=1)
        _annotate_bars(ax, values, metric_key)

    fig.suptitle("Options scenario comparison", fontsize=16)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.97))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the options demo across all current scenarios with one seed")
    parser.add_argument("--out-dir", default="outputs")
    parser.add_argument("--steps", type=int, default=180)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, float | int | str]] = []
    with tempfile.TemporaryDirectory(prefix="options_scenario_matrix_") as tmp_dir_name:
        tmp_root = Path(tmp_dir_name)
        for scenario in SCENARIOS:
            print(f"[matrix] running scenario={scenario} steps={args.steps} seed={args.seed}", flush=True)
            row = _run_scenario(scenario, args.steps, args.seed, tmp_root)
            rows.append(row)
            print(
                (
                    f"[matrix] {scenario} pnl={float(row['ending_pnl']):.2f} "
                    f"markout={float(row['total_signed_markout']):.2f} "
                    f"toxic={float(row['toxic_fill_rate']):.1%} hedges={int(row['hedge_trade_count'])}"
                ),
                flush=True,
            )

    csv_path = out_dir / "scenario_matrix.csv"
    md_path = out_dir / "scenario_matrix.md"
    png_path = out_dir / "scenario_comparison.png"
    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows, steps=args.steps, seed=args.seed)
    _write_plot(png_path, rows)

    print(f"[matrix] wrote {csv_path}", flush=True)
    print(f"[matrix] wrote {md_path}", flush=True)
    print(f"[matrix] wrote {png_path}", flush=True)


if __name__ == "__main__":
    main()
