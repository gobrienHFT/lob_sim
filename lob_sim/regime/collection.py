"""Independent raw-tape feature collections with physical UTC-day isolation.

Each input replays through the same authoritative engine in its native clock.
No raw-message rewrite, month-long concatenation, invented receipt identity or
transition between unrelated capture runs is needed for a chronological study.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import Config
from ..replay.inspection import file_sha256
from ..research.protocol import ResearchRegistry, UTCDaySplit, chronological_day_split
from ..sim.checkpoint import checkpoint_code_identity
from ..sim.observation import MarketObservation
from ..sim.run_manifest import config_snapshot
from .dataset import (
    MAX_MANIFEST_BYTES,
    PartitionRole,
    FeaturePartition,
    _read_selected_feature_days,
    extract_features,
    load_dataset_manifest,
    publish_json,
    utc_day,
)
from .features import FeatureSpec
from .validation import identity, integer, require_keys, strict_json

MAX_SOURCES = 256
SCHEMA = "lob_sim.hmm_feature_collection.v1"
BOUNDARY_RULE = "native source clocks;independent engine per tape;no cross-source or UTC-day transitions"
CLAIM_REASON = "observed UTC snippets and valid feature rows do not certify ten complete joint-valid days"


def _bounds(entries: list[dict[str, Any]]) -> None:
    """Independent sources cannot count overlapping observed wall spans twice."""
    by_day: dict[str, list[tuple[int, int, str]]] = {}
    for entry in entries:
        for span in entry["wall_spans"]:
            by_day.setdefault(span["utc_day"], []).append((span["first_wall_ns"], span["last_wall_ns"], entry["path"]))
    for spans in by_day.values():
        spans.sort()
        if any(left[1] >= right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError("independent tape wall spans overlap; choose a nonoverlapping registered universe")


def extract_feature_collection(
    inputs: tuple[Path, ...], directory: str | Path, cfg: Config, *, symbol: str, spec: FeatureSpec = FeatureSpec()
) -> dict[str, Any]:
    """Stream each input separately; failed collections have no final manifest.

    Quoting is disabled: extraction cannot depend on strategy fills. Source spans
    record actual immutable engine observations, not projected feature samples.
    They certify neither liveness between receipts nor complete joint-valid days.
    """
    if cfg.hmm is not None:
        raise ValueError("feature collection requires HMM disabled")
    if not isinstance(inputs, tuple) or not inputs or len(inputs) > MAX_SOURCES:
        raise ValueError("inputs must be a nonempty tuple of at most 256 independent tapes")
    if not isinstance(symbol, str) or not symbol or symbol != symbol.upper():
        raise ValueError("collection requires one explicit uppercase symbol")
    sources = tuple(Path(path).resolve() for path in inputs)
    hashes = tuple(file_sha256(path) for path in sources)
    if len(set(sources)) != len(sources) or len(set(hashes)) != len(hashes):
        raise ValueError("duplicate source path/content would duplicate observations")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    (root / "sources").mkdir()
    extraction_cfg = replace(cfg, mm_enabled=False)
    entries = []
    for index, (source, expected_hash) in enumerate(zip(sources, hashes)):
        spans: dict[str, dict[str, Any]] = {}

        def observe(observation: MarketObservation) -> None:
            day = utc_day(observation.wall_ns)
            if day not in spans:
                spans[day] = {
                    "utc_day": day,
                    "first_wall_ns": observation.wall_ns,
                    "last_wall_ns": observation.wall_ns,
                    "observations": 0,
                    "first_logical_ns": observation.logical_ns,
                    "last_logical_ns": observation.logical_ns,
                    "wall_offset_ns": observation.wall_ns - observation.logical_ns,
                    "clock_basis": "receive_nanoseconds"
                    if observation.receive_clock
                    else "legacy_compatibility_nanoseconds",
                }
            cell = spans[day]
            cell["first_wall_ns"] = min(cell["first_wall_ns"], observation.wall_ns)
            cell["last_wall_ns"] = max(cell["last_wall_ns"], observation.wall_ns)
            cell["observations"] += 1
            cell["first_logical_ns"] = min(cell["first_logical_ns"], observation.logical_ns)
            cell["last_logical_ns"] = max(cell["last_logical_ns"], observation.logical_ns)
            if cell["wall_offset_ns"] != observation.wall_ns - observation.logical_ns:
                cell["wall_offset_ns"] = None  # Do not pretend a changing wall anchor is a fixed clock.
            basis = "receive_nanoseconds" if observation.receive_clock else "legacy_compatibility_nanoseconds"
            if cell["clock_basis"] != basis:
                cell["clock_basis"] = None

        path = f"sources/{index:06d}"
        try:
            child = extract_features(
                source, root / path, extraction_cfg, spec=spec, symbols=(symbol,), on_observation=observe
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise ValueError(f"collection source {index} ({expected_hash}) extraction failed: {exc}") from exc
        if child["input_sha256"] != expected_hash or file_sha256(source) != expected_hash:
            raise ValueError("raw source changed during collection extraction")
        entries.append(
            {
                "path": path,
                "input_sha256": expected_hash,
                "dataset_sha256": child["dataset_sha256"],
                "manifest_file_sha256": file_sha256(root / path / "manifest.json"),
                "day_files": child["day_files"],
                "wall_spans": [spans[day] for day in sorted(spans)],
                "sample_count": child["sample_count"],
            }
        )
    _bounds(entries)
    for entry in entries:
        _validate_spans(entry)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "features": spec.as_dict(),
        "symbol": symbol,
        "config": config_snapshot(extraction_cfg),
        "code_identity": checkpoint_code_identity(),
        "sources": entries,
        "sample_count": sum(e["sample_count"] for e in entries),
        "claim_ready": False,
        "claim_reason": CLAIM_REASON,
        "boundary_rule": BOUNDARY_RULE,
    }
    payload["collection_sha256"] = identity(payload)
    publish_json(root / "manifest.json", payload)
    return payload


def _validate_spans(entry: Any) -> None:
    """A valid feature day needs actual observations, not a projected wall jump."""
    if not isinstance(entry["wall_spans"], list):
        raise ValueError("wall spans must be an array")
    days = []
    for value in entry["wall_spans"]:
        span = require_keys(
            value,
            {
                "utc_day",
                "first_wall_ns",
                "last_wall_ns",
                "observations",
                "first_logical_ns",
                "last_logical_ns",
                "wall_offset_ns",
                "clock_basis",
            },
            "source wall span",
        )
        first = integer(span["first_wall_ns"], "first wall ns")
        last = integer(span["last_wall_ns"], "last wall ns", minimum=first)
        integer(span["observations"], "wall observation count", minimum=1)
        logical_first = integer(span["first_logical_ns"], "first logical ns")
        logical_last = integer(span["last_logical_ns"], "last logical ns", minimum=logical_first)
        offset = span["wall_offset_ns"]
        if offset is not None and (
            type(offset) is not int or first - logical_first != offset or last - logical_last != offset
        ):
            raise ValueError("inconsistent source wall/logical clock anchor")
        if span["clock_basis"] not in {None, "receive_nanoseconds", "legacy_compatibility_nanoseconds"}:
            raise ValueError("unsupported source clock basis")
        if utc_day(first) != span["utc_day"] or utc_day(last) != span["utc_day"]:
            raise ValueError("source wall span UTC mismatch")
        days.append(span["utc_day"])
    if days != sorted(set(days)):
        raise ValueError("duplicate/regressing wall-span days")
    if any(day["utc_day"] not in days and day["status_counts"].get("VALID", 0) for day in entry["day_files"]):
        raise ValueError("valid feature day has no actual source observations")


def load_collection_manifest(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    with (root / "manifest.json").open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("collection manifest exceeds size limit")
    data = dict(
        require_keys(
            strict_json(raw.decode("utf-8")),
            {
                "schema_version",
                "features",
                "symbol",
                "config",
                "code_identity",
                "sources",
                "sample_count",
                "claim_ready",
                "claim_reason",
                "boundary_rule",
                "collection_sha256",
            },
            "feature collection manifest",
        )
    )
    if data["schema_version"] != SCHEMA or data["collection_sha256"] != identity(
        {k: v for k, v in data.items() if k != "collection_sha256"}
    ):
        raise ValueError("collection schema/hash mismatch")
    FeatureSpec.from_dict(data["features"])
    code = require_keys(
        data["code_identity"], {"schema_version", "algorithm", "file_count", "sha256"}, "collection code identity"
    )
    integer(code["file_count"], "source file count", minimum=1)
    if (
        code["schema_version"] != "lob_sim.checkpoint_code_identity.v1"
        or code["algorithm"] != "sha256"
        or not isinstance(code["sha256"], str)
        or len(code["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in code["sha256"])
    ):
        raise ValueError("invalid collection source-code identity")
    if (
        not isinstance(data["symbol"], str)
        or not data["symbol"]
        or data["symbol"] != data["symbol"].upper()
        or data["boundary_rule"] != BOUNDARY_RULE
        or data["claim_reason"] != CLAIM_REASON
        or not isinstance(data["config"], dict)
        or data["config"].get("mm_enabled") is not False
        or data["config"].get("hmm") is not None
    ):
        raise ValueError("invalid collection symbol/config/boundary/claim contract")
    integer(data["sample_count"], "collection sample_count")
    if not isinstance(data["sources"], list) or not 1 <= len(data["sources"]) <= MAX_SOURCES:
        raise ValueError("invalid source census")
    hashes = set()
    for index, value in enumerate(data["sources"]):
        entry = require_keys(
            value,
            {
                "path",
                "input_sha256",
                "dataset_sha256",
                "manifest_file_sha256",
                "day_files",
                "wall_spans",
                "sample_count",
            },
            "collection source",
        )
        if entry["path"] != f"sources/{index:06d}":
            raise ValueError("noncanonical or unsafe source path")
        path = root / entry["path"]
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("source escapes collection directory") from exc
        if file_sha256(path / "manifest.json") != entry["manifest_file_sha256"]:
            raise ValueError("source manifest byte checksum mismatch")
        child = load_dataset_manifest(path)
        if (
            any(child[key] != entry[key] for key in ("input_sha256", "dataset_sha256", "day_files", "sample_count"))
            or child["features"] != data["features"]
            or child["symbols"] != [data["symbol"]]
            or child["config"] != data["config"]
            or child["code_identity"] != data["code_identity"]
        ):
            raise ValueError("source manifest identity/config/features mismatch")
        if entry["input_sha256"] in hashes:
            raise ValueError("duplicate source content identity")
        hashes.add(entry["input_sha256"])
        _validate_spans(entry)
    _bounds(data["sources"])
    if data["sample_count"] != sum(e["sample_count"] for e in data["sources"]) or data["claim_ready"] is not False:
        raise ValueError("collection census or unsupported claim readiness")
    return data


def collection_split(directory: str | Path) -> UTCDaySplit:
    manifest = load_collection_manifest(directory)
    days = [
        day["utc_day"]
        for source in manifest["sources"]
        for day in source["day_files"]
        if day["status_counts"].get("VALID", 0)
    ]
    return replace(chronological_day_split(days), claim_ready=False, reason=manifest["claim_reason"])


def read_collection_partition(
    directory: str | Path,
    split: UTCDaySplit,
    role: PartitionRole,
    *,
    symbol: str,
    registry: ResearchRegistry | None = None,
    max_rows: int = 1_000_000,
) -> FeaturePartition:
    """Open selected physical day files only, never another partition's rows."""
    integer(max_rows, "max_rows", minimum=1)
    if role not in ("calibration", "validation", "test"):
        raise ValueError("unknown partition role")
    if role == "test" and (registry is None or not registry.frozen):
        raise ValueError("freeze ResearchRegistry before opening test features")
    root = Path(directory)
    manifest = load_collection_manifest(root)
    if symbol != manifest["symbol"] or split != collection_split(root):
        raise ValueError("collection split/symbol mismatch")
    rows: list[tuple[float, ...]] = []
    lengths: list[int] = []
    instrument = None
    days = getattr(split, role + "_days")
    for day in days:
        selected = [
            source
            for source in manifest["sources"]
            if any(item["utc_day"] == day and item["status_counts"].get("VALID", 0) for item in source["day_files"])
        ]
        selected.sort(
            key=lambda source: next(span["first_wall_ns"] for span in source["wall_spans"] if span["utc_day"] == day)
        )
        for source in selected:
            if len(rows) >= max_rows:
                raise ValueError("offline fit row cap exceeded; select a smaller registered collection")
            child = load_dataset_manifest(root / source["path"])
            values, spans, units = _read_selected_feature_days(
                root / source["path"], child, (day,), symbol, max_rows - len(rows)
            )
            if instrument is not None and units != instrument:
                raise ValueError("collection cannot fit different instrument grids")
            instrument = units
            rows.extend(values)
            lengths.extend(spans)  # Always a fresh sequence across raw source boundaries.
    if instrument is None:
        raise ValueError("collection partition has no valid feature rows")
    return FeaturePartition(
        role,
        days,
        split.digest,
        manifest["collection_sha256"],
        FeatureSpec.from_dict(manifest["features"]),
        symbol,
        instrument,
        tuple(rows),
        tuple(lengths),
    )
