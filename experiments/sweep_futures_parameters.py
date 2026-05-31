from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


SWEEP_FIELDS = [
    "rank",
    "diagnostic_score",
    "strategy_profile",
    "half_spread_bps",
    "queue_repost_lots",
    "fill_count",
    "fill_rate",
    "avg_spread_captured",
    "adverse_fill_rate_1s",
    "avg_markout_1s",
    "inventory_stdev",
    "max_drawdown",
    "fill_from_top_rate",
    "avg_queue_ahead_lots",
    "queue_fill_count",
    "max_queue_ahead_lots",
    "avg_fill_wait_ms",
    "fill_source_counts",
    "order_lifecycle_counts",
    "self_trade_prevention_count",
    "total_pnl",
    "total_fees",
    "kill_switch_triggered",
    "kill_switch_reason",
]


def _parse_decimals(raw: str) -> list[Decimal]:
    values = [Decimal(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("At least one decimal value is required")
    return values


def _parse_ints(raw: str) -> list[int]:
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("At least one integer value is required")
    return values


def _diagnostic_score(summary: dict) -> float:
    """Rank runs for inspection without implying live profitability."""

    return (
        float(summary["avg_spread_captured"])
        + float(summary["avg_markout_1s"])
        - 10.0 * float(summary["adverse_fill_rate_1s"])
        - 0.25 * float(summary["inventory_stdev"])
        - float(summary["max_drawdown"])
        - 0.001 * float(summary["avg_queue_ahead_lots"])
    )


def run_sweep(
    input_file: Path,
    env_path: str,
    profiles: list[str],
    half_spreads_bps: list[Decimal],
    queue_repost_lots: list[int],
) -> list[dict]:
    base_cfg = load_config(env_path)
    rows: list[dict] = []

    for profile in profiles:
        for half_spread in half_spreads_bps:
            for queue_repost in queue_repost_lots:
                cfg = replace(
                    base_cfg,
                    mm_strategy_profile=profile,
                    mm_half_spread_bps=half_spread,
                    mm_layered_inner_spread_bps=half_spread,
                    mm_layered_outer_spread_bps=max(half_spread, half_spread * Decimal("3")),
                    mm_queue_repost_lots=queue_repost,
                )
                engine = SimulationEngine(cfg)
                metrics = engine.run(input_file)
                summary = metrics.get_summary(engine._books)
                rows.append(
                    {
                        "diagnostic_score": _diagnostic_score(summary),
                        "strategy_profile": profile,
                        "half_spread_bps": str(half_spread),
                        "queue_repost_lots": queue_repost,
                        **{key: summary[key] for key in SWEEP_FIELDS if key in summary},
                    }
                )

    rows.sort(
        key=lambda row: (
            float(row["diagnostic_score"]),
            float(row["avg_spread_captured"]),
            -float(row["adverse_fill_rate_1s"]),
            -float(row["inventory_stdev"]),
        ),
        reverse=True,
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def build_sweep_metadata(
    input_file: Path,
    env_path: str,
    profiles: list[str],
    half_spreads_bps: list[Decimal],
    queue_repost_lots: list[int],
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> dict[str, Any]:
    cfg = load_config(env_path)
    cfg_snapshot = config_snapshot(cfg)
    return {
        "input_file": input_file.as_posix(),
        "input_sha256": file_sha256(input_file),
        "env_path": env_path,
        "config_digest": config_digest(cfg_snapshot),
        "feed_adapter": adapter_metadata(adapter),
        "profiles": profiles,
        "half_spreads_bps": [str(value) for value in half_spreads_bps],
        "queue_repost_lots": queue_repost_lots,
        "source": source_state(),
    }


def write_sweep_outputs(
    rows: list[dict],
    out_dir: Path,
    input_file: Path,
    *,
    output_stem: str = "futures_parameter_sweep",
    metadata: dict[str, Any] | None = None,
    command: str | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{output_stem}.csv"
    md_path = out_dir / f"{output_stem}.md"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SWEEP_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in SWEEP_FIELDS})

    table = [
        "| Rank | Profile | Half-spread bps | Queue repost lots | Score | Fills | Fill rate | Avg spread | Adverse 1s | Inventory stdev | Max drawdown |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        table.append(
            "| {rank} | `{strategy_profile}` | {half_spread_bps} | {queue_repost_lots} | "
            "{diagnostic_score:.6f} | {fill_count} | {fill_rate:.6f} | {avg_spread_captured:.6f} | "
            "{adverse_fill_rate_1s:.6f} | {inventory_stdev:.6f} | {max_drawdown:.6f} |".format(**row)
        )

    metadata_lines = [f"- Input file: `{input_file.as_posix()}`"]
    if metadata:
        metadata_lines.extend(
            [
                f"- Input SHA-256: `{metadata['input_sha256']}`",
                f"- Config digest: `{metadata['config_digest']}`",
                f"- Feed adapter: `{metadata['feed_adapter']['name']}` (`{metadata['feed_adapter']['venue_label']}`)",
                f"- Profiles: `{', '.join(metadata['profiles'])}`",
                f"- Half-spread bps grid: `{', '.join(metadata['half_spreads_bps'])}`",
                f"- Queue repost lots grid: `{', '.join(str(value) for value in metadata['queue_repost_lots'])}`",
                f"- Git commit at run time: `{metadata['source']['git_commit']}`",
                f"- Git dirty at run time: `{metadata['source']['git_dirty']}`",
            ]
        )
    if command:
        metadata_lines.extend(["", "Exact command:", "", "```bash", command, "```"])

    md_path.write_text(
        "\n".join(
            [
                "# Futures Parameter Sweep",
                "",
                *metadata_lines,
                "",
                "- Ranking score is diagnostic only; it is not an alpha or profitability claim.",
                "- Use this table to inspect how queue refresh, spread width, fill quality, adverse markout, and inventory variance move together on one deterministic fixture.",
                "",
                *table,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {"csv": csv_path, "markdown": md_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic futures MM parameter sweep on one replay input")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--out-dir", default="outputs/futures_sweeps", help="Directory for CSV/Markdown outputs")
    parser.add_argument("--profiles", default="baseline,layered_mm,research_mm", help="Comma-separated strategy profiles")
    parser.add_argument("--half-spreads-bps", default="0.05,0.10,0.25", help="Comma-separated half-spread bps values")
    parser.add_argument("--queue-repost-lots", default="0,5,99", help="Comma-separated queue repost thresholds")
    args = parser.parse_args()

    profiles = [profile.strip() for profile in args.profiles.split(",") if profile.strip()]
    half_spreads_bps = _parse_decimals(args.half_spreads_bps)
    queue_repost_lots = _parse_ints(args.queue_repost_lots)
    rows = run_sweep(
        input_file=Path(args.file),
        env_path=args.env,
        profiles=profiles,
        half_spreads_bps=half_spreads_bps,
        queue_repost_lots=queue_repost_lots,
    )
    metadata = build_sweep_metadata(
        input_file=Path(args.file),
        env_path=args.env,
        profiles=profiles,
        half_spreads_bps=half_spreads_bps,
        queue_repost_lots=queue_repost_lots,
    )
    command = (
        f"python experiments/sweep_futures_parameters.py --file {args.file} "
        f"--env {args.env} --out-dir {args.out_dir} "
        f"--profiles {args.profiles} --half-spreads-bps {args.half_spreads_bps} "
        f"--queue-repost-lots {args.queue_repost_lots}"
    )
    paths = write_sweep_outputs(rows, Path(args.out_dir), Path(args.file), metadata=metadata, command=command)
    print(f"Wrote {len(rows)} sweep rows")
    for label, path in paths.items():
        print(f"- {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
