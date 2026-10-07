"""Independent saved model-set and winning-candidate numerical verification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.regime.artifact import load_model
from lob_sim.regime.fit import FitConfig
from lob_sim.regime.validation import identity, require_keys
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json, require_finalized
from lob_sim.research.feature_reader import admitted_parents, load_prepared, read_fit_partition
from lob_sim.research.fit_validation import select_validation_candidate, verify_fit_candidate
from lob_sim.research.interval_reader import digest
from lob_sim.research.registered_protocol import FrozenProtocol


def load_model_set(root: Path, protocol: FrozenProtocol, *, registry_sha256: str) -> dict[str, Any]:
    if digest(registry_sha256, "explicit frozen registry digest") != protocol.digest:
        raise ValueError("frozen registry digest mismatch before model-set access")
    require_finalized(root, "manifest.json")
    if any(root.rglob("*.partial")):
        raise ValueError("model set retains an incomplete candidate artifact")
    manifest = read_json(root / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "complete",
            "registry_sha256",
            "prepared_sha256",
            "code_identity",
            "cells",
            "all_selected_models_available",
            "claim_ready",
            "scope",
            "models_sha256",
        },
        "registered model set",
    )
    if (
        manifest["schema_version"] != "lob_sim.registered_real_models.v1"
        or manifest["complete"] is not True
        or manifest["claim_ready"] is not False
    ):
        raise ValueError("model-set schema/completion/claim mismatch")
    if manifest["models_sha256"] != identity({k: v for k, v in manifest.items() if k != "models_sha256"}):
        raise ValueError("model-set content identity differs")
    registered = protocol.snapshot()
    if manifest["registry_sha256"] != protocol.digest or manifest["code_identity"] != registered["code_identity"]:
        raise ValueError("model-set registry/code parents differ")
    digest(manifest["prepared_sha256"], "prepared model parent")
    expected = [
        (f["interval_ns"], symbol)
        for f in registered["experiment"]["features"]
        for symbol in registered["experiment"]["symbols"]
    ]
    if (
        not isinstance(manifest["cells"], list)
        or [(cell.get("interval_ns"), cell.get("symbol")) for cell in manifest["cells"]] != expected
    ):
        raise ValueError("model-set symbol/cadence census differs")
    for cell in manifest["cells"]:
        require_keys(
            cell,
            {"symbol", "interval_ns", "directory", "training", "validation", "candidates", "validation_selected_k"},
            "model cell",
        )
        name = "cadence_" + str(cell["interval_ns"]) + "__" + cell["symbol"]
        if (
            cell["directory"] != name
            or not isinstance(cell["candidates"], list)
            or [c.get("k") for c in cell["candidates"]] != registered["experiment"]["fitting"]["state_counts"]
        ):
            raise ValueError("candidate K census/path differs")
        for record in cell["candidates"]:
            require_keys(
                record,
                {"k", "directory", "report_sha256", "report_file_sha256", "model_sha256", "model_file_sha256"},
                "model candidate binding",
            )
            if record["directory"] != name + "/k_" + str(record["k"]):
                raise ValueError("noncanonical candidate directory")
            path = (root / record["directory"]).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("candidate directory escapes model set")
            for key in ("report_sha256", "report_file_sha256"):
                digest(record[key], key)
            if (record["model_sha256"] is None) != (record["model_file_sha256"] is None):
                raise ValueError("candidate model availability differs")
            if record["model_sha256"] is not None:
                digest(record["model_sha256"], "model identity")
                digest(record["model_file_sha256"], "model file identity")
    available = all(cell["validation_selected_k"] is not None for cell in manifest["cells"])
    if (
        type(manifest["all_selected_models_available"]) is not bool
        or manifest["all_selected_models_available"] != available
    ):
        raise ValueError("model availability declaration differs")
    return manifest


def verify_registered_models(
    root: Path,
    prepared_root: Path,
    protocol: FrozenProtocol,
    day_bundle: Path,
    inputs: tuple[Path, ...],
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    manifest = load_model_set(root, protocol, registry_sha256=registry_sha256)
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    prepared = load_prepared(prepared_root, protocol, registry_sha256=registry_sha256)
    if prepared["prepared_sha256"] != manifest["prepared_sha256"]:
        raise ValueError("model-set prepared parent differs")
    frozen = protocol.snapshot()["experiment"]["fitting"]
    cfg = FitConfig(**{**frozen, "state_counts": tuple(frozen["state_counts"])})
    model_count = 0
    for cell in manifest["cells"]:
        train = read_fit_partition(
            prepared_root,
            protocol,
            parents,
            "calibration",
            cell["symbol"],
            cell["interval_ns"],
            registry_sha256=registry_sha256,
        )
        validation = read_fit_partition(
            prepared_root,
            protocol,
            parents,
            "validation",
            cell["symbol"],
            cell["interval_ns"],
            registry_sha256=registry_sha256,
        )
        if cell["training"] != train.provenance() or cell["validation"] != validation.provenance():
            raise ValueError("model cell feature parents differ")
        candidates = []
        for record in cell["candidates"]:
            folder = root / record["directory"]
            if file_sha256(folder / "fit_report.json") != record["report_file_sha256"]:
                raise ValueError("fit report file checksum differs")
            report = read_json(folder / "fit_report.json")
            if report.get("report_sha256") != record["report_sha256"]:
                raise ValueError("fit report parent differs")
            model = None
            if record["model_sha256"] is not None:
                if file_sha256(folder / "model.json") != record["model_file_sha256"]:
                    raise ValueError("model file checksum differs")
                model = load_model(folder / "model.json")
                if model.model_sha256 != record["model_sha256"]:
                    raise ValueError("model content identity differs")
                model_count += 1
            elif (folder / "model.json").exists():
                raise ValueError("unregistered model exists for a failed candidate")
            verify_fit_candidate(report, model, train, validation, replace(cfg, state_counts=(record["k"],)))
            candidates.extend(report["candidates"])
        chosen = select_validation_candidate(candidates, cfg.validation_tie_per_observation)
        if cell["validation_selected_k"] != (chosen["k"] if chosen else None):
            raise ValueError("model-set validation selection differs")
    return {
        "schema_version": "lob_sim.registered_real_models_verification.v1",
        "verified": True,
        "registry_sha256": protocol.digest,
        "models_sha256": manifest["models_sha256"],
        "winning_models": model_count,
        "scope": "serialized calibration/validation provenance,complete attempt ledger,frozen selection,train-only scaler and independent forward likelihood of each saved winning K;not discarded restart parameter reconstruction or execution truth",
    }
