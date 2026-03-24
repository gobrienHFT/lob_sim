from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from lob_sim.config import load_config
from lob_sim.sim.engine import SimulationEngine


COMPARISON_FIELDS: list[tuple[str, str]] = [
    ("quote_count", "quote_count"),
    ("cancel_count", "cancel_count"),
    ("fill_count", "fill_count"),
    ("fill_from_top_count", "fill_from_top_count"),
    ("avg_queue_ahead_lots", "avg_queue_ahead_lots"),
    ("avg_markout_1s", "avg_markout_1s"),
    ("inventory_stdev", "inventory_stdev"),
    ("realized_pnl", "realized_pnl"),
    ("unrealized_pnl", "unrealized_pnl"),
    ("kill_switch_triggered", "kill_switch_triggered"),
]


def _extract_comparison_metrics(summary: dict) -> dict:
    return {key: summary[key] for _label, key in COMPARISON_FIELDS} | {
        "strategy_profile": summary["strategy_profile"],
        "total_pnl": summary["total_pnl"],
        "fill_rate": summary["fill_rate"],
        "adverse_fill_rate_1s": summary["adverse_fill_rate_1s"],
    }


def _run_profile(path: Path, env_path: str, profile: str) -> dict:
    cfg = replace(load_config(env_path), mm_strategy_profile=profile)
    engine = SimulationEngine(cfg)
    metrics = engine.run(path)
    return metrics.get_summary(engine._books)


def compare_profiles(path: Path, env_path: str, candidate_profile: str) -> dict:
    baseline = _run_profile(path, env_path, "baseline")
    candidate = _run_profile(path, env_path, candidate_profile)
    return {
        "input_file": str(path),
        "baseline_profile": "baseline",
        "candidate_profile": candidate_profile,
        "baseline": _extract_comparison_metrics(baseline),
        "candidate": _extract_comparison_metrics(candidate),
    }


def _print_markdown_table(result: dict) -> None:
    print("| Metric | Baseline | Candidate |")
    print("|---|---:|---:|")
    baseline = result["baseline"]
    candidate = result["candidate"]
    for label, key in COMPARISON_FIELDS:
        print(f"| {label} | {baseline[key]} | {candidate[key]} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare baseline and layered futures strategy profiles on one replay input")
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument(
        "--candidate-profile",
        default="layered_mm",
        choices=("layered_mm",),
        help="Optional stronger profile to compare against the baseline",
    )
    args = parser.parse_args()

    result = compare_profiles(Path(args.file), args.env, args.candidate_profile)
    print(f"Input file: {result['input_file']}")
    print(f"Baseline profile: {result['baseline_profile']}")
    print(f"Candidate profile: {result['candidate_profile']}")
    print()
    _print_markdown_table(result)
    print()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
