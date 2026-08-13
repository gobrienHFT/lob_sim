from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER, adapter_metadata
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.protocol import ResearchRegistry
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_digest, config_snapshot, source_state


COMPARISON_FIELDS: list[tuple[str, str]] = [
    ("quote_count", "quote_count"),
    ("cancel_count", "cancel_count"),
    ("fill_count", "fill_count"),
    ("quote_fill_probability", "quote_fill_probability"),
    ("fills_per_quote_request", "fills_per_quote_request"),
    ("fills_per_arrived_order", "fills_per_arrived_order"),
    ("fill_from_top_count", "fill_from_top_count"),
    ("avg_queue_ahead_lots", "avg_queue_ahead_lots"),
    ("avg_arrival_queue_ahead_lots", "avg_arrival_queue_ahead_lots"),
    ("max_arrival_queue_ahead_lots", "max_arrival_queue_ahead_lots"),
    ("avg_markout_1s", "avg_markout_1s"),
    ("inventory_stdev", "inventory_stdev"),
    ("realized_pnl", "realized_pnl"),
    ("unrealized_pnl", "unrealized_pnl"),
    ("kill_switch_triggered", "kill_switch_triggered"),
]

PROFILE_COMPARISON_STUDY = "futures_strategy_profile_comparison"
PROFILE_COMPARISON_REGISTRY_SCHEMA = "lob_sim.futures_strategy_profile_registry.v1"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _portable_path(path: Path) -> str:
    """Keep committed provenance portable across checkouts and operating systems."""

    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _extract_comparison_metrics(summary: dict) -> dict:
    return {key: summary[key] for _label, key in COMPARISON_FIELDS} | {
        "strategy_profile": summary["strategy_profile"],
        "total_pnl": summary["total_pnl"],
        "quote_fill_probability": summary["quote_fill_probability"],
        "fills_per_quote_request": summary["fills_per_quote_request"],
        "fills_per_arrived_order": summary["fills_per_arrived_order"],
        "adverse_fill_rate_1s": summary["adverse_fill_rate_1s"],
        "integrity": summary.get("integrity", {}),
        "evidence_quality": summary.get("evidence_quality", {}),
    }


def _build_profile_registry(
    base_config: Any,
    candidate_profile: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Register both comparison profiles before either simulation starts."""

    registry = ResearchRegistry()
    variant_ids: dict[str, str] = {}
    for profile in ("baseline", candidate_profile):
        cfg = replace(base_config, mm_strategy_profile=profile)
        variant_ids[profile] = registry.register(
            f"profile:{profile}",
            {
                "study": PROFILE_COMPARISON_STUDY,
                "strategy_profile": profile,
                "normalized_config": config_snapshot(cfg),
            },
        )
    return variant_ids, registry.freeze()


def _run_profile(path: Path, env_path: str, profile: str, *, base_config: Any | None = None) -> dict:
    cfg = replace(base_config or load_config(env_path), mm_strategy_profile=profile)
    engine = SimulationEngine(cfg)
    metrics = engine.run(path)
    summary = metrics.get_summary(engine._books)
    # The profile comparison is a compact research report, but it must carry
    # the same validity boundary as a full simulation artifact.  In particular,
    # legacy clips can produce useful diagnostics while remaining non-claim-ready.
    annotations = engine._summary_annotations()
    summary.update({"integrity": annotations["integrity"], "evidence_quality": annotations["evidence_quality"]})
    return summary


def compare_profiles(path: Path, env_path: str, candidate_profile: str) -> dict:
    base_config = load_config(env_path)
    variant_ids, registry_snapshot = _build_profile_registry(base_config, candidate_profile)
    baseline = _extract_comparison_metrics(_run_profile(path, env_path, "baseline", base_config=base_config))
    candidate = _extract_comparison_metrics(_run_profile(path, env_path, candidate_profile, base_config=base_config))
    baseline["registry_variant_id"] = variant_ids["baseline"]
    candidate["registry_variant_id"] = variant_ids[candidate_profile]
    result = {
        "input_file": _portable_path(path),
        "input_sha256": file_sha256(path),
        "env_path": _portable_path(Path(env_path)),
        "config_digest": config_digest(config_snapshot(base_config)),
        "feed_adapter": adapter_metadata(DEFAULT_REPLAY_ADAPTER),
        "source": source_state(),
        "baseline_profile": "baseline",
        "candidate_profile": candidate_profile,
        "baseline": baseline,
        "candidate": candidate,
        "research_registry": registry_snapshot,
    }
    _validate_profile_registry_result(result)
    return result


def _validate_profile_registry_result(result: dict[str, Any]) -> None:
    registry = result.get("research_registry")
    if not isinstance(registry, dict) or registry.get("frozen") is not True:
        raise ValueError("strategy profile comparison requires a frozen research registry")
    variants = registry.get("variants")
    if not isinstance(variants, list) or len(variants) != 2:
        raise ValueError("strategy profile comparison registry must contain exactly two variants")
    by_id = {
        str(variant.get("variant_id")): variant
        for variant in variants
        if isinstance(variant, dict) and variant.get("variant_id")
    }
    rows = (result.get("baseline"), result.get("candidate"))
    row_ids = [str(row.get("registry_variant_id", "")) for row in rows if isinstance(row, dict)]
    if len(row_ids) != 2 or len(set(row_ids)) != 2 or set(row_ids) != set(by_id):
        raise ValueError("strategy profile comparison rows do not bind one-to-one to the frozen registry")
    for row, expected_profile in zip(rows, ("baseline", str(result["candidate_profile"]))):
        if not isinstance(row, dict):
            raise ValueError("strategy profile comparison row is not an object")
        variant = by_id.get(str(row.get("registry_variant_id")))
        config = variant.get("config", {}) if isinstance(variant, dict) else {}
        if not isinstance(config, dict) or config.get("strategy_profile") != expected_profile:
            raise ValueError("strategy profile comparison row is not bound to its registered configuration")


def build_profile_registry_sidecar(result: dict[str, Any]) -> dict[str, Any]:
    """Return the machine-readable provenance for a profile comparison."""

    _validate_profile_registry_result(result)
    rows = [
        {
            "strategy_profile": result["baseline_profile"],
            "registry_variant_id": result["baseline"]["registry_variant_id"],
            "claim_ready": result["baseline"]["integrity"].get("claim_ready"),
            "markout_evidence": result["baseline"]["evidence_quality"].get("markouts"),
        },
        {
            "strategy_profile": result["candidate_profile"],
            "registry_variant_id": result["candidate"]["registry_variant_id"],
            "claim_ready": result["candidate"]["integrity"].get("claim_ready"),
            "markout_evidence": result["candidate"]["evidence_quality"].get("markouts"),
        },
    ]
    return {
        "schema_version": PROFILE_COMPARISON_REGISTRY_SCHEMA,
        "study_type": PROFILE_COMPARISON_STUDY,
        "input_file": result["input_file"],
        "input_sha256": result["input_sha256"],
        "env_path": result["env_path"],
        "config_digest": result["config_digest"],
        "feed_adapter": result["feed_adapter"],
        "source": result["source"],
        "research_registry": result["research_registry"],
        "rows": rows,
        "row_registry_variant_ids": [row["registry_variant_id"] for row in rows],
    }


def _print_markdown_table(result: dict) -> None:
    print("| Metric | Baseline | Candidate |")
    print("|---|---:|---:|")
    baseline = result["baseline"]
    candidate = result["candidate"]
    for label, key in COMPARISON_FIELDS:
        print(f"| {label} | {baseline[key]} | {candidate[key]} |")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare baseline and opt-in futures strategy profiles on one replay input"
    )
    parser.add_argument("--file", required=True, help="Path to NDJSON or NDJSON.GZ replay file")
    parser.add_argument("--env", default=".env.example", help="Config source for replay parameters")
    parser.add_argument(
        "--candidate-profile",
        default="research_mm",
        choices=("layered_mm", "research_mm"),
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
