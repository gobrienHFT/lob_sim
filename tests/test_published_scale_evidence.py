"""Independent byte audit of published diagnostics, not timing authentication."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath


def test_committed_scale_inventory_preserves_every_selected_producer_byte():
    repository = Path(__file__).resolve().parents[1]
    root = repository / "docs/research_scale_evidence"
    manifest = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema_version", "complete", "claim_ready", "publisher_sha256", "files", "scope"}
    assert manifest["schema_version"] == "lob_sim.research_scale_evidence_inventory.v1"
    assert manifest["complete"] is True and manifest["claim_ready"] is False
    assert (
        manifest["publisher_sha256"]
        == sha256((repository / "experiments/publish_scale_evidence.py").read_bytes()).hexdigest()
    )
    entries = manifest["files"]
    assert isinstance(entries, list) and 1 <= len(entries) <= 64
    labels, total = set(), 0
    for entry in entries:
        assert set(entry) == {"path", "source", "sha256", "size_bytes"}
        label = entry["path"]
        assert isinstance(label, str) and Path(label).name == label and label not in labels
        labels.add(label)
        source = PurePosixPath(entry["source"])
        assert not source.is_absolute() and ".." not in source.parts and "\\" not in entry["source"]
        path = (root / label).resolve()
        assert path.parent == root.resolve() and not (root / label).is_symlink()
        assert type(entry["size_bytes"]) is int and 0 <= entry["size_bytes"] <= 8 * 1024**2
        with path.open("rb") as handle:
            content = handle.read(8 * 1024**2 + 1)
        assert len(content) == entry["size_bytes"]
        assert sha256(content).hexdigest() == entry["sha256"]
        total += len(content)
    assert total <= 64 * 1024**2
    assert {path.name for path in root.iterdir()} == labels | {"inventory.json"}
