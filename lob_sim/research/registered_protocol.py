"""Content-frozen real-market protocol and guarded partition access.

The earlier diagnostic registry remains unchanged. This protocol requires a
verified >=10-day admission bundle and freezes the whole experiment, not just
a list of strategy names. It guards supported APIs, not arbitrary OS access.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Literal

from lob_sim.config import Config, fill_assumption_config_for_profile
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.features import FeatureSpec
from lob_sim.regime.fit import FitConfig, restart_seed
from lob_sim.regime.hysteresis import HysteresisConfig
from lob_sim.regime.policy import RegimePolicyConfig
from lob_sim.regime.validation import canonical_json, identity, require_keys, strict_json
from lob_sim.research.day_bundle import verify_day_bundle
from lob_sim.research.evidence_io import read_json
from lob_sim.research.interval_reader import digest
from lob_sim.research.protocol import chronological_day_split
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot

Role = Literal["calibration", "validation", "test"]


def experiment_specification(cfg: Config) -> dict[str, Any]:
    """Versioned plan, chosen without reading features, fits or outcomes.

    Fixed transit delays are sensitivities, not measured gateway latency.
    Cadences 1/2/5 seconds bound fit memory for a ten-day minimum study. Longer
    studies still fail at the frozen row cap rather than quietly subsample.
    """
    if cfg.hmm is not None:
        raise ValueError("register from an HMM-disabled core configuration")
    base = replace(
        cfg,
        symbols=("BTCUSDT", "ETHUSDT"),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
        sim_latency_mode="fixed",
        sim_latency_samples_ms=(),
        sim_order_latency_ms=0.0,
        sim_cancel_latency_ms=0.0,
        sim_adverse_markout_seconds=1.0,
    )
    return _experiment_from_snapshot(config_snapshot(base))


def _experiment_from_snapshot(base: dict[str, Any]) -> dict[str, Any]:
    fit = FitConfig()
    intervals = (1_000_000_000, 2_000_000_000, 5_000_000_000)
    scenarios = []
    for name, signal in (("conservative", "trade"), ("base", "depth"), ("aggressive", "depth")):
        assumption = fill_assumption_config_for_profile(name)
        effective = replace(
            assumption,
            depth_reductions_consume_queue=signal == "depth",
            agg_trades_consume_queue=signal == "trade",
        )
        scenarios.append(
            {
                "name": name,
                "sim_fill_model": signal,
                "fill_assumption": assumption.as_dict(),
                "effective_fill_assumption": effective.as_dict(),
                "passive_execution_equivalence_class": "confirmed_trade" if signal == "trade" else "displayed_decrease",
            }
        )
    return {
        "schema_version": "lob_sim.real_market_experiment.v1",
        "question": "Does causal regime information provide stable incremental information about execution quality, adverse selection, inventory risk or quote behaviour across execution assumptions?",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "base_configuration": base,
        "features": [
            FeatureSpec(interval_ns=interval, window_steps=10_000_000_000 // interval).as_dict()
            for interval in intervals
        ],
        "hysteresis": [
            {
                "interval_ns": interval,
                "configuration": HysteresisConfig(
                    confirmation_samples=(3_000_000_000 + interval - 1) // interval,
                    minimum_state_age_samples=(3_000_000_000 + interval - 1) // interval,
                ).as_dict(),
            }
            for interval in intervals
        ],
        "cadence_contract": "fixed ten-second trailing window;three-second hysteresis rounded up to complete samples",
        "primary_feature_interval_ns": 1_000_000_000,
        "fitting": fit.as_dict(),
        "restart_seeds": [
            {"k": k, "restart": r, "seed": restart_seed(fit.seed, k, r)}
            for k in fit.state_counts
            for r in range(fit.restarts)
        ],
        "preprocessing": "calibration_only:clip_0.005_0.995_then_population_standardization;constant_scale_1",
        "model_selection": "best_train_restart_per_K;max_validation_LL_per_row;within_tolerance_min_train_BIC_then_K",
        "model_sensitivity": "all_registered_K_and_restarts_in_fit_ledger;primary_validation_selected_model;cadence_refits",
        "variants": ["baseline", "hmm_observe", "hmm_policy"],
        "policy": asdict(RegimePolicyConfig()),
        "fill_scenarios": scenarios,
        "fill_contract": "mutually exclusive trade/depth execution;named base/aggressive have the same passive matching rule and may differ only in diagnostics;not three independent bounds",
        "latency_ms": [0, 1, 5, 10, 25, 50],
        "latency_contract": "fixed equal new/cancel transit sensitivity;not measured observation/compute/ack latency",
        "pairing": "identical source/seed/fill/latency/feature-cadence;observe must match baseline native actions/fills",
        "replay_contract": {
            "unit": "single_symbol_source_day;fresh engine and zero inventory per unit",
            "wall_clock": "fixed first-receipt capture projection;not fitted to future anchors",
            "freshness": "causal explicit depth/trade timeout controls at five/sixty seconds;new observations cannot rescue elapsed intervals",
            "warmup": "source-day book reconstruction before scoring;no cross-partition feature window",
            "scope": "conditional day/source experiments;not one continuously funded portfolio",
        },
        "fees": {
            "maker_bps": base["fees_maker_bps"],
            "taker_bps": base["fees_taker_bps"],
            "funding": "not captured;excluded;economic output is simulated pre-funding",
        },
        "primary_outcomes": [
            "maker_signed_markout_1s_bps",
            "time_weighted_absolute_inventory",
            "reserved_exposure",
            "quote_cancel_activity",
        ],
        "secondary_outcomes": [
            "fills_by_source_and_liquidity",
            "fees",
            "gross_spread_capture",
            "markouts_100ms_1s_5s_30s",
            "adverse_selection_rate",
            "marked_unmarked_coverage",
            "risk_kill_events",
            "gross_net_pnl_if_fully_marked",
        ],
        "statistics": {
            "method": "paired_moving_clock_block_bootstrap",
            "primary_block_minutes": 30,
            "sensitivity_block_minutes": [5, 60],
            "confidence_level": 0.95,
            "replications": 2000,
            "seed": 7,
            "period_minutes": 1,
            "strata": "never_bridge_day_source_epoch_or_invalid_gap",
            "minimum_blocks": 2,
            "missing": "null_if_insufficient;no_zero_fills_for_missing_valuation",
        },
        "exclusions": [
            "not_eligible_full_utc_day",
            "invalid_joint_depth_trade_clock_capture",
            "stale_or_missing_marks",
            "feature_warmup_or_invalid",
            "utc_partition_boundary_within_50ms_clock_tolerance",
            "noncontiguous_sequences",
            "failed_fit_retained_not_replaced",
            "resource_cap_failure_retained_not_subsampled",
        ],
        "resource_limits": {
            "maximum_fit_rows_per_partition": 1_000_000,
            "maximum_registered_days": 4096,
            "minimum_free_disk_bytes": 2 * 1024**3,
        },
        "claims": "registered conditional public-L2 execution scenarios;not real fills, profitability, production or employer equivalence",
    }


@dataclass(frozen=True)
class FrozenProtocol:
    """Private canonical value prevents callers mutating a loaded registry."""

    canonical_value: str

    def __post_init__(self) -> None:
        data = strict_json(self.canonical_value)
        require_keys(
            data,
            {
                "schema_version",
                "frozen",
                "day_bundle_sha256",
                "day_report_sha256",
                "sources",
                "eligible_days",
                "split",
                "experiment",
                "experiment_sha256",
                "code_identity",
                "registry_sha256",
            },
            "frozen real-market registry",
        )
        if data["schema_version"] != "lob_sim.frozen_real_market_registry.v1" or data["frozen"] is not True:
            raise ValueError("real-market registry must be explicitly frozen")
        if data["registry_sha256"] != identity({k: v for k, v in data.items() if k != "registry_sha256"}):
            raise ValueError("frozen registry content identity mismatch")
        for key in ("day_bundle_sha256", "day_report_sha256", "experiment_sha256", "registry_sha256"):
            digest(data[key], key)
        if data["experiment_sha256"] != identity(data["experiment"]):
            raise ValueError("registered experiment identity mismatch")
        if (
            not isinstance(data["eligible_days"], list)
            or len(data["eligible_days"]) < 10
            or len(data["eligible_days"]) > 4096
        ):
            raise ValueError("freeze requires at least ten eligible UTC days")
        split = chronological_day_split(data["eligible_days"])
        if split.as_dict() != data["split"] or list(split.all_days) != data["eligible_days"] or not split.claim_ready:
            raise ValueError("registered chronological split mismatch")
        if not isinstance(data["sources"], list) or not 1 <= len(data["sources"]) <= 256:
            raise ValueError("frozen registry requires bounded admitted source identities")
        for source in data["sources"]:
            require_keys(
                source,
                {"capture_id", "input_sha256", "report_sha256", "clock_sha256", "research_usable"},
                "registered source",
            )
            # Ineligible sources can coexist in a day bundle. They may never
            # be credited as empirical observations by feature admission.
            for key in ("input_sha256", "report_sha256", "clock_sha256"):
                digest(source[key], key)
            if type(source["research_usable"]) is not bool:
                raise ValueError("registered source admission must be boolean")
        if (
            not isinstance(data["experiment"], dict)
            or data["experiment"].get("schema_version") != "lob_sim.real_market_experiment.v1"
        ):
            raise ValueError("unsupported registered experiment")
        base = data["experiment"].get("base_configuration")
        if (
            not isinstance(base, dict)
            or base.get("symbols") != ["BTCUSDT", "ETHUSDT"]
            or base.get("mm_enabled") is not True
            or "hmm" in base
        ):
            raise ValueError("registered core configuration mismatch")
        if canonical_json(data["experiment"]) != canonical_json(_experiment_from_snapshot(base)):
            raise ValueError("unsupported modified v1 experiment contract")
        # Normalize to prevent whitespace becoming an alternate registry.
        object.__setattr__(self, "canonical_value", canonical_json(data))

    @property
    def digest(self) -> str:
        return self.snapshot()["registry_sha256"]

    def snapshot(self) -> dict[str, Any]:
        return dict(strict_json(self.canonical_value))

    def authorize(self, role: Role, day: str, *, registry_sha256: str) -> None:
        # This check precedes all file/path inspection of the target partition.
        if digest(registry_sha256, "explicit frozen registry digest") != self.digest:
            raise ValueError("frozen registry digest differs; no partition access")
        if role not in ("calibration", "validation", "test") or day not in self.snapshot()["split"][role + "_days"]:
            raise ValueError("date does not belong to the registered partition")

    def open_feature_day(self, root: Path, role: Role, day: str, symbol: str, *, registry_sha256: str) -> BinaryIO:
        self.authorize(role, day, registry_sha256=registry_sha256)
        if symbol not in self.snapshot()["experiment"]["symbols"]:
            raise ValueError("symbol is not registered")
        directory = (root / role).resolve()
        if not directory.is_relative_to(root.resolve()):
            raise ValueError("partition directory escapes the feature bundle")
        path = (directory / (day + "__" + symbol + ".jsonl")).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("feature file escapes its physical partition")
        return path.open("rb")


def load_protocol(path: Path, *, registry_sha256: str) -> FrozenProtocol:
    # Require an external expected digest before even opening this registry.
    expected = digest(registry_sha256, "explicit frozen registry digest")
    protocol = FrozenProtocol(canonical_json(read_json(path)))
    if protocol.digest != expected:
        raise ValueError("frozen registry digest mismatch")
    return protocol


def verify_protocol_binding(
    protocol: FrozenProtocol, day_bundle: Path, inputs: tuple[Path, ...], *, registry_sha256: str
) -> dict[str, Any]:
    """Re-certify protocol parents before any prepared features/study access."""
    if digest(registry_sha256, "explicit frozen registry digest") != protocol.digest:
        raise ValueError("frozen registry digest mismatch before parent access")
    data = protocol.snapshot()
    proof = verify_day_bundle(day_bundle, inputs)
    days = read_json(day_bundle / "days.json")
    if (
        proof["ready_for_registration"] is not True
        or data["day_bundle_sha256"] != proof["bundle_sha256"]
        or any(data[k] != days[k] for k in ("sources", "eligible_days"))
        or data["day_report_sha256"] != days["report_sha256"]
    ):
        raise ValueError("frozen protocol differs from independently admitted source/day universe")
    return {
        "schema_version": "lob_sim.real_market_registry_verification.v1",
        "verified": True,
        "registry_sha256": protocol.digest,
        "day_bundle_sha256": proof["bundle_sha256"],
        "scope": "frozen content and admitted raw/interval/day bindings;not author or execution truth",
    }


def register_protocol(day_bundle: Path, inputs: tuple[Path, ...], cfg: Config, output: Path) -> FrozenProtocol:
    proof = verify_day_bundle(day_bundle, inputs)
    if proof["ready_for_registration"] is not True:
        raise ValueError(
            "representative-data blocker: fewer than ten eligible UTC days or unstable instrument identity"
        )
    days = read_json(day_bundle / "days.json")
    experiment = experiment_specification(cfg)
    payload = {
        "schema_version": "lob_sim.frozen_real_market_registry.v1",
        "frozen": True,
        "day_bundle_sha256": proof["bundle_sha256"],
        "day_report_sha256": days["report_sha256"],
        "sources": days["sources"],
        "eligible_days": days["eligible_days"],
        "split": chronological_day_split(days["eligible_days"]).as_dict(),
        "experiment": experiment,
        "experiment_sha256": identity(experiment),
        "code_identity": checkpoint_code_identity(),
    }
    payload["registry_sha256"] = identity(payload)
    frozen = FrozenProtocol(canonical_json(payload))
    publish_json(output, frozen.snapshot())
    return frozen
