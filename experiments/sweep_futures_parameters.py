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
from lob_sim.research.protocol import ResearchRegistry
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


SWEEP_FIELDS = [
    "rank",
    "registry_variant_id",
    "diagnostic_score",
    "strategy_profile",
    "half_spread_bps",
    "queue_repost_lots",
    "fill_count",
    "quote_fill_probability",
    "fills_per_quote_request",
    "fills_per_arrived_order",
    "avg_spread_captured",
    "adverse_fill_rate_1s",
    "avg_markout_1s",
    "markout_by_fill_source",
    "inventory_stdev",
    "max_drawdown",
    "fill_from_top_rate",
    "avg_queue_ahead_lots",
    "queue_fill_count",
    "max_queue_ahead_lots",
    "resting_arrival_queue_samples",
    "arrival_with_queue_ahead_count",
    "avg_arrival_queue_ahead_lots",
    "max_arrival_queue_ahead_lots",
    "avg_fill_wait_ms",
    "fill_source_counts",
    "order_lifecycle_counts",
    "self_trade_prevention_count",
    "total_pnl",
    "total_fees",
    "kill_switch_triggered",
    "kill_switch_reason",
]


def _variant_key(profile: str, half_spread_bps: Decimal, queue_repost_lots: int) -> tuple[str, str, int]:
    return profile, str(half_spread_bps), queue_repost_lots


def _build_sweep_registry(
    base_cfg: Any,
    profiles: list[str],
    half_spreads_bps: list[Decimal],
    queue_repost_lots: list[int],
) -> tuple[dict[tuple[str, str, int], str], dict[str, Any]]:
    registry = ResearchRegistry()
    variant_ids: dict[tuple[str, str, int], str] = {}
    for profile in profiles:
        for half_spread in half_spreads_bps:
            for queue_repost in queue_repost_lots:
                key = _variant_key(profile, half_spread, queue_repost)
                if key in variant_ids:
                    raise ValueError(f"duplicate parameter-sweep variant: {key!r}")
                cfg = replace(
                    base_cfg,
                    mm_strategy_profile=profile,
                    mm_half_spread_bps=half_spread,
                    mm_layered_inner_spread_bps=half_spread,
                    mm_layered_outer_spread_bps=max(half_spread, half_spread * Decimal("3")),
                    mm_queue_repost_lots=queue_repost,
                )
                name = f"parameter:{profile}:half_spread_bps={half_spread}:queue_repost_lots={queue_repost}"
                variant_ids[key] = registry.register(
                    name,
                    {
                        "study": "futures_parameter_sweep",
                        "strategy_profile": profile,
                        "half_spread_bps": str(half_spread),
                        "queue_repost_lots": queue_repost,
                        "normalized_config": config_snapshot(cfg),
                    },
                )
    return variant_ids, registry.freeze()


def _validate_registry_rows(rows: list[dict[str, Any]], registry_snapshot: dict[str, Any]) -> None:
    variants = registry_snapshot.get("variants", [])
    by_id = {
        str(variant["variant_id"]): variant
        for variant in variants
        if isinstance(variant, dict) and "variant_id" in variant
    }
    row_ids = [str(row.get("registry_variant_id", "")) for row in rows]
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(by_id):
        raise ValueError("parameter-sweep rows do not bind one-to-one to the frozen research registry")
    for row in rows:
        variant = by_id.get(str(row["registry_variant_id"]))
        config = variant.get("config", {}) if variant else {}
        if (
            config.get("strategy_profile") != row.get("strategy_profile")
            or config.get("half_spread_bps") != row.get("half_spread_bps")
            or config.get("queue_repost_lots") != row.get("queue_repost_lots")
        ):
            raise ValueError(f"parameter-sweep row is not bound to its registered configuration: {row!r}")


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
        - 0.0005 * float(summary["avg_arrival_queue_ahead_lots"])
    )


def run_sweep(
    input_file: Path,
    env_path: str,
    profiles: list[str],
    half_spreads_bps: list[Decimal],
    queue_repost_lots: list[int],
) -> list[dict]:
    base_cfg = load_config(env_path)
    registry_variant_ids, registry_snapshot = _build_sweep_registry(
        base_cfg,
        profiles,
        half_spreads_bps,
        queue_repost_lots,
    )
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
                        "registry_variant_id": registry_variant_ids[_variant_key(profile, half_spread, queue_repost)],
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
    _validate_registry_rows(rows, registry_snapshot)
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
    _, registry_snapshot = _build_sweep_registry(
        cfg,
        profiles,
        half_spreads_bps,
        queue_repost_lots,
    )
    return {
        "input_file": input_file.as_posix(),
        "input_sha256": file_sha256(input_file),
        "env_path": env_path,
        "config_digest": config_digest(cfg_snapshot),
        "feed_adapter": adapter_metadata(adapter),
        "fill_model": cfg.sim_fill_model,
        "profiles": profiles,
        "half_spreads_bps": [str(value) for value in half_spreads_bps],
        "queue_repost_lots": queue_repost_lots,
        "research_registry": registry_snapshot,
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
    registry_path: Path | None = None

    if metadata and isinstance(metadata.get("research_registry"), dict):
        registry_path = out_dir / f"{output_stem}_registry.json"
        registry_payload = {
            "schema_version": "lob_sim.futures_parameter_sweep_registry.v1",
            "study_type": "futures_parameter_sweep",
            "input_file": metadata.get("input_file"),
            "input_sha256": metadata.get("input_sha256"),
            "config_digest": metadata.get("config_digest"),
            "research_registry": metadata["research_registry"],
            "row_registry_variant_ids": sorted(
                {str(row["registry_variant_id"]) for row in rows if row.get("registry_variant_id")}
            ),
        }
        registry_path.write_text(
            json.dumps(registry_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SWEEP_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in SWEEP_FIELDS})

    table = [
        "| Rank | Profile | Half-spread bps | Queue repost lots | Score | Fills | Quote-fill probability | Fills / quote request | Avg spread | Adverse 1s | Inventory stdev | Max drawdown |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        table.append(
            "| {rank} | `{strategy_profile}` | {half_spread_bps} | {queue_repost_lots} | "
            "{diagnostic_score:.6f} | {fill_count} | {quote_fill_probability:.6f} | "
            "{fills_per_quote_request:.6f} | {avg_spread_captured:.6f} | "
            "{adverse_fill_rate_1s:.6f} | {inventory_stdev:.6f} | {max_drawdown:.6f} |".format(**row)
        )

    metadata_lines = [f"- Input file: `{input_file.as_posix()}`"]
    if metadata:
        metadata_lines.extend(
            [
                f"- Input SHA-256: `{metadata['input_sha256']}`",
                f"- Config digest: `{metadata['config_digest']}`",
                f"- Feed adapter: `{metadata['feed_adapter']['name']}` (`{metadata['feed_adapter']['venue_label']}`)",
                f"- Public-L2 fill model: `{metadata['fill_model']}` (mutually exclusive scenario)",
                f"- Profiles: `{', '.join(metadata['profiles'])}`",
                f"- Half-spread bps grid: `{', '.join(metadata['half_spreads_bps'])}`",
                f"- Queue repost lots grid: `{', '.join(str(value) for value in metadata['queue_repost_lots'])}`",
                f"- Frozen research registry SHA-256: `{metadata['research_registry']['registry_sha256']}`",
                f"- Registry sidecar: `{registry_path.name if registry_path else output_stem + '_registry.json'}`",
                f"- Git commit at run time: `{metadata['source']['git_commit']}`",
                f"- Git dirty at run time: `{metadata['source']['git_dirty']}`",
            ]
        )
    if command:
        metadata_lines.extend(["", "Exact command:", "", "```bash", command, "```"])

    evidence_notes = [
        "- Ranking score is diagnostic only; it is not an alpha or profitability claim.",
        "- `quote_fill_probability` is bounded by arrived orders; `fills_per_quote_request` can exceed one when a single order has multiple partial fills.",
        "- Use this table to inspect how queue refresh, spread width, fill quality, adverse markout, and inventory variance move together on one deterministic fixture.",
    ]
    if rows and all(int(row.get("fill_count", 0)) == 0 for row in rows):
        evidence_notes.append(
            "- This tiny committed clip produced no confirmed-trade fills; it is a zero-fill diagnostic, not economic evidence."
        )

    md_path.write_text(
        "\n".join(
            [
                "# Futures Parameter Sweep",
                "",
                *metadata_lines,
                "",
                *evidence_notes,
                "",
                *table,
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    outputs = {"csv": csv_path, "markdown": md_path}
    if registry_path is not None:
        outputs["registry"] = registry_path
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic futures MM parameter sweep on one replay input")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--out-dir", default="outputs/futures_sweeps", help="Directory for CSV/Markdown outputs")
    parser.add_argument(
        "--profiles", default="baseline,layered_mm,research_mm", help="Comma-separated strategy profiles"
    )
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
