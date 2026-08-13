from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.protocol import ResearchRegistry
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


LATENCY_SWEEP_FIELDS = [
    "rank",
    "registry_variant_id",
    "scenario_id",
    "diagnostic_score",
    "strategy_profile",
    "fill_model",
    "order_latency_ms",
    "cancel_latency_ms",
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
    "memory_bounded_by_tape_duration",
]


VALID_FILL_MODELS = ("trade", "depth")


def _variant_key(
    profile: str,
    fill_model: str,
    order_latency_ms: float,
    cancel_latency_ms: float,
) -> tuple[str, str, float, float]:
    return profile, fill_model, order_latency_ms, cancel_latency_ms


def _scenario_id(profile: str, fill_model: str, order_latency_ms: float, cancel_latency_ms: float) -> str:
    return (
        f"public_l2_latency:{profile}:fill={fill_model}:"
        f"order_ms={order_latency_ms:g}:cancel_ms={cancel_latency_ms:g}"
    )


def _normalize_fill_models(fill_models: list[str] | tuple[str, ...] | None, default: str) -> tuple[str, ...]:
    values = tuple(fill_models) if fill_models is not None else (default,)
    normalized = tuple(str(value).strip().lower() for value in values if str(value).strip())
    if not normalized:
        raise ValueError("At least one fill model is required")
    if any(value not in VALID_FILL_MODELS for value in normalized):
        raise ValueError("fill models must be trade or depth")
    if len(set(normalized)) != len(normalized):
        raise ValueError("fill models must be unique")
    return normalized


def _build_latency_registry(
    base_cfg: Any,
    profile: str,
    fill_models: tuple[str, ...],
    order_latencies_ms: list[float],
    cancel_latencies_ms: list[float],
) -> tuple[dict[tuple[str, str, float, float], str], dict[str, Any]]:
    registry = ResearchRegistry()
    variant_ids: dict[tuple[str, str, float, float], str] = {}
    for fill_model in fill_models:
        for order_latency in order_latencies_ms:
            for cancel_latency in cancel_latencies_ms:
                key = _variant_key(profile, fill_model, order_latency, cancel_latency)
                if key in variant_ids:
                    raise ValueError(f"duplicate latency-sweep variant: {key!r}")
                cfg = replace(
                    base_cfg,
                    mm_strategy_profile=profile,
                    sim_fill_model=fill_model,
                    sim_order_latency_ms=order_latency,
                    sim_cancel_latency_ms=cancel_latency,
                )
                name = (
                    f"latency:{profile}:fill={fill_model}:"
                    f"order_ms={order_latency:g}:cancel_ms={cancel_latency:g}"
                )
                variant_ids[key] = registry.register(
                    name,
                    {
                        "study": "futures_latency_sweep",
                        "strategy_profile": profile,
                        "fill_model": fill_model,
                        "scenario_id": _scenario_id(profile, fill_model, order_latency, cancel_latency),
                        "order_latency_ms": order_latency,
                        "cancel_latency_ms": cancel_latency,
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
        raise ValueError("latency-sweep rows do not bind one-to-one to the frozen research registry")
    for row in rows:
        variant = by_id.get(str(row["registry_variant_id"]))
        config = variant.get("config", {}) if variant else {}
        if (
            config.get("strategy_profile") != row.get("strategy_profile")
            or config.get("fill_model") != row.get("fill_model")
            or config.get("scenario_id") != row.get("scenario_id")
            or config.get("order_latency_ms") != row.get("order_latency_ms")
            or config.get("cancel_latency_ms") != row.get("cancel_latency_ms")
        ):
            raise ValueError(f"latency-sweep row is not bound to its registered configuration: {row!r}")


def _parse_floats(raw: str) -> list[float]:
    values = [float(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("At least one latency value is required")
    if any(value < 0 for value in values):
        raise ValueError("Latency values must be non-negative")
    return values


def _parse_fill_models(raw: str) -> list[str]:
    return list(_normalize_fill_models([part.strip() for part in raw.split(",")], "trade"))


def _diagnostic_score(summary: dict[str, Any], order_latency_ms: float, cancel_latency_ms: float) -> float:
    """Rank rows for inspection without implying low-latency execution performance."""

    latency_penalty = 0.01 * (order_latency_ms + cancel_latency_ms)
    return (
        float(summary["avg_spread_captured"])
        + float(summary["avg_markout_1s"])
        - 10.0 * float(summary["adverse_fill_rate_1s"])
        - 0.25 * float(summary["inventory_stdev"])
        - float(summary["max_drawdown"])
        - 0.001 * float(summary["avg_queue_ahead_lots"])
        - 0.001 * float(summary["avg_fill_wait_ms"])
        - latency_penalty
    )


def run_latency_sweep(
    input_file: Path,
    env_path: str,
    *,
    profile: str,
    order_latencies_ms: list[float],
    cancel_latencies_ms: list[float],
    fill_models: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    base_cfg = load_config(env_path)
    normalized_fill_models = _normalize_fill_models(fill_models, base_cfg.sim_fill_model)
    registry_variant_ids, registry_snapshot = _build_latency_registry(
        base_cfg,
        profile,
        normalized_fill_models,
        order_latencies_ms,
        cancel_latencies_ms,
    )
    rows: list[dict[str, Any]] = []

    for fill_model in normalized_fill_models:
        for order_latency in order_latencies_ms:
            for cancel_latency in cancel_latencies_ms:
                cfg = replace(
                    base_cfg,
                    mm_strategy_profile=profile,
                    sim_fill_model=fill_model,
                    sim_order_latency_ms=order_latency,
                    sim_cancel_latency_ms=cancel_latency,
                )
                # This study consumes only aggregate metrics.  Keeping the full
                # event/audit trace here makes a latency matrix scale with tape
                # duration and obscures the bounded-memory contract that ordinary
                # runs already provide through streaming export.
                engine = SimulationEngine(cfg, retain_event_trace=False, retain_audit_rows=False)
                metrics = engine.run(input_file)
                summary = metrics.get_summary(engine._books)
                rows.append(
                    {
                        "registry_variant_id": registry_variant_ids[
                            _variant_key(profile, fill_model, order_latency, cancel_latency)
                        ],
                        "scenario_id": _scenario_id(profile, fill_model, order_latency, cancel_latency),
                        "diagnostic_score": _diagnostic_score(summary, order_latency, cancel_latency),
                        "strategy_profile": profile,
                        "fill_model": fill_model,
                        "order_latency_ms": order_latency,
                        "cancel_latency_ms": cancel_latency,
                    "memory_bounded_by_tape_duration": bool(
                        summary["audit_retention"]["memory_bounded_by_tape_duration"]
                        and engine.event_trace_retention()["memory_bounded_by_tape_duration"]
                    ),
                        **{key: summary[key] for key in LATENCY_SWEEP_FIELDS if key in summary},
                    }
                )

    rows.sort(
        key=lambda row: (
            float(row["diagnostic_score"]),
            -float(row["order_latency_ms"]),
            -float(row["cancel_latency_ms"]),
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


def build_latency_sweep_metadata(
    input_file: Path,
    env_path: str,
    *,
    profile: str,
    order_latencies_ms: list[float],
    cancel_latencies_ms: list[float],
    fill_models: list[str] | tuple[str, ...] | None = None,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> dict[str, Any]:
    cfg = load_config(env_path)
    normalized_fill_models = _normalize_fill_models(fill_models, cfg.sim_fill_model)
    cfg_snapshot = config_snapshot(cfg)
    _, registry_snapshot = _build_latency_registry(
        cfg,
        profile,
        normalized_fill_models,
        order_latencies_ms,
        cancel_latencies_ms,
    )
    return {
        "input_file": input_file.as_posix(),
        "input_sha256": file_sha256(input_file),
        "env_path": env_path,
        "base_config_digest": config_digest(cfg_snapshot),
        "feed_adapter": adapter_metadata(adapter),
        "fill_model": normalized_fill_models[0] if len(normalized_fill_models) == 1 else "multiple",
        "fill_models": list(normalized_fill_models),
        "profile": profile,
        "order_latencies_ms": order_latencies_ms,
        "cancel_latencies_ms": cancel_latencies_ms,
        "research_registry": registry_snapshot,
        "source": source_state(),
    }


def _format_latency(value: float) -> str:
    return f"{value:g}"


def write_latency_sweep_outputs(
    rows: list[dict[str, Any]],
    out_dir: Path,
    input_file: Path,
    *,
    output_stem: str = "futures_latency_sweep",
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
            "schema_version": "lob_sim.futures_latency_sweep_registry.v2",
            "study_type": "futures_latency_sweep",
            "input_file": metadata.get("input_file"),
            "input_sha256": metadata.get("input_sha256"),
            "base_config_digest": metadata.get("base_config_digest"),
            "fill_models": metadata.get("fill_models", [metadata.get("fill_model")]),
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
        writer = csv.DictWriter(handle, fieldnames=LATENCY_SWEEP_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in LATENCY_SWEEP_FIELDS})

    table = [
        "| Rank | Profile | Fill model | Order latency ms | Cancel latency ms | Score | Fills | Quote-fill probability | Fills / quote request | Avg spread | Adverse 1s | Avg wait ms | Inventory stdev | Max drawdown |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        table.append(
            "| {rank} | `{strategy_profile}` | `{fill_model}` | {order_latency_ms:g} | {cancel_latency_ms:g} | "
            "{diagnostic_score:.6f} | {fill_count} | {quote_fill_probability:.6f} | "
            "{fills_per_quote_request:.6f} | {avg_spread_captured:.6f} | "
            "{adverse_fill_rate_1s:.6f} | {avg_fill_wait_ms:.6f} | {inventory_stdev:.6f} | {max_drawdown:.6f} |".format(
                **row
            )
        )

    metadata_lines = [f"- Input file: `{input_file.as_posix()}`"]
    if metadata:
        metadata_lines.extend(
            [
                f"- Input SHA-256: `{metadata['input_sha256']}`",
                f"- Base config digest: `{metadata['base_config_digest']}`",
                f"- Feed adapter: `{metadata['feed_adapter']['name']}` (`{metadata['feed_adapter']['venue_label']}`)",
                "- Public-L2 fill models: `"
                + "`, `".join(metadata.get("fill_models", [metadata["fill_model"]]))
                + "` (mutually exclusive scenarios)",
                f"- Strategy profile: `{metadata['profile']}`",
                "- Order latency grid ms: `"
                + ", ".join(_format_latency(value) for value in metadata["order_latencies_ms"])
                + "`",
                "- Cancel latency grid ms: `"
                + ", ".join(_format_latency(value) for value in metadata["cancel_latencies_ms"])
                + "`",
                f"- Frozen research registry SHA-256: `{metadata['research_registry']['registry_sha256']}`",
                f"- Registry sidecar: `{registry_path.name if registry_path else output_stem + '_registry.json'}`",
                f"- Git commit at run time: `{metadata['source']['git_commit']}`",
                f"- Git dirty at run time: `{metadata['source']['git_dirty']}`",
            ]
        )
    if command:
        metadata_lines.extend(["", "Exact command:", "", "```bash", command, "```"])

    evidence_notes = [
        "- Latency values are modeled order-arrival and cancel-ack delays inside the replay simulator, not measured gateway, colocated, or exchange latency.",
        "- Ranking score is diagnostic only; it is not a latency-arbitrage, alpha, or profitability claim.",
        "- `quote_fill_probability` is bounded by arrived orders; `fills_per_quote_request` can exceed one when a single order has multiple partial fills.",
        "- Fill models are mutually exclusive public-L2 execution signals; the matrix is a scenario envelope, not a true fill bound.",
        "- Use this table to inspect how queue position, fill quality, adverse markout, and cancel races respond to explicit latency assumptions on one deterministic fixture.",
        "- The sweep uses aggregate-only metrics with event and audit rows disabled in memory; use the bounded streaming runner when individual audit rows are required.",
    ]
    if rows and all(int(row.get("fill_count", 0)) == 0 for row in rows):
        evidence_notes.append(
            "- This tiny committed clip produced no confirmed-trade fills; it is a zero-fill diagnostic, not economic evidence."
        )

    md_path.write_text(
        "\n".join(
            [
                "# Futures Latency Sensitivity Sweep",
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
    parser = argparse.ArgumentParser(description="Run a deterministic futures latency sensitivity sweep")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument(
        "--out-dir", default="outputs/futures_latency_sweeps", help="Directory for CSV/Markdown outputs"
    )
    parser.add_argument("--profile", default="baseline", help="Strategy profile to sweep")
    parser.add_argument(
        "--fill-models",
        default="trade",
        help="Comma-separated mutually exclusive public-L2 fill models (trade,depth)",
    )
    parser.add_argument("--order-latencies-ms", default="0,10,50", help="Comma-separated order latency values in ms")
    parser.add_argument("--cancel-latencies-ms", default="0,10,50", help="Comma-separated cancel latency values in ms")
    args = parser.parse_args()

    order_latencies_ms = _parse_floats(args.order_latencies_ms)
    cancel_latencies_ms = _parse_floats(args.cancel_latencies_ms)
    fill_models = _parse_fill_models(args.fill_models)
    rows = run_latency_sweep(
        input_file=Path(args.file),
        env_path=args.env,
        profile=args.profile,
        order_latencies_ms=order_latencies_ms,
        cancel_latencies_ms=cancel_latencies_ms,
        fill_models=fill_models,
    )
    metadata = build_latency_sweep_metadata(
        input_file=Path(args.file),
        env_path=args.env,
        profile=args.profile,
        order_latencies_ms=order_latencies_ms,
        cancel_latencies_ms=cancel_latencies_ms,
        fill_models=fill_models,
    )
    command = (
        f"python experiments/sweep_futures_latency.py --file {args.file} "
        f"--env {args.env} --out-dir {args.out_dir} --profile {args.profile} "
        f"--fill-models {args.fill_models} "
        f"--order-latencies-ms {args.order_latencies_ms} --cancel-latencies-ms {args.cancel_latencies_ms}"
    )
    paths = write_latency_sweep_outputs(rows, Path(args.out_dir), Path(args.file), metadata=metadata, command=command)
    print(f"Wrote {len(rows)} latency sweep rows")
    for label, path in paths.items():
        print(f"- {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
