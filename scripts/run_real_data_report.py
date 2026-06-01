from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.benchmark_futures_replay import benchmark_reviewer_modes, write_benchmark_json
from lob_sim.config import load_config
from lob_sim.replay.inspection import inspect_stream
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import output_artifact_snapshot
from lob_sim.util import write_summary_csv
from scripts.audit_futures_pack import audit_futures_pack


LOCAL_ONLY_NOTE = (
    "local-only raw data: the replay input is not committed; publish the input SHA-256, report, "
    "and summary artifacts unless the raw file is small and shareable."
)


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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


def _render_report(
    *,
    input_path: Path,
    output_dir: Path,
    inspection: dict[str, Any],
    summary: dict[str, Any],
    audit_result: dict[str, Any],
    benchmark: dict[str, Any],
) -> str:
    lifecycle = summary.get("order_lifecycle_counts", {})
    benchmark_modes = benchmark.get("modes", {})
    return "\n".join(
        [
            "# Local Real Data Report",
            "",
            f"Input file: `{input_path.resolve()}`",
            f"Input SHA-256: `{inspection['sha256']}`",
            f"Raw-data policy: {LOCAL_ONLY_NOTE}",
            f"Output directory: `{output_dir.resolve()}`",
            "",
            "## Event Counts",
            "",
            f"- Records: `{inspection['records']}`",
            f"- Duration seconds: `{inspection.get('duration_seconds')}`",
            f"- Counts by type: `{_json_line(inspection['counts_by_type'])}`",
            f"- Counts by symbol: `{_json_line(inspection['counts_by_symbol'])}`",
            f"- Replay gaps: `{summary.get('event_counts', {}).get('book_gap_count', 0)}`",
            "",
            "## Fill Evidence",
            "",
            f"- Fill count: `{summary.get('fill_count')}`",
            f"- Quote count: `{summary.get('quote_count')}`",
            f"- Arrived orders: `{lifecycle.get('arrived')}`",
            f"- Quote-fill probability: `{summary.get('quote_fill_probability')}`",
            f"- Fills per quote request: `{summary.get('fills_per_quote_request')}`",
            f"- Fills per arrived order: `{summary.get('fills_per_arrived_order')}`",
            f"- Fill source mix: `{_json_line(summary.get('fill_source_counts', {}))}`",
            f"- Markout by source: `{_json_line(summary.get('markout_by_fill_source', {}))}`",
            "",
            "## Risk And Inventory",
            "",
            f"- Total PnL: `{summary.get('total_pnl')}`",
            f"- Max drawdown: `{summary.get('max_drawdown')}`",
            f"- Inventory stdev: `{summary.get('inventory_stdev')}`",
            f"- Inventory by symbol: `{_json_line(summary.get('inventory_by_symbol', {}))}`",
            "",
            "## Audit And Benchmark",
            "",
            f"- Pack audit ok: `{audit_result.get('ok')}`",
            f"- Audit issue count: `{len(audit_result.get('issues', []))}`",
            f"- Benchmark modes: `{', '.join(sorted(benchmark_modes))}`",
            f"- Replay events/sec: `{benchmark_modes.get('replay_only', {}).get('timing', {}).get('events_per_second')}`",
            f"- Simulation+export events/sec: `{benchmark_modes.get('simulation_with_event_trace_export', {}).get('timing', {}).get('events_per_second')}`",
            "",
            "## Limits",
            "",
            "- This report does not claim alpha, profitability, production latency, or private fill truth.",
            "- Passive fills are queue-aware public-data inferences over L2/aggTrade records.",
            "- Publish this report and hashes when the raw NDJSON capture is too large or not appropriate to commit.",
            "",
        ]
    )


def run_report(
    *,
    input_path: Path,
    env_path: str,
    out_dir: Path,
    label: str | None,
    runs: int,
) -> dict[str, Path]:
    input_path = input_path.resolve()
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
    audit_result = audit_futures_pack(pack_dir)
    benchmark = benchmark_reviewer_modes(input_path, env_path, runs=runs, pack_dir=pack_dir)

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
    report_json_path.write_text(
        json.dumps(
            {
                "local_only_raw_data": True,
                "raw_data_policy": LOCAL_ONLY_NOTE,
                "input": inspection,
                "pack_dir": _display_path(pack_dir),
                "audit": audit_result,
                "summary": audited_summary,
                "benchmark": benchmark,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report_md_path.write_text(
        _render_report(
            input_path=input_path,
            output_dir=output_dir,
            inspection=inspection,
            summary=audited_summary,
            audit_result=audit_result,
            benchmark=benchmark,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "output_dir": output_dir,
        "pack_dir": pack_dir,
        "report_md": report_md_path,
        "report_json": report_json_path,
        "inspection_json": inspection_path,
        "audit_json": audit_path,
        "benchmark_json": benchmark_path,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local-only real-data replay evidence report")
    parser.add_argument("--file", required=True, type=Path, help="Recorded NDJSON or NDJSON.GZ input")
    parser.add_argument("--env", default=".env.example", help="Config source for simulation and benchmark")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/real_data_runs"), help="Report output root")
    parser.add_argument("--label", help="Optional stable run label under --out-dir")
    parser.add_argument("--runs", type=int, default=1, help="Runs per non-replay benchmark mode")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = run_report(
        input_path=args.file,
        env_path=args.env,
        out_dir=args.out_dir,
        label=args.label,
        runs=max(1, args.runs),
    )
    print("Local real-data report generated:")
    for name, path in paths.items():
        print(f"- {name}: {_display_path(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
