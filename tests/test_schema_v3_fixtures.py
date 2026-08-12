from __future__ import annotations

from pathlib import Path

from lob_sim.replay.runner import replay
from lob_sim.replay.inspection import inspect_stream


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_schema_v3_case"


def test_committed_schema_v3_fixture_is_claim_ready() -> None:
    result = replay(FIXTURE_DIR / "input_fixture.ndjson")

    assert result.validity is not None
    assert result.validity.claim_ready is True
    assert result.validity.capture_valid is True
    assert result.validity.clock_valid is True
    assert result.validity.capture_trailer_seen is True
    assert result.validity.boundary_count == 2
    assert all(boundary.kind == "recovered" for boundary in result.validity.boundaries)
    assert result.symbols["BTCUSDT"].validity is not None
    assert result.symbols["BTCUSDT"].validity.execution_inputs_valid is True


def test_committed_adversarial_schema_v3_fixture_fails_closed() -> None:
    result = replay(FIXTURE_DIR / "adversarial_fixture.ndjson")

    assert result.validity is not None
    assert result.validity.claim_ready is False
    assert result.validity.capture_valid is False
    assert result.validity.clock_valid is False
    assert result.validity.capture_trailer_seen is True
    assert result.symbols["BTCUSDT"].validity is not None
    assert result.symbols["BTCUSDT"].validity.execution_inputs_valid is False
    scopes = {boundary.scope for boundary in result.validity.boundaries if boundary.kind == "invalidated"}
    assert {"stream", "clock", "book", "capture"}.issubset(scopes)
    assert result.validity.boundaries_omitted == 0


def test_committed_schema_v3_fixture_inspection_reports_receipt_liveness() -> None:
    clean = inspect_stream(FIXTURE_DIR / "input_fixture.ndjson").capture_liveness
    adversarial = inspect_stream(FIXTURE_DIR / "adversarial_fixture.ndjson").capture_liveness

    assert clean.schema_version == 3
    assert clean.receive_clock is True
    assert clean.records_with_receipt == 7
    assert clean.records_missing_receipt == 0
    assert clean.first_recv_seq == 1
    assert clean.last_recv_seq == 7
    assert clean.receive_sequence_gaps == 0
    assert clean.receive_sequence_regressions == 0
    assert clean.monotonic_regressions == 0
    assert clean.max_interarrival_ns == 100
    assert clean.routes == {"control": 2, "market": 2, "public": 3}
    assert clean.trailer_seen is True
    assert clean.receipt_integrity_ok is True

    assert adversarial.receive_sequence_regressions == 0
    assert adversarial.monotonic_regressions == 1
    assert adversarial.invalidation_event_count == 1
    assert adversarial.capture_event_counts["overflow"] == 1
    assert adversarial.receipt_integrity_ok is False
