from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import argparse
import csv
from typing import Iterable

import matplotlib.pyplot as plt

from lob_sim.config import load_config
from lob_sim.sim.engine import SimulationEngine


def _as_decimal(value) -> Decimal:
    return Decimal(str(value))


def _run_once(cfg, file_path: str, overrides: dict) -> dict:
    runtime_cfg = replace(cfg, **overrides)
    engine = SimulationEngine(runtime_cfg)
    metrics = engine.run(file_path)
    _, summary = engine.write_outputs(file_path, metrics)
    return summary


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _spread_width_sweep(cfg, file_path: str, out_dir: Path) -> list[dict]:
    candidates = ["0.03", "0.05", "0.10", "0.20", "0.40", "0.80"]
    rows: list[dict] = []
    for half_spread in candidates:
        summary = _run_once(cfg, file_path, {"mm_half_spread_bps": _as_decimal(half_spread)})
        rows.append(
            {
                "mm_half_spread_bps": float(half_spread),
                "total_pnl": summary["total_pnl"],
                "fill_rate": summary["fill_rate"],
                "avg_spread_captured": summary["avg_spread_captured"],
                "fill_count": summary["fill_count"],
                "queue_fill_count": summary["queue_fill_count"],
            }
        )

    _write_csv(out_dir / "spread_width_sweep.csv", rows)

    x = [r["mm_half_spread_bps"] for r in rows]
    plt.figure(figsize=(9, 5))
    plt.plot(x, [r["total_pnl"] for r in rows], marker="o", label="Total PnL")
    plt.ylabel("Total PnL")
    plt.twinx()
    plt.plot(x, [r["fill_rate"] for r in rows], marker="x", color="tab:orange", label="Fill rate")
    plt.ylabel("Fill rate")
    plt.title("Spread width effect on PnL and fill rate")
    plt.xlabel("Half spread bps")
    plt.tight_layout()
    plt.savefig(out_dir / "spread_width.png")
    plt.close()
    return rows


def _inventory_skew_sweep(cfg, file_path: str, out_dir: Path) -> list[dict]:
    candidates = ["0", "4", "8", "12", "20"]
    rows: list[dict] = []
    for skew in candidates:
        summary = _run_once(cfg, file_path, {"mm_skew_bps_per_unit": _as_decimal(skew)})
        rows.append(
            {
                "mm_skew_bps_per_unit": float(skew),
                "total_pnl": summary["total_pnl"],
                "inventory_stdev": summary["inventory_stdev"],
                "adverse_fill_rate_1s": summary["adverse_fill_rate_1s"],
                "max_queue_ahead_lots": summary["max_queue_ahead_lots"],
            }
        )

    _write_csv(out_dir / "inventory_skew_sweep.csv", rows)

    x = [r["mm_skew_bps_per_unit"] for r in rows]
    plt.figure(figsize=(9, 5))
    plt.plot(x, [r["adverse_fill_rate_1s"] for r in rows], marker="o")
    plt.title("Inventory skewing impact on adverse selection")
    plt.xlabel("Skew bps / unit")
    plt.ylabel("Adverse fill rate (1s)")
    plt.tight_layout()
    plt.savefig(out_dir / "inventory_skew.png")
    plt.close()
    return rows


def _latency_impact(cfg, file_path: str, out_dir: Path) -> list[dict]:
    candidates = ["5", "10", "20", "40", "80", "160"]
    rows: list[dict] = []
    for latency_ms in candidates:
        override = {
            "sim_order_latency_ms": float(latency_ms),
            "sim_cancel_latency_ms": float(latency_ms),
        }
        summary = _run_once(cfg, file_path, override)
        rows.append(
            {
                "sim_order_latency_ms": float(latency_ms),
                "fill_rate": summary["fill_rate"],
                "max_queue_ahead_lots": summary["max_queue_ahead_lots"],
                "fill_from_top_rate": summary["fill_from_top_rate"],
                "total_pnl": summary["total_pnl"],
            }
        )

    _write_csv(out_dir / "latency_sweep.csv", rows)

    x = [r["sim_order_latency_ms"] for r in rows]
    plt.figure(figsize=(9, 5))
    plt.plot(x, [r["max_queue_ahead_lots"] for r in rows], marker="o")
    plt.title("Latency impact on queue position")
    plt.xlabel("Order/cancel latency (ms)")
    plt.ylabel("Max queue ahead lots")
    plt.tight_layout()
    plt.savefig(out_dir / "latency_queue.png")
    plt.close()
    return rows


def _adverse_drift_experiment(cfg, file_path: str, out_dir: Path) -> list[dict]:
    spread_levels = ["0.03", "0.10", "0.30"]
    results: list[dict] = []
    for half_spread in spread_levels:
        summary = _run_once(cfg, file_path, {"mm_half_spread_bps": _as_decimal(half_spread)})
        stats = defaultdict(lambda: [0, 0])
        for event in summary.get("markout_events", []):
            if not event.get("fill_mid"):
                continue
            drift = _as_decimal(event["mid_after"]) - _as_decimal(event["fill_mid"])
            bucket = "up" if drift >= 0 else "down"
            stats[bucket][1] += 1
            if event.get("adverse"):
                stats[bucket][0] += 1

        row = {"mm_half_spread_bps": float(half_spread)}
        for bucket in ("up", "down"):
            adverse, total = stats[bucket]
            row[f"adverse_rate_{bucket}"] = float(Decimal(adverse) / Decimal(total)) if total else 0.0
            row[f"sample_count_{bucket}"] = total
        results.append(row)

    _write_csv(out_dir / "drift_adverse_sweep.csv", results)

    x = [r["mm_half_spread_bps"] for r in results]
    up = [r["adverse_rate_up"] for r in results]
    down = [r["adverse_rate_down"] for r in results]
    width = 0.35
    idx = range(len(x))
    plt.figure(figsize=(9, 5))
    plt.bar([i - width / 2 for i in idx], up, width=width, label="After up drift")
    plt.bar([i + width / 2 for i in idx], down, width=width, label="After down drift")
    plt.xticks(list(idx), [str(x_i) for x_i in x])
    plt.title("Adverse selection after drift regimes")
    plt.xlabel("Half spread bps")
    plt.ylabel("Adverse fraction")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "drift_adverse.png")
    plt.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run simulation experiment sweeps")
    parser.add_argument("--env", default=".env", help="Path to .env file (falls back to .env.example)")
    parser.add_argument("--file", required=True)
    parser.add_argument("--out-dir", default="experiments/output")
    args = parser.parse_args()

    cfg = load_config(args.env)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _spread_width_sweep(cfg, args.file, out_dir)
    _inventory_skew_sweep(cfg, args.file, out_dir)
    _latency_impact(cfg, args.file, out_dir)
    _adverse_drift_experiment(cfg, args.file, out_dir)

    print(f"Experiment outputs written to {out_dir}")


if __name__ == "__main__":
    main()
