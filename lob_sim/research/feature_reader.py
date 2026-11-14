"""Separate artifact re-reader for physical real-market feature partitions.

This checks stored feature shape, causal admission/clock geometry and
partition/parent identity. It does not prove an exchange model or recompute
the native feature formulas from a second book implementation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from lob_sim.book.types import SymbolSpec
from lob_sim.regime.dataset import FeaturePartition, ROW_FIELDS, instrument_grid_identity, utc_day
from lob_sim.regime.features import FeatureSample, FeatureSpec
from lob_sim.regime.validation import identity, integer, require_keys, strict_json
from lob_sim.record.envelope import ValidityState
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_decision import DAY_NS
from lob_sim.research.evidence_io import read_json, require_finalized
from lob_sim.research.interval_reader import digest, iter_verified_intervals, load_audit_report
from lob_sim.research.registered_protocol import FrozenProtocol, Role, verify_protocol_binding

ROW_LIMIT = 32 * 1024
FIELDS = {
    "schema_version",
    "native",
    "registry_sha256",
    "source_report_sha256",
    "clock_sha256",
    "instrument_sha256",
    "role",
    "admitted",
    "exclusions",
    "sequence_id",
}


class _CoverageOracle:
    """Last invalid/epoch barrier rather than producer valid-since state."""

    def __init__(self, root: Path, report: dict[str, Any]):
        self.rows = iter_verified_intervals(root, report)
        self.current: dict[str, Any] | None = None
        self.barrier = report["first_logical_ns"]
        self.epoch_key: Any = None
        self.previous_target: int | None = None

    def admits(self, start: int, target: int) -> bool:
        if self.previous_target is not None and target < self.previous_target:
            raise ValueError("serialized features regress their admission cursor")
        self.previous_target = target
        while self.current is None or self.current["logical_end_ns"] <= target:
            item = next(self.rows, None)
            if item is None:
                self.current = None
                return False
            epochs = tuple(
                (s, tuple(v["epochs"]) if v["epochs"] is not None else None) for s, v in sorted(item["symbols"].items())
            )
            if self.epoch_key is not None and epochs != self.epoch_key:
                self.barrier = max(self.barrier, item["logical_start_ns"])
            if not item["joint_valid"]:
                self.barrier = max(self.barrier, item["logical_end_ns"])
            self.epoch_key, self.current = epochs, item
        return bool(
            self.current["logical_start_ns"] <= target < self.current["logical_end_ns"]
            and self.current["joint_valid"]
            and start >= self.barrier
        )

    def finish(self) -> None:
        for _ in self.rows:
            pass


def load_prepared(root: Path, protocol: FrozenProtocol, *, registry_sha256: str) -> dict[str, Any]:
    if digest(registry_sha256, "explicit frozen registry digest") != protocol.digest:
        raise ValueError("registry digest mismatch before prepared artifact access")
    require_finalized(root, "manifest.json")
    if any(root.rglob("*.partial")):
        raise ValueError("prepared directory retains incomplete day files")
    manifest = read_json(root / "manifest.json")
    require_keys(
        manifest,
        {
            "schema_version",
            "complete",
            "registry_sha256",
            "day_bundle_sha256",
            "code_identity",
            "configuration",
            "cadences",
            "scope",
            "claim_ready",
            "prepared_sha256",
        },
        "prepared real features",
    )
    if (
        manifest["schema_version"] != "lob_sim.prepared_real_features.v1"
        or manifest["complete"] is not True
        or manifest["claim_ready"] is not False
    ):
        raise ValueError("prepared artifact version/completion mismatch")
    if manifest["prepared_sha256"] != identity({k: v for k, v in manifest.items() if k != "prepared_sha256"}):
        raise ValueError("prepared artifact identity mismatch")
    registered = protocol.snapshot()
    if any(manifest[k] != registered[k] for k in ("registry_sha256", "day_bundle_sha256", "code_identity")):
        raise ValueError("prepared protocol/code parents differ")
    if manifest["configuration"] != {**registered["experiment"]["base_configuration"], "mm_enabled": False}:
        raise ValueError("prepared configuration differs from the frozen extraction contract")
    cadences = manifest["cadences"]
    if not isinstance(cadences, list) or len(cadences) != len(registered["experiment"]["features"]):
        raise ValueError("prepared cadence census mismatch")
    for entry, contract in zip(cadences, registered["experiment"]["features"], strict=True):
        require_keys(
            entry,
            {"directory", "features", "day_files", "source_records", "excluded_unregistered_date_samples"},
            "prepared cadence",
        )
        spec = FeatureSpec.from_dict(contract)
        if entry["features"] != contract or entry["directory"] != "cadence_" + str(spec.interval_ns):
            raise ValueError("prepared cadence identity mismatch")
        files = entry["day_files"]
        if not isinstance(files, list) or len(files) > 8192:
            raise ValueError("prepared day artifact cap exceeded")
        seen = set()
        for file in files:
            require_keys(
                file,
                {"role", "utc_day", "symbol", "path", "sha256", "rows", "admitted_rows", "exclusion_counts"},
                "prepared day file",
            )
            protocol.authorize(file["role"], file["utc_day"], registry_sha256=registry_sha256)
            if file["symbol"] not in registered["experiment"]["symbols"]:
                raise ValueError("prepared symbol is unregistered")
            key = (file["utc_day"], file["symbol"])
            if key in seen or file["path"] != f"{file['role']}/{file['utc_day']}__{file['symbol']}.jsonl":
                raise ValueError("duplicate or noncanonical prepared day file")
            seen.add(key)
            digest(file["sha256"], "prepared file checksum")
            if integer(file["admitted_rows"], "admitted rows") > integer(file["rows"], "day rows"):
                raise ValueError("prepared row conservation violated")
            if not isinstance(file["exclusion_counts"], dict):
                raise ValueError("prepared exclusion census must be an object")
            for reason, count in file["exclusion_counts"].items():
                if (
                    not isinstance(reason, str)
                    or len(reason) > 128
                    or not 0 < integer(count, "exclusion count") <= file["rows"]
                ):
                    raise ValueError("prepared exclusion census is unbounded/inconsistent")
        sources = entry["source_records"]
        if not isinstance(sources, list) or len(sources) != len(registered["sources"]):
            raise ValueError("prepared source census differs from registration")
        if len({s.get("input_sha256") for s in sources}) != len(sources) or {
            s.get("input_sha256") for s in sources
        } != {s["input_sha256"] for s in registered["sources"]}:
            raise ValueError("prepared source identities differ from registration")
        total_samples = 0
        source_contracts = {s["input_sha256"]: s for s in registered["sources"]}
        for source in sources:
            usable = source_contracts[source["input_sha256"]]["research_usable"]
            if not usable:
                require_keys(source, {"input_sha256", "status"}, "excluded prepared source")
                if source["status"] != "not_research_usable":
                    raise ValueError("ineligible source cannot contribute feature rows")
                continue
            require_keys(
                source, {"input_sha256", "status", "sample_count", "native_status_counts"}, "prepared source census"
            )
            if source["status"] != "prepared" or not isinstance(source["native_status_counts"], dict):
                raise ValueError("prepared source status/census mismatch")
            count = integer(source["sample_count"], "source samples")
            if sum(integer(n, "status samples", minimum=1) for n in source["native_status_counts"].values()) != count:
                raise ValueError("native feature source census differs")
            total_samples += count
        excluded = entry["excluded_unregistered_date_samples"]
        if not isinstance(excluded, dict) or set(excluded) - {"not_registered_date"}:
            raise ValueError("unsupported feature date exclusion")
        if total_samples != sum(f["rows"] for f in files) + sum(
            integer(n, "excluded date samples", minimum=1) for n in excluded.values()
        ):
            raise ValueError("prepared feature rows are missing from the source census")
    access = read_json(root / "test_access.json")
    if access != {
        "schema_version": "lob_sim.real_market_test_access.v1",
        "registry_sha256": protocol.digest,
        "phase": "fixed_feature_preparation",
        "test_days": registered["split"]["test_days"],
    }:
        raise ValueError("prepared test access receipt mismatch")
    return manifest


@dataclass
class _Sequence:
    source: str
    key: Any
    sample_ns: int
    sequence_id: str


def iter_admitted_day(
    root: Path,
    protocol: FrozenProtocol,
    cadence: dict[str, Any],
    file: dict[str, Any],
    parents: dict[str, tuple[Path, dict[str, Any]]],
    *,
    registry_sha256: str,
) -> Iterator[dict[str, Any]]:
    """Drain even excluded rows before trusting checksum/statistics.

    Model readers select one physical role. All-parent verification happens
    separately and is not a fitting routine's permission to inspect test rows.
    """
    role, day, symbol = file["role"], file["utc_day"], file["symbol"]
    protocol.authorize(role, day, registry_sha256=registry_sha256)
    spec = FeatureSpec.from_dict(cadence["features"])
    cadence_root = (root / cadence["directory"]).resolve()
    if not cadence_root.is_relative_to(root.resolve()):
        raise ValueError("prepared cadence escapes its bundle")
    checksum = sha256()
    count = admitted_count = 0
    reasons: Counter[str] = Counter()
    previous: _Sequence | None = None
    last_wall: int | None = None
    oracles: dict[str, _CoverageOracle] = {}
    registered = protocol.snapshot()
    eligible = set(registered["eligible_days"])
    registered_sources = {s["input_sha256"]: s for s in registered["sources"]}
    with protocol.open_feature_day(cadence_root, role, day, symbol, registry_sha256=registry_sha256) as handle:
        while raw := handle.readline(ROW_LIMIT + 1):
            if len(raw) > ROW_LIMIT or not raw.endswith(b"\n"):
                raise ValueError("prepared feature row too large or truncated")
            checksum.update(raw)
            row = dict(require_keys(strict_json(raw.decode("utf-8")), FIELDS, "admitted feature row"))
            native = require_keys(row["native"], ROW_FIELDS, "native feature row")
            if (
                row["schema_version"] != "lob_sim.admitted_feature_row.v1"
                or row["registry_sha256"] != protocol.digest
                or row["role"] != role
                or native["schema_version"] != "lob_sim.hmm_feature_row.v1"
                or native["symbol"] != symbol
                or native["utc_day"] != day
                or native["feature_identity"] != spec.digest
            ):
                raise ValueError("serialized prepared row identity mismatch")
            source = digest(native["input_sha256"], "prepared source identity")
            if source not in parents:
                raise ValueError("prepared row has an unbound source")
            audit_root, audit = parents[source]
            registered_source = registered_sources.get(source)
            if (
                registered_source is None
                or registered_source["research_usable"] is not True
                or any(registered_source[k] != audit[k] for k in ("report_sha256", "clock_sha256", "capture_id"))
            ):
                raise ValueError("prepared source admission differs from frozen registration")
            if (
                not audit["research_usable"]
                or row["source_report_sha256"] != audit["report_sha256"]
                or row["clock_sha256"] != audit["clock_sha256"]
            ):
                raise ValueError("prepared row source was not admitted")
            if row["instrument_sha256"] != audit["instruments"][symbol]["sha256"]:
                raise ValueError("prepared full instrument identity mismatch")
            units = audit["instruments"][symbol]["spec"]
            grid = SymbolSpec(
                symbol,
                Decimal(units["tick_size"]),
                Decimal(units["step_size"]),
                contract_multiplier=Decimal(units["contract_multiplier"]),
                venue=units["venue"],
            )
            if native["instrument_sha256"] != instrument_grid_identity(grid, canonical_grid=True):
                raise ValueError("prepared feature grid differs from admitted instrument")
            clock = CaptureClock.from_dict(audit["clock"])
            sample = integer(native["sample_ns"], "feature sample")
            available = integer(native["available_at_ns"], "feature availability", minimum=sample)
            wall = integer(native["wall_ns"], "feature wall time")
            if (
                wall != clock.origin_wall_ns + sample - clock.origin_logical_ns
                or utc_day(wall) != day
                or sample < audit["first_logical_ns"]
                or available > audit["last_logical_ns"]
                or native["clock_basis"] != "causal_receive_wall_projection"
            ):
                raise ValueError("prepared clock/source geometry mismatch")
            if last_wall is not None and wall <= last_wall:
                raise ValueError("prepared features duplicate or regress UTC time")
            last_wall = wall
            for field in ("receive_seq", "input_row"):
                if integer(native[field], field, minimum=1) > audit["integrity"]["records"]:
                    raise ValueError("feature record identity exceeds its source")
            feature = FeatureSample(
                symbol,
                sample,
                native["receive_seq"],
                tuple(native["epochs"]),
                spec.digest,
                native["status"],
                native["features"],
                native["reset_reason"],
            )
            expected = []
            if feature.status != "VALID":
                expected.append("native_feature_" + feature.status.lower())
            oracle = oracles.get(source)
            if oracle is None:
                oracle = _CoverageOracle(audit_root, audit)
                oracles[source] = oracle
            start = sample - spec.window_ns
            if not oracle.admits(start, max(sample, available - 1)):
                expected.append("trailing_window_or_availability_not_joint_valid")
            if day not in eligible:
                expected.append("not_eligible_full_utc_day")
            if start < clock.origin_logical_ns:
                expected.append("trailing_window_before_source")
            else:
                day_start = wall // DAY_NS * DAY_NS
                if (
                    clock.origin_wall_ns + start - clock.origin_logical_ns < day_start + clock.tolerance_ns
                    or clock.origin_wall_ns + available - clock.origin_logical_ns
                    >= day_start + DAY_NS - clock.tolerance_ns
                ):
                    expected.append("utc_day_or_partition_boundary_within_clock_tolerance")
            if type(row["admitted"]) is not bool or row["admitted"] != (not expected) or row["exclusions"] != expected:
                raise ValueError("serialized feature admission differs from independent interval oracle")
            key = (day, tuple(native["epochs"]), native["sequence_id"])
            if row["admitted"]:
                digest(native["sequence_id"], "native feature sequence")
                if (
                    previous is None
                    or previous.source != source
                    or previous.key != key
                    or previous.sample_ns + spec.interval_ns != sample
                ):
                    seq = identity(
                        {
                            "input_sha256": source,
                            "symbol": symbol,
                            "utc_day": day,
                            "features_sha256": spec.digest,
                            "key": key,
                            "first_sample_ns": sample,
                        }
                    )
                else:
                    seq = previous.sequence_id
                if row["sequence_id"] != seq:
                    raise ValueError("prepared sequence identity differs from independent reconstruction")
                validity = require_keys(native["validity"], set(ValidityState().as_dict()), "native validity")
                for flag in set(validity) - {"reason"}:
                    if type(validity[flag]) is not bool:
                        raise ValueError("native validity flags must be boolean")
                state = ValidityState(**{k: v for k, v in validity.items() if k != "execution_valid"})
                if validity["execution_valid"] != state.execution_valid or not all(
                    (state.book_valid, state.trade_stream_valid, state.clock_valid, state.capture_valid)
                ):
                    raise ValueError("admitted feature has invalid native market inputs")
                previous = _Sequence(source, key, sample, seq)
            else:
                if row["sequence_id"] is not None:
                    raise ValueError("excluded row has a fit sequence identity")
                previous = None
            count += 1
            admitted_count += int(row["admitted"])
            reasons.update(expected)
            yield row
    for oracle in oracles.values():
        oracle.finish()
    if (
        count != file["rows"]
        or admitted_count != file["admitted_rows"]
        or dict(reasons) != file["exclusion_counts"]
        or checksum.hexdigest() != file["sha256"]
    ):
        raise ValueError("prepared file checksum/admission census differs")


def read_fit_partition(
    root: Path,
    protocol: FrozenProtocol,
    parents: dict[str, tuple[Path, dict[str, Any]]],
    role: Role,
    symbol: str,
    interval_ns: int,
    *,
    registry_sha256: str,
) -> FeaturePartition:
    if role not in ("calibration", "validation"):
        raise ValueError("fitting accepts calibration/validation only;never opens test rows")
    # Even a wrong role is rejected before loading metadata or opening rows.
    if registry_sha256 != protocol.digest:
        raise ValueError("registered digest mismatch before fitting partition access")
    manifest = load_prepared(root, protocol, registry_sha256=registry_sha256)
    cadence = next((c for c in manifest["cadences"] if c["features"]["interval_ns"] == interval_ns), None)
    if cadence is None or symbol not in protocol.snapshot()["experiment"]["symbols"]:
        raise ValueError("requested fit symbol/cadence is unregistered")
    maximum = protocol.snapshot()["experiment"]["resource_limits"]["maximum_fit_rows_per_partition"]
    values: list[tuple[float, ...]] = []
    lengths = []
    previous_sequence = None
    units = None
    selected = sorted(
        (f for f in cadence["day_files"] if f["role"] == role and f["symbol"] == symbol), key=lambda f: f["utc_day"]
    )
    for file in selected:
        previous_sequence = None
        for row in iter_admitted_day(root, protocol, cadence, file, parents, registry_sha256=registry_sha256):
            if not row["admitted"]:
                previous_sequence = None
                continue
            if len(values) >= maximum:
                raise ValueError("frozen fit row cap exceeded;no retrospective subsampling")
            grid = row["native"]["instrument_sha256"]
            if units is not None and units != grid:
                raise ValueError("fit source grids are inconsistent")
            units = grid
            if row["sequence_id"] != previous_sequence:
                lengths.append(0)
            lengths[-1] += 1
            values.append(tuple(row["native"]["features"]))
            previous_sequence = row["sequence_id"]
    if not values or units is None:
        raise ValueError("registered fit partition has no admitted features")
    return FeaturePartition(
        role,
        tuple(protocol.snapshot()["split"][role + "_days"]),
        identity(protocol.snapshot()["split"]),
        manifest["prepared_sha256"],
        FeatureSpec.from_dict(cadence["features"]),
        symbol,
        units,
        tuple(values),
        tuple(lengths),
    )


def admitted_parents(
    day_bundle: Path, protocol: FrozenProtocol, inputs: tuple[Path, ...], *, registry_sha256: str
) -> dict[str, tuple[Path, dict[str, Any]]]:
    verify_protocol_binding(protocol, day_bundle, inputs, registry_sha256=registry_sha256)
    return {
        parent["input_sha256"]: (day_bundle / parent["directory"], load_audit_report(day_bundle / parent["directory"]))
        for parent in read_json(day_bundle / "manifest.json")["parents"]
    }


def verify_prepared(
    root: Path, protocol: FrozenProtocol, day_bundle: Path, inputs: tuple[Path, ...], *, registry_sha256: str
) -> dict[str, Any]:
    """Full evidence re-read, not a fitting API: may inspect registered test rows."""
    parents = admitted_parents(day_bundle, protocol, inputs, registry_sha256=registry_sha256)
    manifest = load_prepared(root, protocol, registry_sha256=registry_sha256)
    rows = admitted = 0
    for cadence in manifest["cadences"]:
        for file in cadence["day_files"]:
            for row in iter_admitted_day(root, protocol, cadence, file, parents, registry_sha256=registry_sha256):
                rows += 1
                admitted += int(row["admitted"])
    return {
        "schema_version": "lob_sim.prepared_real_features_verification.v1",
        "verified": True,
        "registry_sha256": protocol.digest,
        "prepared_sha256": manifest["prepared_sha256"],
        "rows": rows,
        "admitted_rows": admitted,
        "scope": "serialized native-feature shape and independent admission/clock/partition geometry;not a second feature/book implementation or provenance authentication",
    }
