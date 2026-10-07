"""Publish immutable held-out replay views only after saved model selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity, require_keys
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.day_view import write_day_view
from lob_sim.research.evidence_io import read_json, require_finalized, retain_failure
from lob_sim.research.feature_reader import admitted_parents
from lob_sim.research.fit_bundle import verify_registered_models, load_model_set
from lob_sim.research.interval_reader import digest
from lob_sim.research.registered_protocol import FrozenProtocol, load_protocol, experiment_specification
from lob_sim.research.study_units import expected_study_units
from lob_sim.research.view_reader import verify_day_view
from lob_sim.sim.checkpoint import checkpoint_code_identity


def load_views(root: Path, protocol: FrozenProtocol, models: dict[str, Any], *, registry_sha256: str) -> dict[str, Any]:
    if digest(registry_sha256, "frozen registry digest") != protocol.digest:
        raise ValueError("registry mismatch before study view access")
    if models["all_selected_models_available"] is not True:
        raise ValueError("study view access requires all frozen model selections")
    require_finalized(root, "manifest.json")
    if any(root.rglob("*.partial")):
        raise ValueError("study view contains partial evidence")
    manifest = read_json(root / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "complete",
            "registry_sha256",
            "models_sha256",
            "code_identity",
            "units",
            "claim_ready",
            "views_sha256",
        },
        "study view manifest",
    )
    if (
        manifest["schema_version"] != "lob_sim.registered_replay_views.v1"
        or manifest["complete"] is not True
        or manifest["claim_ready"] is not False
        or manifest["registry_sha256"] != protocol.digest
        or manifest["models_sha256"] != models["models_sha256"]
        or manifest["code_identity"] != protocol.snapshot()["code_identity"]
        or manifest["views_sha256"] != identity({k: v for k, v in manifest.items() if k != "views_sha256"})
        or not isinstance(manifest["units"], list)
        or not 1
        <= len(manifest["units"])
        <= protocol.snapshot()["experiment"]["resource_limits"]["maximum_study_units"]
    ):
        raise ValueError("study view completion/parents/content differ")
    if read_json(root / "test_access.json") != {
        "schema_version": "lob_sim.real_market_test_access.v1",
        "registry_sha256": protocol.digest,
        "models_sha256": models["models_sha256"],
        "phase": "fixed_model_held_out_views",
        "test_days": protocol.snapshot()["split"]["test_days"],
    }:
        raise ValueError("model-frozen held-out view access receipt differs")
    for index, unit in enumerate(manifest["units"]):
        require_keys(
            unit, {"unit_id", "symbol", "utc_day", "source_sha256", "contract", "directory", "view"}, "study unit"
        )
        if unit["directory"] != f"unit-{index:06d}" or unit["unit_id"] != identity(unit["contract"]):
            raise ValueError("study unit identity/directory differs")
        protocol.authorize("test", unit["utc_day"], registry_sha256=registry_sha256)
        folder = (root / unit["directory"]).resolve()
        name = unit["view"].get("path")
        if not isinstance(name, str) or Path(name).name != name or not folder.is_relative_to(root.resolve()):
            raise ValueError("study view path escapes its bundle")
        if unit["view"].get("contract") != unit["contract"]:
            raise ValueError("study unit/view contract differs")
    return manifest


def verify_views(
    root: Path,
    protocol: FrozenProtocol,
    models: dict[str, Any],
    parents: dict[str, tuple[Path, dict[str, Any]]],
    inputs: tuple[Path, ...],
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    manifest = load_views(root, protocol, models, registry_sha256=registry_sha256)
    expected = expected_study_units(protocol, parents, registry_sha256=registry_sha256)
    keys = {"unit_id", "symbol", "utc_day", "source_sha256", "contract"}
    if [{k: unit[k] for k in keys} for unit in manifest["units"]] != expected:
        raise ValueError("held-out source/day/unit census differs")
    sources = {file_sha256(path): path for path in inputs}
    for unit in manifest["units"]:
        verify_day_view(sources[unit["source_sha256"]], root / unit["directory"] / unit["view"]["path"], unit["view"])
    return {
        "verified": True,
        "registry_sha256": protocol.digest,
        "views_sha256": manifest["views_sha256"],
        "units": len(expected),
    }


def prepare_study_views(
    inputs: tuple[Path, ...],
    day_bundle: Path,
    registry_path: Path,
    prepared_root: Path,
    model_root: Path,
    directory: Path,
    cfg: Config,
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    protocol = load_protocol(registry_path, registry_sha256=registry_sha256)
    registered = protocol.snapshot()
    if (
        identity(experiment_specification(cfg)) != registered["experiment_sha256"]
        or checkpoint_code_identity() != registered["code_identity"]
    ):
        raise ValueError("study preparation configuration/code differs from registry")
    verify_registered_models(model_root, prepared_root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    models = load_model_set(model_root, protocol, registry_sha256=registry_sha256)
    if models["all_selected_models_available"] is not True:
        raise ValueError("model failure blocker: no held-out replay before all registered selections are available")
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    sources = {file_sha256(path): path for path in inputs}
    root = directory.resolve()
    if any(p.resolve().is_relative_to(root) for p in (*inputs, day_bundle, registry_path, prepared_root, model_root)):
        raise ValueError("study view output contains an immutable parent")
    root.mkdir(parents=True, exist_ok=False)
    try:
        publish_json(
            root / "test_access.json",
            {
                "schema_version": "lob_sim.real_market_test_access.v1",
                "registry_sha256": protocol.digest,
                "models_sha256": models["models_sha256"],
                "phase": "fixed_model_held_out_views",
                "test_days": registered["split"]["test_days"],
            },
        )
        units = expected_study_units(protocol, parents, registry_sha256=registry_sha256)
        if not units:
            raise ValueError("no held-out source/day units remain after registered warmup")
        for index, unit in enumerate(units):
            unit["directory"] = f"unit-{index:06d}"
            unit["view"] = write_day_view(
                sources[unit["source_sha256"]],
                root / unit["directory"],
                unit["contract"],
                minimum_free_disk_bytes=registered["experiment"]["resource_limits"]["minimum_free_disk_bytes"],
            )
        if checkpoint_code_identity() != registered["code_identity"]:
            raise ValueError("source changed during view preparation")
        manifest = {
            "schema_version": "lob_sim.registered_replay_views.v1",
            "complete": True,
            "registry_sha256": protocol.digest,
            "models_sha256": models["models_sha256"],
            "code_identity": registered["code_identity"],
            "units": units,
            "claim_ready": False,
        }
        manifest["views_sha256"] = identity(manifest)
        publish_json(root / "manifest.json", manifest)
        verify_views(root, protocol, models, parents, inputs, registry_sha256=registry_sha256)
        return manifest
    except BaseException as exc:
        retain_failure(root, exc, schema="lob_sim.replay_view_failure.v1")
        raise
