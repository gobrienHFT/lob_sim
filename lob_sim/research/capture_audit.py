"""Finalized real-capture audit: native validity plus explicit causal intervals."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import canonical_json, identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.run_manifest import config_snapshot
from lob_sim.research.capture_receipts import audit_receipts
from lob_sim.research.capture_intervals import CaptureIntervalObserver, DEPTH_FRESH_NS, TRADE_FRESH_NS
from lob_sim.research.evidence_io import retain_failure


def audit_capture(input_path: str | Path, directory: str | Path, cfg: Config) -> dict[str, Any]:
    """Exclusive publication; failed inputs retain raw bytes and partial audits.

    No strategy, fill, risk or HMM policy is enabled here. Admission does not
    silently recover a damaged source or replace the source's receipt identity.
    """
    if cfg.hmm is not None:
        raise ValueError("capture validity audit requires HMM disabled")
    source, root = Path(input_path).resolve(), Path(directory)
    if source.is_relative_to(root.resolve()):
        raise ValueError("audit output cannot contain its source capture")
    root.mkdir(parents=True, exist_ok=False)
    handle = None
    try:
        code_identity = checkpoint_code_identity()
        receipts = audit_receipts(source, cfg.symbols)
        extraction_cfg = replace(cfg, mm_enabled=False)
        partial = root / "intervals.jsonl.partial"
        handle = partial.open("xb")

        def emit(row: dict[str, Any]) -> None:
            assert handle is not None
            raw = (canonical_json(row) + "\n").encode("utf-8")
            if len(raw) > 64 * 1024:
                raise ValueError("capture interval row exceeds the bounded record limit")
            handle.write(raw)

        observer = CaptureIntervalObserver(receipts, cfg.symbols, emit)
        engine = SimulationEngine(
            extraction_cfg, market_observer=observer, retain_event_trace=False, retain_audit_rows=False
        )
        engine.run(source)
        if receipts != audit_receipts(source, cfg.symbols):
            raise ValueError("source capture changed during native validity audit")
        if checkpoint_code_identity() != code_identity:
            raise ValueError("processing source changed during native validity audit")
        if observer.instrument_changes:
            receipts["integrity"]["ok"] = False
            receipts["integrity"]["reasons"].append("instrument_identity_changed")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        final = root / "intervals.jsonl"
        os.link(partial, final)
        partial.unlink()
        report = {
            "schema_version": "lob_sim.capture_audit.v1",
            **receipts,
            "input_path": str(source),
            "code_identity": code_identity,
            "configuration": config_snapshot(extraction_cfg),
            "freshness": {
                "depth_ns": DEPTH_FRESH_NS,
                "trade_ns": TRADE_FRESH_NS,
                "interval": "left-known [start,end);no future rescue or EOF extrapolation",
            },
            "duration_ns": observer.duration_ns,
            "joint_valid_ns": observer.joint_valid_ns,
            "joint_mark_available_ns": observer.joint_mark_ns,
            "joint_valid_fraction": observer.joint_valid_ns / observer.duration_ns if observer.duration_ns else None,
            "symbols": observer.totals,
            "instruments": observer.instruments,
            "epoch_changes": observer.epoch_changes,
            "native_book_gap_count": engine.metrics.book_gap_count,
            "native_book_gaps_by_symbol": dict(sorted(engine.metrics.book_gap_count_by_symbol.items())),
            "intervals": {"path": final.name, "sha256": file_sha256(final), "rows": observer.rows},
            "research_usable": receipts["integrity"]["ok"] and receipts["empirical_source"] is True,
            "memory_contract": "one native engine plus per-symbol observer state and one pending interval;no retained detail rows",
            "non_claims": [
                "not complete UTC-day certification",
                "not venue packet-loss or private fill truth",
                "declared source provenance is not author authentication",
            ],
        }
        report["report_sha256"] = identity(report)
        publish_json(root / "report.json", report)
        return report
    except BaseException as exc:
        if handle is not None:
            try:
                handle.close()
            except BaseException as secondary:
                exc.add_note("Interval close failed: " + type(secondary).__name__)
        retain_failure(root, exc, schema="lob_sim.capture_audit_failure.v1", input_path=str(source))
        raise
