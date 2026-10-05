from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.regime.collection import (
    collection_split,
    extract_feature_collection,
    load_collection_manifest,
    read_collection_partition,
)
from lob_sim.regime.dataset import extract_features, load_dataset_manifest, read_partition, dataset_split
from lob_sim.regime.features import FeatureSpec
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.protocol import ResearchRegistry

SECOND = 1_000_000_000
SPEC = FeatureSpec(window_steps=2, depth_levels=2)


def source(path, day, *, offset=0, quantity="0.01", tick="0.1"):
    """Independent receive clock begins at zero in every input."""
    wall = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()) * SECOND + offset * SECOND
    rows = []

    def emit(time, kind, data, route="public"):
        payload = dict(data)
        payload["_capture"] = {
            "recvSeq": len(rows),
            "recvMonotonicNs": time * SECOND,
            "recvWallNs": wall + time * SECOND,
            "route": route,
            "streamEpoch": 0,
            "syncEpoch": 0,
        }
        rows.append({"ts_local": (wall + time * SECOND) / SECOND, "symbol": "BTCUSDT", "type": kind, "data": payload})

    emit(0, "exchangeInfo", {"tickSize": tick, "stepSize": "0.001"}, "control")
    emit(0, "captureEvent", {"event": "connect", "route": "public"})
    emit(0, "captureEvent", {"event": "connect", "route": "market"}, "market")
    emit(1, "snapshot", {"lastUpdateId": 100, "bids": [["99.0", quantity]], "asks": [["101.0", quantity]]})
    for time in range(2, 15):
        emit(
            time,
            "depthUpdate",
            {"U": 100 if time == 2 else 99 + time, "u": 99 + time, "pu": 98 + time, "b": [["99.0", quantity]], "a": []},
        )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def bundle(tmp_path, *, extra=False):
    paths = [source(tmp_path / f"{i}.ndjson", f"2025-01-0{i + 1}") for i in range(3)]
    if extra:
        paths.append(source(tmp_path / "extra.ndjson", "2025-01-01", offset=60))
    cfg = load_config(".env.example", inherit_environment=False)
    directory = tmp_path / "collection"
    manifest = extract_feature_collection(tuple(paths), directory, cfg, symbol="BTCUSDT", spec=SPEC)
    return directory, manifest, cfg


def rehash(directory, mutate):
    path = directory / "manifest.json"
    data = json.loads(path.read_text())
    mutate(data)
    data["collection_sha256"] = identity({k: v for k, v in data.items() if k != "collection_sha256"})
    path.write_text(json.dumps(data))


def test_native_clocks_separate_sources_and_days_without_fake_coverage(tmp_path):
    directory, manifest, cfg = bundle(tmp_path, extra=True)
    assert load_collection_manifest(directory) == manifest
    assert manifest["config"]["mm_enabled"] is False
    assert cfg.mm_enabled
    split = collection_split(directory)
    assert split.calibration_days == ("2025-01-01",)
    assert split.validation_days == ("2025-01-02",)
    assert split.test_days == ("2025-01-03",)
    assert not split.claim_ready
    partition = read_collection_partition(directory, split, "calibration", symbol="BTCUSDT")
    assert partition.lengths == (11, 11)
    assert len(partition.rows) == 22
    assert partition.dataset_sha256 == manifest["collection_sha256"]
    # Two independently replayed tapes with identical native timestamps still
    # contribute TWO sequences. Their observed spans are just fourteen seconds.
    assert all(
        span["last_wall_ns"] - span["first_wall_ns"] == 14 * SECOND
        for entry in manifest["sources"]
        for span in entry["wall_spans"]
    )


def test_collection_values_match_existing_single_source_oracle(tmp_path):
    directory, _, cfg = bundle(tmp_path)
    child = directory / "sources/000000"
    child_manifest = load_dataset_manifest(child)
    values = json.loads((child / "2025-01-01.jsonl").read_text().splitlines()[3])["features"]
    partition = read_collection_partition(directory, collection_split(directory), "calibration", symbol="BTCUSDT")
    assert partition.rows[0] == tuple(values)
    independent = tmp_path / "single"
    old_manifest = extract_features(
        tmp_path / "0.ndjson", independent, replace(cfg, mm_enabled=False), spec=SPEC, symbols=("BTCUSDT",)
    )
    assert child_manifest == old_manifest


def test_no_test_rows_open_before_freeze_or_during_training(tmp_path, monkeypatch):
    directory, _, _ = bundle(tmp_path)
    split = collection_split(directory)
    forbidden = directory / "sources/000002/2025-01-03.jsonl"
    original = Path.open
    opened = []

    def tracked(path, *args, **kwargs):
        opened.append(path)
        if path == forbidden:
            raise AssertionError("untouched test rows opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    read_collection_partition(directory, split, "calibration", symbol="BTCUSDT")
    read_collection_partition(directory, split, "validation", symbol="BTCUSDT")
    with pytest.raises(ValueError, match="freeze ResearchRegistry"):
        read_collection_partition(directory, split, "test", symbol="BTCUSDT")
    assert forbidden not in opened
    monkeypatch.setattr(Path, "open", original)
    forbidden.write_text("corrupt future data\n")
    read_collection_partition(directory, split, "calibration", symbol="BTCUSDT")
    registry = ResearchRegistry()
    registry.register("fixed variants", {"seed": 7})
    registry.freeze()
    with pytest.raises(ValueError, match="checksum"):
        read_collection_partition(directory, split, "test", symbol="BTCUSDT", registry=registry)


@pytest.mark.parametrize("offset", [0, 7, 14])
def test_overlapping_or_touching_sources_do_not_publish_final_manifest(tmp_path, offset):
    cfg = load_config(".env.example", inherit_environment=False)
    paths = (source(tmp_path / "a", "2025-01-01"), source(tmp_path / "b", "2025-01-01", offset=offset, quantity="0.02"))
    root = tmp_path / "failed"
    with pytest.raises(ValueError, match="overlap"):
        extract_feature_collection(paths, root, cfg, symbol="BTCUSDT", spec=SPEC)
    assert not (root / "manifest.json").exists()
    assert (root / "sources/000000/manifest.json").exists()


@pytest.mark.parametrize("duplicate_path", [True, False])
def test_duplicate_sources_rejected_before_output_is_created(tmp_path, duplicate_path):
    path = source(tmp_path / "a", "2025-01-01")
    other = path if duplicate_path else tmp_path / "b"
    if not duplicate_path:
        other.write_bytes(path.read_bytes())
    root = tmp_path / "output"
    with pytest.raises(ValueError, match="duplicate"):
        extract_feature_collection(
            (path, other), root, load_config(".env.example", inherit_environment=False), symbol="BTCUSDT"
        )
    assert not root.exists()


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda d: d.update(sample_count=True), "sample_count"),
        (lambda d: d.update(claim_ready=True), "claim"),
        (lambda d: d.update(boundary_rule="concatenated clocks"), "contract"),
        (lambda d: d["config"].update(mm_enabled=True), "contract"),
        (lambda d: d.update(symbol="btcusdt"), "contract"),
        (lambda d: d["sources"][0].update(path="../escape"), "unsafe"),
        (lambda d: d["sources"][0].update(wall_spans=[]), "no actual"),
        (lambda d: d["sources"][0]["wall_spans"][0].update(observations=False), "observation count"),
        (lambda d: d["sources"][0]["wall_spans"][0].update(last_wall_ns=0), "last wall"),
        (lambda d: d["sources"][0]["wall_spans"][0].update(utc_day="2025-01-02"), "UTC mismatch"),
    ],
)
def test_rehashed_malformed_collection_metadata_fails_closed(tmp_path, mutation, message):
    directory, _, _ = bundle(tmp_path)
    rehash(directory, mutation)
    with pytest.raises(ValueError, match=message):
        load_collection_manifest(directory)


def test_child_manifest_mutation_and_row_cap_are_visible(tmp_path):
    directory, _, _ = bundle(tmp_path, extra=True)
    split = collection_split(directory)
    with pytest.raises(ValueError, match="row cap"):
        read_collection_partition(directory, split, "calibration", symbol="BTCUSDT", max_rows=11)
    child = directory / "sources/000000/manifest.json"
    child.write_text(child.read_text() + " ")
    with pytest.raises(ValueError, match="byte checksum"):
        load_collection_manifest(directory)


def test_source_grid_mismatch_cannot_fit_one_collection_partition(tmp_path):
    directory, _, cfg = bundle(tmp_path, extra=True)
    # Replace a child with a independently extracted different instrument grid;
    # rehash metadata so this tests semantic compatibility, not just corruption.
    different = tmp_path / "different"
    path = source(tmp_path / "different-tape", "2025-01-01", offset=60, tick="0.2")
    child = extract_features(path, different, replace(cfg, mm_enabled=False), spec=SPEC, symbols=("BTCUSDT",))
    target = directory / "sources/000003"
    for file in different.iterdir():
        (target / file.name).write_bytes(file.read_bytes())

    def mutate(data):
        entry = data["sources"][3]
        for key in ("input_sha256", "dataset_sha256", "day_files", "sample_count"):
            entry[key] = child[key]
        entry["manifest_file_sha256"] = file_sha256(target / "manifest.json")

    rehash(directory, mutate)
    with pytest.raises(ValueError, match="different instrument grids"):
        read_collection_partition(directory, collection_split(directory), "calibration", symbol="BTCUSDT")


def test_no_clobber_preserves_collection_and_single_source_api(tmp_path):
    directory, manifest, cfg = bundle(tmp_path)
    before = (directory / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        extract_feature_collection((tmp_path / "0.ndjson",), directory, cfg, symbol="BTCUSDT")
    assert (directory / "manifest.json").read_bytes() == before
    assert load_collection_manifest(directory) == manifest
    # Legacy single-source partitions continue to reject insufficient UTC days.
    with pytest.raises(ValueError, match="three distinct"):
        read_partition(
            directory / "sources/000000", dataset_split(directory / "sources/000000"), "calibration", symbol="BTCUSDT"
        )
