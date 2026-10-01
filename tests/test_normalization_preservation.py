from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.replay import arrow_store

pytest.importorskip("pyarrow")


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.ndjson"
    row = {"ts_local": 1, "symbol": "BTCUSDT", "type": "depthUpdate", "data": {"U": 1, "u": 1, "b": [], "a": []}}
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return source


def test_normalization_refuses_to_replace_its_own_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ValueError, match="source and destination must differ"):
        arrow_store.normalize_to_arrow(source, source)
    assert source.read_bytes() == original
    assert not source.with_name(source.name + ".partial").exists()


@pytest.mark.parametrize("existing", ["final", "partial"])
def test_normalization_preserves_existing_outputs(tmp_path: Path, existing: str) -> None:
    source = _source(tmp_path)
    target = tmp_path / "normalized.arrow"
    protected = target if existing == "final" else target.with_name(target.name + ".partial")
    protected.write_bytes(b"previous evidence")
    with pytest.raises(FileExistsError):
        arrow_store.normalize_to_arrow(source, target)
    assert protected.read_bytes() == b"previous evidence"
    assert source.read_text(encoding="utf-8").startswith('{"ts_local"')


def test_finalization_collision_cannot_replace_another_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    target = tmp_path / "normalized.arrow"
    original_link = arrow_store.os.link

    def collide(partial: Path, final: Path) -> None:
        final.write_bytes(b"competing completed output")
        original_link(partial, final)

    monkeypatch.setattr(arrow_store.os, "link", collide)
    with pytest.raises(FileExistsError):
        arrow_store.normalize_to_arrow(source, target)
    assert target.read_bytes() == b"competing completed output"
    assert target.with_name(target.name + ".partial").exists()


def test_unsupported_atomic_publication_retains_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    target = tmp_path / "normalized.arrow"

    def unsupported(partial: Path, final: Path) -> None:
        raise OSError("hard links unavailable")

    monkeypatch.setattr(arrow_store.os, "link", unsupported)
    with pytest.raises(OSError, match="hard links unavailable"):
        arrow_store.normalize_to_arrow(source, target)
    assert not target.exists()
    assert target.with_name(target.name + ".partial").exists()


def test_changed_source_cannot_finalize_under_an_incorrect_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "normalized.arrow"
    original_iter = arrow_store.iter_records

    def mutate_after_read(path: Path):
        yield from original_iter(path)
        source.write_text(source.read_text(encoding="utf-8").replace('"U": 1', '"U": 0'), encoding="utf-8")

    monkeypatch.setattr(arrow_store, "iter_records", mutate_after_read)
    with pytest.raises(ValueError, match="normalization source changed"):
        arrow_store.normalize_to_arrow(source, target)
    assert not target.exists()
    assert target.with_name(target.name + ".partial").exists()


def test_completed_normalization_is_readable_and_repeat_does_not_replace_it(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "normalized.arrow"
    report = arrow_store.normalize_to_arrow(source, target, batch_size=1)
    original = target.read_bytes()
    assert report["records"] == 1
    assert report["input_sha256"] == arrow_store.file_sha256(source)
    assert len(list(arrow_store.iter_arrow_rows(target))) == 1
    assert not target.with_name(target.name + ".partial").exists()
    with pytest.raises(FileExistsError):
        arrow_store.normalize_to_arrow(source, target)
    assert target.read_bytes() == original
