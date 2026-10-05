"""Versioned content-addressed safe JSON model artifacts (never pickle)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, FeatureSpec
from .model import GaussianHMMParameters
from .preprocess import TrainOnlyScaler
from .validation import canonical_json, identity, require_keys, strict_json


@dataclass(frozen=True)
class FrozenRegimeModel:
    features: FeatureSpec
    scaler: TrainOnlyScaler
    parameters: GaussianHMMParameters
    provenance_json: str

    def __post_init__(self) -> None:
        if self.scaler.feature_identity != self.features.digest or self.scaler.feature_names != FEATURE_NAMES:
            raise ValueError("artifact feature formula/order identity mismatch")
        if self.parameters.feature_count != len(FEATURE_NAMES):
            raise ValueError("artifact model/preprocessor dimension mismatch")
        provenance = strict_json(self.provenance_json)
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError("artifact provenance must be a nonempty JSON object")
        # Immutable canonical text prevents mutation through nested metadata.
        object.__setattr__(self, "provenance_json", canonical_json(provenance))

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_model.v1",
            "features": self.features.as_dict(),
            "preprocessing": self.scaler.as_dict(),
            "parameters": self.parameters.as_dict(),
            "provenance": strict_json(self.provenance_json),
        }

    @cached_property
    def model_sha256(self) -> str:
        # Parameters, scaler, spec and canonical provenance are immutable.
        # A derived non-dataclass cache avoids reserializing the complete fit
        # ledger at every risk boundary; it cannot enter artifact/checkpoint
        # fields or change the independently recomputed serialization identity.
        return identity(self._payload())

    def as_dict(self) -> dict[str, Any]:
        result = self._payload()
        result["model_sha256"] = self.model_sha256
        return result

    @classmethod
    def from_dict(cls, value: object) -> FrozenRegimeModel:
        data = require_keys(
            value,
            {"schema_version", "features", "preprocessing", "parameters", "provenance", "model_sha256"},
            "model artifact",
        )
        if data["schema_version"] != "lob_sim.hmm_model.v1":
            raise ValueError("unsupported artifact schema")
        payload = {key: item for key, item in data.items() if key != "model_sha256"}
        if data["model_sha256"] != identity(payload):
            raise ValueError("model artifact SHA-256 mismatch")
        result = cls(
            FeatureSpec.from_dict(data["features"]),
            TrainOnlyScaler.from_dict(data["preprocessing"]),
            GaussianHMMParameters.from_dict(data["parameters"]),
            canonical_json(data["provenance"]),
        )
        if result.model_sha256 != data["model_sha256"]:
            raise ValueError("noncanonical model artifact")
        return result


MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def load_model(path: str | Path) -> FrozenRegimeModel:
    # Bounded reads even if the file grows after its metadata was inspected.
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("model artifact exceeds size limit")
    return FrozenRegimeModel.from_dict(strict_json(raw.decode("utf-8")))


def save_model(path: str | Path, model: FrozenRegimeModel) -> None:
    """No clobber. Failed writes leave a visibly incomplete .partial file."""

    target = Path(path)
    partial = target.with_name(target.name + ".partial")
    encoded = (canonical_json(model.as_dict()) + "\n").encode("utf-8")
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise ValueError("model artifact exceeds size limit")
    if target.exists():
        raise FileExistsError(target)
    with partial.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    # A hard-link finalization is no-clobber on both Windows and POSIX. Unlike
    # replace(), it cannot overwrite evidence created between the checks.
    os.link(partial, target)
    partial.unlink()
