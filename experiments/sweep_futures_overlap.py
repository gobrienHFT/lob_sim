from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from lob_sim.config import FillAssumptionProfile, fill_assumption_config_for_profile, load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.protocol import ResearchRegistry
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


OVERLAP_SWEEP_SCHEMA_VERSION = "lob_sim.futures_overlap_sensitivity.v1"
DEFAULT_OVERLAP_WINDOWS_MS: tuple[int, ...] = (0, 125, 250)
OVERLAP_SWEEP_FIELDS = [
    "overlap_window_ms",
    "registry_variant_id",
    "input_sha256",
    "config_sha256",
    "state_sha256",
    "fill_count",
    "realized_pnl",
    "unrealized_pnl",
    "total_pnl",
    "total_fees",
    "public_consumption_totals",
    "corroborated_depth_reduction_lots",
    "uncorroborated_depth_reduction_lots",
    "fill_source_counts",
    "execution_claim_ready",
]


def _parse_windows(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError(f"overlap windows must be integer milliseconds: {text!r}") from exc
        if value < 0:
            raise ValueError("overlap windows must be non-negative")
        values.append(value)
    if not values:
        raise ValueError("at least one overlap window is required")
    if len(set(values)) != len(values):
        raise ValueError("overlap windows must be unique")
    return tuple(values)


def _window_config(base_cfg: Any, window_ms: int) -> Any:
    if window_ms < 0:
        raise ValueError("overlap window must be non-negative")
    # Freeze the study to the base profile.  The selected SIM_FILL_MODEL still
    # controls the economic signal; this sweep changes only local trade/depth
    # corroboration timing.
    assumption = fill_assumption_config_for_profile("base")
    assumption = replace(
        assumption,
        overlap_netting_enabled=window_ms > 0,
        overlap_window_seconds=window_ms / 1_000.0,
    )
    return replace(base_cfg, fill_assumption=assumption, mm_enabled=False)


def _variant_name(profile: FillAssumptionProfile, window_ms: int) -> str:
    return f"overlap:{profile}:window_ms={window_ms}"


def _build_registry(
    base_cfg: Any,
    windows_ms: Iterable[int],
) -> tuple[dict[int, str], dict[str, Any]]:
    registry = ResearchRegistry()
    variant_ids: dict[int, str] = {}
    for window_ms in windows_ms:
        if window_ms in variant_ids:
            raise ValueError(f"duplicate overlap window: {window_ms}")
        cfg = _window_config(base_cfg, window_ms)
        variant_ids[window_ms] = registry.register(
            _variant_name(cfg.fill_assumption.profile, window_ms),
            {
                "study": "futures_overlap_sensitivity",
                "fill_assumption_profile": cfg.fill_assumption.profile,
                "sim_fill_model": cfg.sim_fill_model,
                "overlap_window_ms": window_ms,
                "normalized_config": config_snapshot(cfg),
            },
        )
    return variant_ids, registry.freeze()


def _public_totals(summary: dict[str, Any]) -> dict[str, Any]:
    public = summary.get("public_consumption_summary")
    if not isinstance(public, dict):
        return {}
    return {
        "total_observed_lots": public.get("total_observed_lots"),
        "total_modeled_lots": public.get("total_modeled_lots"),
        "total_overlap_netted_lots": public.get("total_overlap_netted_lots"),
        "total_queue_consumed_lots": public.get("total_queue_consumed_lots"),
        "total_unmatched_lots": public.get("total_unmatched_lots"),
        "sources": public.get("sources"),
    }


def _summary_for_engine(engine: SimulationEngine, metrics: Any) -> dict[str, Any]:
    summary = metrics.get_summary(engine._books, specs=engine._specs)
    summary["public_consumption_summary"] = engine.fill_model.public_consumption_summary()
    summary["fill_assumption_diagnostics"] = engine.fill_model.fill_assumption_diagnostics()
    return summary


def _run_window(
    input_file: Path,
    base_cfg: Any,
    window_ms: int,
    registry_variant_id: str,
) -> dict[str, Any]:
    cfg = _window_config(base_cfg, window_ms)
    engine = SimulationEngine(cfg, retain_event_trace=False)
    metrics = engine.run(input_file)
    summary = _summary_for_engine(engine, metrics)
    diagnostics = summary.get("fill_assumption_diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    total_pnl = summary.get("total_pnl")
    if total_pnl is None:
        total_pnl = float(summary.get("realized_pnl", 0.0)) + float(summary.get("unrealized_pnl", 0.0))
    integrity = summary.get("integrity", {})
    claim_ready = integrity.get("claim_ready") if isinstance(integrity, dict) else None
    return {
        "overlap_window_ms": window_ms,
        "registry_variant_id": registry_variant_id,
        "input_sha256": file_sha256(input_file),
        "config_sha256": config_digest(config_snapshot(cfg)),
        "state_sha256": engine.state_sha256(),
        "fill_count": int(summary.get("fill_count", 0)),
        "realized_pnl": float(summary.get("realized_pnl", 0.0)),
        "unrealized_pnl": float(summary.get("unrealized_pnl", 0.0)),
        "total_pnl": float(total_pnl),
        "total_fees": float(summary.get("total_fees", 0.0)),
        "public_consumption_totals": _public_totals(summary),
        "corroborated_depth_reduction_lots": int(diagnostics.get("corroborated_depth_reduction_lots", 0)),
        "uncorroborated_depth_reduction_lots": int(diagnostics.get("uncorroborated_depth_reduction_lots", 0)),
        "fill_source_counts": summary.get("fill_source_counts", {}),
        "execution_claim_ready": bool(claim_ready),
    }


def _validate_rows(
    rows: list[dict[str, Any]],
    windows_ms: tuple[int, ...],
    registry_snapshot: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    observed_windows = tuple(int(row.get("overlap_window_ms", -1)) for row in rows)
    if observed_windows != windows_ms:
        issues.append(f"rows must preserve requested overlap-window order: {windows_ms!r}")
    input_hashes = {row.get("input_sha256") for row in rows}
    if len(input_hashes) != 1:
        issues.append("overlap runs have mixed input hashes")
    variants = registry_snapshot.get("variants", [])
    by_window = {
        int(variant["config"]["overlap_window_ms"]): str(variant["variant_id"])
        for variant in variants
        if isinstance(variant, dict)
        and isinstance(variant.get("config"), dict)
        and "overlap_window_ms" in variant["config"]
        and "variant_id" in variant
    }
    if tuple(sorted(by_window)) != tuple(sorted(windows_ms)):
        issues.append("frozen registry does not cover exactly the requested windows")
    row_ids = [str(row.get("registry_variant_id", "")) for row in rows]
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(by_window.values()):
        issues.append("overlap rows must bind one-to-one to the frozen registry")
    for row in rows:
        window_ms = int(row.get("overlap_window_ms", -1))
        if by_window.get(window_ms) != str(row.get("registry_variant_id")):
            issues.append(f"overlap row {window_ms} ms is not bound to its registry variant")
    return {
        "ok": not issues,
        "issues": issues,
        "windows_ms": list(windows_ms),
        "input_sha256": next(iter(input_hashes)) if len(input_hashes) == 1 else None,
        "registry_variant_ids": {str(key): value for key, value in sorted(by_window.items())},
    }


def run_overlap_sweep(
    input_file: Path,
    env_path: str,
    *,
    windows_ms: tuple[int, ...] = DEFAULT_OVERLAP_WINDOWS_MS,
) -> dict[str, Any]:
    if not windows_ms:
        raise ValueError("at least one overlap window is required")
    base_cfg = load_config(env_path)
    variant_ids, registry_snapshot = _build_registry(base_cfg, windows_ms)
    rows = [_run_window(input_file, base_cfg, window_ms, variant_ids[window_ms]) for window_ms in windows_ms]
    audit = _validate_rows(rows, windows_ms, registry_snapshot)
    if not audit["ok"]:
        raise RuntimeError("; ".join(audit["issues"]))
    cfg = _window_config(base_cfg, windows_ms[0])
    return {
        "schema_version": OVERLAP_SWEEP_SCHEMA_VERSION,
        "study_type": "futures_overlap_sensitivity",
        "input_file": input_file.as_posix(),
        "input_sha256": file_sha256(input_file),
        "env_path": env_path,
        "base_config_digest": config_digest(config_snapshot(cfg)),
        "feed_adapter": adapter_metadata(DEFAULT_REPLAY_ADAPTER),
        "fill_model": cfg.sim_fill_model,
        "fill_assumption_profile": cfg.fill_assumption.profile,
        "windows_ms": list(windows_ms),
        "audit": audit,
        "research_registry": registry_snapshot,
        "source": source_state(),
        "runs": rows,
        "interpretation": {
            "scope": "local public trade/depth corroboration diagnostics",
            "economic_fill_signal": cfg.sim_fill_model,
            "private_fifo_claim": False,
            "private_execution_truth": False,
            "claim_ready": False,
        },
    }


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def write_overlap_outputs(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    output_stem: str = "futures_overlap_sensitivity",
    command: str | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{output_stem}.json"
    csv_path = out_dir / f"{output_stem}.csv"
    md_path = out_dir / f"{output_stem}.md"
    registry_path = out_dir / f"{output_stem}_registry.json"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "lob_sim.futures_overlap_sensitivity_registry.v1",
                "study_type": payload["study_type"],
                "input_sha256": payload["input_sha256"],
                "base_config_digest": payload["base_config_digest"],
                "research_registry": payload["research_registry"],
                "row_registry_variant_ids": [row["registry_variant_id"] for row in payload["runs"]],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OVERLAP_SWEEP_FIELDS)
        writer.writeheader()
        for row in payload["runs"]:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in OVERLAP_SWEEP_FIELDS})

    table = [
        "| Window ms | Fills | Total PnL | Fees | Overlap-netted lots | Corroborated depth lots | Uncorroborated depth lots | State SHA-256 |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["runs"]:
        public = row["public_consumption_totals"]
        table.append(
            "| {window:g} | {fills} | {pnl:.10g} | {fees:.10g} | {overlap} | {corr} | {uncorr} | `{state}` |".format(
                window=float(row["overlap_window_ms"]),
                fills=row["fill_count"],
                pnl=float(row["total_pnl"]),
                fees=float(row["total_fees"]),
                overlap=public.get("total_overlap_netted_lots", 0),
                corr=row["corroborated_depth_reduction_lots"],
                uncorr=row["uncorroborated_depth_reduction_lots"],
                state=row["state_sha256"],
            )
        )
    lines = [
        "# Futures Overlap-Reconciliation Sensitivity",
        "",
        f"- Input file: `{payload['input_file']}`",
        f"- Input SHA-256: `{payload['input_sha256']}`",
        f"- Base config digest: `{payload['base_config_digest']}`",
        f"- Public-L2 economic signal: `{payload['fill_model']}` (mutually exclusive scenario)",
        f"- Frozen research registry SHA-256: `{payload['research_registry']['registry_sha256']}`",
        f"- Registry sidecar: `{registry_path.name}`",
    ]
    if command:
        lines.extend(["", "Exact command:", "", "```bash", command, "```"])
    lines.extend(
        [
            "",
            "Public L2 cannot prove private fills. This is a local corroboration diagnostic, not a private FIFO or execution-truth claim.",
            "The selected trade/depth signal remains mutually exclusive; the window only controls whether the other public feed is treated as corroborating evidence.",
            "The study is intentionally non-economic (`MM_ENABLED=0`) and is not claim-ready even if the input has no detected gap.",
            "",
            *table,
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"json": json_path, "csv": csv_path, "markdown": md_path, "registry": registry_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic public-L2 overlap-window sensitivity sweep")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--out-dir", default="outputs/futures_overlap_sensitivity", help="Directory for outputs")
    parser.add_argument(
        "--windows-ms",
        default=",".join(str(value) for value in DEFAULT_OVERLAP_WINDOWS_MS),
        help="Comma-separated local overlap windows in milliseconds",
    )
    args = parser.parse_args()
    windows_ms = _parse_windows(args.windows_ms)
    payload = run_overlap_sweep(Path(args.file), args.env, windows_ms=windows_ms)
    command = (
        f"python experiments/sweep_futures_overlap.py --file {args.file} --env {args.env} "
        f"--out-dir {args.out_dir} --windows-ms {args.windows_ms}"
    )
    paths = write_overlap_outputs(payload, Path(args.out_dir), command=command)
    print(f"Wrote {len(payload['runs'])} overlap sensitivity rows")
    for label, path in paths.items():
        print(f"- {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
