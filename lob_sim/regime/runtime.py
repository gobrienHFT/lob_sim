"""Frozen model inference on causal feature samples; no execution dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .artifact import FrozenRegimeModel
from .features import FEATURE_NAMES, FeatureSample
from .filter import ForwardFilter
from .hysteresis import HysteresisConfig, HysteresisResult, RegimeHysteresis
from .validation import integer, require_keys


@dataclass(frozen=True)
class RegimeSignal:
    sample: FeatureSample
    model_sha256: str
    scaled_values: tuple[float, ...] | None
    posterior: tuple[float, ...] | None
    next_prior: tuple[float, ...] | None
    confidence: float | None
    entropy: float | None
    normalized_entropy: float | None
    hysteresis: HysteresisResult | None
    filter_reset_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        hysteresis = self.hysteresis
        return {
            "schema_version": "lob_sim.hmm_regime_signal.v1",
            "symbol": self.sample.symbol,
            "sample_ns": self.sample.sample_ns,
            "receive_seq": self.sample.receive_seq,
            "epochs": list(self.sample.epochs),
            "status": self.sample.status,
            "model_sha256": self.model_sha256,
            "feature_identity": self.sample.feature_identity,
            "features": list(self.sample.values) if self.sample.values is not None else None,
            "scaled_features": list(self.scaled_values) if self.scaled_values is not None else None,
            "posterior": list(self.posterior) if self.posterior is not None else None,
            "next_prior": list(self.next_prior) if self.next_prior is not None else None,
            "raw_map_state": hysteresis.raw_map_state if hysteresis is not None else None,
            "active_state": hysteresis.active_state if hysteresis is not None else None,
            "confidence": self.confidence,
            "entropy": self.entropy,
            "normalized_entropy": self.normalized_entropy,
            "candidate_state": hysteresis.candidate_state if hysteresis is not None else None,
            "confirmation_streak": hysteresis.confirmation_streak if hysteresis is not None else 0,
            "state_age_samples": hysteresis.state_age_samples if hysteresis is not None else 0,
            "state_switched": hysteresis.switched if hysteresis is not None else False,
            "state_switch_reason": hysteresis.reason if hysteresis is not None else None,
            "confident": hysteresis.confident if hysteresis is not None else False,
            "feature_reset_reason": self.sample.reset_reason,
            "filter_reset_reason": self.filter_reset_reason,
        }


class CausalRegimeEstimator:
    """O(K*D + K*K) state. Emit a signal to an external bounded sink; don't retain history."""

    def __init__(self, model: FrozenRegimeModel, hysteresis: HysteresisConfig = HysteresisConfig()) -> None:
        self._model = model
        self._model_sha256 = model.model_sha256
        self._feature_identity = model.features.digest
        self.filter = ForwardFilter(model.parameters)
        self.hysteresis = RegimeHysteresis(model.parameters.state_count, hysteresis)
        self._last_sample_ns: int | None = None
        self._last_receive_seq: int | None = None
        self._epochs: tuple[int, int, int] | None = None
        self._symbol: str | None = None
        self._reset_reason: str | None = "initialization"

    @property
    def model(self) -> FrozenRegimeModel:
        return self._model

    def invalidate(self, reason: str) -> None:
        """Clear confidence immediately, including faults between grid samples."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalidation reason must be nonempty")
        self.filter.reset()
        self.hysteresis.reset()
        self._reset_reason = reason

    def checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_estimator_checkpoint.v1",
            "model_sha256": self._model_sha256,
            "filter": self.filter.checkpoint(),
            "hysteresis": self.hysteresis.checkpoint(),
            "last_sample_ns": self._last_sample_ns,
            "last_receive_seq": self._last_receive_seq,
            "epochs": list(self._epochs) if self._epochs is not None else None,
            "symbol": self._symbol,
            "reset_reason": self._reset_reason,
        }

    def restore(self, checkpoint: object) -> None:
        data = require_keys(checkpoint, set(self.checkpoint()), "estimator checkpoint")
        if (
            data["schema_version"] != "lob_sim.hmm_estimator_checkpoint.v1"
            or data["model_sha256"] != self._model_sha256
        ):
            raise ValueError("estimator checkpoint model/schema mismatch")
        candidate = CausalRegimeEstimator(self.model, self.hysteresis.config)
        candidate.filter.restore(data["filter"])
        candidate.hysteresis.restore(data["hysteresis"])
        last, seq, epochs, symbol = (data[key] for key in ("last_sample_ns", "last_receive_seq", "epochs", "symbol"))
        if any(value is None for value in (last, seq, epochs, symbol)):
            if not all(value is None for value in (last, seq, epochs, symbol)) or candidate.filter.samples_seen:
                raise ValueError("inconsistent estimator initialization")
        else:
            integer(last, "last_sample_ns")
            integer(seq, "last_receive_seq")
            if last % self.model.features.interval_ns:
                raise ValueError("estimator checkpoint is off grid")
            # FeatureSample validates symbol and epoch dimensions/types.
            FeatureSample(symbol, last, seq, tuple(epochs), self._feature_identity, "WARMING_UP", None, None)
            candidate._last_sample_ns, candidate._last_receive_seq = last, seq
            candidate._epochs, candidate._symbol = tuple(epochs), symbol
        reason = data["reset_reason"]
        if reason is not None and (not isinstance(reason, str) or not reason or candidate.filter.samples_seen):
            raise ValueError("inconsistent estimator reset state")
        if (
            not candidate.filter.samples_seen
            and candidate.hysteresis.checkpoint()
            != RegimeHysteresis(self.model.parameters.state_count, self.hysteresis.config).checkpoint()
        ):
            raise ValueError("reset estimator retains hysteresis")
        candidate._reset_reason = reason
        self.__dict__.update(candidate.__dict__)

    def update(self, sample: FeatureSample) -> RegimeSignal:
        integer(sample.sample_ns, "sample_ns")
        integer(sample.receive_seq, "receive_seq")
        if sample.feature_identity != self._feature_identity:
            raise ValueError("runtime feature identity mismatch")
        if self._symbol is not None and sample.symbol != self._symbol:
            raise ValueError("one estimator cannot mix symbols")
        if self._last_sample_ns is not None and sample.sample_ns <= self._last_sample_ns:
            raise ValueError("regime sample time must strictly increase")
        if self._last_receive_seq is not None and sample.receive_seq < self._last_receive_seq:
            raise ValueError("regime receive sequence cannot regress")
        if sample.sample_ns % self.model.features.interval_ns:
            raise ValueError("regime sample is not on the frozen clock grid")
        if (sample.status == "VALID") != (sample.values is not None):
            raise ValueError("regime feature status/value mismatch")
        # Validate/transform before any state mutation.
        scaled = (
            self.model.scaler.transform(
                sample.values, feature_names=FEATURE_NAMES, feature_identity=sample.feature_identity
            )
            if sample.values is not None
            else None
        )
        reason = self._reset_reason
        if self._epochs is not None and sample.epochs != self._epochs:
            reason = "epoch_changed"
        elif (
            self._last_sample_ns is not None
            and sample.sample_ns != self._last_sample_ns + self.model.features.interval_ns
        ):
            reason = "sample_interval_gap"
        if sample.status != "VALID":
            reason = sample.status
        if reason is not None:
            self.filter.reset()
            self.hysteresis.reset()
        posterior = next_prior = None
        confidence = entropy = normalized_entropy = None
        hysteresis = None
        if scaled is not None:
            try:
                result = self.filter.update(scaled)
            except ValueError:
                # A numerically unrepresentable emission must not leave the
                # previous confident signal live. Stop inference explicitly.
                self.filter.reset()
                self.hysteresis.reset()
                self._reset_reason = "NONFINITE_FILTER"
                raise
            hysteresis = self.hysteresis.update(result.posterior)
            posterior, next_prior = result.posterior, result.next_prior
            confidence, entropy, normalized_entropy = result.confidence, result.entropy, result.normalized_entropy
        signal = RegimeSignal(
            sample,
            self._model_sha256,
            scaled,
            posterior,
            next_prior,
            confidence,
            entropy,
            normalized_entropy,
            hysteresis,
            reason,
        )
        self._last_sample_ns, self._last_receive_seq, self._epochs, self._symbol = (
            sample.sample_ns,
            sample.receive_seq,
            sample.epochs,
            sample.symbol,
        )
        self._reset_reason = None if sample.status == "VALID" else sample.status
        return signal
