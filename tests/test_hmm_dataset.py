from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.regime.dataset import (
    FeatureDatasetObserver,
    dataset_split,
    extract_features,
    load_dataset_manifest,
    read_partition,
    utc_day,
)
from lob_sim.regime.features import FeatureSpec
from lob_sim.research.protocol import ResearchRegistry
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.observation import MarketObservation

SECOND = 1_000_000_000
WALL = 1_735_689_600 * SECOND  # 2025-01-01 UTC, unrelated to monotonic origin.
SPEC = FeatureSpec(window_steps=2, depth_levels=2)


def cfg():
    return load_config(".env.example", inherit_environment=False)


def tape(path: Path, *, future_qty="0.015", control=None, same_time_trades=False, days=1) -> Path:
    rows = []

    def emit(time, kind, data, route="public", symbol="BTCUSDT", wall_offset=0):
        ns = int(time * SECOND)
        data = dict(data)
        data["_capture"] = {
            "recvSeq": len(rows),
            "recvMonotonicNs": ns,
            "recvWallNs": WALL + wall_offset + ns,
            "route": route,
            "streamEpoch": 0,
            "syncEpoch": 0,
        }
        rows.append({"ts_local": (WALL + wall_offset + ns) / SECOND, "symbol": symbol, "type": kind, "data": data})

    emit(0, "captureMeta", {"schemaVersion": 3, "clock": "receive_time"}, "control", "*")
    emit(0.1, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001"}, "control")
    emit(0.2, "captureEvent", {"event": "connect", "route": "public"}, "public")
    emit(0.3, "captureEvent", {"event": "connect", "route": "market"}, "market")
    update_id = 100
    for day in range(days):
        base = day * 20
        # Each short snippet belongs to a separate UTC day. The wall jump is
        # not an additional valid day of coverage, and the output says so.
        offset = day * (86_400 - 20) * SECOND
        emit(
            base + 1,
            "snapshot",
            {"lastUpdateId": update_id, "bids": [["99.0", "0.01"]], "asks": [["101.0", "0.01"]]},
            wall_offset=offset,
        )
        for time in range(2, 14):
            previous = update_id
            update_id += 1
            emit(
                base + time,
                "depthUpdate",
                {
                    "U": previous if time == 2 else update_id,
                    "u": update_id,
                    "pu": previous,
                    "b": [["99.0", future_qty if time >= 10 else "0.012"]],
                    "a": [["101.0", "0.01"]],
                },
                wall_offset=offset,
            )
            if same_time_trades and time == 6:
                for maker, quantity in ((False, "0.003"), (True, "0.001")):
                    emit(
                        base + time, "aggTrade", {"p": "101.0", "q": quantity, "m": maker}, "market", wall_offset=offset
                    )
            if control is not None and time == 6:
                kind, route, symbol = control
                emit(base + time + 0.1, "captureEvent", {"event": kind, "route": route}, route, symbol, offset)
    emit(
        days * 20 - 6,
        "captureEvent",
        {"event": "capture_trailer", "route": "control"},
        "control",
        "*",
        (days - 1) * (86_400 - 20) * SECOND,
    )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def observed(path: Path):
    rows = []
    observer = FeatureDatasetObserver(SPEC, "a" * 64, lambda row: rows.append(dict(row)))
    engine = SimulationEngine(cfg(), market_observer=observer)
    engine.run(path)
    return engine, observer, rows


def test_read_only_observer_preserves_actions_fills_summary_and_ids(tmp_path):
    path = tape(tmp_path / "input.ndjson")
    baseline = SimulationEngine(cfg())
    baseline.run(path)
    engine, _, rows = observed(path)
    assert rows
    assert engine.event_trace == baseline.event_trace
    assert engine.metrics.get_summary(engine._books, engine._specs) == baseline.metrics.get_summary(
        baseline._books, baseline._specs
    )
    assert engine._id_counter == baseline._id_counter
    assert engine._trace_counter == baseline._trace_counter
    assert engine.latency_model.sampler_state() == baseline.latency_model.sampler_state()
    assert engine._checkpoint_mutable_state() == baseline._checkpoint_mutable_state()


def test_raw_tape_future_mutation_cannot_change_historical_features(tmp_path):
    _, _, left = observed(tape(tmp_path / "left.ndjson", future_qty="0.015"))
    _, _, right = observed(tape(tmp_path / "right.ndjson", future_qty="0.5"))
    assert [row for row in left if row["sample_ns"] < 10 * SECOND] == [
        row for row in right if row["sample_ns"] < 10 * SECOND
    ]
    assert left != right


def test_same_time_trades_are_sampled_after_all_receipts_and_not_available_early(tmp_path):
    _, _, rows = observed(tape(tmp_path / "input.ndjson", same_time_trades=True))
    row = next(row for row in rows if row["sample_ns"] == 6 * SECOND)
    assert row["features"][5] == pytest.approx(0.5)
    assert row["available_at_ns"] == 7 * SECOND
    assert row["wall_ns"] == WALL + 6 * SECOND
    assert row["clock_basis"] == "causal_receive_wall_projection"


@pytest.mark.parametrize(
    "control,expected",
    [
        (("disconnect", "market", "BTCUSDT"), "INVALID_TRADE_STREAM"),
        (("disconnect", "public", "BTCUSDT"), "INVALID_BOOK"),
        (("overflow", "control", "*"), "INVALID_CAPTURE"),
    ],
)
def test_authoritative_control_invalidity_reaches_features(tmp_path, control, expected):
    engine, _, rows = observed(tape(tmp_path / "input.ndjson", control=control))
    invalid = [row for row in rows if row["sample_ns"] >= 7 * SECOND]
    assert invalid and all(row["features"] is None and row["sequence_id"] is None for row in invalid)
    assert expected in {row["status"] for row in invalid}
    if expected == "INVALID_TRADE_STREAM":
        assert engine._syncers["BTCUSDT"].synced  # Observer cannot invalidate book-only execution.
        assert engine._validity_state("BTCUSDT", require_trade=False).execution_valid


def test_no_fabricated_samples_after_end_of_tape_and_windows_bounded(tmp_path):
    _, observer, rows = observed(tape(tmp_path / "input.ndjson"))
    assert rows[-1]["sample_ns"] == 14 * SECOND
    assert rows[-1]["available_at_ns"] == 14 * SECOND
    assert observer._contexts["BTCUSDT"].sampler.retained_bins == SPEC.window_steps


def test_observations_are_immutable_snapshots(tmp_path):
    class Recorder:
        depth_levels = 2
        observations = []

        def before_record(self, logical_ns):
            pass

        def observe(self, observation):
            self.observations.append(observation)

        def finish(self, logical_ns):
            pass

    recorder = Recorder()
    engine = SimulationEngine(cfg(), market_observer=recorder)
    engine.run(tape(tmp_path / "input.ndjson"))
    first = next(obs for obs in recorder.observations if obs.depth_observed)
    assert isinstance(first, MarketObservation)
    assert first.bids == ((990, 12),)
    engine._books["BTCUSDT"].bids[990] = 999
    assert first.bids == ((990, 12),)
    with pytest.raises(FrozenInstanceError):
        first.logical_ns = 0


def test_checkpoint_requests_reject_unpersisted_observer_state_before_replay(tmp_path):
    rows = []
    observer = FeatureDatasetObserver(SPEC, "a" * 64, rows.append)
    engine = SimulationEngine(cfg(), market_observer=observer)
    with pytest.raises(ValueError, match="checkpoint/resume"):
        engine.run(tape(tmp_path / "input.ndjson"), checkpoint_path=tmp_path / "checkpoint.json")
    assert not rows
    assert not (tmp_path / "checkpoint.json").exists()


def bundle(tmp_path):
    path = tape(tmp_path / "input.ndjson", days=3)
    directory = tmp_path / "features"
    manifest = extract_features(path, directory, cfg(), spec=SPEC)
    return directory, manifest


def test_dataset_roundtrip_physically_separates_days_sequences_and_holdout(tmp_path):
    directory, manifest = bundle(tmp_path)
    assert load_dataset_manifest(directory) == manifest
    split = dataset_split(directory)
    assert split.calibration_days == ("2025-01-01",)
    assert split.validation_days == ("2025-01-02",)
    assert split.test_days == ("2025-01-03",)
    assert not split.claim_ready and not manifest["claim_ready"]
    training = read_partition(directory, split, "calibration", symbol="BTCUSDT")
    # Five-second freshness permits samples through t=18 before the next day
    # snippet starts at t=21; this is explicit stale-feed carry, not interpolation.
    assert training.lengths == (15,)
    assert len(training.rows) == 15
    validation = read_partition(directory, split, "validation", symbol="BTCUSDT")
    assert validation.days != training.days
    with pytest.raises(ValueError, match="freeze ResearchRegistry"):
        read_partition(directory, split, "test", symbol="BTCUSDT")
    registry = ResearchRegistry()
    registry.register("all-model-and-policy-variants", {"example": True})
    registry.freeze()
    test = read_partition(directory, split, "test", symbol="BTCUSDT", registry=registry)
    assert test.role == "test"


def test_fit_partition_reader_does_not_open_holdout_file(tmp_path):
    directory, _ = bundle(tmp_path)
    split = dataset_split(directory)
    # Deliberately corrupt both future partitions. Calibration must not read
    # their bytes, parse their values or verify their data-dependent statistics.
    for day in (*split.validation_days, *split.test_days):
        (directory / (day + ".jsonl")).write_bytes(b"NOT JSON\n")
    assert read_partition(directory, split, "calibration", symbol="BTCUSDT").rows
    with pytest.raises(ValueError, match="checksum"):
        read_partition(directory, split, "validation", symbol="BTCUSDT")


def test_dataset_rejects_wrong_split_instrument_missing_symbol_and_row_cap(tmp_path):
    directory, _ = bundle(tmp_path)
    split = dataset_split(directory)
    with pytest.raises(ValueError, match="split does not match"):
        read_partition(directory, replace(split, calibration_days=split.test_days), "calibration", symbol="BTCUSDT")
    with pytest.raises(ValueError, match="no valid feature rows"):
        read_partition(directory, split, "calibration", symbol="ETHUSDT")
    with pytest.raises(ValueError, match="row cap"):
        read_partition(directory, split, "calibration", symbol="BTCUSDT", max_rows=2)


def test_dataset_no_clobber_and_truncation_fail_closed(tmp_path):
    directory, _ = bundle(tmp_path)
    original = (directory / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        extract_features(tmp_path / "input.ndjson", directory, cfg(), spec=SPEC)
    assert (directory / "manifest.json").read_bytes() == original
    incomplete = tmp_path / "broken.ndjson"
    incomplete.write_text('{"ts_local":', encoding="utf-8")
    with pytest.raises(ValueError):
        extract_features(incomplete, tmp_path / "failed", cfg(), spec=SPEC)
    assert not (tmp_path / "failed" / "manifest.json").exists()
    with pytest.raises(FileNotFoundError):
        load_dataset_manifest(tmp_path / "failed")


def test_utc_day_uses_integer_nanoseconds_at_boundary():
    assert utc_day(WALL - 1) == "2024-12-31"
    assert utc_day(WALL) == "2025-01-01"
    with pytest.raises(ValueError):
        utc_day(True)


def test_raw_feature_values_match_independent_price_lot_arithmetic(tmp_path):
    import math

    _, _, rows = observed(tape(tmp_path / "input.ndjson"))
    values = next(row["features"] for row in rows if row["sample_ns"] == 4 * SECOND)
    assert values == pytest.approx(
        (
            200,
            0,
            0,
            2 / 22,
            2 / 22,
            0,
            math.log1p(22),
            0,
            math.log1p(1),
            0,
            10000 * (((1010 * 12 + 990 * 10) / 22 - 1000) / 1000),
            0,
        )
    )


@pytest.mark.parametrize(
    "fault,status",
    [
        ("depth_gap", "INVALID_BOOK"),
        ("off_grid", "INVALID_CAPTURE"),
        ("clock_regression", "INVALID_CLOCK"),
        ("receive_gap", "INVALID_CAPTURE"),
    ],
)
def test_normalizer_gap_and_receipt_failures_invalidate_extracted_features(tmp_path, fault, status):
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
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    _, _, rows = observed(path)
    invalid = [row for row in rows if row["sample_ns"] >= 7 * SECOND]
    assert invalid
    assert all(row["status"] == status and row["features"] is None for row in invalid)


def test_schema_v3_requote_grid_cannot_drift_behind_a_market_timestamp(tmp_path):
    engine = SimulationEngine(cfg())  # Deliberately no HMM observer.
    engine.run(tape(tmp_path / "input.ndjson"))
    decisions = [(index, row) for index, row in enumerate(engine.event_trace) if row["event_type"] == "decision"]
    assert decisions
    assert all(round(row["ts_local"] * SECOND) % 40_000_000 == 0 for _, row in decisions)
    decision_index = next(index for index, row in decisions if row["ts_local"] == 9.0)
    market_index = next(
        index
        for index, row in enumerate(engine.event_trace)
        if row["event_type"] == "market_record" and row["ts_local"] == 9.0
    )
    assert market_index < decision_index


def test_feature_bundle_hashes_reproduce_without_output_path_dependence(tmp_path):
    source = tape(tmp_path / "input.ndjson", days=3)
    left = extract_features(source, tmp_path / "left", cfg(), spec=SPEC)
    right = extract_features(source, tmp_path / "right", cfg(), spec=SPEC)
    assert left == right
    for entry in left["day_files"]:
        assert (tmp_path / "left" / entry["path"]).read_bytes() == (tmp_path / "right" / entry["path"]).read_bytes()


def test_partition_hashes_consumed_bytes_when_file_changes_after_precheck(tmp_path, monkeypatch):
    from lob_sim.regime import dataset

    directory, _ = bundle(tmp_path)
    split = dataset_split(directory)
    original_hash = dataset.file_sha256

    def mutate_after_hash(path):
        result = original_hash(path)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        next(row for row in rows if row["status"] == "VALID")["features"][0] += 10
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return result

    monkeypatch.setattr(dataset, "file_sha256", mutate_after_hash)
    with pytest.raises(ValueError, match="checksum mismatch during read"):
        read_partition(directory, split, "calibration", symbol="BTCUSDT")


def test_audit_sink_failure_propagates_instead_of_dropping_rows(tmp_path):
    def fail(row):
        raise OSError("injected writer failure")

    observer = FeatureDatasetObserver(SPEC, "a" * 64, fail)
    engine = SimulationEngine(cfg(), market_observer=observer)
    with pytest.raises(OSError, match="injected writer failure"):
        engine.run(tape(tmp_path / "input.ndjson"))


def test_cli_discovers_feature_fit_and_inspect_commands(monkeypatch, capsys):
    from lob_sim.cli import main

    monkeypatch.setattr("sys.argv", ["lob-sim", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "regime-features" in output and "regime-fit" in output and "regime-inspect" in output


def test_cli_extraction_and_failed_fit_preserve_report_without_publishing_model(tmp_path, monkeypatch):
    from lob_sim.cli import main

    source = tape(tmp_path / "input.ndjson", days=3)
    directory = tmp_path / "features"
    monkeypatch.setattr(
        "sys.argv",
        [
            "lob-sim",
            "--env",
            ".env.example",
            "regime-features",
            "--file",
            str(source),
            "--out",
            str(directory),
            "--window-steps",
            "2",
            "--symbol",
            "BTCUSDT",
        ],
    )
    main()
    assert load_dataset_manifest(directory)["symbols"] == ["BTCUSDT"]
    # Real extracted constant snippets are deliberately too small to fit.
    pytest.importorskip("hmmlearn")
    model, report = tmp_path / "model.json", tmp_path / "fit.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "lob-sim",
            "regime-fit",
            "--dataset",
            str(directory),
            "--symbol",
            "BTCUSDT",
            "--model",
            str(model),
            "--report",
            str(report),
            "--restarts",
            "2",
        ],
    )
    with pytest.raises(SystemExit, match="No valid HMM candidate"):
        main()
    saved = json.loads(report.read_text())
    assert saved["status"] == "no_valid_candidate" and len(saved["attempts"]) == 8
    assert not model.exists()
