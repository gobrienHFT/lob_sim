from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import (
    FILL_ASSUMPTION_PROFILES,
    FillAssumptionProfile,
    fill_assumption_config_for_profile,
    load_config,
)
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot


ENVELOPE_SCHEMA_VERSION = "lob_sim.fill_assumption_envelope.v1"
ENVELOPE_FIELDS = [
    "profile",
    "run_id",
    "input_digest",
    "config_digest",
    "normalized_config_digest",
    "wall_time_seconds",
    "fill_count",
    "realized_pnl",
    "unrealized_pnl",
    "total_fees",
    "avg_spread_captured",
    "adverse_fill_rate_1s",
    "fill_source_counts",
    "public_consumption_totals",
    "max_inventory",
    "kill_switch_triggered",
    "kill_switch_reason",
    "summary_path",
    "trades_path",
    "event_trace_path",
    "manifest_path",
]


def _normalized_config_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(snapshot)
    normalized.pop("fill_assumption_profile", None)
    normalized.pop("fill_assumption", None)
    return normalized


def _public_consumption_totals(summary: dict[str, Any]) -> dict[str, Any]:
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


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _profile_cfg(base_cfg: Any, profile: FillAssumptionProfile, out_dir: Path) -> Any:
    return replace(
        base_cfg,
        fill_assumption=fill_assumption_config_for_profile(profile),
        record_dir=out_dir / profile,
    )


def _run_profile(input_file: Path, base_cfg: Any, profile: FillAssumptionProfile, out_dir: Path) -> dict[str, Any]:
    cfg = _profile_cfg(base_cfg, profile, out_dir)
    cfg_snapshot = config_snapshot(cfg)
    started = time.perf_counter()
    engine = SimulationEngine(cfg)
    metrics = engine.run(input_file)
    output_files, summary = engine.write_outputs(str(input_file), metrics)
    wall_time_seconds = time.perf_counter() - started

    manifest = json.loads(output_files["manifest"].read_text(encoding="utf-8"))
    return {
        "profile": profile,
        "run_id": summary["run_id"],
        "input_digest": summary["input_sha256"],
        "config_digest": config_digest(cfg_snapshot),
        "normalized_config_digest": config_digest(_normalized_config_snapshot(cfg_snapshot)),
        "wall_time_seconds": wall_time_seconds,
        "fill_count": summary.get("fill_count", 0),
        "realized_pnl": summary.get("realized_pnl", 0.0),
        "unrealized_pnl": summary.get("unrealized_pnl", 0.0),
        "total_fees": summary.get("total_fees", 0.0),
        "avg_spread_captured": summary.get("avg_spread_captured", 0.0),
        "adverse_fill_rate_1s": summary.get("adverse_fill_rate_1s", 0.0),
        "fill_source_counts": summary.get("fill_source_counts", {}),
        "public_consumption_totals": _public_consumption_totals(summary),
        "max_inventory": summary.get("max_inventory", 0.0),
        "kill_switch_triggered": summary.get("kill_switch_triggered", False),
        "kill_switch_reason": summary.get("kill_switch_reason"),
        "summary_path": str(output_files["summary"]),
        "trades_path": str(output_files["trades"]),
        "event_trace_path": str(output_files["event_trace"]),
        "manifest_path": str(output_files["manifest"]),
        "runtime": manifest.get("runtime", {}),
        "source": manifest.get("source", {}),
        "fill_assumption": summary.get("fill_assumption", {}),
    }


def _validate_envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = [row["profile"] for row in rows]
    input_digests = {row["input_digest"] for row in rows}
    normalized_config_digests = {row["normalized_config_digest"] for row in rows}
    issues: list[str] = []
    if profiles != list(FILL_ASSUMPTION_PROFILES):
        issues.append("Envelope must contain conservative, base, aggressive in that order")
    if len(input_digests) != 1:
        issues.append("Envelope runs have mixed input digests")
    if len(normalized_config_digests) != 1:
        issues.append("Envelope runs have mixed normalized config digests")
    return {
        "ok": not issues,
        "issues": issues,
        "profiles": profiles,
        "input_digest": next(iter(input_digests)) if len(input_digests) == 1 else None,
        "normalized_config_digest": (
            next(iter(normalized_config_digests)) if len(normalized_config_digests) == 1 else None
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ENVELOPE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in ENVELOPE_FIELDS})


def _render_report(payload: dict[str, Any], command: str | None = None) -> str:
    rows = payload["runs"]
    table = [
        "| Profile | Fills | Realized PnL | Unrealized PnL | Fees | Avg spread | Adverse 1s | Max inventory | Fill sources | Public consumption |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        public_totals = row["public_consumption_totals"]
        table.append(
            "| `{profile}` | {fill_count} | {realized_pnl:.10g} | {unrealized_pnl:.10g} | "
            "{total_fees:.10g} | {avg_spread_captured:.10g} | {adverse_fill_rate_1s:.6f} | "
            "{max_inventory:.10g} | `{fill_sources}` | `{public}` |".format(
                profile=row["profile"],
                fill_count=row["fill_count"],
                realized_pnl=float(row["realized_pnl"]),
                unrealized_pnl=float(row["unrealized_pnl"]),
                total_fees=float(row["total_fees"]),
                avg_spread_captured=float(row["avg_spread_captured"]),
                adverse_fill_rate_1s=float(row["adverse_fill_rate_1s"]),
                max_inventory=float(row["max_inventory"]),
                fill_sources=json.dumps(row["fill_source_counts"], sort_keys=True),
                public=json.dumps(
                    {key: value for key, value in public_totals.items() if key != "sources"},
                    sort_keys=True,
                ),
            )
        )

    metadata = [
        f"- Input file: `{payload['input_file']}`",
        f"- Input digest: `{payload['audit']['input_digest']}`",
        f"- Normalized config digest: `{payload['audit']['normalized_config_digest']}`",
        f"- Profiles: `{', '.join(payload['audit']['profiles'])}`",
    ]
    if command:
        metadata.extend(["", "Exact command:", "", "```bash", command, "```"])

    return "\n".join(
        [
            "# Fill Assumption Envelope",
            "",
            *metadata,
            "",
            "Public L2 cannot prove private fills. The profiles are assumption bounds, not private execution truth.",
            "Robust conclusions should survive conservative/base/aggressive. Conclusions that only work under aggressive assumptions are weak.",
            "",
            "The runner executes the same replay input and the same normalized simulation config three times; only the fill-assumption profile changes.",
            "",
            *table,
            "",
            "## Artifact Paths",
            "",
            *[
                f"- `{row['profile']}`: summary `{row['summary_path']}`, trades `{row['trades_path']}`, event trace `{row['event_trace_path']}`"
                for row in rows
            ],
            "",
        ]
    )


def run_envelope(input_file: Path, env_path: str, out_dir: Path, command: str | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base_cfg = load_config(env_path)
    rows = [_run_profile(input_file, base_cfg, profile, out_dir) for profile in FILL_ASSUMPTION_PROFILES]
    audit = _validate_envelope(rows)
    payload = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "input_file": input_file.as_posix(),
        "env_path": env_path,
        "profiles": list(FILL_ASSUMPTION_PROFILES),
        "audit": audit,
        "runs": rows,
    }
    if not audit["ok"]:
        raise RuntimeError("; ".join(audit["issues"]))

    json_path = out_dir / "fill_envelope_summary.json"
    csv_path = out_dir / "fill_envelope_summary.csv"
    report_path = out_dir / "fill_envelope_report.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    _write_csv(csv_path, rows)
    report_path.write_text(_render_report(payload, command=command), encoding="utf-8", newline="\n")
    payload["output_files"] = {
        "summary_json": json_path.as_posix(),
        "summary_csv": csv_path.as_posix(),
        "report": report_path.as_posix(),
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run conservative/base/aggressive public-L2 fill assumptions")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--out-dir", default="outputs/fill_envelope", help="Directory for envelope outputs")
    args = parser.parse_args()

    command = f"python experiments/run_fill_assumption_envelope.py --file {args.file} --env {args.env} --out-dir {args.out_dir}"
    payload = run_envelope(Path(args.file), args.env, Path(args.out_dir), command=command)
    print(f"Wrote fill-assumption envelope for {len(payload['runs'])} profiles")
    for label, path in payload["output_files"].items():
        print(f"- {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
