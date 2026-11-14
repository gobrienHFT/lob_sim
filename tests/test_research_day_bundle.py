"""Source-bound admission bundles; fixtures remain explicitly synthetic."""

import json

import pytest

from lob_sim.regime.validation import identity
from lob_sim.research.day_bundle import build_day_bundle, verify_day_bundle
from test_research_capture_intervals import capture, cfg
from test_research_admission_faults import change_manifest
from test_research_days import rehash_report


def bundle(tmp_path):
    source = capture(tmp_path)
    root = tmp_path / "days"
    result = build_day_bundle((source,), root, cfg())
    return source, root, result


def test_bundle_reopens_raw_inputs_and_never_promotes_synthetic_snippets(tmp_path):
    source, root, manifest = bundle(tmp_path)
    result = verify_day_bundle(root, (source,))
    assert result["verified"] and result["eligible_days"] == []
    assert result["bundle_sha256"] == manifest["bundle_sha256"]
    assert not result["ready_for_registration"]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        build_day_bundle((source,), root, cfg())
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_changed_raw_source_is_not_only_a_valid_artifact_hash(tmp_path):
    source, root, _ = bundle(tmp_path)
    change_manifest(source, lambda m: m.update(unknown_forensic_note="changed"))
    with pytest.raises(ValueError, match="mismatched"):
        verify_day_bundle(root, (source,))


def test_corrupt_source_preserves_nested_failure_and_does_not_publish_success(tmp_path):
    source = capture(tmp_path)
    next(source.parent.glob("*.ndjson")).write_bytes(b"corrupt\n")
    root = tmp_path / "days"
    with pytest.raises(ValueError):
        build_day_bundle((source,), root, cfg())
    assert (root / "capture-0000" / "failure.json").exists()
    assert (root / "failure.json").exists()
    assert not (root / "manifest.json").exists()


def test_instrument_metadata_hash_is_independently_checked(tmp_path):
    source, root, _ = bundle(tmp_path)
    rehash_report(root / "capture-0000", lambda m: m["instruments"]["BTCUSDT"]["spec"].update(tick_size="0.2"))
    with pytest.raises(ValueError, match="instrument metadata identity"):
        verify_day_bundle(root, (source,))


def test_paths_cannot_redirect_verification_outside_the_bundle(tmp_path):
    source, root, _ = bundle(tmp_path)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["parents"][0]["directory"] = "../raw"
    manifest["bundle_sha256"] = identity({k: v for k, v in manifest.items() if k != "bundle_sha256"})
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="noncanonical"):
        verify_day_bundle(root, (source,))


def test_failure_receipt_io_failure_does_not_replace_primary_failure(tmp_path, monkeypatch):
    import lob_sim.research.evidence_io as module

    def fail_publish(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(module, "publish_json", fail_publish)
    source = capture(tmp_path)
    next(source.parent.glob("*.ndjson")).write_bytes(b"corrupt\n")
    with pytest.raises(ValueError) as error:
        build_day_bundle((source,), tmp_path / "days", cfg())
    assert any("OSError" in note for note in error.value.__notes__)


def test_cli_research_commands_are_registered(capsys, monkeypatch):
    from lob_sim.cli import main

    monkeypatch.setattr("sys.argv", ["lob_sim", "research-days-verify", "--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert "--bundle" in capsys.readouterr().out
