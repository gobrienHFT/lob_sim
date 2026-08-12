from __future__ import annotations

from pathlib import Path

from lob_sim.replay.runner import replay


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
