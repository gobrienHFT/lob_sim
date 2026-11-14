"""Publish immutable case-plan/receipt and failed-attempt history identities.

Failed data remains in the external execution store, not inside a supposedly
complete study. The publication retains its hashes and relative names; these
are history identities, not independently authenticated claims about an author.
"""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity, canonical_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.case_store import CaseStore
from lob_sim.research.evidence_io import read_json


def execution_specification(
    registered: dict[str, Any],
    registry_sha256: str,
    models_sha256: str,
    views_sha256: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "lob_sim.registered_case_execution.v1",
        "registry_sha256": registry_sha256,
        "models_sha256": models_sha256,
        "views_sha256": views_sha256,
        "code_identity": registered["code_identity"],
        "experiment_sha256": registered["experiment_sha256"],
        "cases": cases,
    }


class ExecutionPublication:
    def __init__(self, root: Path, store: CaseStore):
        self.root, self.store = root.resolve(), store
        self.folder = self.root / "recovery"
        self.folder.mkdir(exist_ok=False)
        (self.folder / "cases").mkdir()
        (self.folder / "histories").mkdir()
        os.link(store.root / "plan.json", self.folder / "plan.json")
        self.bindings: list[dict[str, Any]] = []

    def add(self, case: dict[str, Any], record: dict[str, Any], attempt: Path) -> None:
        case_id = case["case_id"]
        receipt = read_json(attempt / "completed.json")
        if (
            receipt.get("plan_sha256") != self.store.digest
            or receipt.get("case_id") != case_id
            or receipt.get("record_sha256") != identity(record)
            or canonical_json(receipt.get("record")) != canonical_json(record)
        ):
            raise ValueError("completed execution receipt differs from verified case")
        receipt_path = self.folder / "cases" / (case_id + ".json")
        os.link(attempt / "completed.json", receipt_path)
        history = []
        for prior in sorted(attempt.parent.iterdir()):
            if not prior.resolve().is_relative_to(self.store.root) or not prior.is_dir():
                raise ValueError("attempt history escapes its execution store")
            metadata = []
            for path in sorted(prior.iterdir()):
                if path.is_file():
                    if not path.resolve().is_relative_to(prior.resolve()):
                        raise ValueError("attempt metadata escapes its journal")
                    metadata.append({"path": path.name, "sha256": file_sha256(path), "size_bytes": path.stat().st_size})
            partials = []
            for path in sorted((prior / "work").rglob("*")):
                if path.is_file() and (path.name.endswith(".partial") or path.name == "_INCOMPLETE.json"):
                    if not path.resolve().is_relative_to(prior.resolve()):
                        raise ValueError("partial attempt artifact escapes its workspace")
                    partials.append(
                        {
                            "path": path.relative_to(prior).as_posix(),
                            "sha256": file_sha256(path),
                            "size_bytes": path.stat().st_size,
                        }
                    )
            history.append({"attempt": prior.name, "metadata": metadata, "partial_artifacts": partials})
        history_path = self.folder / "histories" / (case_id + ".json")
        publish_json(
            history_path,
            {
                "schema_version": "lob_sim.case_attempt_history.v1",
                "case_id": case_id,
                "completed_attempt": attempt.name,
                "attempts": history,
                "scope": "metadata/partial byte identities;failed data remains external;not provenance authentication",
            },
        )
        self.bindings.append(
            {
                "case_id": case_id,
                "receipt": {
                    "path": receipt_path.relative_to(self.root).as_posix(),
                    "sha256": file_sha256(receipt_path),
                },
                "history": {
                    "path": history_path.relative_to(self.root).as_posix(),
                    "sha256": file_sha256(history_path),
                },
            }
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.study_execution_evidence.v1",
            "plan": {"path": "recovery/plan.json", "sha256": file_sha256(self.folder / "plan.json")},
            "plan_digest": self.store.digest,
            "cases": self.bindings,
            "scope": "sequential verified case reuse;external failed traces retained;not simulation checkpoint append or physical reboot certification",
        }
