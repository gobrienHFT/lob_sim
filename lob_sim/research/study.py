"""Frozen real-market held-out runner, preserving every case and failure.

The independent verifier lives in study_reader, not in this producer. No
calibration/refit/parameter selection occurs after held-out replay access.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os
import shutil
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.study import core_audit_hashes
from lob_sim.regime.validation import canonical_json, identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import retain_failure, STUDY_JSON_LIMIT
from lob_sim.research.feature_reader import admitted_parents, verify_prepared
from lob_sim.research.fit_bundle import load_model_set, verify_registered_models
from lob_sim.research.registered_protocol import load_protocol
from lob_sim.research.replay_engine import ReplayWindow
from lob_sim.research.study_cases import expected_cases, case_configuration
from lob_sim.research.study_replay import run_study_replay
from lob_sim.research.study_statistics import compare_units
from lob_sim.research.study_tables import reduce_tables, publish_tables
from lob_sim.research.study_views import load_views, verify_views
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot


def run_registered_study(
    inputs: tuple[Path, ...],
    day_bundle: Path,
    registry_path: Path,
    prepared_root: Path,
    model_root: Path,
    view_root: Path,
    directory: Path,
    cfg: Config,
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    protocol = load_protocol(registry_path, registry_sha256=registry_sha256)
    registered = protocol.snapshot()
    if checkpoint_code_identity() != registered["code_identity"]:
        raise ValueError("study processing source differs from frozen registry")
    verify_prepared(prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    verify_registered_models(model_root, prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    models = load_model_set(model_root, protocol, registry_sha256=registry_sha256)
    if models["all_selected_models_available"] is not True:
        raise ValueError("all registered validation selections are required; no partial-model study")
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    verify_views(view_root, protocol, models, parents, inputs, registry_sha256=registry_sha256)
    views = load_views(view_root, protocol, models, registry_sha256=registry_sha256)
    cases = expected_cases(protocol, views)
    root = directory.resolve()
    if any(
        p.resolve().is_relative_to(root)
        for p in (*inputs, day_bundle, registry_path, prepared_root, model_root, view_root)
    ):
        raise ValueError("study output contains immutable parents")
    root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    try:
        publish_json(
            root / "plan.json",
            {
                "registry_sha256": protocol.digest,
                "models_sha256": models["models_sha256"],
                "views_sha256": views["views_sha256"],
                "cases": cases,
                "claim_ready": False,
            },
        )
        publish_json(
            root / "test_access.json",
            {
                "schema_version": "lob_sim.real_market_test_access.v1",
                "registry_sha256": protocol.digest,
                "models_sha256": models["models_sha256"],
                "phase": "fixed_model_held_out_study",
                "test_days": registered["split"]["test_days"],
            },
        )
        unit_map = {u["unit_id"]: u for u in views["units"]}
        # Only a single unit/cell's tables need to remain in memory. Completed
        # runs are independently re-read for pooling after all matching passes.
        with (root / "attempts.jsonl.partial").open("xb") as journal:
            for case in cases:
                unit = unit_map[case["unit_id"]]
                path = view_root / unit["directory"] / unit["view"]["path"]
                cfg_case = case_configuration(cfg, protocol, case, model_root, models, root / "runs" / case["case_id"])
                floor = registered["experiment"]["resource_limits"]["minimum_free_disk_bytes"]
                if shutil.disk_usage(root).free < floor:
                    raise RuntimeError("registered study disk floor reached")
                if (
                    checkpoint_code_identity() != registered["code_identity"]
                    or file_sha256(path) != unit["view"]["sha256"]
                ):
                    raise ValueError("registered source/view changed during study")
                try:
                    files, summary = run_study_replay(
                        cfg_case, path, ReplayWindow(**unit["contract"]["window"]), minimum_free_disk_bytes=floor
                    )
                    tables = (
                        None
                        if case["variant"] == "baseline"
                        else publish_tables(
                            files["summary"].parent / "clock_periods.jsonl",
                            reduce_tables(files, summary, unit["contract"]),
                        )
                    )
                    record: dict[str, Any] = {
                        "case": case,
                        "configuration": config_snapshot(cfg_case),
                        "run_directory": files["summary"].parent.relative_to(root).as_posix(),
                        "artifacts": {k: {"path": p.name, "sha256": file_sha256(p)} for k, p in files.items()},
                        "core_audits": core_audit_hashes(files),
                        "tables": tables,
                        "status": "completed",
                    }
                    results.append(record)
                except BaseException as exc:
                    journal.write(
                        (
                            canonical_json({"case": case, "status": "failed", "error_type": type(exc).__name__}) + "\n"
                        ).encode()
                    )
                    journal.flush()
                    os.fsync(journal.fileno())
                    raise  # Do not publish a surviving, selection-biased subset.
                journal.write(
                    (
                        canonical_json(
                            {"case_id": case["case_id"], "status": "completed", "record_sha256": identity(record)}
                        )
                        + "\n"
                    ).encode()
                )
                journal.flush()
                os.fsync(journal.fileno())
        os.link(root / "attempts.jsonl.partial", root / "attempts.jsonl")
        (root / "attempts.jsonl.partial").unlink()
        grouped: dict[Any, list[Any]] = defaultdict(list)
        # Use the separate serialized run re-reader, not retained engines.
        from lob_sim.research.study_run_reader import verify_case
        from lob_sim.research.study_tables import iter_tables

        for index in range(0, len(results), 3):
            baseline, observed, policy = results[index : index + 3]
            if baseline["core_audits"] != observed["core_audits"]:
                raise ValueError("baseline/observe core parity failed; measurement proxy forbidden")
            case = policy["case"]
            unit = unit_map[case["unit_id"]]
            view_path = view_root / unit["directory"] / unit["view"]["path"]
            for record in (baseline, observed, policy):
                verify_case(
                    root,
                    record,
                    record["case"],
                    case_configuration(
                        cfg, protocol, record["case"], model_root, models, root / "runs" / record["case"]["case_id"]
                    ),
                    view_path,
                    unit,
                )

            def stored(record: dict[str, Any]) -> tuple[dict[str, Any], ...]:
                return tuple(iter_tables(root / record["run_directory"] / record["tables"]["path"], record["tables"]))

            grouped[(case["symbol"], case["interval_ns"], case["fill_scenario"], case["latency_ms"])].append(
                (case["source_sha256"], policy, observed)
            )
        (root / "comparisons").mkdir()
        for key, records in sorted(grouped.items()):
            # Tables are retained for this explicitly capped cell only, never
            # for the whole grid or full receipt/trace duration.
            units = [(sha, stored(left), stored(right)) for sha, left, right in records]
            symbol, interval, scenario, latency = key
            report = {
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
            name = identity({"registry_sha256": protocol.digest, "cell": key}) + ".json"
            publish_json(root / "comparisons" / name, report)
            comparisons.append(
                {"cell": list(key), "path": "comparisons/" + name, "sha256": file_sha256(root / "comparisons" / name)}
            )
        if checkpoint_code_identity() != registered["code_identity"]:
            raise ValueError("study source changed before publication")
        manifest = {
            "schema_version": "lob_sim.registered_real_study.v1",
            "complete": True,
            "claim_ready": False,
            "registry_sha256": protocol.digest,
            "models_sha256": models["models_sha256"],
            "views_sha256": views["views_sha256"],
            "code_identity": registered["code_identity"],
            "results": results,
            "comparisons": comparisons,
            "attempts_sha256": file_sha256(root / "attempts.jsonl"),
            "plan_sha256": file_sha256(root / "plan.json"),
            "scope": "complete registered held-out conditional source/day grid;simulated public-L2 execution and pre-funding economics;not a profitability,significance,private-fill or production claim",
        }
        manifest["study_sha256"] = identity(manifest)
        if len(canonical_json(manifest).encode("utf-8")) + 1 > STUDY_JSON_LIMIT:
            raise ValueError("registered study metadata exceeds its explicit publication budget")
        publish_json(root / "manifest.json", manifest)
        from lob_sim.research.study_reader import verify_registered_study

        verify_registered_study(
            root,
            inputs,
            day_bundle,
            registry_path,
            prepared_root,
            model_root,
            view_root,
            cfg,
            registry_sha256=registry_sha256,
        )
        return manifest
    except BaseException as exc:
        retain_failure(
            root,
            exc,
            schema="lob_sim.registered_real_study_failure.v1",
            completed_cases=len(results),
            expected_cases=len(cases),
        )
        raise
