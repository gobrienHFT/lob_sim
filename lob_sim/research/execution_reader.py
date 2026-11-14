"""Independent serialized execution-plan/receipt re-reader; never runs cases.

The enclosing study verifier separately checks every native data artifact.
History hashes describe retained external failed data, not authentication or
independent revalidation of that absent data.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from typing import Any

from lob_sim.regime.validation import canonical_json, identity, require_keys, integer
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.case_store import MAX_ATTEMPTS
from lob_sim.research.evidence_io import read_json, STUDY_JSON_LIMIT


def _file(root: Path, binding: dict[str, Any], name: str) -> Path:
    require_keys(binding, {"path", "sha256"}, "execution file binding")
    path = (root / name).resolve()
    if binding["path"] != name or not path.is_relative_to(root.resolve()) or file_sha256(path) != binding["sha256"]:
        raise ValueError("execution artifact path/checksum differs")
    return path


def _history(data: dict[str, Any], case_id: str, receipt_sha: str) -> None:
    require_keys(data, {"schema_version", "case_id", "completed_attempt", "attempts", "scope"}, "attempt history")
    attempts = data["attempts"]
    if (
        data["schema_version"] != "lob_sim.case_attempt_history.v1"
        or data["case_id"] != case_id
        or not isinstance(attempts, list)
        or not 1 <= len(attempts) <= MAX_ATTEMPTS
        or data["completed_attempt"] != f"attempt_{len(attempts) - 1:06d}"
    ):
        raise ValueError("execution attempt census/completion differs")
    allowed = {n + suffix for n in ("started.json", "failed.json", "completed.json") for suffix in ("", ".partial")}
    for index, attempt in enumerate(attempts):
        require_keys(attempt, {"attempt", "metadata", "partial_artifacts"}, "attempt identity")
        if attempt["attempt"] != f"attempt_{index:06d}":
            raise ValueError("attempt ordering/census differs")
        metadata, partials = attempt["metadata"], attempt["partial_artifacts"]
        if (
            not isinstance(metadata, list)
            or len(metadata) > len(allowed)
            or not isinstance(partials, list)
            or len(partials) > 64
        ):
            raise ValueError("attempt history exceeds fixed record limits")
        names = []
        for entry in metadata + partials:
            require_keys(entry, {"path", "sha256", "size_bytes"}, "attempt byte identity")
            integer(entry["size_bytes"], "attempt byte size")
            if not isinstance(entry["sha256"], str) or re.fullmatch("[0-9a-f]{64}", entry["sha256"]) is None:
                raise ValueError("attempt byte digest invalid")
            name = entry["path"]
            if (
                not isinstance(name, str)
                or "\\" in name
                or PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
            ):
                raise ValueError("attempt byte identity path unsafe")
        for entry in metadata:
            name = entry["path"]
            if name not in allowed:
                raise ValueError("unknown attempt metadata")
            names.append(name)
        if names != sorted(set(names)):
            raise ValueError("attempt metadata duplicates or changes ordering")
        partial_names = [entry["path"] for entry in partials]
        if partial_names != sorted(set(partial_names)) or any(
            not n.startswith("work/") or not (n.endswith(".partial") or n.endswith("/_INCOMPLETE.json"))
            for n in partial_names
        ):
            raise ValueError("partial history path/census differs")
        if index == len(attempts) - 1:
            hashes = {entry["path"]: entry["sha256"] for entry in metadata}
            if (
                "started.json" not in hashes
                or hashes.get("completed.json") != receipt_sha
                or "failed.json" in hashes
                or partials
            ):
                raise ValueError("completed attempt history conflicts with its receipt")
            if "completed.json.partial" in hashes and hashes["completed.json.partial"] != receipt_sha:
                raise ValueError("completion partial conflicts with committed receipt")
        elif "completed.json" in names:
            raise ValueError("duplicate completed execution attempt")


def verify_execution_evidence(
    root: Path,
    binding: dict[str, Any],
    registered: dict[str, Any],
    registry_sha256: str,
    models_sha256: str,
    views_sha256: str,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    require_keys(binding, {"schema_version", "plan", "plan_digest", "cases", "scope"}, "study execution evidence")
    expected_spec = {
        "schema_version": "lob_sim.registered_case_execution.v1",
        "registry_sha256": registry_sha256,
        "models_sha256": models_sha256,
        "views_sha256": views_sha256,
        "code_identity": registered["code_identity"],
        "experiment_sha256": registered["experiment_sha256"],
        "cases": cases,
    }
    expected_plan = {
        "schema_version": "lob_sim.durable_case_plan.v1",
        "specification": expected_spec,
        "plan_sha256": identity(expected_spec),
    }
    plan_path = _file(root, binding["plan"], "recovery/plan.json")
    if (
        binding["schema_version"] != "lob_sim.study_execution_evidence.v1"
        or binding["plan_digest"] != identity(expected_spec)
        or canonical_json(read_json(plan_path, maximum_bytes=STUDY_JSON_LIMIT)) != canonical_json(expected_plan)
        or not isinstance(binding["cases"], list)
        or len(binding["cases"]) != len(cases)
    ):
        raise ValueError("execution plan/case census differs from complete frozen study")
    for case, record, item in zip(cases, results, binding["cases"], strict=True):
        require_keys(item, {"case_id", "receipt", "history"}, "execution case binding")
        case_id = case["case_id"]
        if item["case_id"] != case_id:
            raise ValueError("execution case binding differs")
        receipt = read_json(_file(root, item["receipt"], "recovery/cases/" + case_id + ".json"))
        require_keys(
            receipt, {"schema_version", "plan_sha256", "case_id", "record", "record_sha256"}, "execution case receipt"
        )
        if (
            receipt["schema_version"] != "lob_sim.durable_case_completion.v1"
            or receipt["plan_sha256"] != binding["plan_digest"]
            or receipt["case_id"] != case_id
            or receipt["record_sha256"] != identity(record)
            or canonical_json(receipt["record"]) != canonical_json(record)
        ):
            raise ValueError("execution receipt differs from verified native case")
        history = read_json(_file(root, item["history"], "recovery/histories/" + case_id + ".json"))
        _history(history, case_id, item["receipt"]["sha256"])
    return {
        "verified": True,
        "plan_digest": binding["plan_digest"],
        "cases": len(cases),
        "scope": "complete receipt/data correspondence;external failed-history hashes are not provenance authentication",
    }
