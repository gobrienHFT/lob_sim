from __future__ import annotations

import argparse
import json
import platform
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import (
    config_digest,
    config_snapshot,
    instrument_specs_snapshot,
    source_state,
)


DETERMINISM_SCHEMA_VERSION = "lob_sim.futures_determinism.v1"
COMPARED_SURFACES = ("metrics_summary", "event_trace")


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


def _trace_event_counts(event_trace: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("event_type", "<missing>")) for row in event_trace)
    return dict(sorted(counts.items()))


def _run_once(
    input_file: Path,
    cfg: Any,
    adapter: ReplayFeedAdapter,
) -> dict[str, Any]:
    engine = SimulationEngine(cfg, adapter=adapter)
    metrics = engine.run(input_file)
    summary = metrics.get_summary(engine._books)
    event_trace = list(engine.event_trace)
    return {
        "summary": summary,
        "event_trace": event_trace,
        "summary_sha256": canonical_sha256(summary),
        "event_trace_sha256": canonical_sha256(event_trace),
        "instrument_specs": instrument_specs_snapshot(engine._specs),
    }


def _run_report(index: int, run: dict[str, Any]) -> dict[str, Any]:
    summary = run["summary"]
    event_trace = run["event_trace"]
    markout_events = summary.get("markout_events", [])
    return {
        "run_index": index,
        "summary_sha256": run["summary_sha256"],
        "event_trace_sha256": run["event_trace_sha256"],
        "fill_count": summary.get("fill_count", 0),
        "quote_count": summary.get("quote_count", 0),
        "event_trace_count": len(event_trace),
        "markout_event_count": len(markout_events) if isinstance(markout_events, list) else 0,
        "fill_source_counts": summary.get("fill_source_counts", {}),
        "order_lifecycle_counts": summary.get("order_lifecycle_counts", {}),
        "event_trace_type_counts": _trace_event_counts(event_trace),
    }


def check_determinism(
    input_file: Path,
    env_path: str,
    *,
    runs: int = 2,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> dict[str, Any]:
    if runs < 2:
        raise ValueError("runs must be at least 2 so determinism can be compared")

    cfg = load_config(env_path)
    cfg_snapshot = config_snapshot(cfg)
    run_results = [_run_once(input_file, cfg, adapter) for _ in range(runs)]
    baseline = run_results[0]
    per_run = [_run_report(index, result) for index, result in enumerate(run_results, start=1)]

    mismatches: list[dict[str, Any]] = []
    for report in per_run[1:]:
        summary_matches = report["summary_sha256"] == baseline["summary_sha256"]
        event_trace_matches = report["event_trace_sha256"] == baseline["event_trace_sha256"]
        if not summary_matches or not event_trace_matches:
            mismatches.append(
                {
                    "run_index": report["run_index"],
                    "summary_matches": summary_matches,
                    "event_trace_matches": event_trace_matches,
                    "summary_sha256": report["summary_sha256"],
                    "event_trace_sha256": report["event_trace_sha256"],
                }
            )

    deterministic = not mismatches
    return {
        "schema_version": DETERMINISM_SCHEMA_VERSION,
        "input_file": input_file.as_posix(),
        "input_sha256": file_sha256(input_file),
        "config": cfg_snapshot,
        "config_digest": config_digest(cfg_snapshot),
        "feed_adapter": adapter_metadata(adapter),
        "instrument_specs": baseline.get("instrument_specs", {}),
        "runtime": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "source": source_state(),
        "compared_surfaces": list(COMPARED_SURFACES),
        "runs": runs,
        "deterministic": deterministic,
        "baseline": {
            "summary_sha256": baseline["summary_sha256"],
            "event_trace_sha256": baseline["event_trace_sha256"],
            "fill_count": per_run[0]["fill_count"],
            "quote_count": per_run[0]["quote_count"],
            "event_trace_count": per_run[0]["event_trace_count"],
            "markout_event_count": per_run[0]["markout_event_count"],
        },
        "per_run": per_run,
        "mismatches": mismatches,
    }


def write_determinism_json(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rerun a futures replay fixture and prove summary/event-trace determinism by hash."
    )
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument("--runs", type=int, default=2, help="Number of repeated in-memory simulation runs")
    parser.add_argument("--json-out", help="Optional path for a machine-readable determinism report")
    args = parser.parse_args()

    result = check_determinism(Path(args.file), args.env, runs=args.runs)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.json_out:
        write_determinism_json(result, Path(args.json_out))
    return 0 if result["deterministic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
