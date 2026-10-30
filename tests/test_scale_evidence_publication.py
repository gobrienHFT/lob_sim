"""Selected evidence is copied exactly; no raw-tree or overwrite shortcut."""

import pytest

import experiments.publish_scale_evidence as publication


def test_exact_copy_exclusive_output_and_duplicate_label_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "ROOT", tmp_path)
    source = tmp_path / "source.json"
    source.write_bytes(b'{"producer":"original"}\r\n')
    output = tmp_path / "published"
    result = publication.publish(output, [("raw.json", source)])
    assert result["complete"] and result["claim_ready"] is False
    assert (output / "raw.json").read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError):
        publication.publish(output, [("raw.json", source)])
    with pytest.raises(ValueError, match="duplicate"):
        publication.publish(tmp_path / "duplicate", [("raw.json", source)] * 2)
    assert not (tmp_path / "duplicate").exists()


def test_refuses_directories_oversize_and_escape_before_creating_output(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "ROOT", tmp_path)
    source = tmp_path / "source.json"
    source.write_bytes(b"12345")
    monkeypatch.setattr(publication, "MAX_FILE_BYTES", 4)
    for label, path in [("large.json", source), ("../escape", source), ("directory", tmp_path)]:
        target = tmp_path / "never_created"
        with pytest.raises(ValueError):
            publication.publish(target, [(label, path)])
        assert not target.exists()
