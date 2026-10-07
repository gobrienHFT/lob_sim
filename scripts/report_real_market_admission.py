"""Publish a compact source-bound network smoke report, never a soak claim."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity
from lob_sim.research.day_bundle import verify_day_bundle
from lob_sim.research.evidence_io import read_json
from lob_sim.research.soak_reader import verify_soak


def report(capture_root: Path, day_root: Path, output: Path) -> dict[str, Any]:
    host_proof = verify_soak(capture_root)
    completion = read_json(capture_root / "completion.json")
    capture_path = capture_root / completion["capture_manifest"]
    proof = verify_day_bundle(day_root, (capture_path,))
    day_manifest = read_json(day_root / "manifest.json")
    parent = next(p for p in day_manifest["parents"] if p["input_sha256"] == completion["capture_manifest_sha256"])
    audit = read_json(day_root / parent["directory"] / "report.json")
    days = read_json(day_root / "days.json")
    runtime = audit["capture_runtime"]["runtime"]
    duration = audit["duration_ns"]
    stop = audit["stop_ns"]
    result = {
        "schema_version": "lob_sim.real_market_admission_smoke.v1",
        "role": "public-network smoke and evidence admission mechanics;not representative soak or research",
        "source": {
            "capture_id": audit["capture_id"],
            "manifest_sha256": audit["input_sha256"],
            "segments": audit["segments"],
            "configured_seconds": completion["configured_seconds"],
            "source_revision": runtime["source"],
            "code_identity": runtime["code_identity"],
            "python_version": runtime["python_version"],
            "platform": runtime["platform"],
        },
        "processing": {
            "code_identity": audit["code_identity"],
            "config_sha256": identity(audit["configuration"]),
            "audit_sha256": audit["report_sha256"],
            "day_bundle_sha256": proof["bundle_sha256"],
            "day_report_sha256": days["report_sha256"],
            "clock_sha256": audit["clock_sha256"],
            "intervals": audit["intervals"],
        },
        "integrity": audit["integrity"],
        "liveness": audit["liveness"],
        "host_telemetry": completion["host_telemetry"],
        "validity": {
            "observed_duration_ns": duration,
            "joint_valid_ns": audit["joint_valid_ns"],
            "joint_mark_available_ns": audit["joint_mark_available_ns"],
            "joint_valid_fraction_of_observed_capture": audit["joint_valid_fraction"],
            "declared_shutdown_tail_ns": audit["last_logical_ns"] - stop if stop is not None else None,
            "symbols": audit["symbols"],
            "instruments": audit["instruments"],
            "epoch_changes": audit["epoch_changes"],
            "native_book_gap_count": audit["native_book_gap_count"],
        },
        "day_admission": {
            "rule": days["rule"],
            "rule_sha256": days["rule_sha256"],
            "days": [
                {
                    k: d[k]
                    for k in (
                        "utc_day",
                        "total_utc_ns",
                        "covered_ns",
                        "joint_valid_ns",
                        "joint_mark_available_ns",
                        "uncovered_ns",
                        "eligible",
                        "exclusions",
                    )
                }
                for d in days["days"]
            ],
            "eligible_days": days["eligible_days"],
            "ready_for_registration": days["ready_for_registration"],
            "registration_blockers": days["registration_blockers"],
        },
        "independent_re_read": {"host": host_proof, "day_bundle": proof},
        "empirical_claim_ready": False,
        "non_claims": [
            "not 24-hour-plus soak or bounded-memory-duration proof",
            "not ten eligible research days",
            "not held-out HMM or strategy evidence",
            "not private fills, historical FIFO or zero venue packet loss",
            "sampled RSS/loop/writer telemetry is not trading latency",
            "dirty source state is disclosed, not a clean-clone release",
        ],
    }
    result["report_sha256"] = identity(result)
    publish_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--days", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = report(args.capture, args.days, args.out)
    print(result["report_sha256"])


if __name__ == "__main__":
    main()
