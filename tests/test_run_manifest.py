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


def test_source_state_uses_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    source = {"git_commit": "abc123", "git_branch": "master", "git_dirty": False}
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, json.dumps(source))

    assert run_manifest.source_state() == source


def test_source_state_rejects_non_object_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(run_manifest.SOURCE_STATE_OVERRIDE_ENV, '["not", "an", "object"]')

    with pytest.raises(ValueError, match=run_manifest.SOURCE_STATE_OVERRIDE_ENV):
        run_manifest.source_state()
