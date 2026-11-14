"""One native case, optionally reused through a verified immutable store.

The one-shot path retains its original ordering and export contract. Durable
work happens outside the publication directory; only completed verified files
are linked into the final study, never failed-attempt partials.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.regime.study import core_audit_hashes
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.case_publication import materialize_case
from lob_sim.research.case_store import CaseStore
from lob_sim.research.replay_engine import ReplayWindow, ResearchConfig
from lob_sim.research.study_replay import run_study_replay
from lob_sim.research.study_run_reader import verify_case
from lob_sim.research.study_tables import publish_tables, reduce_tables
from lob_sim.sim.run_manifest import config_snapshot


def execute_case(
    root: Path,
    case: dict[str, Any],
    cfg: ResearchConfig,
    view: Path,
    unit: dict[str, Any],
    minimum_free: int,
    *,
    store: CaseStore | None = None,
) -> tuple[dict[str, Any], Path | None]:
    def produce(work: Path) -> dict[str, Any]:
        configuration = replace(cfg, record_dir=work / "runs" / case["case_id"])
        files, summary = run_study_replay(
            configuration,
            view,
            ReplayWindow(**unit["contract"]["window"]),
            minimum_free_disk_bytes=minimum_free,
        )
        tables = (
            None
            if case["variant"] == "baseline"
            else publish_tables(
                files["summary"].parent / "clock_periods.jsonl", reduce_tables(files, summary, unit["contract"])
            )
        )
        return {
            "case": case,
            "configuration": config_snapshot(configuration),
            "run_directory": files["summary"].parent.relative_to(work).as_posix(),
            "artifacts": {k: {"path": p.name, "sha256": file_sha256(p)} for k, p in files.items()},
            "core_audits": core_audit_hashes(files),
            "tables": tables,
            "status": "completed",
        }

    if store is None:
        return produce(root), None

    def verify(work: Path, record: dict[str, Any]) -> None:
        configuration = replace(cfg, record_dir=work / "runs" / case["case_id"])
        verify_case(work, record, case, configuration, view, unit)

    work, record, _reused = store.run_case(case["case_id"], produce, verify)
    materialize_case(work, root, record)
    # Every destination is re-read by the existing producer and final graph
    # verifier. Hard-link publication does not rewrite any native bytes.
    return record, work.parent
