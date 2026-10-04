"""Explicit, train-relative risk controls; never an alpha or venue model.

The controller is stateless. It consumes only the causal signal passed by the
scheduler, and cannot access a book, RNG, inventory, fills or hard risk limits.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .artifact import FrozenRegimeModel
from .validation import finite, identity, integer, probabilities, require_keys, strict_json, vector

RISK_SIGNATURE = (
    "equal_rank_weights:spread,volatility,negative_depth,absolute_L1_pressure,absolute_trade_pressure;training_only"
)


def training_risk_scores(model: FrozenRegimeModel) -> tuple[float, ...]:
    """Reject absent/inconsistent policy provenance, not merely missing numbers.

    Content hashes are identity, not a trusted-author signature. These checks
    cannot prove that a third party actually used the declared training tape.
    Hand-specified diagnostic models remain usable in observation mode only.
    """
    metadata = strict_json(model.provenance_json)
    training = metadata.get("training")
    if (
        not isinstance(training, dict)
        or training.get("role") != "calibration"
        or training.get("feature_identity") != model.features.digest
        or metadata.get("risk_signature") != RISK_SIGNATURE
        or metadata.get("occupancy_method") != "retrospective_training_smoothing;never_runtime_inference"
    ):
        raise ValueError("policy requires explicit training-only risk characterization")
    rows = integer(training.get("rows"), "training rows", minimum=1)
    for key in ("rows_sha256", "dataset_sha256", "split_sha256", "instrument_sha256"):
        value = training.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("policy training provenance requires SHA-256 identities")
    states = metadata.get("state_characterization")
    k = model.parameters.state_count
    if not isinstance(states, list) or len(states) != k:
        raise ValueError("policy risk characterization state count mismatch")
    scores, occupancy, effective = [], [], []
    for index, state in enumerate(states):
        if not isinstance(state, dict) or state.get("state") != f"STATE_{index}":
            raise ValueError("policy risk characterization must use canonical state order")
        ranks = vector(state.get("risk_component_ranks", ()), "risk component ranks", 5)
        score = finite(state.get("risk_score"), "training risk score")
        occupied = finite(state.get("training_occupancy"), "training occupancy")
        count = finite(state.get("training_effective_observations"), "training effective observations")
        if (
            not 0 <= score <= 1
            or not 0 < occupied <= 1
            or count <= 0
            or not math.isclose(count, occupied * rows, rel_tol=1e-9, abs_tol=1e-9)
            or any(
                not 0 <= rank <= 1 or not math.isclose(rank * (k - 1), round(rank * (k - 1)), abs_tol=1e-10)
                for rank in ranks
            )
            or not math.isclose(score, math.fsum(ranks) / 5, rel_tol=0, abs_tol=1e-12)
        ):
            raise ValueError("inconsistent training risk ranks/score/occupancy")
        scores.append(score)
        occupancy.append(occupied)
        effective.append(count)
    if (
        scores != sorted(scores)
        or not math.isclose(math.fsum(occupancy), 1, abs_tol=1e-9)
        or not math.isclose(math.fsum(effective), rows, rel_tol=1e-9, abs_tol=1e-9)
    ):
        raise ValueError("noncanonical training risk order or occupancy")
    for column in range(5):
        column_ranks = [state["risk_component_ranks"][column] for state in states]
        if any(
            not math.isclose(rank, sum(other < rank for other in column_ranks) / (k - 1), rel_tol=0, abs_tol=1e-10)
            for rank in column_ranks
        ):
            raise ValueError("inconsistent cross-state training percentile ranks")
    return tuple(scores)


@dataclass(frozen=True)
class RegimePolicyConfig:
    spread_slope: float = 2.0
    max_spread_multiplier: float = 3.0
    size_reduction: float = 0.75
    min_size_multiplier: float = 0.25
    inventory_reduction: float = 0.5
    min_inventory_multiplier: float = 0.5
    skew_slope: float = 1.0
    max_skew_multiplier: float = 2.0
    refresh_slope: float = 3.0
    max_refresh_multiplier: float = 4.0
    base_max_quote_age_ms: float = 2000.0
    uncertainty_weight: float = 0.25
    stand_aside_risk: float = 0.95
    high_risk_state_confidence: float = 0.90

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            parsed = finite(value, name)
            if parsed < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, parsed)
        for name in ("max_spread_multiplier", "max_skew_multiplier", "max_refresh_multiplier"):
            if not 1 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} must be in [1,100]")
        for name in ("size_reduction", "inventory_reduction", "min_size_multiplier", "min_inventory_multiplier"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0,1]")
        for name in ("stand_aside_risk", "high_risk_state_confidence"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0,1]")
        if not 1 <= self.base_max_quote_age_ms <= 3_600_000:
            raise ValueError("base_max_quote_age_ms must be in [1,3600000]")

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": "lob_sim.hmm_policy_config.v1", **asdict(self)}

    @classmethod
    def from_dict(cls, value: object) -> RegimePolicyConfig:
        data = dict(require_keys(value, set(cls().as_dict()), "policy configuration"))
        if data.pop("schema_version") != "lob_sim.hmm_policy_config.v1":
            raise ValueError("unsupported policy configuration schema")
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> RegimePolicyConfig:
        with Path(path).open("rb") as handle:
            raw = handle.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("policy configuration exceeds size limit")
        return cls.from_dict(strict_json(raw.decode("utf-8")))


@dataclass(frozen=True)
class RegimeControls:
    posterior_weighted_risk: float | None
    effective_risk: float | None
    spread_multiplier: float
    size_multiplier: float
    inventory_limit_multiplier: float
    skew_multiplier: float
    refresh_multiplier: float
    max_quote_age_ns: int
    stand_aside: bool
    reason: str

    def __post_init__(self) -> None:
        for name in ("posterior_weighted_risk", "effective_risk"):
            value = getattr(self, name)
            if value is not None and not 0 <= finite(value, name) <= 1:
                raise ValueError("invalid policy risk score")
        for name in ("spread_multiplier", "skew_multiplier", "refresh_multiplier"):
            if not 1 <= finite(getattr(self, name), name) <= 100:
                raise ValueError("policy must not narrow width/skew or extend quote age")
        for name in ("size_multiplier", "inventory_limit_multiplier"):
            if not 0 <= finite(getattr(self, name), name) <= 1:
                raise ValueError("policy must not increase size or hard position limits")
        integer(self.max_quote_age_ns, "max_quote_age_ns", minimum=1)
        if type(self.stand_aside) is not bool or not isinstance(self.reason, str) or not self.reason:
            raise ValueError("invalid policy stand-aside/reason")

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": "lob_sim.hmm_controls.v1", **asdict(self)}


@dataclass(frozen=True)
class RegimeRiskPolicy:
    model: FrozenRegimeModel
    config: RegimePolicyConfig = RegimePolicyConfig()
    model_sha256: str = field(init=False)
    state_risks: tuple[float, ...] = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.model, FrozenRegimeModel) or not isinstance(self.config, RegimePolicyConfig):
            raise ValueError("policy requires immutable configuration")
        object.__setattr__(self, "model_sha256", self.model.model_sha256)
        object.__setattr__(self, "state_risks", training_risk_scores(self.model))
        object.__setattr__(
            self, "identity", identity({"model_sha256": self.model_sha256, "config": self.config.as_dict()})
        )

    def _controls(self, weighted: float | None, risk: float | None, stand_aside: bool, reason: str) -> RegimeControls:
        cfg, r = self.config, 1.0 if risk is None else risk
        refresh = min(cfg.max_refresh_multiplier, 1 + cfg.refresh_slope * r)
        return RegimeControls(
            weighted,
            risk,
            min(cfg.max_spread_multiplier, 1 + cfg.spread_slope * r),
            max(cfg.min_size_multiplier, 1 - cfg.size_reduction * r),
            max(cfg.min_inventory_multiplier, 1 - cfg.inventory_reduction * r),
            min(cfg.max_skew_multiplier, 1 + cfg.skew_slope * r),
            refresh,
            max(1, int(cfg.base_max_quote_age_ms * 1_000_000 / refresh)),
            stand_aside,
            reason,
        )

    def evaluate(
        self, signal: Mapping[str, Any], *, enter_probability: float = 0.70, maximum_normalized_entropy: float = 0.95
    ) -> RegimeControls:
        if not 0.5 < finite(enter_probability, "policy enter probability") <= 1 or not (
            0 <= finite(maximum_normalized_entropy, "policy maximum entropy") <= 1
        ):
            raise ValueError("invalid policy confidence/entropy thresholds")
        if signal.get("model_sha256") != self.model_sha256:
            raise ValueError("policy signal model mismatch")
        if signal.get("status") != "VALID":
            return self._controls(None, None, True, "invalid_or_warming_information")
        validity = signal.get("validity")
        if isinstance(validity, dict) and validity.get("execution_valid") is not True:
            return self._controls(None, None, True, "invalid_information_set")
        k = len(self.state_risks)
        posterior = probabilities(signal.get("posterior", ()), "policy posterior", k)
        raw = max(range(k), key=posterior.__getitem__)
        entropy = -math.fsum(p * math.log(p) for p in posterior if p > 0) / math.log(k)
        if (
            integer(signal.get("raw_map_state"), "policy raw state") != raw
            or not math.isclose(
                finite(signal.get("confidence"), "policy confidence"), posterior[raw], rel_tol=0, abs_tol=1e-12
            )
            or not math.isclose(
                finite(signal.get("normalized_entropy"), "policy entropy"), entropy, rel_tol=0, abs_tol=1e-12
            )
        ):
            raise ValueError("inconsistent policy posterior diagnostics")
        weighted = math.fsum(p * r for p, r in zip(posterior, self.state_risks))
        risk = min(1.0, weighted + self.config.uncertainty_weight * entropy)
        active = signal.get("active_state")
        if active is None:
            return self._controls(weighted, risk, True, "unconfirmed_state")
        if integer(active, "policy active state") >= k:
            raise ValueError("policy active state out of range")
        if posterior[raw] < enter_probability or entropy > maximum_normalized_entropy:
            return self._controls(weighted, risk, True, "uncertain_state")
        if risk >= self.config.stand_aside_risk:
            return self._controls(weighted, risk, True, "effective_risk_threshold")
        if (
            self.state_risks[active] == max(self.state_risks)
            and min(self.state_risks) < max(self.state_risks)
            and posterior[active] >= self.config.high_risk_state_confidence
        ):
            return self._controls(weighted, risk, True, "high_risk_active_state")
        return self._controls(weighted, risk, False, "posterior_weighted_risk")
