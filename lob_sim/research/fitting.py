"""Registered per-symbol/cadence fits, preserving every K and restart ledger.

All winning K models are saved, not just the validation-selected one. No
outcome data or test feature partition is passed to fitting or selection.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.artifact import save_model
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.fit import FitConfig, fit_candidates
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import retain_failure
from lob_sim.research.feature_reader import admitted_parents, load_prepared, read_fit_partition
from lob_sim.research.registered_protocol import experiment_specification, load_protocol
from lob_sim.sim.checkpoint import checkpoint_code_identity


def fit_registered_models(
    prepared_root: Path,
    registry_path: Path,
    day_bundle: Path,
    inputs: tuple[Path, ...],
    directory: Path,
    cfg: Config,
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    protocol = load_protocol(registry_path, registry_sha256=registry_sha256)
    registered = protocol.snapshot()
    code = checkpoint_code_identity()
    if (
        code != registered["code_identity"]
        or identity(experiment_specification(cfg)) != registered["experiment_sha256"]
    ):
        raise ValueError("fitting code/configuration differs from the frozen protocol")
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    prepared = load_prepared(prepared_root, protocol, registry_sha256=registry_sha256)
    root = directory.resolve()
    if any(p.resolve().is_relative_to(root) for p in (*inputs, prepared_root, registry_path, day_bundle)):
        raise ValueError("fit output cannot contain its immutable parents")
    root.mkdir(parents=True, exist_ok=False)
    try:
        frozen_fit = registered["experiment"]["fitting"]
        fit = FitConfig(**{**frozen_fit, "state_counts": tuple(frozen_fit["state_counts"])})
        cells = []
        for cadence in prepared["cadences"]:
            interval = cadence["features"]["interval_ns"]
            for symbol in registered["experiment"]["symbols"]:
                name = cadence["directory"] + "__" + symbol
                folder = root / name
                folder.mkdir()
                train = read_fit_partition(
                    prepared_root, protocol, parents, "calibration", symbol, interval, registry_sha256=registry_sha256
                )
                validation = read_fit_partition(
                    prepared_root, protocol, parents, "validation", symbol, interval, registry_sha256=registry_sha256
                )
                candidates, records = [], []
                for k in fit.state_counts:
                    if (
                        shutil.disk_usage(root).free
                        < registered["experiment"]["resource_limits"]["minimum_free_disk_bytes"]
                    ):
                        raise RuntimeError("frozen fit disk-resource floor reached")
                    candidate_root = folder / ("k_" + str(k))
                    candidate_root.mkdir()
                    result = fit_candidates(train, validation, replace(fit, state_counts=(k,)))
                    report = result.report()
                    publish_json(candidate_root / "fit_report.json", report)
                    candidate = report["candidates"][0]
                    candidates.append(candidate)
                    record = {
                        "k": k,
                        "directory": name + "/k_" + str(k),
                        "report_sha256": report["report_sha256"],
                        "report_file_sha256": file_sha256(candidate_root / "fit_report.json"),
                        "model_sha256": None,
                        "model_file_sha256": None,
                    }
                    if result.model is not None:
                        save_model(candidate_root / "model.json", result.model)
                        record.update(
                            model_sha256=result.model.model_sha256,
                            model_file_sha256=file_sha256(candidate_root / "model.json"),
                        )
                    records.append(record)
                valid = [candidate for candidate in candidates if candidate["status"] == "valid"]
                selected = None
                if valid:
                    best = max(candidate["validation_log_likelihood_per_observation"] for candidate in valid)
                    near = [
                        candidate
                        for candidate in valid
                        if best - candidate["validation_log_likelihood_per_observation"]
                        <= fit.validation_tie_per_observation
                    ]
                    selected = min(
                        near, key=lambda candidate: (candidate["training_bic"], candidate["k"], candidate["restart"])
                    )["k"]
                cells.append(
                    {
                        "symbol": symbol,
                        "interval_ns": interval,
                        "directory": name,
                        "training": train.provenance(),
                        "validation": validation.provenance(),
                        "candidates": records,
                        "validation_selected_k": selected,
                    }
                )
        admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
        if (
            checkpoint_code_identity() != code
            or load_prepared(prepared_root, protocol, registry_sha256=registry_sha256)["prepared_sha256"]
            != prepared["prepared_sha256"]
        ):
            raise ValueError("fit source or prepared parent changed during fitting")
        manifest = {
            "schema_version": "lob_sim.registered_real_models.v1",
            "complete": True,
            "registry_sha256": protocol.digest,
            "prepared_sha256": prepared["prepared_sha256"],
            "code_identity": code,
            "cells": cells,
            "all_selected_models_available": all(cell["validation_selected_k"] is not None for cell in cells),
            "claim_ready": False,
            "scope": "calibration-only EM/scaler;validation selects K;all K winning models and restart ledgers retained;no test feature rows or outcomes",
        }
        manifest["models_sha256"] = identity(manifest)
        publish_json(root / "manifest.json", manifest)
        from lob_sim.research.fit_bundle import verify_registered_models

        verify_registered_models(root, prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
        return manifest
    except BaseException as exc:
        retain_failure(root, exc, schema="lob_sim.registered_real_fit_failure.v1")
        raise
