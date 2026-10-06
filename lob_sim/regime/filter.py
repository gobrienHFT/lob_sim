"""Causal log-space forward recursion. No Viterbi, smoothing or fit dependency."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from .model import GaussianHMMParameters
from .validation import finite, identity, integer, probabilities, require_keys, vector


def _log(value: float) -> float:
    return math.log(value) if value > 0 else -math.inf


def _logsumexp(values: Sequence[float]) -> float:
    largest = max(values)
    if largest == -math.inf:
        return largest
    return largest + math.log(math.fsum(math.exp(value - largest) for value in values))


@dataclass(frozen=True)
class FilterResult:
    posterior: tuple[float, ...]
    next_prior: tuple[float, ...]
    log_likelihood_increment: float
    samples_seen: int

    @property
    def map_state(self) -> int:
        # First index wins exact ties; semantic ordering is frozen at fitting.
        return max(range(len(self.posterior)), key=self.posterior.__getitem__)

    @property
    def confidence(self) -> float:
        return max(self.posterior)

    @property
    def entropy(self) -> float:
        return -math.fsum(p * math.log(p) for p in self.posterior if p > 0)

    @property
    def normalized_entropy(self) -> float:
        return self.entropy / math.log(len(self.posterior))


class ForwardFilter:
    """One transition per call/sample, with an explicit reset at sequence boundaries.

    Keep normalized *log* probabilities internally. A tiny state probability
    rounded to zero in an exported posterior must not disappear from subsequent
    inference. Rejected input updates leave the previous filter state unchanged.
    """

    def __init__(self, parameters: GaussianHMMParameters) -> None:
        self._parameters = parameters
        self._log_transition = tuple(tuple(_log(p) for p in row) for row in parameters.transition)
        self._log_posterior: tuple[float, ...] | None = None
        self.samples_seen = 0

    @property
    def parameters(self) -> GaussianHMMParameters:
        return self._parameters

    def reset(self) -> None:
        self._log_posterior = None
        self.samples_seen = 0

    @property
    def posterior(self) -> tuple[float, ...] | None:
        return None if self._log_posterior is None else tuple(math.exp(p) for p in self._log_posterior)

    def _prior(self) -> tuple[float, ...]:
        if self._log_posterior is None:
            return tuple(_log(p) for p in self.parameters.start)
        return tuple(
            _logsumexp(tuple(self._log_posterior[left] + row[right] for left, row in enumerate(self._log_transition)))
            for right in range(self.parameters.state_count)
        )

    def update(self, observation: Sequence[float]) -> FilterResult:
        values = vector(observation, "observation", self.parameters.feature_count)
        emissions: list[float] = []
        for means, variances in zip(self.parameters.means, self.parameters.variances):
            try:
                emission = -0.5 * math.fsum(
                    math.log(2 * math.pi) + math.log(var) + ((x - mean) / math.sqrt(var)) ** 2
                    for x, mean, var in zip(values, means, variances)
                )
            except (OverflowError, ValueError) as exc:
                raise ValueError("non-finite Gaussian emission; input rejected") from exc
            if not math.isfinite(emission):
                raise ValueError("non-finite Gaussian emission; input rejected")
            emissions.append(emission)
        weights = tuple(prior + emission for prior, emission in zip(self._prior(), emissions))
        normalizer = _logsumexp(weights)
        if not math.isfinite(normalizer):
            raise ValueError("non-finite forward likelihood; input rejected")
        logs = tuple(weight - normalizer for weight in weights)
        # Renormalize after subtracting a large likelihood to contain cancellation.
        residual = _logsumexp(logs)
        logs = tuple(value - residual for value in logs)
        posterior = tuple(math.exp(value) for value in logs)
        next_prior = tuple(
            math.fsum(posterior[left] * row[right] for left, row in enumerate(self.parameters.transition))
            for right in range(self.parameters.state_count)
        )
        result = FilterResult(posterior, next_prior, normalizer, self.samples_seen + 1)
        self._log_posterior = logs
        self.samples_seen += 1
        return result

    def checkpoint(self) -> dict[str, Any]:
        # None encodes impossible states; JSON must never carry +/-infinity.
        return {
            "schema_version": "lob_sim.hmm_filter_checkpoint.v1",
            "parameters_sha256": identity(self.parameters.as_dict()),
            "samples_seen": self.samples_seen,
            "log_posterior": (
                [None if value == -math.inf else value for value in self._log_posterior]
                if self._log_posterior is not None
                else None
            ),
        }

    def restore(self, checkpoint: object) -> None:
        data = require_keys(
            checkpoint, {"schema_version", "parameters_sha256", "samples_seen", "log_posterior"}, "filter checkpoint"
        )
        if data["schema_version"] != "lob_sim.hmm_filter_checkpoint.v1" or data["parameters_sha256"] != identity(
            self.parameters.as_dict()
        ):
            raise ValueError("filter checkpoint model/schema mismatch")
        count = integer(data["samples_seen"], "samples_seen")
        raw = data["log_posterior"]
        logs = None
        if raw is not None:
            if not isinstance(raw, list) or len(raw) != self.parameters.state_count or count == 0:
                raise ValueError("filter checkpoint dimension/count mismatch")
            logs = tuple(-math.inf if value is None else finite(value, "log_posterior") for value in raw)
            if any(value > 1e-10 for value in logs):
                raise ValueError("log_posterior cannot be positive")
            probabilities(tuple(math.exp(value) for value in logs), "checkpoint posterior", len(logs))
        elif count != 0:
            raise ValueError("empty filter checkpoint must have zero samples")
        self._log_posterior, self.samples_seen = logs, count
