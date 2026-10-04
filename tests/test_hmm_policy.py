from __future__ import annotations

import copy
import json
import math
import sys
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import SymbolSpec
from lob_sim.config import ConfigError, _config_from_values
from lob_sim.oracle import Checkpoint, read_checkpoint, write_checkpoint
from lob_sim.regime.artifact import save_model
from lob_sim.regime.policy import (
    RISK_SIGNATURE,
    RegimePolicyConfig,
    RegimeRiskPolicy,
    training_risk_scores,
)
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import canonical_json, identity, strict_json
from lob_sim.sim.checkpoint import decode, encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.metrics import PositionState
from lob_sim.sim.mm_strategy import MarketMakingStrategy
from lob_sim.sim.orders import Order
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.run_manifest import config_snapshot
from test_hmm_dataset import SECOND, cfg
from test_hmm_execution import audit, execution_tape, stage
from test_hmm_observation import Recorder, settings


def policy_settings(policy=None):
    """Hand-specified control oracle; not a fitted or recovered market regime."""
    base = settings()
    instrument = identity(
        {
            "symbol": "BTCUSDT",
            "tick_size": "0.1",
            "step_size": "0.001",
            "contract_multiplier": "1",
            "venue": "BINANCE_USDM",
        }
    )
    metadata = {
        "purpose": "hand_specified_policy_test;not_economic_evidence",
        "training": {
            "role": "calibration",
            "symbol": "BTCUSDT",
            "feature_identity": base.model.features.digest,
            "rows": 100,
            "rows_sha256": "a" * 64,
            "dataset_sha256": "b" * 64,
            "split_sha256": "c" * 64,
            "instrument_sha256": instrument,
        },
        "risk_signature": RISK_SIGNATURE,
        "occupancy_method": "retrospective_training_smoothing;never_runtime_inference",
        "state_characterization": [
            {
                "state": f"STATE_{state}",
                "risk_score": float(state),
                "risk_component_ranks": [float(state)] * 5,
                "training_occupancy": 0.5,
                "training_effective_observations": 50.0,
            }
            for state in range(2)
        ],
    }
    # This deliberate low-risk emission matches the positive-valued test tape.
    model = replace(
        base.model, parameters=base.model.parameters.permute((1, 0)), provenance_json=canonical_json(metadata)
    )
    return HMMSettings(model, "BTCUSDT", base.hysteresis, "policy", policy or RegimePolicyConfig())


def configuration(**overrides):
    values = {
        "hmm": policy_settings(),
        "mm_strategy_profile": "hmm_regime_mm",
        "mm_order_qty": Decimal("0.01"),
        "mm_half_spread_bps": Decimal("0"),
        "mm_layered_inner_spread_bps": Decimal("0"),
        "mm_layered_outer_spread_bps": Decimal("0"),
        "fees_maker_bps": Decimal("0"),
        "fees_taker_bps": Decimal("0"),
        "mm_toxicity_spread_factor": Decimal("0"),
    }
    values.update(overrides)
    return replace(cfg(), **values)


def signal(model, posterior=(1.0, 0.0), *, active=0, status="VALID"):
    raw = max(range(len(posterior)), key=posterior.__getitem__)
    entropy = -sum(p * math.log(p) for p in posterior if p) / math.log(len(posterior))
    return {
        "model_sha256": model.model_sha256,
        "status": status,
        "posterior": list(posterior),
        "raw_map_state": raw,
        "active_state": active,
        "confidence": posterior[raw],
        "normalized_entropy": entropy,
        "entropy": entropy * math.log(len(posterior)),
    }


def controlled(engine, *, high_at=None):
    """Scheduler oracle only. Real inference is exercised in the resume tests."""

    def snapshot(logical_ns):
        high = high_at is not None and logical_ns >= high_at
        value = signal(engine.cfg.hmm.model, (0.0, 1.0) if high else (1.0, 0.0), active=int(high))
        value.update(
            {
                "sample_ns": logical_ns - 1,
                "available_at_ns": logical_ns,
                "receive_seq": max(0, logical_ns),
                "epochs": [engine._syncers["BTCUSDT"].epoch, 0, 0],
            }
        )
        return value

    engine.regime.snapshot = snapshot


def policy_tape(path):
    execution_tape(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    expanded = []
    for row in rows:
        expanded.append(row)
        if row["type"] == "depthUpdate" and row["data"]["_capture"]["recvMonotonicNs"] in {8 * SECOND, 9 * SECOND}:
            for maker in (False, True):
                trade = copy.deepcopy(row)
                trade["type"] = "aggTrade"
                trade["data"] = {
                    "p": "99.9" if maker else "100.1",
                    "q": "0.1",
                    "m": maker,
                    "_capture": {**row["data"]["_capture"], "route": "market"},
                }
                expanded.append(trade)
    for index, row in enumerate(expanded):
        row["data"]["_capture"]["recvSeq"] = index
    path.write_text("".join(json.dumps(row) + "\n" for row in expanded))
    return path


def book():
    spec = SymbolSpec("BTCUSDT", Decimal("0.1"), Decimal("0.001"))
    value = LocalOrderBook("BTCUSDT", spec, 20)
    value.reset_from_snapshot(100, {999: 5}, {1001: 5})
    return value


def test_policy_math_matches_explicit_independent_scalar_oracle():
    model = policy_settings().model
    policy = RegimeRiskPolicy(model)
    result = policy.evaluate(signal(model, (0.8, 0.2)))
    entropy = -(0.8 * math.log(0.8) + 0.2 * math.log(0.2)) / math.log(2)
    expected = 0.2 + 0.25 * entropy
    assert result.posterior_weighted_risk == 0.2
    assert result.effective_risk == pytest.approx(expected)
    assert result.spread_multiplier == pytest.approx(1 + 2 * expected)
    assert result.size_multiplier == pytest.approx(1 - 0.75 * expected)
    assert result.inventory_limit_multiplier == pytest.approx(1 - 0.5 * expected)
    assert result.skew_multiplier == pytest.approx(1 + expected)
    assert result.refresh_multiplier == pytest.approx(1 + 3 * expected)
    assert result.max_quote_age_ns == int(2_000_000_000 / (1 + 3 * expected))
    assert not result.stand_aside


def test_monotonic_controls_for_1001_posterior_risks_with_fixed_uncertainty_weight():
    model = policy_settings().model
    policy = RegimeRiskPolicy(model, RegimePolicyConfig(uncertainty_weight=0))
    prior = None
    for step in range(1001):
        risk = step / 1000
        current = policy.evaluate(signal(model, (1 - risk, risk)))
        assert current.effective_risk == risk
        if prior is not None:
            assert current.spread_multiplier >= prior.spread_multiplier
            assert current.size_multiplier <= prior.size_multiplier
            assert current.inventory_limit_multiplier <= prior.inventory_limit_multiplier
            assert current.skew_multiplier >= prior.skew_multiplier
            assert current.max_quote_age_ns <= prior.max_quote_age_ns
        prior = current


def test_uncertainty_penalty_can_only_tighten_controls_for_the_same_posterior():
    model = policy_settings().model
    value = signal(model, (0.8, 0.2))
    plain = RegimeRiskPolicy(model, RegimePolicyConfig(uncertainty_weight=0)).evaluate(value)
    penalized = RegimeRiskPolicy(model, RegimePolicyConfig(uncertainty_weight=0.4)).evaluate(value)
    assert penalized.effective_risk > plain.effective_risk
    assert penalized.size_multiplier < plain.size_multiplier
    assert penalized.inventory_limit_multiplier < plain.inventory_limit_multiplier
    assert penalized.spread_multiplier > plain.spread_multiplier


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "STALE"}, "invalid_or_warming_information"),
        ({"status": "WARMING_UP"}, "invalid_or_warming_information"),
        ({"validity": {"execution_valid": False}}, "invalid_information_set"),
        ({"active_state": None}, "unconfirmed_state"),
        ({"posterior": [0.5, 0.5], "confidence": 0.5, "normalized_entropy": 1.0}, "uncertain_state"),
    ],
)
def test_unknown_invalid_and_uncertain_information_stands_aside(changes, reason):
    model = policy_settings().model
    result = RegimeRiskPolicy(model).evaluate({**signal(model), **changes})
    assert result.stand_aside and result.reason == reason


def test_high_risk_stand_aside_and_configured_caps():
    model = policy_settings().model
    assert RegimeRiskPolicy(model).evaluate(signal(model, (0.0, 1.0), active=1)).reason == "effective_risk_threshold"
    cfg_policy = RegimePolicyConfig(
        uncertainty_weight=0,
        stand_aside_risk=1,
        max_spread_multiplier=1.1,
        max_skew_multiplier=1.1,
        max_refresh_multiplier=1.1,
        min_size_multiplier=0.8,
        min_inventory_multiplier=0.9,
    )
    result = RegimeRiskPolicy(model, cfg_policy).evaluate(signal(model, (0.01, 0.99), active=1))
    assert result.reason == "high_risk_active_state"
    assert (result.spread_multiplier, result.skew_multiplier, result.refresh_multiplier) == (1.1, 1.1, 1.1)
    assert (result.size_multiplier, result.inventory_limit_multiplier) == (0.8, 0.9)


@pytest.mark.parametrize(
    "field,value",
    [
        ("spread_slope", -1),
        ("max_spread_multiplier", 0.9),
        ("size_reduction", 1.1),
        ("min_size_multiplier", -0.1),
        ("inventory_reduction", 1.1),
        ("min_inventory_multiplier", 1.01),
        ("skew_slope", True),
        ("max_skew_multiplier", float("nan")),
        ("refresh_slope", -1),
        ("base_max_quote_age_ms", 0),
        ("uncertainty_weight", float("inf")),
        ("stand_aside_risk", 0),
        ("high_risk_state_confidence", 1.01),
    ],
)
def test_policy_rejects_unsafe_configuration(field, value):
    with pytest.raises(ValueError):
        RegimePolicyConfig(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("posterior", [0.9, 0.9]),
        ("raw_map_state", 1),
        ("active_state", True),
        ("confidence", 0.8),
        ("normalized_entropy", 0.3),
        ("model_sha256", "d" * 64),
    ],
)
def test_policy_rejects_inconsistent_signal(field, value):
    model = policy_settings().model
    with pytest.raises(ValueError):
        RegimeRiskPolicy(model).evaluate({**signal(model), field: value})


@pytest.mark.parametrize("corruption", ["signature", "score", "ranks", "state", "occupancy", "future_role", "missing"])
def test_training_policy_provenance_is_not_an_arbitrary_state_risk_vector(corruption):
    model = policy_settings().model
    assert training_risk_scores(model) == (0.0, 1.0)
    metadata = strict_json(model.provenance_json)
    if corruption == "signature":
        metadata["risk_signature"] = "profit_probability"
    elif corruption == "score":
        metadata["state_characterization"][0]["risk_score"] = 0.3
    elif corruption == "ranks":
        metadata["state_characterization"][0]["risk_component_ranks"] = [0.1] * 5
    elif corruption == "state":
        metadata["state_characterization"].reverse()
    elif corruption == "occupancy":
        metadata["state_characterization"][0]["training_occupancy"] = 0.2
    elif corruption == "future_role":
        metadata["training"]["role"] = "test"
    else:
        del metadata["state_characterization"]
    changed = replace(model, provenance_json=canonical_json(metadata))
    HMMSettings(changed, "BTCUSDT")  # Diagnostic observation remains possible.
    with pytest.raises(ValueError):
        HMMSettings(changed, "BTCUSDT", mode="policy")
    with pytest.raises(ValueError, match="training-only"):
        HMMSettings(settings().model, "BTCUSDT", mode="policy")


def test_namespace_modes_are_explicit_and_observe_config_identity_is_unchanged(tmp_path):
    original = settings().as_dict()
    assert "policy" not in original
    with pytest.raises(ValueError, match="observation-only"):
        replace(settings(), policy=RegimePolicyConfig())
    with pytest.raises(ConfigError, match="requires"):
        replace(cfg(), mm_strategy_profile="hmm_regime_mm")
    with pytest.raises(ConfigError, match="requires"):
        replace(cfg(), hmm=policy_settings())
    with pytest.raises(ConfigError, match="single"):
        replace(configuration(), symbols=("BTCUSDT", "ETHUSDT"))
    controls = RegimeRiskPolicy(policy_settings().model).evaluate(signal(policy_settings().model))
    with pytest.raises(FrozenInstanceError):
        controls.size_multiplier = 2.0
    snapshot = controls.as_dict()
    snapshot["size_multiplier"] = 2.0
    assert controls.size_multiplier == 1.0
    with pytest.raises(ValueError):
        replace(controls, size_multiplier=1.01)
    path = tmp_path / "policy.json"
    path.write_text(canonical_json(RegimePolicyConfig().as_dict()))
    assert RegimePolicyConfig.load(path) == RegimePolicyConfig()
    assert settings().as_dict() == original


def test_six_controls_change_real_quote_components_not_just_diagnostics():
    configuration_value = configuration(mm_skew_bps_per_unit=Decimal("1000"), mm_half_spread_bps=Decimal("20"))
    model = configuration_value.hmm.model
    policy = RegimeRiskPolicy(model)
    low, raised = policy.evaluate(signal(model)), policy.evaluate(signal(model, (0.8, 0.2)))
    base_strategy = MarketMakingStrategy(configuration_value)
    base = base_strategy.propose(book(), Decimal("0.01"), controls=low)
    altered = MarketMakingStrategy(configuration_value).propose(book(), Decimal("0.01"), controls=raised)
    assert all(q.qty_lots == 10 for q in base.quotes)
    assert all(q.qty_lots == 7 for q in altered.quotes)
    assert Decimal(altered.diagnostics["half_spread_ticks"]) > Decimal(base.diagnostics["half_spread_ticks"])
    assert Decimal(altered.diagnostics["outer_spread_ticks"]) > Decimal(base.diagnostics["outer_spread_ticks"])
    assert Decimal(altered.diagnostics["skew_ticks"]) > Decimal(base.diagnostics["skew_ticks"])
    quote = base.quotes[0]
    existing = Order(
        "q",
        "BTCUSDT",
        quote.side,
        quote.price_tick,
        quote.qty_lots,
        created_ts=0,
        remaining_lots=quote.qty_lots,
        refresh_key=quote.refresh_key,
    )
    assert not base_strategy.should_refresh(quote, existing, controls=low, age_ns=1_000_000_000)
    assert base_strategy.should_refresh(quote, existing, controls=raised, age_ns=1_000_000_000)
    assert altered.diagnostics["hmm_controls"]["inventory_limit_multiplier"] < 1
    aside = policy.evaluate(signal(model, (0.0, 1.0), active=1))
    assert not base_strategy.propose(book(), Decimal("0.01"), controls=aside).quotes


def test_sub_lot_reduction_is_zero_not_promoted_to_one_lot():
    value = configuration(mm_order_qty=Decimal("0.001"))
    controls = RegimeRiskPolicy(value.hmm.model).evaluate(signal(value.hmm.model, (0.8, 0.2)))
    plan = MarketMakingStrategy(value).propose(book(), Decimal("0"), controls=controls)
    assert plan.reason == "hmm_size_below_one_lot" and not plan.quotes
    with pytest.raises(ValueError, match="existing strategy"):
        MarketMakingStrategy(cfg()).propose(book(), Decimal("0"), controls=controls)


def test_stand_aside_preserves_cancel_latency_and_pending_cancel_fill(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    sink = Recorder()
    engine = SimulationEngine(configuration(sim_cancel_latency_ms=8000), regime_execution_sink=sink)
    controlled(engine, high_at=5 * SECOND)
    engine.run(path)
    fills = [row for row in sink.rows if row["event_type"] == "fill"]
    assert fills and all(row["order_state_at_fill"] == "pending_cancel" for row in fills)
    assert all(row["decision"]["active_state"] == 0 and row["pre_fill"]["active_state"] == 1 for row in fills)
    assert all(row["ts_local"] < 5 for row in engine.event_trace if row["event_type"] == "order_arrival_scheduled")
    assert any(row["details"].get("reason") == "hmm_stand_aside" for row in engine.event_trace)
    assert engine.metrics.fill_count > 0  # No fictitious flatten or instantaneous disappearance.


def test_soft_inventory_capacity_counts_live_plus_pending_and_does_not_spend_pending_cancels(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    value = configuration(mm_max_position=Decimal("0.015"), sim_order_latency_ms=1500, sim_cancel_latency_ms=8000)
    engine = SimulationEngine(value)
    controlled(engine)
    engine.run(path)
    requests = [row for row in engine.event_trace if row["event_type"] == "order_arrival_scheduled"]
    assert requests and len([row for row in requests if row["ts_local"] == 2]) == 2
    assert any(row["details"].get("reason") == "hmm_soft_position_limit" for row in engine.event_trace)
    assert abs(engine.metrics.inventory_lots("BTCUSDT")) <= 15
    assert not engine._pending_replacement_slots


def test_refresh_expiration_schedules_a_real_cancel_and_waits_for_ack(tmp_path):
    path = execution_tape(tmp_path / "input.ndjson")
    value = configuration(
        hmm=policy_settings(RegimePolicyConfig(base_max_quote_age_ms=500)),
        sim_cancel_latency_ms=3000,
        mm_queue_repost_lots=10000,
    )
    engine = SimulationEngine(value)
    controlled(engine)
    engine.run(path)
    refreshes = [
        row
        for row in engine.event_trace
        if row["event_type"] == "cancel_requested" and row["details"].get("reason") == "replace_quote"
    ]
    assert refreshes
    assert any(row["details"]["refresh_requested"] for row in refreshes)
    replacements = [
        row
        for row in engine.event_trace
        if row["event_type"] == "order_arrival_scheduled" and row["details"].get("cancel_ack_ts") is not None
    ]
    assert replacements and all(row["details"]["arrival_ts"] >= row["details"]["cancel_ack_ts"] for row in replacements)


@pytest.mark.parametrize("limit", [Decimal("0.1"), Decimal("0.0001")])
def test_hard_portfolio_notional_blocks_policy_even_when_hmm_is_confident(tmp_path, limit):
    engine = SimulationEngine(configuration(mm_max_portfolio_notional=limit))
    controlled(engine)
    engine.run(execution_tape(tmp_path / "input.ndjson"))
    assert not engine.metrics.fill_count
    assert not [row for row in engine.event_trace if row["event_type"] == "order_arrival_scheduled"]
    assert any(row["details"].get("reason") == "portfolio_notional_limit" for row in engine.event_trace)


def test_unmarkable_inventory_and_kill_switch_remain_outside_policy(tmp_path):
    engine = SimulationEngine(configuration(mm_max_portfolio_notional=Decimal("100")))
    engine.run(execution_tape(tmp_path / "input.ndjson"), checkpoint_path=tmp_path / "pause.json", stop_after_records=7)
    controls = RegimeRiskPolicy(engine.cfg.hmm.model).evaluate(signal(engine.cfg.hmm.model))
    plan = engine.strategy.propose(engine._books["BTCUSDT"], Decimal("0"), controls=controls)
    engine.metrics.position["UNKNOWN"] = PositionState(lot_size=1)
    assert not engine._hmm_can_send("BTCUSDT", "bid", plan.quotes[0], controls, engine._last_ts)
    engine.metrics.position.pop("UNKNOWN")
    engine._disable_trading()
    assert not engine.hmm_execution.checkpoint()["orders"]
    before = encode(engine._checkpoint_mutable_state())
    engine._handle_decision("BTCUSDT", engine._last_ts)
    engine._handle_arrival("BTCUSDT", {"side": "bid"}, engine._last_ts)
    assert encode(engine._checkpoint_mutable_state()) == before


@pytest.mark.parametrize("cut", [7, 10, 14, 16])
def test_policy_checkpoint_resume_actual_filter_identical_state_and_all_audits(tmp_path, cut):
    path = policy_tape(tmp_path / "input.ndjson")
    value = configuration(sim_markout_horizons_ms=(100, 1000, 5000, 30000))
    full = SimulationEngine(value)
    full.run(path)
    checkpoint = tmp_path / "checkpoint.json"
    SimulationEngine(value).run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    resumed = SimulationEngine(value)
    resumed.run(path, resume_from=checkpoint)
    assert any(row["details"].get("hmm_policy_reason") == "posterior_weighted_risk" for row in full.event_trace)
    assert full.metrics.fill_count > 0
    assert resumed.state_sha256() == full.state_sha256()
    assert resumed.event_trace == full.event_trace
    assert resumed.hmm_execution.checkpoint() == full.hmm_execution.checkpoint()


@pytest.mark.parametrize("tamper", ["missing", "increase", "decrease", "bool", "quantity"])
def test_pending_policy_limit_tamper_fails_before_core_mutation(tmp_path, tamper):
    path = execution_tape(tmp_path / "input.ndjson")
    value = configuration(sim_order_latency_ms=1500)
    source = SimulationEngine(value)
    checkpoint = tmp_path / "checkpoint.json"
    source.run(path, checkpoint_path=checkpoint, stop_after_records=13)
    saved = read_checkpoint(checkpoint)
    state = copy.deepcopy(saved.state)
    decoded = decode(state["engine"])
    pending = next(action for action in decoded["actions"] if action.kind == "order_arrival")
    if tamper == "missing":
        del pending.payload["hmm_soft_position_lots"]
    elif tamper == "bool":
        pending.payload["hmm_soft_position_lots"] = True
    elif tamper == "quantity":
        pending.payload["qty_lots"] += 1
    else:
        pending.payload["hmm_soft_position_lots"] += 1 if tamper == "increase" else -1
    state["engine"] = encode(decoded)
    write_checkpoint(
        checkpoint, Checkpoint.create(saved.event_index, saved.logical_time, state, schema_version=saved.schema_version)
    )
    target = SimulationEngine(value)
    before = encode(target._checkpoint_mutable_state())
    with pytest.raises(ValueError, match="soft position|reservation"):
        target.run(path, resume_from=checkpoint)
    assert encode(target._checkpoint_mutable_state()) == before


def test_policy_cli_publishes_verified_bundle_and_rejects_incompatible_profile(tmp_path, monkeypatch, capsys):
    from lob_sim import cli

    path = execution_tape(tmp_path / "input.ndjson")
    model = tmp_path / "model.json"
    save_model(model, policy_settings().model)
    monkeypatch.setattr(cli, "load_config", lambda *_: replace(cfg(), record_dir=tmp_path))
    arguments = ["lob-sim", "simulate", "--file", str(path), "--hmm", "policy", "--hmm-model", str(model)]
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit):
        cli.main()
    assert "requires MM_STRATEGY_PROFILE=hmm_regime_mm" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", arguments + ["--strategy", "hmm_regime_mm"])
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["hmm"]["strategy_intervention"] is True
    assert report["hmm"]["config"]["policy"] == RegimePolicyConfig().as_dict()
    manifest = json.loads(Path(report["output_files"]["manifest"]).read_text())
    assert manifest["config"]["hmm"]["policy"] == RegimePolicyConfig().as_dict()


def test_policy_config_is_frozen_into_manifest_not_followed_after_load(tmp_path):
    from dotenv import dotenv_values

    model, policy_path = tmp_path / "model.json", tmp_path / "policy.json"
    save_model(model, policy_settings().model)
    policy = RegimePolicyConfig(base_max_quote_age_ms=3000)
    policy_path.write_text(canonical_json(policy.as_dict()))
    values = dict(dotenv_values(".env.example", interpolate=False))
    values.update(
        {
            "HMM_MODE": "policy",
            "MM_STRATEGY_PROFILE": "hmm_regime_mm",
            "HMM_MODEL_PATH": str(model),
            "HMM_POLICY_PATH": str(policy_path),
            "RECORD_DIR": str(tmp_path),
        }
    )
    value = _config_from_values(values)
    policy_path.write_text("corrupt after load")
    assert value.hmm.policy == policy
    assert config_snapshot(value)["hmm"]["policy"] == policy.as_dict()
    _, report = run_bounded_simulation(value, execution_tape(tmp_path / "input.ndjson"))
    assert report["hmm"]["config"]["policy"] == policy.as_dict()


def test_policy_arrival_uses_sent_constraint_not_later_regime_information(tmp_path):
    sink = Recorder()
    engine = SimulationEngine(
        configuration(sim_order_latency_ms=1500, sim_cancel_latency_ms=8000), regime_execution_sink=sink
    )
    controlled(engine, high_at=3 * SECOND)
    engine.run(execution_tape(tmp_path / "input.ndjson"))
    arrivals = [
        row
        for row in engine.event_trace
        if row["event_type"] == "order_arrival" and row["ts_local"] == 3.5 and not row["details"].get("rejected")
    ]
    assert len(arrivals) == 4
    fills = [row for row in sink.rows if row["event_type"] == "fill"]
    assert fills and all(row["decision"]["active_state"] == 0 and row["arrival"]["active_state"] == 1 for row in fills)
    assert any(row["details"].get("reason") == "hmm_stand_aside" for row in engine.event_trace)


def test_hard_position_rechecked_at_arrival_independent_of_confident_policy(tmp_path):
    engine = SimulationEngine(configuration(sim_order_latency_ms=1500))
    controlled(engine)
    engine.run(execution_tape(tmp_path / "input.ndjson"), checkpoint_path=tmp_path / "pause.json", stop_after_records=7)
    intent = next(
        action for action in engine._actions if action.kind == "order_arrival" and action.payload["side"] == "bid"
    )
    engine._actions.remove(intent)
    engine.metrics.position["BTCUSDT"] = PositionState(lot_size=49)
    engine._handle_arrival("BTCUSDT", intent.payload, 3.5)
    assert any(row["details"].get("reason") == "risk_limit" for row in engine.event_trace)
    assert not engine.fill_model.get_orders("BTCUSDT", "bid")


@pytest.mark.parametrize("route,event", [("market", "disconnect"), ("public", "disconnect"), ("control", "overflow")])
def test_policy_quotes_fail_closed_on_actual_between_sample_feed_fault(tmp_path, route, event):
    path = policy_tape(tmp_path / "input.ndjson")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    index = next(i for i, row in enumerate(rows) if row["data"]["_capture"]["recvMonotonicNs"] == 7 * SECOND)
    fault = copy.deepcopy(rows[index])
    fault["type"] = "captureEvent"
    fault["symbol"] = "*" if route == "control" else "BTCUSDT"
    fault["data"] = {"event": event, "route": route, "_capture": {**fault["data"]["_capture"], "route": route}}
    fault["data"]["_capture"]["recvMonotonicNs"] = 6_100_000_000
    fault["data"]["_capture"]["recvWallNs"] -= 900_000_000
    fault["ts_local"] -= 0.9
    rows.insert(index, fault)
    for index, row in enumerate(rows):
        row["data"]["_capture"]["recvSeq"] = index
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    engine = SimulationEngine(configuration())
    engine.run(path)
    requests = [row for row in engine.event_trace if row["event_type"] == "order_arrival_scheduled"]
    # Capture-integrity prevalidation may halt the unresolved action batch
    # entirely. Preserve that existing fail-closed boundary, not a fictitious fill.
    if route != "control":
        assert requests
    assert all(row["ts_local"] < 6.1 for row in requests)
    assert engine.regime.snapshot(14 * SECOND)["posterior"] is None
    assert not engine.hmm_execution.checkpoint()["orders"]


def test_missing_instrument_exposure_also_fails_closed_without_hmm():
    engine = SimulationEngine(replace(cfg(), mm_max_portfolio_notional=Decimal("100")))
    engine.metrics.position["UNKNOWN"] = PositionState(lot_size=1)
    total, subtotals, missing = engine._portfolio_notional_reservation()
    assert total is None and subtotals == {} and missing == ("UNKNOWN",)


def test_policy_identity_and_rank_metadata_cannot_be_mutated():
    policy = RegimeRiskPolicy(policy_settings().model)
    with pytest.raises(FrozenInstanceError):
        policy.state_risks = (0.0, 0.0)
    metadata = strict_json(policy.model.provenance_json)
    for state in metadata["state_characterization"]:
        state["risk_component_ranks"] = [1.0] * 5
        state["risk_score"] = 1.0
    with pytest.raises(ValueError, match="cross-state"):
        training_risk_scores(replace(policy.model, provenance_json=canonical_json(metadata)))


@pytest.mark.parametrize(
    "field,value", [("enter_probability", 0.5), ("enter_probability", True), ("maximum_normalized_entropy", 1.1)]
)
def test_controller_rejects_unsafe_confidence_threshold_overrides(field, value):
    model = policy_settings().model
    with pytest.raises(ValueError):
        RegimeRiskPolicy(model).evaluate(signal(model), **{field: value})


def test_future_valid_tape_cannot_rewrite_historical_policy_requests(tmp_path):
    left = policy_tape(tmp_path / "left.ndjson")
    right = policy_tape(tmp_path / "right.ndjson")
    rows = [json.loads(line) for line in right.read_text().splitlines()]
    for row in rows:
        if row["type"] == "depthUpdate" and row["data"]["_capture"]["recvMonotonicNs"] >= 10 * SECOND:
            row["data"]["b"][0][1] = "0.5"
    right.write_text("".join(json.dumps(row) + "\n" for row in rows))
    traces, audits = [], []
    for path in (left, right):
        sink = Recorder()
        engine = SimulationEngine(configuration(), regime_execution_sink=sink)
        engine.run(path)
        prefix = copy.deepcopy([row for row in engine.event_trace if row["ts_local"] < 10])
        for row in prefix:
            # Full-tape provenance changes, but is never an inference input.
            row["details"].get("hmm", {}).pop("input_sha256", None)
        traces.append(prefix)
        audits.append([row for row in sink.rows if row["event_type"] == "fill" and row["logical_ns"] < 10 * SECOND])
    assert traces[0] == traces[1]
    assert audits[0] == audits[1] and audits[0]


def test_quote_age_keeps_single_nanosecond_precision_and_rejects_unknown_acceptance():
    subject = audit()
    accepted_ns = 1_800_000_000_000_000_000
    assert float(accepted_ns) == float(accepted_ns + 1)  # The float path loses this distinction.
    subject.accept_order("order", stage(0, accepted_ns - 1), stage(0, accepted_ns))
    assert subject.order_age_ns("order", accepted_ns + 1) == 1
    with pytest.raises(ValueError, match="precede acceptance"):
        subject.order_age_ns("order", accepted_ns - 1)
    with pytest.raises(ValueError, match="accepted order"):
        subject.order_age_ns("missing", accepted_ns + 1)
