"""Raw captures -> native audits -> whole-day census, with explicit parents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity, require_keys
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_audit import audit_capture
from lob_sim.research.capture_receipts import audit_receipts
from lob_sim.research.day_eligibility import REQUIRED_SYMBOLS, summarize_days, verify_day_report
from lob_sim.research.evidence_io import read_json, require_finalized, retain_failure
from lob_sim.research.interval_reader import load_audit_report
from lob_sim.sim.checkpoint import checkpoint_code_identity


def _sources(inputs: tuple[Path, ...]) -> list[tuple[str, Path]]:
    if not isinstance(inputs, tuple) or not 1 <= len(inputs) <= 256:
        raise ValueError("day bundle requires 1..256 explicit source manifests")
    sources = sorted((file_sha256(p), p.resolve()) for p in inputs)
    if len({s[0] for s in sources}) != len(sources):
        raise ValueError("duplicate source content")
    return sources


def build_day_bundle(inputs: tuple[Path, ...], directory: Path, cfg: Config) -> dict[str, Any]:
    if set(cfg.symbols) != set(REQUIRED_SYMBOLS) or cfg.hmm is not None:
        raise ValueError("research days require BTCUSDT+ETHUSDT with HMM disabled")
    sources = _sources(inputs)
    root = directory.resolve()
    if any(path.is_relative_to(root) for _, path in sources):
        raise ValueError("day output cannot contain its input captures")
    root.mkdir(parents=True, exist_ok=False)
    try:
        code = checkpoint_code_identity()
        parents = []
        audits = []
        for index, (sha, source) in enumerate(sources):
            name = f"capture-{index:04d}"
            audit_root = root / name
            report = audit_capture(source, audit_root, cfg)
            if report["input_sha256"] != sha:
                raise ValueError("capture source changed before audit publication")
            parents.append({"directory": name, "input_sha256": sha, "report_sha256": report["report_sha256"]})
            audits.append(audit_root)
        days = summarize_days(tuple(audits))
        publish_json(root / "days.json", days)
        if checkpoint_code_identity() != code:
            raise ValueError("research processing code changed during day census")
        manifest = {
            "schema_version": "lob_sim.research_day_bundle.v1",
            "complete": True,
            "parents": parents,
            "day_report_sha256": days["report_sha256"],
            "code_identity": code,
            "verification_scope": "raw receipt integrity plus independent serialized interval/day census;not independent exchange or provenance truth",
        }
        manifest["bundle_sha256"] = identity(manifest)
        publish_json(root / "manifest.json", manifest)
        # Do not trust successful writes; reopen the published evidence.
        verify_day_bundle(root, inputs)
        return manifest
    except BaseException as exc:
        retain_failure(root, exc, schema="lob_sim.research_day_bundle_failure.v1")
        raise


def verify_day_bundle(directory: Path, inputs: tuple[Path, ...]) -> dict[str, Any]:
    """Explicit source bindings allow moving bundles without path rewriting.

    Raw bytes and causal/receipt metadata are rechecked, then the separate
    reader integrates serialized interval geometry and re-applies day rules.
    It does not rerun the producer or pretend to prove native book semantics.
    """
    root = directory.resolve()
    require_finalized(root, "manifest.json")
    manifest = read_json(root / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "complete",
            "parents",
            "day_report_sha256",
            "code_identity",
            "verification_scope",
            "bundle_sha256",
        },
        "day bundle manifest",
    )
    if manifest["schema_version"] != "lob_sim.research_day_bundle.v1" or manifest["complete"] is not True:
        raise ValueError("unsupported/incomplete day bundle")
    if manifest["bundle_sha256"] != identity({k: v for k, v in manifest.items() if k != "bundle_sha256"}):
        raise ValueError("day bundle content identity mismatch")
    sources = dict(_sources(inputs))
    parents = manifest["parents"]
    if not isinstance(parents, list) or len(parents) != len(sources):
        raise ValueError("day source bindings differ from bundle")
    audits = []
    for index, parent in enumerate(parents):
        require_keys(parent, {"directory", "input_sha256", "report_sha256"}, "day audit parent")
        if parent["directory"] != f"capture-{index:04d}" or parent["input_sha256"] not in sources:
            raise ValueError("noncanonical or mismatched day audit parent")
        audit_root = (root / parent["directory"]).resolve()
        if not audit_root.is_relative_to(root):
            raise ValueError("day audit parent escapes its bundle")
        report = load_audit_report(audit_root)
        if any(parent[k] != report[k] for k in ("input_sha256", "report_sha256")):
            raise ValueError("day audit parent identity mismatch")
        raw = audit_receipts(sources.pop(parent["input_sha256"]), REQUIRED_SYMBOLS)
        for key, value in raw.items():
            if key == "integrity":
                # Native extraction can only add an instrument-change defect,
                # never remove a raw receipt defect or turn invalid into valid.
                reasons = set(value["reasons"])
                reported = set(report[key]["reasons"])
                if not reasons.issubset(reported) or reported - reasons not in (set(), {"instrument_identity_changed"}):
                    raise ValueError("serialized integrity differs from raw receipts")
            elif report[key] != value:
                raise ValueError("serialized capture metadata differs from raw receipts: " + key)
        audits.append(audit_root)
    if sources:
        raise ValueError("unbound raw sources")
    days = read_json(root / "days.json")
    if days.get("report_sha256") != manifest["day_report_sha256"]:
        raise ValueError("day report parent identity mismatch")
    verify_day_report(tuple(audits), days)
    return {
        "schema_version": "lob_sim.research_day_bundle_verification.v1",
        "verified": True,
        "bundle_sha256": manifest["bundle_sha256"],
        "source_count": len(parents),
        "eligible_days": days["eligible_days"],
        "ready_for_registration": days["ready_for_registration"],
        "scope": manifest["verification_scope"],
    }
