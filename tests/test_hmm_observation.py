from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

from lob_sim.regime.artifact import FrozenRegimeModel, save_model
from lob_sim.regime.features import FEATURE_NAMES, BookView, CausalFeatureSampler, FeatureSpec, FeatureValidity
from lob_sim.regime.hysteresis import HysteresisConfig
from lob_sim.regime.model import GaussianHMMParameters
from lob_sim.regime.observation import verify_trace
from lob_sim.regime.preprocess import TrainOnlyScaler
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.runtime import CausalRegimeEstimator
from lob_sim.regime.observation import RegimeObserver
from lob_sim.book.types import SymbolSpec
from lob_sim.record.envelope import ValidityState
from lob_sim.regime.validation import canonical_json, strict_json
from lob_sim.sim.checkpoint import encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.runner import run_bounded_simulation
from lob_sim.sim.run_manifest import config_snapshot
from lob_sim.sim.sinks import NullSink
from lob_sim.sim.observation import MarketObservation
from test_hmm_dataset import SECOND, SPEC, cfg, tape


def settings(*, training=None):
    d = len(FEATURE_NAMES)
    scaler = TrainOnlyScaler.fit_training(
        [(-1.0,) * d, (1.0,) * d], feature_names=FEATURE_NAMES, feature_identity=SPEC.digest, clip_quantiles=None
    )
    params = GaussianHMMParameters(
        (0.5, 0.5), ((0.95, 0.05), (0.05, 0.95)), ((-1.0,) * d, (1.0,) * d), ((1.0,) * d, (1.0,) * d)
    )
    metadata = {"purpose": "hand_specified_observation_test;not_economic_evidence"}
    if training is not None:
        metadata["training"] = training
    return HMMSettings(
        FrozenRegimeModel(SPEC, scaler, params, canonical_json(metadata)),
        "BTCUSDT",
        HysteresisConfig(confirmation_samples=2),
    )


class Recorder(NullSink):
    memory_bounded = False  # Deliberate fixture-only sink, not the ordinary CLI.

    def __init__(self):
        self.rows = []

    def write(self, row):
        self.rows.append(copy.deepcopy(dict(row)))


def run(path, configuration=None):
    sink = Recorder()
    engine = SimulationEngine(configuration or replace(cfg(), hmm=settings()), regime_sink=sink)
    engine.run(path)
    return engine, sink.rows


def without_diagnostics(rows):
    result = copy.deepcopy(rows)
    for row in result:
        details = row.get("details", {})
        for key in list(details):
            if key == "hmm" or key.startswith("hmm_"):
                del details[key]
    return result


@pytest.mark.parametrize("profile", ["baseline", "layered_mm", "research_mm"])
@pytest.mark.parametrize("fill_model", ["trade", "depth"])
def test_observation_only_preserves_all_execution_actions_fills_and_accounting(tmp_path, profile, fill_model):
    path = tape(tmp_path / "input.ndjson", same_time_trades=True)
    configuration = replace(
        cfg(),
        mm_strategy_profile=profile,
        sim_fill_model=fill_model,
        mm_half_spread_bps=Decimal("0"),
        fees_maker_bps=Decimal("0"),
        fees_taker_bps=Decimal("0"),
    )
    baseline = SimulationEngine(configuration)
    baseline.run(path)
    observed, rows = run(path, replace(configuration, hmm=settings()))
    assert rows and any(row["status"] == "VALID" for row in rows)
    assert without_diagnostics(observed.event_trace) == baseline.event_trace
    assert observed.metrics.get_summary(observed._books, observed._specs) == baseline.metrics.get_summary(
        baseline._books, baseline._specs
    )
    # Event trace carries extra diagnostics; every other continuation field is identical.
    left, right = observed._checkpoint_mutable_state(), baseline._checkpoint_mutable_state()
    left = {**left, "event_trace": without_diagnostics(left["event_trace"])}

    def strip_attribution(value):
        if isinstance(value, dict):
            if value.get("__type__") == "mapping":
                return {
                    "__type__": "mapping",
                    "items": [
                        [key, strip_attribution(item)]
                        for key, item in value["items"]
                        if not isinstance(key, str)
                        or key not in {"hmm_decision", "hmm_attributions", "hmm_attribution"}
                    ],
                }
            return {
                key: strip_attribution(item)
                for key, item in value.items()
                if key not in {"hmm_decision", "hmm_attributions", "hmm_attribution"}
            }
        if isinstance(value, (tuple, list)):
            return [strip_attribution(item) for item in value]
        return value

    assert strip_attribution(encode(left)) == strip_attribution(encode(right))
    assert observed.latency_model.sampler_state() == baseline.latency_model.sampler_state()
    decisions = [row for row in observed.event_trace if row["event_type"] == "decision"]
    assert any(row["details"]["hmm_valid"] for row in decisions)
    assert all(row["details"]["hmm_policy_reason"] == "observation_only" for row in decisions)


def test_future_raw_observation_mutation_preserves_historical_filter_and_hysteresis(tmp_path):
    left, left_rows = run(tape(tmp_path / "left.ndjson", future_qty="0.015"))
    right, right_rows = run(tape(tmp_path / "right.ndjson", future_qty="0.5"))

    def prefix(rows):
        # A complete tape hash necessarily changes. Its identity is not an input to inference.
        return [
            {k: v for k, v in row.items() if k != "input_sha256"}
            for row in rows
            if row["available_at_ns"] < 10 * SECOND
        ]

    assert prefix(left_rows) == prefix(right_rows)

    def decisions(engine):
        return [
            {k: v for k, v in row["details"].items() if k != "hmm"}
            for row in engine.event_trace
            if row["event_type"] == "decision" and row["ts_local"] < 10
        ]

    assert decisions(left) == decisions(right)
    assert left_rows != right_rows


@pytest.mark.parametrize(
    "control,status",
    [
        (("disconnect", "market", "BTCUSDT"), "INVALID_TRADE_STREAM"),
        (("disconnect", "public", "BTCUSDT"), "INVALID_BOOK"),
        (("overflow", "control", "*"), "INVALID_CAPTURE"),
    ],
)
def test_fault_between_grid_points_invalidates_immediately_not_next_sample(tmp_path, control, status):
    engine, rows = run(tape(tmp_path / "input.ndjson", control=control))
    before = [row for row in rows if row["event_type"] == "sample" and row["sample_ns"] == 6 * SECOND]
    assert before[0]["status"] == "VALID" and before[0]["confidence"] > 0.99
    boundary = [row for row in rows if row["event_type"] == "invalidation" and row["available_at_ns"] == 6_100_000_000]
    assert len(boundary) == 1 and boundary[0]["status"] == status
    assert boundary[0]["posterior"] is None
    assert engine.regime.estimator.filter.samples_seen == 0
    assert engine.regime.snapshot(6_100_000_001)["posterior"] is None
    after = [row for row in rows if row["available_at_ns"] > 6_100_000_000]
    assert all(row["posterior"] is None for row in after)


def test_same_time_trade_batch_not_used_by_decisions_before_sample_available(tmp_path):
    engine, rows = run(tape(tmp_path / "input.ndjson", same_time_trades=True))
    sample = next(row for row in rows if row["event_type"] == "sample" and row["sample_ns"] == 6 * SECOND)
    assert sample["available_at_ns"] == 7 * SECOND and sample["features"][5] == 0.5
    decisions = [row for row in engine.event_trace if row["event_type"] == "decision" and 6 <= row["ts_local"] < 7]
    assert decisions and all(row["details"]["hmm"].get("sample_ns", 0) < 6 * SECOND for row in decisions)


@pytest.mark.parametrize("cut", [7, 10, 14, 16])
def test_observation_checkpoint_resume_identical_state_trace_hash_and_core_actions(tmp_path, cut):
    path = tape(tmp_path / "input.ndjson", same_time_trades=True)
    configuration = replace(cfg(), hmm=settings())
    uninterrupted = SimulationEngine(configuration)
    uninterrupted.run(path)
    paused = SimulationEngine(configuration)
    checkpoint = tmp_path / "state.json"
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    resumed = SimulationEngine(configuration)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == uninterrupted.state_sha256()
    assert resumed.event_trace == uninterrupted.event_trace
    assert resumed.regime.checkpoint() == uninterrupted.regime.checkpoint()
    assert resumed.metrics.fill_audit_sha256 == uninterrupted.metrics.fill_audit_sha256
    assert resumed.metrics.markout_audit_sha256 == uninterrupted.metrics.markout_audit_sha256


def test_checkpoint_rejects_different_model_and_corrupt_regime_before_engine_mutation(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    paused = SimulationEngine(configuration)
    checkpoint = tmp_path / "state.json"
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=10)
    changed = replace(configuration.hmm.model, parameters=configuration.hmm.model.parameters.permute((1, 0)))
    wrong = SimulationEngine(replace(configuration, hmm=HMMSettings(changed, "BTCUSDT")))
    before = encode(wrong._checkpoint_mutable_state())
    with pytest.raises(ValueError, match="configuration digest"):
        wrong.run(path, resume_from=checkpoint)
    assert encode(wrong._checkpoint_mutable_state()) == before
    original = paused.regime.checkpoint()
    corrupt = copy.deepcopy(original)
    corrupt["contexts"][0]["sampler"]["current"]["trade_lots"] = -1
    with pytest.raises(ValueError):
        paused.regime.restore(corrupt)
    assert paused.regime.checkpoint() == original


def test_checkpoint_with_streamed_regime_output_rejects_before_any_resume_rows(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    checkpoint = tmp_path / "state.json"
    SimulationEngine(configuration).run(path, checkpoint_path=checkpoint, stop_after_records=10)

    # Recorder inherits NullSink for fixture use; a genuine external sink is not a NullSink.
    class Sink:
        memory_bounded = True

        def write(self, row):
            raise AssertionError("resume must not write")

        def close(self):
            pass

    with pytest.raises(ValueError, match="regime checkpoint resume requires NullSink"):
        SimulationEngine(configuration, regime_sink=Sink()).run(path, resume_from=checkpoint)


def test_transactional_runtime_bundle_reproduces_regime_audit_and_preserves_baseline_csv(tmp_path):
    path = tape(tmp_path / "input.ndjson", same_time_trades=True)
    base_cfg = replace(cfg(), record_dir=tmp_path / "base")
    baseline_files, baseline_summary = run_bounded_simulation(base_cfg, path)
    files, summary = run_bounded_simulation(replace(base_cfg, record_dir=tmp_path / "hmm", hmm=settings()), path)
    assert len(files) == 9 and not (files["manifest"].parent / "_INCOMPLETE.json").exists()
    verify_trace(files["regime_trace"], summary["hmm"])
    manifest = json.loads(files["manifest"].read_text())
    assert manifest["config"]["hmm"]["model_sha256"] == settings().model.model_sha256
    for name in ("trades", "markouts"):
        assert files[name].read_bytes() == baseline_files[name].read_bytes()
    assert summary["audit_retention"]["fill_audit_sha256"] == baseline_summary["audit_retention"]["fill_audit_sha256"]
    assert "hmm" not in config_snapshot(base_cfg)
    # Serialized corruption cannot silently finalize as a valid audit.
    data = files["regime_trace"].read_text()
    files["regime_trace"].write_text(data.replace("STATE_1", "STATE_0"))
    with pytest.raises(ValueError, match="count/hash"):
        verify_trace(files["regime_trace"], summary["hmm"])


def test_ten_repeated_runtime_traces_are_identical_and_no_history_retained(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    summaries = []
    for _ in range(10):
        engine = SimulationEngine(replace(cfg(), hmm=settings()), retain_event_trace=False, retain_audit_rows=False)
        engine.run(path)
        summaries.append(engine.regime.summary())
        assert engine.regime.summary()["retained_samples"] <= 1
        assert engine.regime.summary()["retained_window_bins"] <= SPEC.window_steps
        assert len(engine.regime._contexts) == 1
        assert engine.event_trace == [] and engine.metrics.fills_log == []
    assert all(summary == summaries[0] for summary in summaries)


def test_hmm_runtime_requires_no_fitting_imports_and_cli_discovers_observe(tmp_path):
    model_path = tmp_path / "model.json"
    save_model(model_path, settings().model)
    path = tape(tmp_path / "input.ndjson")
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "--env", ".env.example", "simulate", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--hmm" in result.stdout and "--hmm-model" in result.stdout
    script = """
import sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pyarrow':
            raise ImportError('simulate a lightweight base install without the optional storage stack')
        if fullname.split('.')[0] in {'hmmlearn', 'numpy', 'scipy', 'sklearn'}:
            raise AssertionError('runtime imported fitting dependency: ' + fullname)
sys.meta_path.insert(0, Guard())
from dataclasses import replace
from lob_sim.config import load_config
from lob_sim.regime.settings import HMMSettings
from lob_sim.sim.engine import SimulationEngine
c = replace(load_config('.env.example', inherit_environment=False), hmm=HMMSettings.load(sys.argv[1], symbol='BTCUSDT'))
e = SimulationEngine(c, retain_event_trace=False, retain_audit_rows=False)
e.run(sys.argv[2])
assert e.regime.summary()['sample_count'] > 0
"""
    subprocess.run([sys.executable, "-c", script, str(model_path), str(path)], check=True)


def test_model_instrument_identity_checked_against_authoritative_metadata(tmp_path):
    with pytest.raises(ValueError, match="training symbol"):
        settings(training={"symbol": "ETHUSDT"})
    with pytest.raises(ValueError, match="instrument grid"):
        run(
            tape(tmp_path / "input.ndjson"),
            replace(cfg(), hmm=settings(training={"symbol": "BTCUSDT", "instrument_sha256": "b" * 64})),
        )


@pytest.mark.parametrize("status", ["VALID", "INVALID_BOOK", "INVALID_TRADE_STREAM", "STALE"])
def test_sampler_checkpoint_roundtrip_preserves_partial_window_and_invalid_states(status):
    sampler = CausalFeatureSampler("BTCUSDT", SPEC)
    book = BookView(((99, 10),), ((101, 10),))
    for seq in range(4):
        list(sampler.advance_before(seq * SECOND))
        sampler.observe(seq * SECOND, seq, book=book, validity=FeatureValidity(), depth_event=True)
    if status != "VALID":
        list(sampler.advance_before(4 * SECOND))
        validity = FeatureValidity(book=status != "INVALID_BOOK", trade=status != "INVALID_TRADE_STREAM")
        sampler.observe(4 * SECOND, 4, book=book, validity=validity, depth_event=status != "STALE")
        if status == "STALE":
            list(sampler.advance_before(10 * SECOND))
    checkpoint = strict_json(canonical_json(sampler.checkpoint()))
    other = CausalFeatureSampler("BTCUSDT", SPEC)
    other.restore(checkpoint)
    assert other.checkpoint() == sampler.checkpoint()
    for seq in range(11, 16):
        assert list(other.advance_before(seq * SECOND)) == list(sampler.advance_before(seq * SECOND))
        for item in (other, sampler):
            item.observe(seq * SECOND, seq, book=book, validity=FeatureValidity(), depth_event=True)
    assert list(other.finish(15 * SECOND)) == list(sampler.finish(15 * SECOND))


@pytest.mark.parametrize(
    "update",
    [
        {"next_ns": 123},
        {"last_receive_seq": True},
        {"feature_identity": "bad"},
        {"bins": [{}] * 3},
        {"previous_mid": float("nan")},
        {"valid_since_ns": 999 * SECOND},
    ],
)
def test_sampler_checkpoint_rejection_is_atomic(update):
    sampler = CausalFeatureSampler("BTCUSDT", FeatureSpec(window_steps=2))
    sampler.observe(0, 0, book=BookView(((99, 10),), ((101, 10),)), validity=FeatureValidity(), depth_event=True)
    before = sampler.checkpoint()
    with pytest.raises(ValueError):
        sampler.restore({**before, **update})
    assert sampler.checkpoint() == before


@pytest.mark.parametrize(
    "control", [("disconnect", "public", "BTCUSDT"), ("disconnect", "market", "BTCUSDT"), ("overflow", "control", "*")]
)
@pytest.mark.parametrize("cut", [11, 12])
def test_checkpoint_after_fault_or_ignored_record_is_a_real_boundary(tmp_path, control, cut):
    path = tape(tmp_path / "input.ndjson", control=control)
    configuration = replace(cfg(), hmm=settings())
    full = SimulationEngine(configuration)
    full.run(path)
    checkpoint = tmp_path / "state.json"
    paused = SimulationEngine(configuration)
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    assert checkpoint.is_file() and paused._last_event_index == cut
    assert paused.regime.snapshot(paused._last_logical_ns)["posterior"] is None
    resumed = SimulationEngine(configuration)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == full.state_sha256()
    assert resumed.regime.summary() == full.regime.summary()
    assert resumed.event_trace == full.event_trace


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("cut", [1, 2, 3, 4])
def test_metadata_and_control_records_also_honor_stop_and_checkpoint(tmp_path, enabled, cut):
    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings() if enabled else None)
    checkpoint = tmp_path / "state.json"
    paused = SimulationEngine(configuration)
    paused.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    assert checkpoint.is_file() and paused._last_event_index == cut
    full = SimulationEngine(configuration)
    full.run(path)
    resumed = SimulationEngine(configuration)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == full.state_sha256()


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", 0.25),
        ("active_state", True),
        ("next_prior", [0.5, 0.5]),
        ("entropy", 1.0),
        ("scaled_features", [0.0] * 12),
        ("available_at_ns", 1),
        ("model_sha256", "a" * 64),
    ],
)
def test_observer_checkpoint_rejects_inconsistent_latest_signal_atomically(tmp_path, field, value):
    engine, _ = run(tape(tmp_path / "input.ndjson"))
    before = copy.deepcopy(engine.regime.checkpoint())
    corrupt = copy.deepcopy(before)
    corrupt["latest"][field] = value
    with pytest.raises(ValueError):
        engine.regime.restore(corrupt)
    assert engine.regime.checkpoint() == before


def test_long_virtual_observation_stream_keeps_only_fixed_window_and_current_signal():
    observer = RegimeObserver(settings())
    observer.bind_input("a" * 64)
    instrument = SymbolSpec("BTCUSDT", Decimal("0.1"), Decimal("0.001"))
    for index in range(2001):
        ns = index * SECOND
        observer.before_record(ns)
        observer.observe(
            MarketObservation(
                "BTCUSDT",
                instrument,
                ns,
                index,
                index,
                ns,
                True,
                ValidityState(True, True, True, True),
                (0, 0, 0),
                ((990, 10),),
                ((1010, 10),),
                True,
            )
        )
    observer.finish(2000 * SECOND)
    summary = observer.summary()
    assert summary["sample_count"] == 2000
    assert summary["retained_samples"] == 1 and summary["retained_window_bins"] == SPEC.window_steps
    # The new fixed K-by-feature diagnostics add counters, not row history.
    assert len(canonical_json(observer.checkpoint())) < 20_000
    assert summary["state_diagnostics"]["retained_episodes"] <= 2
    assert all(len(cells) == 3 for cells in observer.diagnostics.checkpoint()["cells"].values())
    assert observer.snapshot(2010 * SECOND)["status"] == "STALE"
    assert observer.snapshot(2010 * SECOND)["posterior"] is None


def test_regime_writer_failure_preserves_incomplete_bundle_and_stops(tmp_path, monkeypatch):
    from lob_sim.sim.sinks import StreamingCsvSink

    original = StreamingCsvSink.write

    def fail(self, row):
        if self.path.name == "regime_trace.csv" and row["status"] == "VALID":
            raise OSError("injected regime audit writer failure")
        return original(self, row)

    monkeypatch.setattr(StreamingCsvSink, "write", fail)
    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), record_dir=tmp_path, hmm=settings())
    with pytest.raises(OSError, match="writer failure"):
        run_bounded_simulation(configuration, path)
    directory = next(configuration.output_dir.iterdir())
    assert (directory / "_INCOMPLETE.json").exists()
    assert (directory / "regime_trace.csv.partial").exists()
    assert not (directory / "manifest.json").exists()


def test_estimator_checkpoint_restores_without_mutation_or_future_observations(tmp_path):
    engine, _ = run(tape(tmp_path / "input.ndjson"))
    original = engine.regime.estimator
    checkpoint = strict_json(canonical_json(original.checkpoint()))
    restored = CausalRegimeEstimator(original.model, original.hysteresis.config)
    restored.restore(checkpoint)
    assert restored.checkpoint() == original.checkpoint()
    from lob_sim.regime.features import FeatureSample

    sample = FeatureSample("BTCUSDT", 15 * SECOND, 100, (1, 0, 0), SPEC.digest, "VALID", (1.0,) * 12, "epoch_changed")
    assert restored.update(sample) == original.update(sample)
    for change in (
        {"last_sample_ns": 1},
        {"last_receive_seq": True},
        {"epochs": [True, 0, 0]},
        {"reset_reason": "invalid_while_filter_active"},
        {"model_sha256": "c" * 64},
    ):
        before = restored.checkpoint()
        with pytest.raises(ValueError):
            restored.restore({**before, **change})
        assert restored.checkpoint() == before


@pytest.mark.parametrize(
    "fault,status",
    [
        ("depth_gap", "INVALID_BOOK"),
        ("off_grid", "INVALID_BOOK"),
        ("clock_regression", "INVALID_CLOCK"),
        ("receive_sequence_gap", "INVALID_CAPTURE"),
    ],
)
def test_integrated_model_cannot_keep_confidence_after_corrupt_observation(tmp_path, fault, status):
    path = tape(tmp_path / "input.ndjson")
    records = [json.loads(line) for line in path.read_text().splitlines()]
    target = next(
        row
        for row in records
        if row["type"] == "depthUpdate" and row["data"]["_capture"]["recvMonotonicNs"] == 6 * SECOND
    )
    if fault == "depth_gap":
        target["data"]["pu"] = 999
    elif fault == "off_grid":
        target["data"]["b"][0][1] = "0.0123"
    elif fault == "clock_regression":
        target["data"]["_capture"]["recvMonotonicNs"] = 4 * SECOND + SECOND // 2
    else:
        for row in records[records.index(target) :]:
            row["data"]["_capture"]["recvSeq"] += 1
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    engine, rows = run(path)
    assert any(row["event_type"] == "invalidation" and row["status"] == status for row in rows)
    assert engine.regime.snapshot(14 * SECOND)["posterior"] is None
    assert all(row["posterior"] is None for row in rows if row["available_at_ns"] >= 7 * SECOND)


def test_cli_observe_publishes_complete_bundle_with_frozen_model(tmp_path, monkeypatch, capsys):
    from lob_sim import cli

    path = tape(tmp_path / "input.ndjson")
    model_path = tmp_path / "model.json"
    save_model(model_path, settings().model)
    monkeypatch.setattr(cli, "load_config", lambda *_: replace(cfg(), record_dir=tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lob-sim",
            "simulate",
            "--file",
            str(path),
            "--strategy",
            "research_mm",
            "--hmm",
            "observe",
            "--hmm-model",
            str(model_path),
            "--hmm-symbol",
            "BTCUSDT",
        ],
    )
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["hmm"]["config"]["model_sha256"] == settings().model.model_sha256
    assert report["hmm"]["strategy_intervention"] is False
    from pathlib import Path

    directory = Path(report["output_files"]["manifest"]).parent
    assert (directory / "hmm_model.json").is_file()
    assert (directory / "regime_trace.csv").is_file()
    assert not (directory / "_INCOMPLETE.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("HMM_ENTER_PROBABILITY", "0.5"),
        ("HMM_CONFIRM_SAMPLES", "0"),
        ("HMM_MIN_STATE_AGE_SAMPLES", "-1"),
        ("HMM_MAX_NORMALIZED_ENTROPY", "1.1"),
    ],
)
def test_hmm_environment_namespace_rejects_unsafe_thresholds(tmp_path, field, value):
    from dotenv import dotenv_values
    from lob_sim.config import ConfigError, _config_from_values

    model_path = tmp_path / "model.json"
    save_model(model_path, settings().model)
    values = dict(dotenv_values(".env.example", interpolate=False))
    values.update({"HMM_MODE": "observe", "HMM_MODEL_PATH": str(model_path), "HMM_SYMBOL": "BTCUSDT", field: value})
    with pytest.raises(ConfigError, match="HMM configuration"):
        _config_from_values(values)


def test_environment_load_and_immutable_model_do_not_follow_file_mutation(tmp_path):
    from dotenv import dotenv_values
    from lob_sim.config import _config_from_values

    model_path = tmp_path / "model.json"
    save_model(model_path, settings().model)
    values = dict(dotenv_values(".env.example", interpolate=False))
    values.update(
        {"HMM_MODE": "observe", "HMM_MODEL_PATH": str(model_path), "HMM_SYMBOL": "BTCUSDT", "RECORD_DIR": str(tmp_path)}
    )
    configuration = _config_from_values(values)
    model_path.write_text("corrupt after configuration loaded")
    files, summary = run_bounded_simulation(configuration, tape(tmp_path / "input.ndjson"))
    assert summary["hmm"]["config"]["model_sha256"] == settings().model.model_sha256
    from lob_sim.regime.artifact import load_model

    assert load_model(files["hmm_model"]).model_sha256 == settings().model.model_sha256


def test_audit_consumer_cannot_mutate_live_signal_or_its_canonical_hash(tmp_path):
    class MutatingSink(NullSink):
        def write(self, row):
            if row["posterior"] is not None:
                row["posterior"][0] = 0.5
                row["features"][0] = -999.0

    path = tape(tmp_path / "input.ndjson")
    configuration = replace(cfg(), hmm=settings())
    original = SimulationEngine(configuration)
    original.run(path)
    attacked = SimulationEngine(configuration, regime_sink=MutatingSink())
    attacked.run(path)
    assert attacked.regime._latest == original.regime._latest
    assert attacked.regime.summary() == original.regime.summary()
    assert attacked.state_sha256() == original.state_sha256()


def test_checkpoint_is_an_independent_snapshot_not_a_live_signal_handle(tmp_path):
    engine, _ = run(tape(tmp_path / "input.ndjson"))
    expected = copy.deepcopy(engine.regime._latest)
    checkpoint = engine.regime.checkpoint()
    checkpoint["latest"]["posterior"][0] = 0.5
    checkpoint["latest"]["features"][0] = -999.0
    assert engine.regime._latest == expected
