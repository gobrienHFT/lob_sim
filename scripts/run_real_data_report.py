from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.benchmark_futures_replay import benchmark_reviewer_modes, write_benchmark_json
from lob_sim.config import load_config
from lob_sim.replay.inspection import inspect_stream
from lob_sim.replay.reader import iter_records
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import output_artifact_snapshot
from lob_sim.util import write_summary_csv
from scripts.audit_futures_pack import audit_futures_pack


REAL_DATA_REPORT_SCHEMA_VERSION = "lob_sim.real_data_report.v1"
RAW_DATA_POLICY = "local-only raw data; raw NDJSON is not committed"
LOCAL_ONLY_NOTE = (
    f"{RAW_DATA_POLICY}; publish the input SHA-256, report, and summary artifacts unless the raw file "
    "is small and shareable."
)
TARGET_MIN_DURATION_SECONDS = 10 * 60
TARGET_MAX_DURATION_SECONDS = 30 * 60
SUPPORTED_INPUT_SUFFIXES = (".ndjson", ".ndjson.gz")


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, previous_value in previous.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


def _safe_label(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return cleaned or "real_data_run"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _json_line(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_input_path(input_path: Path) -> Path:
    resolved = input_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Real-data input does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Real-data input must be a file, got: {resolved}")
    if not any(resolved.name.endswith(suffix) for suffix in SUPPORTED_INPUT_SUFFIXES):
        raise ValueError("Real-data input must be an NDJSON or NDJSON.GZ file")
    return resolved


def _validate_work_output_dir(out_dir: Path) -> Path:
    resolved = out_dir.resolve()
    docs_dir = (REPO_ROOT / "docs").resolve()
    if _is_relative_to(resolved, docs_dir):
        raise ValueError(
            "--out-dir writes local audit packs, traces, and CSVs; keep it outside docs/. "
            "Use --publish-dir docs/real_data_runs for committed report-only artifacts."
        )
    return resolved


def _validate_publish_dir(publish_dir: Path) -> Path:
    resolved = publish_dir.resolve()
    docs_dir = (REPO_ROOT / "docs").resolve()
    real_runs_dir = (REPO_ROOT / "docs" / "real_data_runs").resolve()
    if _is_relative_to(resolved, docs_dir) and not _is_relative_to(resolved, real_runs_dir):
        raise ValueError("Committed real-data reports belong under docs/real_data_runs")
    return resolved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _copy_to_local_pack(
    *,
    input_path: Path,
    generated_paths: dict[str, Path],
    summary: dict[str, Any],
    output_dir: Path,
) -> Path:
    pack_dir = output_dir / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    committed_paths = {
        "event_trace": pack_dir / "event_trace.csv",
        "summary": pack_dir / "summary.json",
        "summary_csv": pack_dir / "summary.csv",
        "trades": pack_dir / "trades.csv",
        "manifest": pack_dir / "manifest.json",
    }

    shutil.copyfile(generated_paths["trades"], committed_paths["trades"])
    shutil.copyfile(generated_paths["event_trace"], committed_paths["event_trace"])

    provenance = {
        "data_class": "recorded_public_data",
        "source": "local_recorded_public_data",
        "purpose": "larger local real-tape replay evidence without committing raw capture data",
        "script": "scripts/run_real_data_report.py",
        "raw_data_policy": LOCAL_ONLY_NOTE,
    }
    summary = dict(summary)
    summary["fixture_provenance"] = provenance
    summary["output_files"] = {name: _display_path(path) for name, path in committed_paths.items()}
    committed_paths["summary"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_summary_csv(committed_paths["summary_csv"], summary, exclude_keys={"fills", "markout_events"})

    manifest = json.loads(generated_paths["manifest"].read_text(encoding="utf-8"))
    manifest["input"]["path"] = str(input_path.resolve())
    manifest["outputs"] = dict(summary["output_files"])
    manifest["fixture_provenance"] = provenance
    manifest["output_artifacts"] = output_artifact_snapshot(committed_paths, path_formatter=_display_path)
    committed_paths["manifest"].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")

    (pack_dir / "README.md").write_text(
        "\n".join(
            [
                "# Local Real Data Pack",
                "",
                "This pack is generated from recorded public-data input on the local machine.",
                LOCAL_ONLY_NOTE,
                "",
                "The raw NDJSON file is intentionally not copied into the repository output pack.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    (pack_dir / "case_notes.md").write_text(
        "\n".join(
            [
                "# Local Real Data Case Notes",
                "",
                f"- Input path: `{input_path.resolve()}`",
                f"- Input SHA-256: `{summary['input_sha256']}`",
                f"- Policy: {LOCAL_ONLY_NOTE}",
                "- Data class: recorded public-data replay; this is not synthetic.",
                "- Limitation: fills remain public L2/aggTrade queue-inference events, not private exchange execution reports.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return pack_dir


def _benchmark_mode_context(benchmark: dict[str, Any], mode_name: str) -> dict[str, Any]:
    mode = benchmark.get("modes", {}).get(mode_name, {})
    if not isinstance(mode, dict):
        return {}
    timing = mode.get("timing", {})
    memory = mode.get("memory", {})
    if not isinstance(timing, dict):
        timing = {}
    if not isinstance(memory, dict):
        memory = {}
    return {
        "wall_time_seconds": timing.get("wall_time_seconds"),
        "events_per_second": timing.get("events_per_second"),
        "wall_time_p50_seconds": timing.get("wall_time_p50_seconds"),
        "wall_time_p99_seconds": timing.get("wall_time_p99_seconds"),
        "loop_latency_p50_us": timing.get("loop_latency_p50_us"),
        "loop_latency_p99_us": timing.get("loop_latency_p99_us"),
        "peak_traced_mib": memory.get("peak_traced_mib"),
    }


def _longer_run_commands(symbol: str) -> list[str]:
    return [
        "copy .env.example .env.real-data",
        "python -m lob_sim.cli --env .env.real-data collect",
        (
            "python scripts/run_real_data_report.py --file data/raw_....ndjson.gz --env .env.real-data "
            f"--label {symbol}_30m --publish-dir docs/real_data_runs"
        ),
    ]


def _longer_run_env(symbol: str) -> dict[str, str]:
    return {
        "SYMBOLS": symbol,
        "COLLECT_SECONDS": "1800",
        "RECORD_DIR": "data",
        "RECORD_GZIP": "1",
        "TRADE_STREAM_SUFFIX": "@trade",
        "RESYNC_ON_GAP": "1",
    }


def _public_trade_source_counts(input_path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in iter_records(input_path):
        if record.type == "aggTrade":
            counts[str(record.data.get("e") or "unknown")] += 1
    return dict(sorted(counts.items()))


def _build_report_payload(
    *,
    input_path: Path,
    output_dir: Path,
    pack_dir: Path,
    inspection: dict[str, Any],
    summary: dict[str, Any],
    manifest: dict[str, Any],
    audit_result: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    lifecycle = summary.get("order_lifecycle_counts", {})
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    event_counts = dict(summary.get("event_counts", {}))
    event_counts["book_gap_count_by_symbol"] = summary.get("book_gap_count_by_symbol", {})
    trade_source_counts = _public_trade_source_counts(input_path)
    symbols = inspection.get("symbols") or sorted(summary.get("instrument_specs", {}))
    symbol = symbols[0] if len(symbols) == 1 else ",".join(symbols)
    duration_seconds = inspection.get("duration_seconds")
    meets_target = (
        isinstance(duration_seconds, (int, float))
        and TARGET_MIN_DURATION_SECONDS <= float(duration_seconds) <= TARGET_MAX_DURATION_SECONDS
    )
    audit_counts = audit_result.get("counts", {})
    if not isinstance(audit_counts, dict):
        audit_counts = {}
    benchmark_metadata = benchmark.get("metadata", {})
    if not isinstance(benchmark_metadata, dict):
        benchmark_metadata = {}
    source = (
        manifest.get("source") if isinstance(manifest.get("source"), dict) else benchmark_metadata.get("source", {})
    )

    return {
        "schema_version": REAL_DATA_REPORT_SCHEMA_VERSION,
        "generated_at_utc": _utc_now(),
        "raw_data_policy": RAW_DATA_POLICY,
        "input": {
            "label": _display_path(input_path),
            "sha256": inspection["sha256"],
            "file_size_bytes": inspection["file_size_bytes"],
            "symbol": symbol,
            "symbols": symbols,
            "duration_seconds": duration_seconds,
            "first_ts_local": inspection.get("first_ts_local"),
            "last_ts_local": inspection.get("last_ts_local"),
        },
        "source": source,
        "local_artifacts": {
            "output_dir": _display_path(output_dir),
            "pack_dir": _display_path(pack_dir),
            "report_only_docs_safe": True,
        },
        "target_window": {
            "requested": "10-30 minutes",
            "min_duration_seconds": TARGET_MIN_DURATION_SECONDS,
            "max_duration_seconds": TARGET_MAX_DURATION_SECONDS,
            "observed_duration_seconds": duration_seconds,
            "meets_target": meets_target,
            "label": "target-window public tape" if meets_target else "short local public tape",
            "env_overrides": {} if meets_target else _longer_run_env(str(symbol or "BTCUSDT")),
            "longer_run_commands": [] if meets_target else _longer_run_commands(str(symbol or "BTCUSDT")),
        },
        "event_counts": event_counts,
        "public_trade_source_counts": trade_source_counts,
        "fills": {
            "fill_count": summary.get("fill_count"),
            "quote_count": summary.get("quote_count"),
            "cancel_count": summary.get("cancel_count"),
            "arrived_orders": lifecycle.get("arrived"),
            "quote_fill_probability": summary.get("quote_fill_probability"),
            "fills_per_quote_request": summary.get("fills_per_quote_request"),
            "fills_per_arrived_order": summary.get("fills_per_arrived_order"),
            "fill_from_top_rate": summary.get("fill_from_top_rate"),
            "avg_fill_wait_ms": summary.get("avg_fill_wait_ms"),
            "fill_source_counts": summary.get("fill_source_counts", {}),
        },
        "markout_by_fill_source": summary.get("markout_by_fill_source", {}),
        "risk": {
            "total_pnl": summary.get("total_pnl"),
            "realized_pnl": summary.get("realized_pnl"),
            "unrealized_pnl": summary.get("unrealized_pnl"),
            "max_drawdown": summary.get("max_drawdown"),
            "avg_inventory": summary.get("avg_inventory"),
            "inventory_stdev": summary.get("inventory_stdev"),
            "inventory_by_symbol": summary.get("inventory_by_symbol", {}),
            "self_trade_prevention_count": summary.get("self_trade_prevention_count"),
        },
        "audit": {
            "ok": audit_result.get("ok"),
            "issue_count": len(audit_result.get("issues", [])),
            "event_trace_rows": audit_counts.get("event_trace_rows", summary.get("event_trace_count")),
            "queue_consumption_rows": audit_counts.get("queue_consumption_rows"),
        },
        "benchmark": {
            "schema_version": benchmark.get("schema_version"),
            "config_digest": benchmark_metadata.get("config_digest"),
            "feed_adapter": benchmark_metadata.get("feed_adapter"),
            "python_version": benchmark_metadata.get("python_version"),
            "platform": benchmark_metadata.get("platform"),
            "replay_only": _benchmark_mode_context(benchmark, "replay_only"),
            "simulation_no_export": _benchmark_mode_context(benchmark, "simulation_no_export"),
            "simulation_with_streaming_audit_export": _benchmark_mode_context(
                benchmark, "simulation_with_streaming_audit_export"
            ),
            "pack_audit": _benchmark_mode_context(benchmark, "pack_audit"),
        },
        "interpretation": [
            (
                "Negative or positive PnL is not the point of this artifact; the value is deterministic "
                "public-L2 replay, queue-aware fill evidence, fill-source attribution, event-time auditability, "
                "and benchmark context."
            ),
            "Passive fills are public-data queue inferences, not private exchange execution reports.",
            (
                "Replay public trade-print records use the simulator's aggTrade-compatible schema; "
                f"raw Binance event types observed inside those records are {_json_line(trade_source_counts)}."
            ),
        ],
        "limits": [
            "public_l2_not_private_execution_reports",
            "passive_fills_are_public_data_queue_inferences",
            "not_alpha_or_profitability_claim",
            "not_production_latency_claim",
            "not_gateway_readiness_claim",
        ],
    }


def _render_report(payload: dict[str, Any]) -> str:
    input_meta = payload["input"]
    target = payload["target_window"]
    fills = payload["fills"]
    risk = payload["risk"]
    audit = payload["audit"]
    benchmark = payload["benchmark"]
    event_counts = payload["event_counts"]
    trade_source_counts = payload.get("public_trade_source_counts", {})
    markouts = payload["markout_by_fill_source"]
    markout_rows = [
        "| Source | Samples | Adverse Samples | Average Markout 1s | Adverse Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for source in ("depth_update", "agg_trade", "taker_order"):
        stats = markouts.get(source, {}) if isinstance(markouts, dict) else {}
        markout_rows.append(
            "| `{source}` | {samples} | {adverse_samples} | {avg_markout_1s} | {adverse_fill_rate_1s} |".format(
                source=source,
                samples=stats.get("samples", 0),
                adverse_samples=stats.get("adverse_samples", 0),
                avg_markout_1s=stats.get("avg_markout_1s", 0),
                adverse_fill_rate_1s=stats.get("adverse_fill_rate_1s", 0),
            )
        )
    command_lines = [f"```bash\n{command}\n```" for command in target.get("longer_run_commands", [])]
    env_overrides = target.get("env_overrides", {})
    env_block = []
    if isinstance(env_overrides, dict) and env_overrides:
        env_block = [
            "Set these values in `.env.real-data` before collecting:",
            "",
            "```dotenv",
            *[f"{key}={value}" for key, value in env_overrides.items()],
            "```",
            "",
        ]
    return "\n".join(
        [
            f"# {input_meta.get('symbol') or 'Local'} Public-Data Report",
            "",
            "This is a committed report-only artifact for a local public-data replay. The raw NDJSON tape is not committed.",
            "",
            "## Input",
            "",
            f"- Local raw file label: `{input_meta.get('label')}`",
            f"- Raw-data policy: {payload['raw_data_policy']}",
            f"- Input SHA-256: `{input_meta.get('sha256')}`",
            f"- File size: `{input_meta.get('file_size_bytes')}` bytes",
            f"- Symbol: `{input_meta.get('symbol')}`",
            f"- Duration seconds: `{input_meta.get('duration_seconds')}`",
            f"- Target window: `{target.get('requested')}`; observed label: `{target.get('label')}`",
            f"- Meets 10-30 minute target: `{str(target.get('meets_target')).lower()}`",
            f"- Source state: `{_json_line(payload.get('source', {}))}`",
            "",
            "## Event Counts",
            "",
            f"- Records processed: `{event_counts.get('records_processed')}`",
            f"- `exchangeInfo`: `{event_counts.get('exchange_info')}`",
            f"- `snapshot`: `{event_counts.get('snapshot')}`",
            f"- `depthUpdate`: `{event_counts.get('depth_update')}`",
            f"- `aggTrade`: `{event_counts.get('agg_trade')}`",
            f"- Raw public trade event types inside `aggTrade` records: `{_json_line(trade_source_counts)}`",
            f"- Depth changes applied: `{event_counts.get('depth_changes_applied')}`",
            f"- Book gaps: `{event_counts.get('book_gap_count')}`",
            f"- Gap count by symbol: `{_json_line(event_counts.get('book_gap_count_by_symbol', {}))}`",
            "",
            "## Fill Evidence",
            "",
            f"- Fill count: `{fills.get('fill_count')}`",
            f"- Quote count: `{fills.get('quote_count')}`",
            f"- Cancel count: `{fills.get('cancel_count')}`",
            f"- Arrived orders: `{fills.get('arrived_orders')}`",
            f"- Quote-fill probability: `{fills.get('quote_fill_probability')}`",
            f"- Fills per quote request: `{fills.get('fills_per_quote_request')}`",
            f"- Fills per arrived order: `{fills.get('fills_per_arrived_order')}`",
            f"- Fill-source mix: `{_json_line(fills.get('fill_source_counts', {}))}`",
            f"- Fill-from-top rate: `{fills.get('fill_from_top_rate')}`",
            f"- Average fill wait ms: `{fills.get('avg_fill_wait_ms')}`",
            "",
            "## Markouts",
            "",
            *markout_rows,
            "",
            "## Inventory And Drawdown",
            "",
            f"- Total PnL: `{risk.get('total_pnl')}`",
            f"- Realized PnL: `{risk.get('realized_pnl')}`",
            f"- Unrealized PnL: `{risk.get('unrealized_pnl')}`",
            f"- Max drawdown: `{risk.get('max_drawdown')}`",
            f"- Average inventory: `{risk.get('avg_inventory')}`",
            f"- Inventory stdev: `{risk.get('inventory_stdev')}`",
            f"- Final inventory: `{_json_line(risk.get('inventory_by_symbol', {}))}`",
            f"- Self-trade prevention count: `{risk.get('self_trade_prevention_count')}`",
            "",
            "## Audit And Benchmark",
            "",
            f"- Local pack audit ok: `{str(audit.get('ok')).lower()}`",
            f"- Audit issue count: `{audit.get('issue_count')}`",
            f"- Event trace rows audited locally: `{audit.get('event_trace_rows')}`",
            f"- Queue-consumption rows audited locally: `{audit.get('queue_consumption_rows')}`",
            f"- Replay-only wall time seconds: `{benchmark['replay_only'].get('wall_time_seconds')}`",
            f"- Replay-only events/sec: `{benchmark['replay_only'].get('events_per_second')}`",
            f"- Replay-only p50 loop latency us: `{benchmark['replay_only'].get('loop_latency_p50_us')}`",
            f"- Replay-only p99 loop latency us: `{benchmark['replay_only'].get('loop_latency_p99_us')}`",
            f"- Simulation without export events/sec: `{benchmark['simulation_no_export'].get('events_per_second')}`",
            (
                "- Simulation with event-trace export events/sec: "
                f"`{benchmark['simulation_with_streaming_audit_export'].get('events_per_second')}`"
            ),
            (
                "- Simulation with event-trace export peak traced MiB: "
                f"`{benchmark['simulation_with_streaming_audit_export'].get('peak_traced_mib')}`"
            ),
            f"- Runtime: Python `{benchmark.get('python_version')}`, platform `{benchmark.get('platform')}`",
            "",
            "## Plain Interpretation",
            "",
            *[f"- {line}" for line in payload["interpretation"]],
            "",
            "## Longer Target Run",
            "",
            (
                "The available local tape is below the requested 10-30 minute window. Use these exact commands "
                "to create and publish a longer report-only artifact:"
                if not target.get("meets_target")
                else "This run meets the requested 10-30 minute window."
            ),
            "",
            *env_block,
            *command_lines,
            "",
            "## Limits",
            "",
            "- This report does not claim alpha, profitability, production latency, or private fill truth.",
            "- Passive fills are queue-aware public-data inferences over L2/aggTrade records.",
            "- It is not a production gateway-readiness claim.",
            "",
        ]
    )


def _write_report_pair(payload: dict[str, Any], *, markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_render_report(payload), encoding="utf-8", newline="\n")


def run_report(
    *,
    input_path: Path,
    env_path: str,
    out_dir: Path,
    label: str | None,
    runs: int,
    publish_dir: Path | None = None,
) -> dict[str, Path]:
    input_path = _validate_input_path(input_path)
    out_dir = _validate_work_output_dir(out_dir)
    publish_dir = _validate_publish_dir(publish_dir) if publish_dir is not None else None
    inspection = inspect_stream(input_path).as_dict()
    run_label = _safe_label(label or f"{input_path.stem}_{inspection['sha256'][:12]}")
    output_dir = (out_dir / run_label).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with _temporary_env({"RECORD_DIR": str(output_dir / "record_dir")}):
        cfg = load_config(env_path)
        engine = SimulationEngine(cfg)
        metrics = engine.run(input_path)
        generated_paths, summary = engine.write_outputs(str(input_path), metrics)

    pack_dir = _copy_to_local_pack(
        input_path=input_path,
        generated_paths=generated_paths,
        summary=summary,
        output_dir=output_dir,
    )
    audited_summary = json.loads((pack_dir / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    audit_result = audit_futures_pack(pack_dir)
    benchmark = benchmark_reviewer_modes(input_path, env_path, runs=runs, pack_dir=pack_dir)
    payload = _build_report_payload(
        input_path=input_path,
        output_dir=output_dir,
        pack_dir=pack_dir,
        inspection=inspection,
        summary=audited_summary,
        manifest=manifest,
        audit_result=audit_result,
        benchmark=benchmark,
    )

    inspection_path = output_dir / "inspection.json"
    report_json_path = output_dir / "local_real_data_report.json"
    benchmark_path = output_dir / "benchmark.json"
    audit_path = output_dir / "audit.json"
    report_md_path = output_dir / "local_real_data_report.md"

    inspection_path.write_text(json.dumps(inspection, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    audit_path.write_text(
        json.dumps(audit_result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    write_benchmark_json(benchmark, benchmark_path)
    _write_report_pair(payload, markdown_path=report_md_path, json_path=report_json_path)

    paths = {
        "output_dir": output_dir,
        "pack_dir": pack_dir,
        "report_md": report_md_path,
        "report_json": report_json_path,
        "inspection_json": inspection_path,
        "audit_json": audit_path,
        "benchmark_json": benchmark_path,
    }
    if publish_dir is not None:
        published_md_path = publish_dir / f"{run_label}.md"
        published_json_path = publish_dir / f"{run_label}.json"
        _write_report_pair(payload, markdown_path=published_md_path, json_path=published_json_path)
        paths["published_report_md"] = published_md_path
        paths["published_report_json"] = published_json_path
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local-only real-data replay evidence report")
    parser.add_argument("--file", required=True, type=Path, help="Recorded NDJSON or NDJSON.GZ input")
    parser.add_argument("--env", default=".env.example", help="Config source for simulation and benchmark")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/real_data_runs"), help="Report output root")
    parser.add_argument("--label", help="Optional stable run label under --out-dir")
    parser.add_argument("--runs", type=int, default=1, help="Runs per non-replay benchmark mode")
    parser.add_argument(
        "--publish-dir",
        type=Path,
        help="Optional report-only destination, usually docs/real_data_runs; writes only <label>.md and <label>.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = run_report(
        input_path=args.file,
        env_path=args.env,
        out_dir=args.out_dir,
        label=args.label,
        runs=max(1, args.runs),
        publish_dir=args.publish_dir,
    )
    print("Local real-data report generated:")
    for name, path in paths.items():
        print(f"- {name}: {_display_path(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
