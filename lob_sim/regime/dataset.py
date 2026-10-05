"""Causal raw-tape extraction and physically separated whole-UTC-day features.

Extraction uses SimulationEngine's authoritative synchronization and validity.
Fitting readers open only the requested day files, not the untouched test days.
Runtime windows and row export are bounded; offline fitting has an explicit cap.
"""

from __future__ import annotations

import os
from hashlib import sha256
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Literal

from ..config import Config
from ..replay.inspection import file_sha256
from ..research.protocol import ResearchRegistry, UTCDaySplit, chronological_day_split
from ..sim.checkpoint import checkpoint_code_identity
from ..sim.observation import MarketObservation
from ..sim.run_manifest import config_snapshot
from .features import FEATURE_NAMES, BookView, CausalFeatureSampler, FeatureSample, FeatureSpec, FeatureValidity
from .validation import canonical_json, identity, integer, require_keys, strict_json

DAY_NS = 86_400_000_000_000
MAX_ROW_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
PartitionRole = Literal["calibration", "validation", "test"]
ROW_FIELDS = {
    "schema_version",
    "symbol",
    "sample_ns",
    "available_at_ns",
    "receive_seq",
    "input_row",
    "wall_ns",
    "utc_day",
    "clock_basis",
    "epochs",
    "validity",
    "feature_identity",
    "instrument_sha256",
    "input_sha256",
    "status",
    "features",
    "reset_reason",
    "sequence_id",
}


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def utc_day(wall_ns: int) -> str:
    integer(wall_ns, "wall_ns")
    # Integer seconds avoid float rounding a final nanosecond into the next day.
    return datetime.fromtimestamp(wall_ns // 1_000_000_000, timezone.utc).date().isoformat()


def instrument_identity(observation: MarketObservation) -> str:
    spec = observation.spec
    return identity(
        {
            "symbol": spec.symbol,
            "tick_size": str(spec.tick_size),
            "step_size": str(spec.step_size),
            "contract_multiplier": str(spec.contract_multiplier),
            "venue": spec.venue,
        }
    )


@dataclass
class _Context:
    sampler: CausalFeatureSampler
    observation: MarketObservation
    instrument_sha256: str
    generation: int = 0
    previous_sequence_key: tuple[Any, ...] | None = None
    sequence_id: str | None = None
    last_valid_ns: int | None = None


class FeatureDatasetObserver:
    """A read-only observer with no second book reconstruction or trace retention."""

    def __init__(
        self,
        spec: FeatureSpec,
        input_sha256: str,
        emit: Callable[[Mapping[str, Any]], None],
        *,
        symbols: tuple[str, ...] = (),
        on_observation: Callable[[MarketObservation], None] | None = None,
    ) -> None:
        self.spec, self.input_sha256, self.emit = spec, input_sha256, emit
        self.symbols = frozenset(symbols)
        self.on_observation = on_observation
        self.depth_levels = spec.depth_levels
        self._contexts: dict[str, _Context] = {}
        self.sample_count = 0
        self.status_counts: Counter[str] = Counter()

    def _emit(self, context: _Context, sample: FeatureSample, available_at_ns: int) -> None:
        observation = context.observation
        # Close using the last *known* wall/monotonic anchor. A new receipt's
        # wall timestamp is never used to interpolate an earlier feature row.
        wall_ns = observation.wall_ns + sample.sample_ns - observation.logical_ns
        day = utc_day(wall_ns)
        key = (day, sample.epochs, context.instrument_sha256, context.generation)
        if sample.status != "VALID":
            context.previous_sequence_key = context.sequence_id = context.last_valid_ns = None
        elif key != context.previous_sequence_key or context.last_valid_ns != sample.sample_ns - self.spec.interval_ns:
            context.sequence_id = identity(
                {
                    "input_sha256": self.input_sha256,
                    "symbol": sample.symbol,
                    "key": key,
                    "first_sample_ns": sample.sample_ns,
                }
            )
            context.previous_sequence_key = key
        if sample.status == "VALID":
            context.last_valid_ns = sample.sample_ns
        self.sample_count += 1
        self.status_counts[sample.status] += 1
        self.emit(
            {
                "schema_version": "lob_sim.hmm_feature_row.v1",
                "symbol": sample.symbol,
                "sample_ns": sample.sample_ns,
                "available_at_ns": available_at_ns,
                "receive_seq": observation.receive_seq,
                "input_row": observation.input_row,
                "wall_ns": wall_ns,
                "utc_day": day,
                "clock_basis": "causal_receive_wall_projection" if observation.receive_clock else "legacy_diagnostic",
                "epochs": list(sample.epochs),
                "validity": observation.validity.as_dict(),
                "feature_identity": sample.feature_identity,
                "instrument_sha256": context.instrument_sha256,
                "input_sha256": self.input_sha256,
                "status": sample.status,
                "features": list(sample.values) if sample.values is not None else None,
                "reset_reason": sample.reset_reason,
                "sequence_id": context.sequence_id,
            }
        )

    def before_record(self, logical_ns: int) -> None:
        for symbol in sorted(self._contexts):
            context = self._contexts[symbol]
            for sample in context.sampler.advance_before(logical_ns):
                self._emit(context, sample, logical_ns)

    def observe(self, observation: MarketObservation) -> None:
        if self.symbols and observation.symbol not in self.symbols:
            return
        if self.on_observation is not None:
            self.on_observation(observation)
        instrument = instrument_identity(observation)
        context = self._contexts.get(observation.symbol)
        if context is None or context.instrument_sha256 != instrument:
            context = _Context(CausalFeatureSampler(observation.symbol, self.spec), observation, instrument)
            self._contexts[observation.symbol] = context
        previous = context.observation
        validity = FeatureValidity(
            observation.validity.book_valid,
            observation.validity.trade_stream_valid,
            observation.validity.clock_valid,
            observation.validity.capture_valid,
            observation.epochs,
        )
        if validity.status != "VALID" or observation.epochs != previous.epochs:
            context.generation += 1
        book = (
            BookView(observation.bids, observation.asks)
            if (observation.bids and observation.asks and observation.bids[0][0] < observation.asks[0][0])
            else None
        )
        # Input index advances even on metadata/control records. It is solely
        # an internal tie key; the actual receipt sequence is preserved above.
        context.sampler.observe(
            observation.logical_ns,
            observation.input_row,
            book=book,
            validity=validity,
            depth_event=observation.depth_observed,
            trade=observation.trade,
            changes=observation.changes,
        )
        context.observation = observation

    def finish(self, logical_ns: int) -> None:
        for symbol in sorted(self._contexts):
            context = self._contexts[symbol]
            for sample in context.sampler.finish(logical_ns):
                self._emit(context, sample, logical_ns)


def publish_json(path: Path, value: Mapping[str, Any]) -> None:
    """Exclusive, fsynced, no-clobber publication; failures preserve .partial."""
    if path.exists():
        raise FileExistsError(path)
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as handle:
        handle.write((canonical_json(dict(value)) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.link(partial, path)
    partial.unlink()


class _DayWriter:
    """One open row stream; only per-day counts/paths are retained, not rows."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.days: dict[str, Counter[str]] = {}
        self._day: str | None = None
        self._handle: BinaryIO | None = None

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None

    def write(self, row: Mapping[str, Any]) -> None:
        day = str(row["utc_day"])
        if day != self._day:
            self.close()
            path = self.directory / (day + ".jsonl.partial")
            self._handle = path.open("ab" if day in self.days else "xb")
            self._day = day
            self.days.setdefault(day, Counter())
        assert self._handle is not None
        encoded = (canonical_json(dict(row)) + "\n").encode("utf-8")
        if len(encoded) > MAX_ROW_BYTES:
            raise ValueError("feature row exceeds size limit")
        self._handle.write(encoded)
        self.days[day][str(row["status"])] += 1

    def finalize(self) -> list[dict[str, Any]]:
        self.close()
        files = []
        for day, counts in sorted(self.days.items()):
            final = self.directory / (day + ".jsonl")
            partial = final.with_name(final.name + ".partial")
            os.link(partial, final)
            partial.unlink()
            files.append(
                {
                    "utc_day": day,
                    "path": final.name,
                    "file_sha256": file_sha256(final),
                    "rows": sum(counts.values()),
                    "status_counts": dict(sorted(counts.items())),
                }
            )
        return files


def extract_features(
    input_path: str | Path,
    directory: str | Path,
    cfg: Config,
    *,
    spec: FeatureSpec = FeatureSpec(),
    symbols: tuple[str, ...] = (),
    on_observation: Callable[[MarketObservation], None] | None = None,
) -> dict[str, Any]:
    """Immutable daily feature bundle. A failed extraction has no final manifest.

    The emitter streams every valid/invalid sample. No trace/fill audit rows
    are retained by the engine. This is extraction, not a PnL experiment.
    """
    from ..sim.engine import SimulationEngine

    if cfg.hmm is not None:
        raise ValueError("feature extraction requires HMM disabled")
    source = Path(input_path)
    input_hash = file_sha256(source)
    writer = _DayWriter(Path(directory))
    observer = FeatureDatasetObserver(spec, input_hash, writer.write, symbols=symbols, on_observation=on_observation)
    engine = SimulationEngine(
        cfg,
        market_observer=observer,
        retain_event_trace=False,
        retain_audit_rows=False,
    )
    try:
        engine.run(source)
        if input_hash != file_sha256(source):
            raise ValueError("input changed during feature extraction")
        files = writer.finalize()
        payload = {
            "schema_version": "lob_sim.hmm_feature_dataset.v1",
            "features": spec.as_dict(),
            "input_sha256": input_hash,
            "code_identity": checkpoint_code_identity(),
            "config": config_snapshot(cfg),
            "symbols": list(symbols),
            "sample_count": observer.sample_count,
            "status_counts": dict(sorted(observer.status_counts.items())),
            "day_files": files,
            "claim_ready": False,
            "claim_reason": "feature rows alone do not certify ten complete joint-valid UTC days",
        }
        payload["dataset_sha256"] = identity(payload)
        publish_json(Path(directory) / "manifest.json", payload)
        return payload
    finally:
        writer.close()


@dataclass(frozen=True)
class FeaturePartition:
    """Finite offline fit input with explicit role, provenance and sequence lengths."""

    role: PartitionRole
    days: tuple[str, ...]
    split_sha256: str
    dataset_sha256: str
    feature_spec: FeatureSpec
    symbol: str
    instrument_sha256: str
    rows: tuple[tuple[float, ...], ...]
    lengths: tuple[int, ...]

    def __post_init__(self) -> None:
        from .validation import vector

        if self.role not in {"calibration", "validation", "test"}:
            raise ValueError("unknown partition role")
        if not isinstance(self.days, tuple) or not self.days or tuple(sorted(set(self.days))) != self.days:
            raise ValueError("partition days must be sorted, distinct and nonempty")
        if any(not _digest(value) for value in (self.split_sha256, self.dataset_sha256, self.instrument_sha256)):
            raise ValueError("partition provenance must contain SHA-256 identities")
        if (
            not isinstance(self.feature_spec, FeatureSpec)
            or not isinstance(self.symbol, str)
            or not self.symbol.strip()
        ):
            raise ValueError("partition needs a feature spec and symbol")
        for day in self.days:
            datetime.strptime(day, "%Y-%m-%d")
        if not self.rows or not self.lengths or sum(self.lengths) != len(self.rows):
            raise ValueError("partition sequence lengths must cover nonempty rows")
        for length in self.lengths:
            integer(length, "sequence length", minimum=1)
        object.__setattr__(self, "rows", tuple(vector(row, "feature row", len(FEATURE_NAMES)) for row in self.rows))
        object.__setattr__(self, "lengths", tuple(self.lengths))

    def provenance(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "days": list(self.days),
            "split_sha256": self.split_sha256,
            "dataset_sha256": self.dataset_sha256,
            "feature_identity": self.feature_spec.digest,
            "symbol": self.symbol,
            "instrument_sha256": self.instrument_sha256,
            "rows": len(self.rows),
            "lengths": list(self.lengths),
            "rows_sha256": identity(self.rows),
        }


def load_dataset_manifest(directory: str | Path) -> dict[str, Any]:
    path = Path(directory) / "manifest.json"
    with path.open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("feature manifest exceeds size limit")
    value = strict_json(raw.decode("utf-8"))
    expected = {
        "schema_version",
        "features",
        "input_sha256",
        "code_identity",
        "config",
        "symbols",
        "sample_count",
        "status_counts",
        "day_files",
        "claim_ready",
        "claim_reason",
        "dataset_sha256",
    }
    data = dict(require_keys(value, expected, "feature dataset manifest"))
    unsigned = {key: item for key, item in data.items() if key != "dataset_sha256"}
    if data["schema_version"] != "lob_sim.hmm_feature_dataset.v1" or data["dataset_sha256"] != identity(unsigned):
        raise ValueError("feature dataset manifest schema/hash mismatch")
    FeatureSpec.from_dict(data["features"])
    if not _digest(data["input_sha256"]):
        raise ValueError("invalid input SHA-256")
    if not isinstance(data["day_files"], list):
        raise ValueError("day_files must be an array")
    days = []
    for item in data["day_files"]:
        entry = require_keys(item, {"utc_day", "path", "file_sha256", "rows", "status_counts"}, "day file")
        day = entry["utc_day"]
        if not isinstance(day, str) or datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day:
            raise ValueError("invalid UTC day")
        if entry["path"] != day + ".jsonl":
            raise ValueError("unsafe or noncanonical day file path")
        integer(entry["rows"], "day row count", minimum=1)
        if not _digest(entry["file_sha256"]) or not isinstance(entry["status_counts"], dict):
            raise ValueError("invalid day checksum/counts")
        for count in entry["status_counts"].values():
            integer(count, "status count", minimum=1)
        if sum(entry["status_counts"].values()) != entry["rows"]:
            raise ValueError("day status counts disagree with rows")
        days.append(day)
    if days != sorted(set(days)):
        raise ValueError("day files must be distinct and sorted")
    if sum(entry["rows"] for entry in data["day_files"]) != integer(data["sample_count"], "sample_count"):
        raise ValueError("dataset sample count mismatch")
    return data


def dataset_split(directory: str | Path) -> UTCDaySplit:
    manifest = load_dataset_manifest(directory)
    # The standard protocol's claim_ready flag refers to a certified universe.
    # Extraction only identifies days containing valid rows, not full valid days.
    days = [item["utc_day"] for item in manifest["day_files"] if item["status_counts"].get("VALID", 0)]
    split = chronological_day_split(days)
    return replace(split, claim_ready=False, reason=manifest["claim_reason"])


def _rows(path: Path, expected_sha256: str) -> Iterator[dict[str, Any]]:
    digest = sha256()
    with path.open("rb") as handle:
        while raw := handle.readline(MAX_ROW_BYTES + 1):
            digest.update(raw)
            if len(raw) > MAX_ROW_BYTES or not raw.endswith(b"\n"):
                raise ValueError("oversized or incomplete feature row")
            value = strict_json(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("feature row must be an object")
            yield value
    # Hash the bytes actually consumed, not just a separate pre-read snapshot.
    # No FeaturePartition is returned if a writer changed data in between.
    if digest.hexdigest() != expected_sha256:
        raise ValueError("feature day checksum mismatch during read")


def read_partition(
    directory: str | Path,
    split: UTCDaySplit,
    role: PartitionRole,
    *,
    symbol: str,
    registry: ResearchRegistry | None = None,
    max_rows: int = 1_000_000,
) -> FeaturePartition:
    """Never open other partitions' files; verify all bytes of selected days.

    Test access requires an already frozen study registry. This is a workflow
    guard, not a security boundary against a researcher opening files directly.
    """
    integer(max_rows, "max_rows", minimum=1)
    if role not in {"calibration", "validation", "test"}:
        raise ValueError("unknown partition role")
    if role == "test" and (registry is None or not registry.frozen):
        raise ValueError("freeze ResearchRegistry before opening test features")
    manifest = load_dataset_manifest(directory)
    spec = FeatureSpec.from_dict(manifest["features"])
    expected_split = dataset_split(directory)
    if split != expected_split:
        raise ValueError("split does not match the dataset's chronological UTC-day universe")
    selected_days = getattr(split, role + "_days")
    rows, lengths, instrument = _read_selected_feature_days(directory, manifest, selected_days, symbol, max_rows)
    return FeaturePartition(
        role,
        selected_days,
        split.digest,
        manifest["dataset_sha256"],
        spec,
        symbol,
        instrument,
        rows,
        lengths,
    )


def _read_selected_feature_days(
    directory: str | Path, manifest: Mapping[str, Any], selected_days: tuple[str, ...], symbol: str, max_rows: int
) -> tuple[tuple[tuple[float, ...], ...], tuple[int, ...], str]:
    """One decoder for ordinary and independent-source collection partitions.

    Callers enforce the split/registry contract before selected day files open.
    Native input hashes, clocks, day/epoch boundaries and integrity checks remain
    identical; this function never concatenates independent source clocks.
    """
    integer(max_rows, "max_rows", minimum=1)
    spec = FeatureSpec.from_dict(manifest["features"])
    rows: list[tuple[float, ...]] = []
    lengths: list[int] = []
    instrument: str | None = None
    previous_sequence: str | None = None
    previous_sample: int | None = None
    previous_epochs: tuple[int, int, int] | None = None
    for entry in manifest["day_files"]:
        if entry["utc_day"] not in selected_days:
            continue
        path = Path(directory) / entry["path"]
        try:
            path.resolve().relative_to(Path(directory).resolve())
        except ValueError as exc:
            raise ValueError("day file escapes feature dataset directory") from exc
        if file_sha256(path) != entry["file_sha256"]:
            raise ValueError("feature day checksum mismatch: " + entry["utc_day"])
        count = 0
        status_counts: Counter[str] = Counter()
        previous_sequence = previous_sample = None
        previous_epochs = None
        last_seen_sample: int | None = None
        for row in _rows(path, entry["file_sha256"]):
            require_keys(row, ROW_FIELDS, "feature row")
            count += 1
            status_counts[str(row.get("status"))] += 1
            if (
                row.get("schema_version") != "lob_sim.hmm_feature_row.v1"
                or row.get("feature_identity") != spec.digest
                or row.get("input_sha256") != manifest["input_sha256"]
                or row.get("utc_day") != entry["utc_day"]
                or utc_day(integer(row.get("wall_ns"), "wall_ns")) != entry["utc_day"]
            ):
                raise ValueError("feature row identity/day mismatch")
            if row.get("symbol") != symbol:
                continue
            integer(row.get("sample_ns"), "sample_ns")
            integer(row.get("available_at_ns"), "available_at_ns")
            if last_seen_sample is not None and row["sample_ns"] <= last_seen_sample:
                raise ValueError("regressing or duplicate feature sample")
            last_seen_sample = row["sample_ns"]
            if row["available_at_ns"] < row["sample_ns"]:
                raise ValueError("feature available before its causal sample time")
            sample = FeatureSample(
                symbol,
                row["sample_ns"],
                row["receive_seq"],
                tuple(row["epochs"]),
                row["feature_identity"],
                row["status"],
                row.get("features"),
                row.get("reset_reason"),
            )
            if sample.status != "VALID":
                previous_sequence = previous_sample = None
                previous_epochs = None
                continue
            if len(rows) >= max_rows:
                raise ValueError("offline fit row cap exceeded; select a smaller registered dataset")
            if not _digest(row.get("sequence_id")) or not _digest(row.get("instrument_sha256")):
                raise ValueError("valid feature row needs a sequence identity")
            if not isinstance(row["validity"], dict) or row["validity"].get("execution_valid") is not True:
                raise ValueError("valid feature row has invalid market inputs")
            if instrument is not None and instrument != row.get("instrument_sha256"):
                raise ValueError("cannot fit different instrument grids together")
            instrument = row["instrument_sha256"]
            if (
                row["sequence_id"] != previous_sequence
                or previous_sample != sample.sample_ns - spec.interval_ns
                or sample.epochs != previous_epochs
            ):
                lengths.append(0)
            assert sample.values is not None
            rows.append(sample.values)
            lengths[-1] += 1
            previous_sequence, previous_sample = row["sequence_id"], sample.sample_ns
            previous_epochs = sample.epochs
        if count != entry["rows"] or dict(status_counts) != entry["status_counts"]:
            raise ValueError("feature day counts mismatch")
    if not rows or instrument is None:
        raise ValueError("requested symbol/partition has no valid feature rows")
    return tuple(rows), tuple(lengths), instrument
