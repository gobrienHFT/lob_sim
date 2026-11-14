"""Independently reopen the entire serialized held-out evidence graph.

Does not call the study runner or matching engine. Native auditors reconstruct
accounting/statistic inputs from files; paired inference uses the separately
tested clock bootstrap. This is not provenance authentication or fill truth.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.validation import identity, require_keys, strict_json, canonical_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json, require_finalized, STUDY_JSON_LIMIT
from lob_sim.research.feature_reader import admitted_parents, verify_prepared
from lob_sim.research.fit_bundle import load_model_set, verify_registered_models
from lob_sim.research.registered_protocol import load_protocol
from lob_sim.research.study_cases import expected_cases, case_configuration
from lob_sim.research.study_run_reader import verify_case
from lob_sim.research.study_statistics import compare_units
from lob_sim.research.study_tables import iter_tables
from lob_sim.research.study_views import load_views, verify_views


def verify_registered_study(
    root: Path,
    inputs: tuple[Path, ...],
    day_bundle: Path,
    registry_path: Path,
    prepared_root: Path,
    model_root: Path,
    view_root: Path,
    cfg: Config,
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    protocol = load_protocol(registry_path, registry_sha256=registry_sha256)  # Before held-out/artifact IO.
    registered = protocol.snapshot()
    feature_proof = verify_prepared(prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    model_proof = verify_registered_models(
        model_root, prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256
    )
    models = load_model_set(model_root, protocol, registry_sha256=registry_sha256)
    if models["all_selected_models_available"] is not True:
        raise ValueError("incomplete registered model universe")
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    view_proof = verify_views(view_root, protocol, models, parents, inputs, registry_sha256=registry_sha256)
    views = load_views(view_root, protocol, models, registry_sha256=registry_sha256)
    cases = expected_cases(protocol, views)
    root = root.resolve()
    require_finalized(root, "manifest.json")
    if any(root.rglob("*.partial")) or any(root.rglob("_INCOMPLETE.json")):
        raise ValueError("study retains incomplete run or attempt evidence")
    manifest = read_json(root / "manifest.json", maximum_bytes=STUDY_JSON_LIMIT)
    require_keys(
        manifest,
        {
            "schema_version",
            "complete",
            "claim_ready",
            "registry_sha256",
            "models_sha256",
            "views_sha256",
            "code_identity",
            "results",
            "comparisons",
            "attempts_sha256",
            "plan_sha256",
            "scope",
            "study_sha256",
        },
        "registered study manifest",
    )
    if (
        manifest["schema_version"] != "lob_sim.registered_real_study.v1"
        or manifest["complete"] is not True
        or manifest["claim_ready"] is not False
        or manifest["registry_sha256"] != protocol.digest
        or manifest["models_sha256"] != models["models_sha256"]
        or manifest["views_sha256"] != views["views_sha256"]
        or manifest["code_identity"] != registered["code_identity"]
        or manifest["study_sha256"] != identity({k: v for k, v in manifest.items() if k != "study_sha256"})
        or not isinstance(manifest["results"], list)
        or len(manifest["results"]) != len(cases)
    ):
        raise ValueError("study registry/model/view/case completion identity differs")
    # Reject a changed sensitivity census before opening any case artifacts.
    cells = sorted({(c["symbol"], c["interval_ns"], c["fill_scenario"], c["latency_ms"]) for c in cases})
    if not isinstance(manifest["comparisons"], list) or len(manifest["comparisons"]) != len(cells):
        raise ValueError("paired sensitivity grid census differs")
    for key, binding in zip(cells, manifest["comparisons"], strict=True):
        require_keys(binding, {"cell", "path", "sha256"}, "paired comparison binding")
        if binding["cell"] != list(key) or binding["path"] != (
            "comparisons/" + identity({"registry_sha256": protocol.digest, "cell": key}) + ".json"
        ):
            raise ValueError("paired comparison cell/path differs")
    plan = read_json(root / "plan.json")
    if (
        plan
        != {
            "registry_sha256": protocol.digest,
            "models_sha256": models["models_sha256"],
            "views_sha256": views["views_sha256"],
            "cases": cases,
            "claim_ready": False,
        }
        or file_sha256(root / "plan.json") != manifest["plan_sha256"]
    ):
        raise ValueError("study plan differs from its complete registered grid")
    for receipt_path, phase in (
        (root / "test_access.json", "fixed_model_held_out_study"),
        (view_root / "test_access.json", "fixed_model_held_out_views"),
    ):
        if read_json(receipt_path) != {
            "schema_version": "lob_sim.real_market_test_access.v1",
            "registry_sha256": protocol.digest,
            "models_sha256": models["models_sha256"],
            "phase": phase,
            "test_days": registered["split"]["test_days"],
        }:
            raise ValueError("registered model-frozen test access differs")
    unit_map = {u["unit_id"]: u for u in views["units"]}
    grouped: dict[Any, list[Any]] = defaultdict(list)
    results = manifest["results"]
    for case, record in zip(cases, results, strict=True):
        require_keys(
            record,
            {"case", "configuration", "run_directory", "artifacts", "core_audits", "tables", "status"},
            "study case result",
        )
        if record["status"] != "completed":
            raise ValueError("an incomplete case cannot be omitted or substituted")
        unit = unit_map[case["unit_id"]]
        view_path = view_root / unit["directory"] / unit["view"]["path"]
        verify_case(
            root,
            record,
            case,
            case_configuration(cfg, protocol, case, model_root, models, root / "runs" / case["case_id"]),
            view_path,
            unit,
        )
    with (root / "attempts.jsonl").open("rb") as handle:
        for case, record in zip(cases, results, strict=True):
            raw = handle.readline(16 * 1024 + 1)
            if (
                len(raw) > 16 * 1024
                or not raw.endswith(b"\n")
                or strict_json(raw.decode())
                != {"case_id": case["case_id"], "status": "completed", "record_sha256": identity(record)}
            ):
                raise ValueError("attempt ledger omits, duplicates or changes a case")
        if handle.read(1) or file_sha256(root / "attempts.jsonl") != manifest["attempts_sha256"]:
            raise ValueError("attempt ledger tail/checksum differs")
    for index in range(0, len(results), 3):
        baseline, observed, policy = results[index : index + 3]
        if baseline["core_audits"] != observed["core_audits"]:
            raise ValueError("baseline/observe parity failed; baseline measurement proxy forbidden")
        case = policy["case"]
        grouped[(case["symbol"], case["interval_ns"], case["fill_scenario"], case["latency_ms"])].append(
            (case["source_sha256"], policy, observed)
        )
    if not isinstance(manifest["comparisons"], list) or len(manifest["comparisons"]) != len(grouped):
        raise ValueError("paired sensitivity grid census differs")

    def rows(record: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple(iter_tables(root / record["run_directory"] / record["tables"]["path"], record["tables"]))

    for (key, records), binding in zip(sorted(grouped.items()), manifest["comparisons"], strict=True):
        require_keys(binding, {"cell", "path", "sha256"}, "paired comparison binding")
        name = "comparisons/" + identity({"registry_sha256": protocol.digest, "cell": key}) + ".json"
        path = (root / name).resolve()
        if (
            binding["cell"] != list(key)
            or binding["path"] != name
            or not path.is_relative_to(root)
            or file_sha256(path) != binding["sha256"]
        ):
            raise ValueError("paired comparison cell/path/checksum differs")
        units = [(sha, rows(left), rows(right)) for sha, left, right in records]
        symbol, interval, scenario, latency = key
        expected = {
            "registry_sha256": protocol.digest,
            "symbol": symbol,
            "interval_ns": interval,
            "fill_scenario": scenario,
            "latency_ms": latency,
            "left": "hmm_policy",
            "right": "baseline",
            "measurement_source": "hmm_observe only after exact core event/fill/markout parity",
            "comparison": compare_units(units, registered["experiment"]["statistics"], (100, 1000, 5000, 30_000)),
        }
        if canonical_json(read_json(path)) != canonical_json(expected):
            raise ValueError("stored paired statistics differ from verified native tables")
    return {
        "schema_version": "lob_sim.registered_real_study_verification.v1",
        "verified": True,
        "registry_sha256": protocol.digest,
        "study_sha256": manifest["study_sha256"],
        "features": feature_proof,
        "models": model_proof,
        "views": view_proof,
        "cases": len(cases),
        "paired_cells": len(grouped),
        "claim_ready": False,
        "scope": "serialized raw/day/registry/feature/model/view/scenario/lifecycle/risk/accounting/state and paired-statistic graph;not matching-engine re-execution,independent exchange truth or provenance authentication",
    }
