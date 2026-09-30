from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.oracle import Checkpoint, write_checkpoint
from lob_sim.replay.adapters import BinanceUsdMReplayAdapter
from lob_sim.sim.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    checkpoint_adapter_identity,
    checkpoint_code_identity,
)
from lob_sim.sim.engine import SimulationEngine


FIXTURE = Path(__file__).resolve().parents[1] / "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson"


def test_checkpoint_source_identity_is_portable_sorted_and_git_independent(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    # Opposite creation orders must yield the same identity. No Git repository
    # exists, so this also exercises source shipped in an ordinary wheel.
    source_bytes = {"b.py": b"B = 2\n", "a.py": b"A = 1\n"}
    for name in source_bytes:
        (first / name).write_bytes(source_bytes[name])
    for name in reversed(source_bytes):
        (second / name).write_bytes(source_bytes[name])
    (second / "notes.md").write_text("documentation is not executable source", encoding="utf-8")

    expected = sha256()
    for name, content in sorted(source_bytes.items()):
        encoded = name.encode("utf-8")
        expected.update(len(encoded).to_bytes(8, "big"))
        expected.update(encoded)
        expected.update(sha256(content).digest())
    identity = checkpoint_code_identity(first)
    assert identity["sha256"] == expected.hexdigest()
    assert identity["file_count"] == 2
    assert checkpoint_code_identity(second) == identity
    (second / "a.py").write_bytes(b"A = 9\n")
    assert checkpoint_code_identity(second) != identity
    (first / "untracked.py").write_bytes(b"NEW = 1\n")
    assert checkpoint_code_identity(first) != identity


def test_checkpoint_identity_rejects_missing_package_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="readable Python package source"):
        checkpoint_code_identity(tmp_path)


def test_checkpoint_adapter_identity_includes_declared_contract() -> None:
    first = BinanceUsdMReplayAdapter()
    second = BinanceUsdMReplayAdapter()
    assert checkpoint_adapter_identity(first) == checkpoint_adapter_identity(second)
    second.name = "changed_adapter_contract"
    assert checkpoint_adapter_identity(first) != checkpoint_adapter_identity(second)


def _paused(tmp_path: Path) -> tuple[SimulationEngine, Path, Checkpoint]:
    engine = SimulationEngine(load_config(".env.example"))
    path = tmp_path / "checkpoint.json"
    engine.run(FIXTURE, checkpoint_path=path, stop_after_records=3)
    checkpoint = engine.write_state_checkpoint(FIXTURE, path)
    return engine, path, checkpoint


def test_checkpoint_rejects_code_drift_before_mutating_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paused, path, checkpoint = _paused(tmp_path)
    assert checkpoint.state["code_identity"] == checkpoint_code_identity()
    resumed = SimulationEngine(paused.cfg)
    before = resumed.state_sha256()
    changed = dict(checkpoint.state["code_identity"], sha256="0" * 64)
    monkeypatch.setattr("lob_sim.sim.engine.checkpoint_code_identity", lambda: changed)
    with pytest.raises(ValueError, match="source-code identity"):
        resumed.run(FIXTURE, resume_from=path)
    assert resumed.state_sha256() == before


def test_checkpoint_rejects_adapter_drift_before_mutating_engine(tmp_path: Path) -> None:
    paused, path, _checkpoint = _paused(tmp_path)
    changed = BinanceUsdMReplayAdapter()
    changed.name = "changed_adapter_contract"
    resumed = SimulationEngine(paused.cfg, adapter=changed)
    before = resumed.state_sha256()
    with pytest.raises(ValueError, match="adapter identity"):
        resumed.run(FIXTURE, resume_from=path)
    assert resumed.state_sha256() == before


@pytest.mark.parametrize("field", ["code_identity", "adapter_identity"])
def test_checkpoint_rejects_missing_identity_even_with_valid_state_hash(tmp_path: Path, field: str) -> None:
    paused, path, checkpoint = _paused(tmp_path)
    state = dict(checkpoint.state)
    state.pop(field)
    write_checkpoint(
        path,
        Checkpoint.create(
            event_index=checkpoint.event_index,
            logical_time=checkpoint.logical_time,
            state=state,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
        ),
    )
    with pytest.raises(ValueError, match="identity"):
        SimulationEngine(paused.cfg).run(FIXTURE, resume_from=path)


@pytest.mark.parametrize("legacy_field", ["outer", "inner"])
def test_checkpoint_rejects_legacy_schema_without_identity_migration(tmp_path: Path, legacy_field: str) -> None:
    paused, path, checkpoint = _paused(tmp_path)
    state = dict(checkpoint.state)
    if legacy_field == "inner":
        state["schema_version"] = "lob_sim.simulation_checkpoint.v2"
    write_checkpoint(
        path,
        Checkpoint.create(
            event_index=checkpoint.event_index,
            logical_time=checkpoint.logical_time,
            state=state,
            schema_version="lob_sim.simulation_checkpoint.v2" if legacy_field == "outer" else CHECKPOINT_SCHEMA_VERSION,
        ),
    )
    with pytest.raises(ValueError, match="unsupported simulation checkpoint schema"):
        SimulationEngine(paused.cfg).run(FIXTURE, resume_from=path)
