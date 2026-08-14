from __future__ import annotations

import json

import pytest

from lob_sim.book.types import InstrumentSpec
from lob_sim.sim import run_manifest


def test_instrument_specs_snapshot_is_stable_json_metadata() -> None:
    snapshot = run_manifest.instrument_specs_snapshot(
        {
            "BTCUSDT": InstrumentSpec(
                symbol="BTCUSDT",
                tick_size="0.10",
                step_size="0.001",
                price_currency="USDT",
                quantity_unit="BTC",
                contract_multiplier="1",
                venue="BINANCE_USDM",
            )
        }
    )

    assert snapshot == {
        "BTCUSDT": {
            "symbol": "BTCUSDT",
            "venue": "BINANCE_USDM",
            "price_currency": "USDT",
            "quantity_unit": "BTC",
            "tick_size": "0.10",
            "step_size": "0.001",
            "contract_multiplier": "1",
        }
    }


def test_simulation_assumptions_snapshot_documents_public_data_limits() -> None:
    assumptions = run_manifest.simulation_assumptions_snapshot()

    assert assumptions["schema_version"] == run_manifest.SIMULATION_ASSUMPTIONS_SCHEMA_VERSION
    assert assumptions["fill_assumption_profile"] == "base"
    assert assumptions["fill_assumption"]["profile"] == "base"
    assert assumptions["private_exchange_execution_reports"] is False
    assert assumptions["queue_priority_model"] == "synthetic_queue_ahead_by_price_level"
    assert assumptions["overlap_netting"]["window_seconds"] == 0.125
    assert "not_private_exchange_fill_truth" in assumptions["limitations"]
    assert "public_l2_cannot_distinguish_all_cancels_from_trades" in assumptions["limitations"]


def test_claim_gate_snapshot_is_portable_and_never_a_profitability_claim() -> None:
    gate = run_manifest.claim_gate_snapshot(
        {
            "integrity": {"claim_ready": False},
            "evidence_quality": {"markouts": "diagnostic_only", "markout_reason": "legacy clock"},
            "valuation_complete": False,
            "fill_provenance": {"complete": False},
            "audit_retention": {},
            "markout_horizon_summary": {
                "100": {
                    "horizon_ms": 100,
                    "resolved_samples": 1,
                    "invalidated_samples": 1,
                    "unresolved_samples": 1,
                    "coverage": 0.5,
                    "mean_resolution_lag_ms": 12.0,
                    "max_resolution_lag_ms": 12.0,
                }
            },
        }
    )

    assert gate["schema_version"] == run_manifest.CLAIM_GATE_SCHEMA_VERSION
    assert gate["execution_claim_ready"] is False
    assert gate["markout_clock_claim_ready"] is False
    assert gate["valuation_complete"] is False
    assert gate["model_output_complete"] is False
    assert gate["markout_coverage"]["100"]["coverage"] == 0.5
    assert gate["claim_matrix"]["modeled_pnl"]["status"] == "diagnostic_only"
    assert "legacy clock" in gate["claim_matrix"]["subsecond_markouts"]["reason_codes"]


def test_source_state_uses_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {"git_commit": "abc123", "git_branch": "master", "git_dirty": False}
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, json.dumps(source))

    assert run_manifest.source_state() == source


def test_source_state_rejects_non_object_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, '["not", "an", "object"]')

    with pytest.raises(ValueError, match=run_manifest.SOURCE_STATE_OVERRIDE_ENV):
        run_manifest.source_state()


def test_code_identity_is_streamed_and_self_describing() -> None:
    identity = run_manifest.code_identity()

    assert identity["schema_version"] == "lob_sim.code_identity.v1"
    assert identity["algorithm"] == "sha256"
    assert identity["complete"] is True
    assert identity["file_count"] > 0
    assert isinstance(identity["sha256"], str)
    assert len(identity["sha256"]) == 64
