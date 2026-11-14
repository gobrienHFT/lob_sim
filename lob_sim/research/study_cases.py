"""Complete registered grid and exact per-case configuration, without tuning."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import Config, FillAssumptionConfig
from lob_sim.regime.artifact import load_model
from lob_sim.regime.hysteresis import HysteresisConfig
from lob_sim.regime.policy import RegimePolicyConfig
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import identity
from lob_sim.research.registered_protocol import FrozenProtocol, experiment_specification
from lob_sim.research.replay_engine import ResearchConfig, research_config
from lob_sim.sim.run_manifest import config_snapshot


def expected_cases(protocol: FrozenProtocol, views: dict[str, Any]) -> list[dict[str, Any]]:
    experiment = protocol.snapshot()["experiment"]
    cases = []
    for unit in views["units"]:
        for feature in experiment["features"]:
            for scenario in experiment["fill_scenarios"]:
                for latency in experiment["latency_ms"]:
                    for variant in experiment["variants"]:
                        case = {
                            "unit_id": unit["unit_id"],
                            "symbol": unit["symbol"],
                            "utc_day": unit["utc_day"],
                            "source_sha256": unit["source_sha256"],
                            "interval_ns": feature["interval_ns"],
                            "fill_scenario": scenario["name"],
                            "latency_ms": latency,
                            "variant": variant,
                        }
                        case["case_id"] = identity({"registry_sha256": protocol.digest, **case})
                        cases.append(case)
    if len(views["units"]) > experiment["resource_limits"]["maximum_study_units"]:
        raise ValueError("registered study unit cap exceeded; no silent subset")
    return cases


def case_configuration(
    cfg: Config,
    protocol: FrozenProtocol,
    case: dict[str, Any],
    model_root: Path,
    models: dict[str, Any],
    run_root: Path,
) -> ResearchConfig:
    experiment = protocol.snapshot()["experiment"]
    if identity(experiment_specification(cfg)) != protocol.snapshot()["experiment_sha256"]:
        raise ValueError("study configuration differs from frozen experiment")
    # Derive from the same explicit normalization used by registration, not
    # from caller defaults that were removed in its frozen base snapshot.
    base = replace(
        cfg,
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        hmm=None,
        sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
        sim_adverse_markout_seconds=1.0,
        sim_latency_mode="fixed",
        sim_latency_samples_ms=(),
        sim_order_latency_ms=0.0,
        sim_cancel_latency_ms=0.0,
        symbols=("BTCUSDT", "ETHUSDT"),
    )
    if config_snapshot(base) != experiment["base_configuration"]:
        raise ValueError("normalized study baseline differs")
    scenario = next(s for s in experiment["fill_scenarios"] if s["name"] == case["fill_scenario"])
    runtime = None
    if case["variant"] != "baseline":
        cell = next(
            c for c in models["cells"] if (c["symbol"], c["interval_ns"]) == (case["symbol"], case["interval_ns"])
        )
        candidate = next(c for c in cell["candidates"] if c["k"] == cell["validation_selected_k"])
        model = load_model(model_root / candidate["directory"] / "model.json")
        if model.model_sha256 != candidate["model_sha256"]:
            raise ValueError("selected model identity differs")
        hysteresis = next(
            h["configuration"] for h in experiment["hysteresis"] if h["interval_ns"] == case["interval_ns"]
        )
        policy = RegimePolicyConfig(**experiment["policy"]) if case["variant"] == "hmm_policy" else None
        runtime = HMMSettings(
            model, case["symbol"], HysteresisConfig(**hysteresis), "policy" if policy else "observe", policy
        )
    result = research_config(
        base,
        hmm=runtime,
        mm_strategy_profile="hmm_regime_mm" if case["variant"] == "hmm_policy" else "research_mm",
        fill_assumption=FillAssumptionConfig(**scenario["fill_assumption"]),
        sim_fill_model=scenario["sim_fill_model"],
        sim_order_latency_ms=float(case["latency_ms"]),
        sim_cancel_latency_ms=float(case["latency_ms"]),
        record_dir=run_root,
    )
    if result.effective_fill_assumption.as_dict() != scenario["effective_fill_assumption"]:
        raise ValueError("effective execution signal differs from registry")
    return result
