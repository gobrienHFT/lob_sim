from __future__ import annotations

import argparse
import csv
import tempfile
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt

from lob_sim.options.demo import OptionsMarketMakerDemo, build_options_config


TOXIC_FLOW_PROBS = (0.20, 0.40, 0.55, 0.70)
BASE_HALF_SPREADS = (0.06, 0.09, 0.12, 0.15)

CSV_FIELDS = [
    "toxic_flow_prob",
    "base_half_spread",
    "seed",
    "steps",
    "ending_pnl",
    "gross_spread_captured",
    "total_signed_markout",
    "toxic_fill_rate",
    "adverse_fill_rate",
    "hedge_trade_count",
    "max_inventory_contracts",
]

PLOT_METRICS: list[tuple[str, str]] = [
    ("ending_pnl", "Ending PnL"),
    ("gross_spread_captured", "Gross spread"),
    ("total_signed_markout", "Signed markout"),
    ("adverse_fill_rate", "Adverse fill rate"),
]


def _run_point(
    toxic_flow_prob: float,
    base_half_spread: float,
    steps: int,
    seed: int,
    tmp_root: Path,
) -> dict[str, float | int]:
    base_config = build_options_config(steps=steps, seed=seed, scenario="toxic_flow")
    config = replace(
        base_config,
        toxic_flow_prob=toxic_flow_prob,
        base_half_spread=base_half_spread,
        scenario_description="Toxic-flow versus spread sensitivity sweep.",
    )
    summary = OptionsMarketMakerDemo(config).run(
        tmp_root / f"t{int(round(toxic_flow_prob * 100)):02d}_s{int(round(base_half_spread * 1000)):03d}",
        progress_every=0,
        write_artifacts=False,
    )
    return {
        "toxic_flow_prob": toxic_flow_prob,
        "base_half_spread": base_half_spread,
        "seed": seed,
        "steps": steps,
        "ending_pnl": float(summary["ending_pnl"]),
        "gross_spread_captured": float(summary["gross_spread_captured"]),
        "total_signed_markout": float(summary["total_signed_markout"]),
        "toxic_fill_rate": float(summary["toxic_fill_rate"]),
        "adverse_fill_rate": float(summary["adverse_fill_rate"]),
        "hedge_trade_count": int(summary["hedge_trade_count"]),
        "max_inventory_contracts": int(summary["max_inventory_contracts"]),
    }


def _write_csv(path: Path, rows: list[dict[str, float | int]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _fmt_metric(key: str, value: float | int) -> str:
    if key.endswith("_rate"):
        return f"{float(value):.1%}"
    if key in {"seed", "steps", "hedge_trade_count", "max_inventory_contracts"}:
        return str(int(value))
    return f"{float(value):.2f}"


def _write_markdown(path: Path, rows: list[dict[str, float | int]], *, steps: int, seed: int) -> None:
    lines = [
        "# Toxicity versus spread sensitivity",
        "",
        (
            "Deterministic sweep on the `toxic_flow` preset. Seed and step count stay fixed so the only moving "
            "parts are toxic-flow share and baseline quote width."
        ),
        "",
        f"- Seed: `{seed}`",
        f"- Steps: `{steps}`",
        f"- Toxic flow probabilities: `{', '.join(f'{value:.2f}' for value in TOXIC_FLOW_PROBS)}`",
        f"- Base half-spreads: `{', '.join(f'{value:.2f}' for value in BASE_HALF_SPREADS)}`",
        "",
        "| Toxic flow prob | Base half-spread | Ending PnL | Gross spread | Signed markout | Toxic fill rate | Adverse fill rate | Hedge trades | Max inventory |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{float(row['toxic_flow_prob']):.2f}",
                    f"{float(row['base_half_spread']):.2f}",
                    _fmt_metric("ending_pnl", row["ending_pnl"]),
                    _fmt_metric("gross_spread_captured", row["gross_spread_captured"]),
                    _fmt_metric("total_signed_markout", row["total_signed_markout"]),
                    _fmt_metric("toxic_fill_rate", row["toxic_fill_rate"]),
                    _fmt_metric("adverse_fill_rate", row["adverse_fill_rate"]),
                    _fmt_metric("hedge_trade_count", row["hedge_trade_count"]),
                    _fmt_metric("max_inventory_contracts", row["max_inventory_contracts"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## what this shows",
            "- Wider baseline spreads improve gross spread capture, but they do not erase adverse-selection losses when the toxic share rises.",
            "- As toxic flow probability increases, signed markout and adverse-fill rate generally deteriorate even with the same random seed.",
            "",
            "## what this does not show",
            "- This is still a synthetic dealer study, so it does not calibrate spread setting from real venue queue position or real customer flow.",
            "- One fixed seed and one short horizon show local trade-offs, not a fully robust parameter search or production spread optimizer.",
            "",
            f"Plot: [toxicity_spread_heatmap.png]({path.with_name('toxicity_spread_heatmap.png').name})",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _matrix(rows: list[dict[str, float | int]], metric: str) -> list[list[float]]:
    values: dict[tuple[float, float], float] = {
        (float(row["toxic_flow_prob"]), float(row["base_half_spread"])): float(row[metric]) for row in rows
    }
    return [
        [values[(toxic_prob, spread)] for spread in BASE_HALF_SPREADS]
        for toxic_prob in TOXIC_FLOW_PROBS
    ]


def _draw_metric_heatmap(ax: plt.Axes, rows: list[dict[str, float | int]], metric: str, title: str) -> None:
    matrix = _matrix(rows, metric)
    max_abs = max((abs(value) for row in matrix for value in row), default=0.0)
    cmap = "RdBu_r" if any(value < 0.0 for row in matrix for value in row) else "Blues"
    if cmap == "RdBu_r":
        image = ax.imshow(matrix, cmap=cmap, vmin=-max_abs if max_abs else -1.0, vmax=max_abs if max_abs else 1.0)
    else:
        image = ax.imshow(matrix, cmap=cmap)
    ax.set_title(title, fontsize=13, pad=8)
    ax.set_xlabel("Base half-spread", fontsize=10)
    ax.set_ylabel("Toxic flow prob", fontsize=10)
    ax.set_xticks(range(len(BASE_HALF_SPREADS)), [f"{spread:.2f}" for spread in BASE_HALF_SPREADS])
    ax.set_yticks(range(len(TOXIC_FLOW_PROBS)), [f"{prob:.2f}" for prob in TOXIC_FLOW_PROBS])
    ax.tick_params(axis="both", labelsize=9)
    for row_idx, row in enumerate(matrix):
        for col_idx, value in enumerate(row):
            if metric.endswith("_rate"):
                label = f"{value:.0%}"
            else:
                label = f"{value:.0f}"
            text_color = "white" if max_abs and abs(value) > max_abs * 0.55 else "black"
            ax.text(col_idx, row_idx, label, ha="center", va="center", fontsize=8, color=text_color)
    colorbar = plt.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.ax.tick_params(labelsize=8)


def _write_plot(path: Path, rows: list[dict[str, float | int]]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle("Toxicity versus spread sensitivity", fontsize=16)
    for ax, (metric, title) in zip(axes.flat, PLOT_METRICS, strict=True):
        _draw_metric_heatmap(ax, rows, metric, title)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep toxic-flow share and base spread for the options demo")
    parser.add_argument("--out-dir", default="outputs")
    parser.add_argument("--steps", type=int, default=180)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, float | int]] = []
    with tempfile.TemporaryDirectory(prefix="options_toxicity_spread_") as tmp_dir_name:
        tmp_root = Path(tmp_dir_name)
        for toxic_prob in TOXIC_FLOW_PROBS:
            for spread in BASE_HALF_SPREADS:
                print(
                    (
                        f"[sensitivity] toxic_flow_prob={toxic_prob:.2f} "
                        f"base_half_spread={spread:.2f} steps={args.steps} seed={args.seed}"
                    ),
                    flush=True,
                )
                row = _run_point(toxic_prob, spread, args.steps, args.seed, tmp_root)
                rows.append(row)

    rows.sort(key=lambda row: (float(row["toxic_flow_prob"]), float(row["base_half_spread"])))
    csv_path = out_dir / "toxicity_spread_sensitivity.csv"
    md_path = out_dir / "toxicity_spread_sensitivity.md"
    png_path = out_dir / "toxicity_spread_heatmap.png"
    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows, steps=args.steps, seed=args.seed)
    _write_plot(png_path, rows)

    print(f"[sensitivity] wrote {csv_path}", flush=True)
    print(f"[sensitivity] wrote {md_path}", flush=True)
    print(f"[sensitivity] wrote {png_path}", flush=True)


if __name__ == "__main__":
    main()
