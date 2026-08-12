"""Run a deterministic fail-closed fault matrix for the reviewer gate.

The matrix deliberately exercises integrity boundaries rather than market
economics.  A passing result means the reader, replay reducer, simulation
clock, and segmented capture validator all reject or quarantine the injected
faults without turning them into claim-ready evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lob_sim.config import load_config
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter, recover_valid_envelopes, validate_segment
from lob_sim.replay.inspection import inspect_stream
from lob_sim.sim.engine import SimulationEngine


CLEAN_FIXTURE = REPO_ROOT / "docs/sample_outputs/futures_schema_v3_case/input_fixture.ndjson"
ADVERSARIAL_FIXTURE = REPO_ROOT / "docs/sample_outputs/futures_schema_v3_case/adversarial_fixture.ndjson"
REPORT_SCHEMA_VERSION = "lob_sim.fault_injection.v1"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )


def _simulation_integrity(path: Path) -> dict[str, Any]:
    engine = SimulationEngine(load_config(REPO_ROOT / ".env.example"))
    engine.run(path)
    annotations = engine._summary_annotations()
    integrity = annotations["integrity"]
    return {
        "capture_valid": integrity["capture_valid"],
        "claim_ready": integrity["claim_ready"],
        "clock_invalidated": integrity["clock_invalidated"],
        "receive_clock_regressions": integrity["receive_clock_regressions"],
        "receive_sequence_gaps": integrity["receive_sequence_gaps"],
        "snapshot_attempts_rejected": integrity["snapshot_attempts_rejected"],
    }


def _clean_case() -> dict[str, Any]:
    inspection = inspect_stream(CLEAN_FIXTURE)
    observed = _simulation_integrity(CLEAN_FIXTURE)
    expected = {
        "receipt_integrity_ok": True,
        "capture_valid": True,
        "claim_ready": True,
    }
    actual = {
        "receipt_integrity_ok": inspection.capture_liveness.receipt_integrity_ok,
        "capture_valid": observed["capture_valid"],
        "claim_ready": observed["claim_ready"],
    }
    return _case_result("clean_schema_v3_control", expected, actual)


def _adversarial_case() -> dict[str, Any]:
    inspection = inspect_stream(ADVERSARIAL_FIXTURE)
    observed = _simulation_integrity(ADVERSARIAL_FIXTURE)
    expected = {
        "receipt_integrity_ok": False,
        "capture_valid": False,
        "claim_ready": False,
    }
    actual = {
        "receipt_integrity_ok": inspection.capture_liveness.receipt_integrity_ok,
        "capture_valid": observed["capture_valid"],
        "claim_ready": observed["claim_ready"],
    }
    return _case_result("capture_failure_and_rejected_snapshot", expected, actual)


def _mutated_fixture(source: Path, target: Path, mutate: Callable[[list[dict[str, Any]]], None]) -> Path:
    rows = _rows(source)
    mutate(rows)
    _write_rows(target, rows)
    return target


def _sequence_gap_case(directory: Path) -> dict[str, Any]:
    def mutate(rows: list[dict[str, Any]]) -> None:
        changed = False
        for row in rows:
            capture = row.get("data", {}).get("_capture")
            if isinstance(capture, dict) and int(capture.get("recvSeq", -1)) >= 4:
                capture["recvSeq"] = int(capture["recvSeq"]) + 1
                changed = True
        if not changed:
            raise AssertionError("control fixture did not contain receive sequence 4 or later")

    path = _mutated_fixture(CLEAN_FIXTURE, directory / "sequence_gap.ndjson", mutate)
    inspection = inspect_stream(path)
    observed = _simulation_integrity(path)
    expected = {
        "receive_sequence_gaps": 1,
        "capture_valid": False,
        "claim_ready": False,
    }
    actual = {
        "receive_sequence_gaps": inspection.capture_liveness.receive_sequence_gaps,
        "capture_valid": observed["capture_valid"],
        "claim_ready": observed["claim_ready"],
    }
    return _case_result("receipt_sequence_gap", expected, actual)


def _clock_regression_case(directory: Path) -> dict[str, Any]:
    def mutate(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            capture = row.get("data", {}).get("_capture")
            if isinstance(capture, dict) and capture.get("recvSeq") == 5:
                capture["recvMonotonicNs"] = 350
                return
        raise AssertionError("control fixture did not contain receive sequence 5")

    path = _mutated_fixture(CLEAN_FIXTURE, directory / "clock_regression.ndjson", mutate)
    inspection = inspect_stream(path)
    observed = _simulation_integrity(path)
    expected = {
        "monotonic_regressions": 1,
        "receive_clock_regressions": 1,
        "clock_invalidated": True,
        "claim_ready": False,
    }
    actual = {
        "monotonic_regressions": inspection.capture_liveness.monotonic_regressions,
        "receive_clock_regressions": observed["receive_clock_regressions"],
        "clock_invalidated": observed["clock_invalidated"],
        "claim_ready": observed["claim_ready"],
    }
    return _case_result("receipt_monotonic_regression", expected, actual)


def _envelope(sequence: int, *, capture_id: str) -> EventEnvelope:
    return EventEnvelope(
        capture_id=capture_id,
        schema_version=SCHEMA_V3,
        venue="BINANCE_USDM",
        instrument="BTCUSDT",
        event_kind="depthUpdate",
        route="public",
        recv_seq=sequence,
        recv_wall_ns=1_000_000_000 + sequence,
        recv_monotonic_ns=5_000 + sequence,
        stream_epoch=1,
        sync_epoch=1,
        payload={"U": sequence, "u": sequence, "b": [], "a": []},
    )


def _truncated_segment_case(directory: Path) -> dict[str, Any]:
    try:
        with SegmentedCaptureWriter(directory, "truncated", compression="none") as writer:
            writer.write(_envelope(1, capture_id="truncated"))
            raise RuntimeError("injected interruption")
    except RuntimeError:
        pass
    partial = directory / "truncated_000000.ndjson.partial"
    report = validate_segment(partial)
    observed = {
        "complete": report.complete,
        "ok": report.ok,
        "recovered_prefix_records": len(list(recover_valid_envelopes(partial))),
        "manifest_exists": (directory / "truncated.manifest.json").exists(),
    }
    expected = {
        "complete": False,
        "ok": False,
        "recovered_prefix_records": 1,
        "manifest_exists": False,
    }
    return _case_result("truncated_segment", expected, observed)


def _checksum_case(directory: Path) -> dict[str, Any]:
    with SegmentedCaptureWriter(directory, "checksum", compression="none") as writer:
        writer.write(_envelope(1, capture_id="checksum"))
    segment = directory / "checksum_000000.ndjson"
    rows = _rows(segment)
    event = next(row for row in rows if row.get("record") == "event")
    event["event"]["payload"]["U"] = 999
    segment.write_text(
        "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )
    report = validate_segment(segment)
    observed = {
        "ok": report.ok,
        "checksum_issue": any("payload checksum mismatch" in issue for issue in report.issues),
        "recoverable_records": len(list(recover_valid_envelopes(segment))),
    }
    expected = {
        "ok": False,
        "checksum_issue": True,
        "recoverable_records": 0,
    }
    return _case_result("payload_checksum_corruption", expected, observed)


def _case_result(name: str, expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "expected": expected,
        "observed": observed,
        "pass": expected == observed,
    }


def run_fault_matrix() -> dict[str, Any]:
    with TemporaryDirectory(prefix="lob_sim_fault_matrix_") as raw_directory:
        directory = Path(raw_directory)
        cases = [
            _clean_case(),
            _adversarial_case(),
            _sequence_gap_case(directory),
            _clock_regression_case(directory),
            _truncated_segment_case(directory),
            _checksum_case(directory),
        ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ok": all(case["pass"] for case in cases),
        "case_count": len(cases),
        "cases": cases,
        "non_claims": [
            "fault-matrix success is not proof of zero venue-side packet loss",
            "fault-matrix success is not proof of private fill truth or production readiness",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="Optional machine-readable report path")
    args = parser.parse_args(argv)
    result = run_fault_matrix()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(encoded, encoding="utf-8", newline="\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
