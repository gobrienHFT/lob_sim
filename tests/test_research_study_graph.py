"""Reduced-grid integration harness on SYNTHETIC data, not day admission.

Only parent-admission/fitting and grid-size boundaries are replaced inside
pytest. Real matching, sinks, models, scenario configs, serialized case audits,
period reducers, statistics and the study re-reader run. There is no production
override flag; real registration continues to reject this synthetic source.
"""

from copy import deepcopy
from dataclasses import replace

import pytest

from lob_sim.book.types import SymbolSpec
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from lob_sim.regime.artifact import save_model
from lob_sim.regime.dataset import publish_json, instrument_grid_identity
from lob_sim.regime.features import FeatureSpec, FEATURE_NAMES
from lob_sim.regime.preprocess import TrainOnlyScaler
from lob_sim.regime.validation import canonical_json, identity, strict_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_view import view_specification, write_day_view
from lob_sim.research.fit_bundle import load_model_set
from lob_sim.research.registered_protocol import FrozenProtocol, experiment_specification
from lob_sim.research.replay_engine import ReplayWindow
from lob_sim.research.study_cases import expected_cases
from lob_sim.sim.checkpoint import checkpoint_code_identity
from test_hmm_policy import policy_settings
from test_research_capture_intervals import cfg, SECOND as S, WALL
from test_research_registry import mechanical_payload


def synthetic_tape(root):
    sequence = 0
    with SegmentedCaptureWriter(root, "synthetic_graph_only", compression="none") as writer:

        def emit(t, kind, data, symbol="*", route="control"):
            nonlocal sequence
            sequence += 1
            writer.write(
                EventEnvelope(
                    "synthetic_graph_only",
                    SCHEMA_V3,
                    "BINANCE_USDM",
                    symbol,
                    kind,
                    route,
                    sequence,
                    WALL + 8 * 86_400 * S + t * S,
                    t * S,
                    data,
                )
            )

        emit(0, "captureMeta", {"schemaVersion": 3, "clock": "receive_time", "source_kind": "synthetic_test"})
        for symbol in ("BTCUSDT", "ETHUSDT"):
            emit(0, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001", "venue": "BINANCE_USDM"}, symbol)
            for route in ("public", "market"):
                emit(0, "captureEvent", {"event": "connect", "route": route}, symbol, route)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            emit(
                1,
                "snapshot",
                {"lastUpdateId": 100, "bids": [["99.9", "0.010"]], "asks": [["100.1", "0.010"]]},
                symbol,
                "public",
            )
        for t in range(2, 131):
            for symbol in ("BTCUSDT", "ETHUSDT"):
                emit(
                    t,
                    "depthUpdate",
                    {
                        "U": 100 if t == 2 else t + 99,
                        "u": t + 99,
                        "pu": 99 if t == 2 else t + 98,
                        "b": [["99.9", "0.012" if t % 2 else "0.008"]],
                        "a": [["100.1", "0.012" if t % 2 else "0.008"]],
                    },
                    symbol,
                    "public",
                )
                emit(
                    t, "aggTrade", {"p": "99.9" if t % 2 else "100.1", "q": "0.020", "m": bool(t % 2)}, symbol, "market"
                )
        emit(131, "captureEvent", {"event": "capture_trailer", "route": "control"})
        writer.update_manifest_metadata({"writer": {"complete": True, "overflow_count": 0}})
    return writer.manifest_path


def hand_models(root, protocol):
    root.mkdir()
    cells = []
    for feature in protocol.snapshot()["experiment"]["features"]:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            spec = FeatureSpec.from_dict(feature)
            base = policy_settings().model
            provenance = dict(strict_json(base.provenance_json))
            provenance["purpose"] = "hand_specified_synthetic_graph_test;not_fitted_or_empirical"
            provenance["training"].update(
                symbol=symbol,
                feature_identity=spec.digest,
                instrument_sha256=instrument_grid_identity(
                    SymbolSpec(symbol, "0.1", "0.001", venue="BINANCE_USDM"), canonical_grid=True
                ),
            )
            scaler = TrainOnlyScaler.fit_training(
                [(-1.0,) * 12, (1.0,) * 12],
                feature_names=FEATURE_NAMES,
                feature_identity=spec.digest,
                clip_quantiles=None,
            )
            model = replace(base, features=spec, scaler=scaler, provenance_json=canonical_json(provenance))
            name = f"cadence_{spec.interval_ns}__{symbol}"
            candidates = []
            for k in (2, 3, 4, 5):
                folder = root / name / f"k_{k}"
                folder.mkdir(parents=True)
                if k == 2:
                    save_model(folder / "model.json", model)
                candidates.append(
                    {
                        "k": k,
                        "directory": name + f"/k_{k}",
                        "report_sha256": "0" * 64,
                        "report_file_sha256": "0" * 64,
                        "model_sha256": model.model_sha256 if k == 2 else None,
                        "model_file_sha256": file_sha256(folder / "model.json") if k == 2 else None,
                    }
                )
            cells.append(
                {
                    "symbol": symbol,
                    "interval_ns": spec.interval_ns,
                    "directory": name,
                    "training": {"synthetic_test_only": True},
                    "validation": {"synthetic_test_only": True},
                    "candidates": candidates,
                    "validation_selected_k": 2,
                }
            )
    manifest = {
        "schema_version": "lob_sim.registered_real_models.v1",
        "complete": True,
        "registry_sha256": protocol.digest,
        "prepared_sha256": "8" * 64,
        "code_identity": protocol.snapshot()["code_identity"],
        "cells": cells,
        "all_selected_models_available": True,
        "claim_ready": False,
        "scope": "hand-specified synthetic harness;not admitted models",
    }
    manifest["models_sha256"] = identity(manifest)
    publish_json(root / "manifest.json", manifest)
    return load_model_set(root, protocol, registry_sha256=protocol.digest)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    import lob_sim.research.study as producer
    import lob_sim.research.study_reader as reader

    root = tmp_path_factory.mktemp("synthetic_graph_only")
    source = synthetic_tape(root / "raw")
    payload = mechanical_payload()
    configuration = replace(cfg(), mm_requote_ms=1_000.0)
    payload["experiment"] = experiment_specification(configuration)
    payload["experiment_sha256"] = identity(payload["experiment"])
    payload["code_identity"] = checkpoint_code_identity()
    payload["registry_sha256"] = identity({k: v for k, v in payload.items() if k != "registry_sha256"})
    protocol = FrozenProtocol(canonical_json(payload))
    registry_path = root / "registry.json"
    publish_json(registry_path, protocol.snapshot())
    model_root = root / "models"
    models = hand_models(model_root, protocol)
    view_root = root / "views"
    contract = view_specification(
        file_sha256(source),
        "3" * 64,
        protocol.digest,
        "2025-01-09",
        CaptureClock(0, WALL + 8 * 86_400 * S),
        ReplayWindow("BTCUSDT", S, 31 * S, 131 * S),
    )
    directory = "unit-000000"
    entry = write_day_view(source, view_root / directory, contract, minimum_free_disk_bytes=0)
    unit = {
        "unit_id": identity(contract),
        "symbol": "BTCUSDT",
        "utc_day": "2025-01-09",
        "source_sha256": file_sha256(source),
        "contract": contract,
        "directory": directory,
        "view": entry,
    }
    views = {
        "schema_version": "lob_sim.registered_replay_views.v1",
        "complete": True,
        "registry_sha256": protocol.digest,
        "models_sha256": models["models_sha256"],
        "code_identity": protocol.snapshot()["code_identity"],
        "units": [unit],
        "claim_ready": False,
    }
    views["views_sha256"] = identity(views)
    publish_json(view_root / "manifest.json", views)
    publish_json(
        view_root / "test_access.json",
        {
            "schema_version": "lob_sim.real_market_test_access.v1",
            "registry_sha256": protocol.digest,
            "models_sha256": models["models_sha256"],
            "phase": "fixed_model_held_out_views",
            "test_days": protocol.snapshot()["split"]["test_days"],
        },
    )
    patch = pytest.MonkeyPatch()

    def reduced_grid(p, v):
        return [c for c in expected_cases(p, v) if c["interval_ns"] == S and c["latency_ms"] in (0, 50)]

    for module in (producer, reader):
        patch.setattr(module, "verify_prepared", lambda *a, **k: {"synthetic_test_only": True})
        patch.setattr(module, "verify_registered_models", lambda *a, **k: {"synthetic_test_only": True})
        patch.setattr(module, "admitted_parents", lambda *a, **k: {})
        patch.setattr(module, "verify_views", lambda *a, **k: {"synthetic_test_only": True})
        patch.setattr(module, "expected_cases", reduced_grid)
    study_root = root / "study"
    args = (
        study_root,
        (source,),
        root / "dummy_days",
        registry_path,
        root / "dummy_prepared",
        model_root,
        view_root,
        configuration,
    )
    try:
        report = producer.run_registered_study(
            args[1], args[2], args[3], args[4], args[5], args[6], args[0], args[7], registry_sha256=protocol.digest
        )
        yield args, protocol, report
    finally:
        patch.undo()


@pytest.mark.long_research
def test_reduced_grid_runs_real_matching_and_reopens_entire_serialized_graph(graph, monkeypatch):
    from lob_sim.research.study_reader import verify_registered_study
    from lob_sim.sim.engine import SimulationEngine

    args, protocol, report = graph

    def forbidden(*a, **k):
        raise AssertionError("verification must not rerun the matching engine")

    monkeypatch.setattr(SimulationEngine, "run", forbidden)
    proof = verify_registered_study(*args, registry_sha256=protocol.digest)
    assert proof["verified"] and proof["cases"] == 18 and proof["paired_cells"] == 6
    assert report["claim_ready"] is False
    assert any(c["case"]["fill_scenario"] == "conservative" for c in report["results"])
    # Non-vacuous: the downstream verifier must see actual fills and at least
    # one complete UTC-minute outcome, not only empty successful files.
    from lob_sim.research.study_tables import iter_tables

    summaries = [
        strict_json((args[0] / record["run_directory"] / "summary.json").read_text()) for record in report["results"]
    ]
    assert any(summary["fill_count"] > 0 for summary in summaries)
    assert any(
        row["risk"]["excluded_reason"] is None
        for record in report["results"]
        if record["tables"] is not None
        for row in iter_tables(args[0] / record["run_directory"] / record["tables"]["path"], record["tables"])
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["results"][0]["case"].update(latency_ms=25),
        lambda r: r["results"].pop(),
        lambda r: r["comparisons"][0].update(cell=["ETHUSDT", S, "conservative", 0]),
    ],
)
@pytest.mark.long_research
def test_rehashed_whole_graph_cannot_change_case_or_sensitivity_census(graph, mutation):
    from lob_sim.research.study_reader import verify_registered_study

    args, protocol, original = graph
    path = args[0] / "manifest.json"
    previous = path.read_bytes()
    changed = deepcopy(original)
    mutation(changed)
    changed["study_sha256"] = identity({k: v for k, v in changed.items() if k != "study_sha256"})
    path.write_text(canonical_json(changed), encoding="utf-8")
    try:
        with pytest.raises(ValueError):
            verify_registered_study(*args, registry_sha256=protocol.digest)
    finally:
        path.write_bytes(previous)


def test_unreduced_grid_contains_all_registered_cases_and_enforces_unit_cap():
    protocol = FrozenProtocol(canonical_json(mechanical_payload()))
    unit = {"unit_id": "3" * 64, "symbol": "BTCUSDT", "utc_day": "2025-01-09", "source_sha256": "4" * 64}
    cases = expected_cases(protocol, {"units": [unit]})
    assert len(cases) == 162 and len({case["case_id"] for case in cases}) == 162
    assert {case["latency_ms"] for case in cases} == {0, 1, 5, 10, 25, 50}
    assert {case["interval_ns"] for case in cases} == {S, 2 * S, 5 * S}
    with pytest.raises(ValueError, match="unit cap"):
        expected_cases(protocol, {"units": [unit] * 33})


@pytest.mark.parametrize("command", ["research-views", "research-study", "research-study-verify"])
def test_invalid_external_digest_precedes_parent_or_test_access(command, tmp_path):
    from lob_sim.research.study_views import prepare_study_views
    from lob_sim.research.study import run_registered_study
    from lob_sim.research.study_reader import verify_registered_study

    missing = tmp_path / "unreadable_parent"
    output = tmp_path / "must_not_exist"
    with pytest.raises(ValueError, match="digest"):
        if command == "research-views":
            prepare_study_views((missing,), missing, missing, missing, missing, output, cfg(), registry_sha256="bad")
        elif command == "research-study":
            run_registered_study(
                (missing,), missing, missing, missing, missing, missing, output, cfg(), registry_sha256="bad"
            )
        else:
            verify_registered_study(
                output, (missing,), missing, missing, missing, missing, missing, cfg(), registry_sha256="bad"
            )
    assert not output.exists()
