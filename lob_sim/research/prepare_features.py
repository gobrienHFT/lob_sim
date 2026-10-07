"""Prepare source-bound physical features only behind a frozen protocol.

Admission/feature
semantics stay separate, and the core engine is not modified by projection.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from lob_sim.config import Config
from lob_sim.regime.dataset import FeatureDatasetObserver, instrument_grid_identity, publish_json
from lob_sim.regime.features import FeatureSpec
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_eligibility import REQUIRED_SYMBOLS
from lob_sim.research.evidence_io import read_json, retain_failure
from lob_sim.research.feature_admission import FeatureAdmission
from lob_sim.research.feature_storage import PartitionWriter
from lob_sim.research.interval_reader import load_audit_report
from lob_sim.research.registered_protocol import Role, experiment_specification, load_protocol, verify_protocol_binding
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.observation import MarketObservation
from lob_sim.sim.run_manifest import config_snapshot


class ProjectedObserver:
    def __init__(self, observer: FeatureDatasetObserver, clock: CaptureClock):
        self.inner, self.clock = observer, clock
        self.depth_levels = observer.depth_levels

    def before_record(self, logical_ns: int) -> None:
        self.inner.before_record(logical_ns)

    def observe(self, observation: MarketObservation) -> None:
        self.inner.observe(replace(observation, wall_ns=self.clock.project(observation.logical_ns)))

    def finish(self, logical_ns: int) -> None:
        self.inner.finish(logical_ns)


def prepare_features(
    inputs: tuple[Path, ...],
    day_bundle: Path,
    registry_path: Path,
    directory: Path,
    cfg: Config,
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    protocol = load_protocol(registry_path, registry_sha256=registry_sha256)
    verify_protocol_binding(protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    registered = protocol.snapshot()
    if identity(experiment_specification(cfg)) != registered["experiment_sha256"]:
        raise ValueError("feature preparation configuration differs from frozen experiment")
    code = checkpoint_code_identity()
    if code != registered["code_identity"]:
        raise ValueError("registered processing source changed before feature preparation")
    root = directory.resolve()
    if any(p.resolve().is_relative_to(root) for p in (*inputs, registry_path)) or day_bundle.resolve().is_relative_to(
        root
    ):
        raise ValueError("prepared output cannot contain its immutable parents")
    root.mkdir(parents=True, exist_ok=False)
    writers: list[PartitionWriter] = []
    try:
        # Access is recorded before native preparation reads any held-out
        # prices/features. It is permission to prepare fixed features, never
        # permission for a calibration fit to read test files or outcomes.
        publish_json(
            root / "test_access.json",
            {
                "schema_version": "lob_sim.real_market_test_access.v1",
                "registry_sha256": protocol.digest,
                "phase": "fixed_feature_preparation",
                "test_days": registered["split"]["test_days"],
            },
        )
        admitted_days = set(registered["eligible_days"])
        roles: dict[str, Role] = {
            day: role for role in ("calibration", "validation", "test") for day in registered["split"][role + "_days"]
        }
        source_paths = {file_sha256(p): p for p in inputs}
        parents = read_json(day_bundle / "manifest.json")["parents"]
        audits = [
            (day_bundle / parent["directory"], load_audit_report(day_bundle / parent["directory"]))
            for parent in parents
        ]
        audits.sort(key=lambda p: (p[1]["clock"]["origin_wall_ns"], p[1]["input_sha256"]))
        base = registered["experiment"]["base_configuration"]
        extraction_cfg = replace(
            cfg,
            symbols=REQUIRED_SYMBOLS,
            mm_enabled=False,
            mm_strategy_profile="research_mm",
            sim_order_latency_ms=0.0,
            sim_cancel_latency_ms=0.0,
            sim_latency_mode="fixed",
            sim_latency_samples_ms=(),
            sim_adverse_markout_seconds=1.0,
            sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
        )
        if config_snapshot(extraction_cfg) != {**base, "mm_enabled": False}:
            raise ValueError("native extraction configuration differs from registered core")
        cadences = []
        for feature_contract in registered["experiment"]["features"]:
            spec = FeatureSpec.from_dict(feature_contract)
            name = "cadence_" + str(spec.interval_ns)
            writer = PartitionWriter(root / name)
            writers.append(writer)
            excluded_dates: Counter[str] = Counter()
            source_records = []
            for audit_root, audit in audits:
                if audit["research_usable"] is not True:
                    source_records.append({"input_sha256": audit["input_sha256"], "status": "not_research_usable"})
                    continue
                clock = CaptureClock.from_dict(audit["clock"])
                gates = {symbol: FeatureAdmission(audit_root, audit) for symbol in REQUIRED_SYMBOLS}
                sequence_state: dict[str, Any] = {}
                grids: dict[str, str] = {}

                def observe_grid(observation: MarketObservation) -> None:
                    grids[observation.symbol] = instrument_grid_identity(observation.spec, canonical_grid=True)

                def emit(native: Any) -> None:
                    row = dict(native)
                    if row["utc_day"] not in roles:
                        excluded_dates["not_registered_date"] += 1
                        return
                    symbol = row["symbol"]
                    row["instrument_sha256"] = grids[symbol]
                    if row["wall_ns"] != clock.project(row["sample_ns"]):
                        raise ValueError("prepared feature projection differs from frozen capture clock")
                    exclusions = gates[symbol].exclusions(row, spec.window_ns, admitted_days)
                    admitted = not exclusions
                    previous = sequence_state.get(symbol)
                    key = (row["utc_day"], tuple(row["epochs"]), row["sequence_id"])
                    if not admitted:
                        sequence_state.pop(symbol, None)
                        sequence_id = None
                    else:
                        if (
                            previous is None
                            or previous["key"] != key
                            or previous["sample_ns"] + spec.interval_ns != row["sample_ns"]
                        ):
                            sequence_id = identity(
                                {
                                    "input_sha256": audit["input_sha256"],
                                    "symbol": symbol,
                                    "utc_day": row["utc_day"],
                                    "features_sha256": spec.digest,
                                    "key": key,
                                    "first_sample_ns": row["sample_ns"],
                                }
                            )
                        else:
                            sequence_id = previous["sequence_id"]
                        sequence_state[symbol] = {"key": key, "sample_ns": row["sample_ns"], "sequence_id": sequence_id}
                    writer.write(
                        {
                            "schema_version": "lob_sim.admitted_feature_row.v1",
                            "native": row,
                            "registry_sha256": protocol.digest,
                            "source_report_sha256": audit["report_sha256"],
                            "clock_sha256": clock.digest,
                            "instrument_sha256": audit["instruments"][symbol]["sha256"],
                            "role": roles[row["utc_day"]],
                            "admitted": admitted,
                            "exclusions": exclusions,
                            "sequence_id": sequence_id,
                        }
                    )

                observer = FeatureDatasetObserver(
                    spec, audit["input_sha256"], emit, symbols=REQUIRED_SYMBOLS, on_observation=observe_grid
                )
                engine = SimulationEngine(
                    extraction_cfg,
                    market_observer=ProjectedObserver(observer, clock),
                    retain_event_trace=False,
                    retain_audit_rows=False,
                )
                engine.run(source_paths[audit["input_sha256"]])
                for gate in gates.values():
                    gate.finish()
                source_records.append(
                    {
                        "input_sha256": audit["input_sha256"],
                        "status": "prepared",
                        "sample_count": observer.sample_count,
                        "native_status_counts": dict(sorted(observer.status_counts.items())),
                    }
                )
            cadences.append(
                {
                    "directory": name,
                    "features": spec.as_dict(),
                    "day_files": writer.finalize(),
                    "source_records": source_records,
                    "excluded_unregistered_date_samples": dict(excluded_dates),
                }
            )
        verify_protocol_binding(protocol, day_bundle, inputs, registry_sha256=registry_sha256)
        if checkpoint_code_identity() != code:
            raise ValueError("processing source changed during feature preparation")
        manifest = {
            "schema_version": "lob_sim.prepared_real_features.v1",
            "complete": True,
            "registry_sha256": protocol.digest,
            "day_bundle_sha256": registered["day_bundle_sha256"],
            "code_identity": code,
            "configuration": config_snapshot(extraction_cfg),
            "cadences": cadences,
            "scope": "fixed causal feature preparation;physical day/role separation;no fitting or outcome selection",
            "claim_ready": False,
        }
        manifest["prepared_sha256"] = identity(manifest)
        publish_json(root / "manifest.json", manifest)
        from lob_sim.research.feature_reader import verify_prepared

        verify_prepared(root, protocol, day_bundle, inputs, registry_sha256=registry_sha256)
        return manifest
    except BaseException as exc:
        for writer in writers:
            writer.abandon(exc)
        retain_failure(root, exc, schema="lob_sim.real_feature_preparation_failure.v1")
        raise
