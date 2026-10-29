"""Read-only complete/prefix forensic controls; no empirical admission."""

from pathlib import Path

import pytest

from experiments.audit_incomplete_capture import audit
from experiments.research_scale_fixture import generate
from lob_sim.replay.inspection import file_sha256


def test_prefix_forensics_never_finalizes_or_rewrites_incomplete_original(tmp_path):
    zstd = pytest.importorskip("zstandard")
    source = tmp_path / "source"
    generate(source, rounds=2, levels=2)
    segment = next(source.glob("*.ndjson.zst"))
    with zstd.ZstdDecompressor().stream_reader(segment.open("rb")) as reader:
        lines = reader.read().splitlines(keepends=True)
    partial = segment.with_name(segment.name + ".partial")
    segment.rename(partial)
    partial.write_bytes(zstd.ZstdCompressor().compress(b"".join(lines[:-1])))
    prior = file_sha256(partial)
    target = tmp_path / "forensic.json"
    report = audit(source, target)
    assert file_sha256(partial) == prior and not segment.exists()
    assert report["source_bytes_unchanged"]
    assert report["finalized_segment_records"] == 0
    assert report["segments"][0]["count"] == 18
    assert not report["segments"][0]["ok"] and not report["segments"][0]["complete"]
    assert "missing complete segment trailer" in report["segments"][0]["issues"]
    with pytest.raises(ValueError, match="already exists"):
        audit(source, target)
    with pytest.raises(ValueError, match="outside original"):
        audit(source, source / "must_not_exist.json")
    assert not (source / "must_not_exist.json").exists()


def test_forensic_report_is_not_published_when_original_bytes_change(tmp_path, monkeypatch):
    import experiments.audit_incomplete_capture as module

    source = tmp_path / "source"
    generate(source, rounds=2, levels=2)
    original = module.validate_segment

    def mutate_after_read(path: Path):
        result = original(path)
        path.write_bytes(path.read_bytes() + b"tampered")
        return result

    monkeypatch.setattr(module, "validate_segment", mutate_after_read)
    with pytest.raises(ValueError, match="changed during audit"):
        audit(source, tmp_path / "must_not_exist.json")
    assert not (tmp_path / "must_not_exist.json").exists()
