"""Per-case serialized audit independent of the research replay producer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lob_sim.audit.streaming_bundle import audit_streaming_bundle
from lob_sim.regime.diagnostics import inspect_run
from lob_sim.regime.study import core_audit_hashes
from lob_sim.regime.validation import canonical_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json
from lob_sim.research.study_tables import iter_tables, reduce_tables
from lob_sim.sim.run_manifest import config_snapshot
from lob_sim.research.replay_engine import ResearchConfig
from lob_sim.research.markout_reader import verify_markout_observations


def verify_case(
    root: Path, record: dict[str, Any], case: dict[str, Any], cfg: ResearchConfig, view_path: Path, unit: dict[str, Any]
) -> tuple[dict[str, Any], ...] | None:
    # Never follow a record's directory until its full registered case binds.
    if record.get("case") != case or record.get("configuration") != config_snapshot(cfg):
        raise ValueError("research case/scenario/configuration correspondence differs")
    folder = (root / record["run_directory"]).resolve()
    if not folder.is_relative_to(root.resolve()) or not record["run_directory"].startswith(
        "runs/" + case["case_id"] + "/outputs/"
    ):
        raise ValueError("research run directory escapes its registered case")
    summary = read_json(folder / "summary.json")
    manifest = read_json(folder / "manifest.json")
    if (
        manifest.get("config") != config_snapshot(cfg)
        or summary.get("input_sha256") != unit["view"]["sha256"]
        or manifest.get("input", {}).get("sha256") != unit["view"]["sha256"]
    ):
        raise ValueError("native run configuration/view identity differs")
    files = {}
    for key, binding in record["artifacts"].items():
        name = binding["path"]
        path = (folder / name).resolve()
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not path.is_relative_to(folder)
            or file_sha256(path) != binding["sha256"]
        ):
            raise ValueError("native case artifact identity/path differs")
        files[key] = path
    required = {"event_trace", "trades", "markouts", "summary", "summary_csv", "manifest"}
    if case["variant"] != "baseline":
        required |= {"hmm_model", "regime_trace", "regime_execution", "regime_quotes", "regime_risk"}
    if (
        set(files) != required
        or files["summary"] != folder / "summary.json"
        or files["manifest"] != folder / "manifest.json"
    ):
        raise ValueError("case artifact census differs")
    audit = audit_streaming_bundle(folder, input_override=view_path)
    if audit.get("ok") is not True or core_audit_hashes(files) != record["core_audits"]:
        raise ValueError("independent native case audit/core correspondence failed")
    verify_markout_observations(view_path, files["markouts"], unit["contract"]["window"]["symbol"])
    if case["variant"] == "baseline":
        if record["tables"] is not None or any(k.startswith("hmm") for k in summary):
            raise ValueError("baseline acquired regime-specific outputs")
        return None
    inspect_run(folder)  # Model, state, lifecycle, risk, markout and economics chains.
    if cfg.hmm is None or summary["hmm"]["config"] != cfg.hmm.as_dict():
        raise ValueError("serialized regime runtime differs from registered model/policy")
    actual = reduce_tables(files, summary, unit["contract"])
    path = folder / record["tables"]["path"]
    stored = iter_tables(path, record["tables"])
    if any(canonical_json(a) != canonical_json(b) for a, b in zip(actual, stored, strict=True)):
        raise ValueError("serialized clock tables differ from native audit reducers")
    return actual
