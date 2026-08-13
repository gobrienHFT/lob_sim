from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments import compare_futures_strategy_profiles as comparison


def _summary(profile: str) -> dict[str, object]:
    values: dict[str, object] = {key: 0 for _label, key in comparison.COMPARISON_FIELDS}
    values.update(
        {
            "strategy_profile": profile,
            "total_pnl": 0.0,
            "adverse_fill_rate_1s": 0.0,
        }
    )
    return values


def test_profile_comparison_freezes_and_binds_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.ndjson"
    input_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        comparison,
        "_run_profile",
        lambda path, env_path, profile, *, base_config=None: _summary(profile),
    )

    result = comparison.compare_profiles(input_path, ".env.example", "research_mm")
    assert result["research_registry"]["frozen"] is True
    assert len(result["research_registry"]["variants"]) == 2
    assert result["baseline"]["registry_variant_id"] != result["candidate"]["registry_variant_id"]

    sidecar = comparison.build_profile_registry_sidecar(result)
    assert sidecar["schema_version"] == "lob_sim.futures_strategy_profile_registry.v1"
    assert sidecar["row_registry_variant_ids"] == [
        result["baseline"]["registry_variant_id"],
        result["candidate"]["registry_variant_id"],
    ]
    json.dumps(sidecar, allow_nan=False)


def test_profile_comparison_rejects_tampered_registry_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.ndjson"
    input_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        comparison,
        "_run_profile",
        lambda path, env_path, profile, *, base_config=None: _summary(profile),
    )
    result = comparison.compare_profiles(input_path, ".env.example", "research_mm")
    result["candidate"]["registry_variant_id"] = result["baseline"]["registry_variant_id"]

    with pytest.raises(ValueError, match="one-to-one"):
        comparison.build_profile_registry_sidecar(result)
