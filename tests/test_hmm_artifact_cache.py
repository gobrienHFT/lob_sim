from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from lob_sim.regime.artifact import FrozenRegimeModel, load_model, save_model
from lob_sim.sim.checkpoint import encode, decode
from test_hmm_observation import settings


def independent_identity(model):
    payload = model.as_dict()
    payload.pop("model_sha256")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def test_frozen_identity_is_computed_once_and_matches_independent_serialization(monkeypatch):
    import lob_sim.regime.artifact as module

    model = settings().model
    original = module.identity
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(module, "identity", counted)
    hashes = [model.model_sha256 for _ in range(100)]
    assert len(calls) == 1
    assert set(hashes) == {independent_identity(model)}
    assert len(calls) == 1
    assert [field.name for field in fields(model)] == ["features", "scaler", "parameters", "provenance_json"]
    assert "model_sha256" not in encode(model)["fields"]
    restored = decode(encode(model))
    assert restored == model
    assert restored.model_sha256 == hashes[0]
    assert len(calls) == 2  # Recompute from immutable source on new construction.


def test_cache_cannot_be_changed_via_frozen_interface_and_replace_gets_new_identity():
    model = settings().model
    before = model.model_sha256
    with pytest.raises(FrozenInstanceError):
        model.model_sha256 = "a" * 64
    updated = replace(model, provenance_json='{"purpose":"different immutable model"}')
    assert updated.model_sha256 != before
    assert updated.model_sha256 == independent_identity(updated)
    assert model.model_sha256 == before


def test_cached_and_uncached_models_have_identical_artifact_bytes(tmp_path):
    cached = settings().model
    encoded = cached.as_dict()
    uncached = FrozenRegimeModel.from_dict(encoded)
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    save_model(first, cached)
    save_model(second, uncached)
    assert first.read_bytes() == second.read_bytes()
    assert load_model(first).model_sha256 == independent_identity(cached)
    data = json.loads(first.read_text())
    data["provenance"]["purpose"] = "corrupt after caching"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        FrozenRegimeModel.from_dict(data)
