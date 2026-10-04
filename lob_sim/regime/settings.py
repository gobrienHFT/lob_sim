"""One immutable opt-in runtime namespace; disabled runs have no HMM config keys."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact import FrozenRegimeModel, load_model
from .hysteresis import HysteresisConfig
from .validation import strict_json


@dataclass(frozen=True)
class HMMSettings:
    model: FrozenRegimeModel
    symbol: str
    hysteresis: HysteresisConfig = HysteresisConfig()
    mode: str = "observe"

    def __post_init__(self) -> None:
        if not isinstance(self.model, FrozenRegimeModel) or not isinstance(self.hysteresis, HysteresisConfig):
            raise ValueError("HMM requires a frozen model and hysteresis configuration")
        if self.mode != "observe":
            raise ValueError("only observation-only HMM mode is implemented")
        if not isinstance(self.symbol, str) or not self.symbol.strip() or self.symbol != self.symbol.upper():
            raise ValueError("HMM symbol must be a nonempty uppercase instrument")
        training = strict_json(self.model.provenance_json).get("training")
        if training is not None and (not isinstance(training, dict) or training.get("symbol") != self.symbol):
            raise ValueError("HMM model training symbol mismatch")

    @classmethod
    def load(
        cls, path: str | Path, *, symbol: str | None = None, hysteresis: HysteresisConfig = HysteresisConfig()
    ) -> HMMSettings:
        model = load_model(path)
        training = strict_json(model.provenance_json).get("training", {})
        selected = symbol or (training.get("symbol") if isinstance(training, dict) else None)
        if selected is None:
            raise ValueError("model has no training symbol; specify --hmm-symbol for a diagnostic model")
        return cls(model, selected, hysteresis)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_runtime_config.v1",
            "mode": self.mode,
            "symbol": self.symbol,
            "model_sha256": self.model.model_sha256,
            "feature_identity": self.model.features.digest,
            "hysteresis": self.hysteresis.as_dict(),
            "model_mode": "frozen_train_model",
        }
