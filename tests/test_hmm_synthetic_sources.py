"""Daily-source contracts frozen before the synthetic-demo exporter.

The independent oracle changes only local receipt identity and cloned headers.
Native feature extraction is tested separately from this serialization oracle.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from lob_sim.config import load_config
from lob_sim.regime.collection import collection_split, extract_feature_collection
from lob_sim.regime.features import FeatureSpec
from lob_sim.regime.synthetic import SyntheticTapeConfig, generate_synthetic_sources, synthetic_rows
from lob_sim.regime.validation import identity

SECOND = 1_000_000_000
DAY = 86_400 * SECOND
REFERENCE = Path(__file__).resolve().parents[1] / "docs/strategy_results/hmm_synthetic_study_reference.json"


def test_committed_default_input_bytes_are_reproducible_not_a_full_research_certificate(tmp_path):
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    expected = reference["native_source_manifest"]
    root = tmp_path / "default-sources"
    actual = generate_synthetic_sources(root, SyntheticTapeConfig(**expected["config"]))
    assert actual == expected
    assert (
        sha256((root / "manifest.json").read_bytes()).hexdigest()
        == (reference["native_run_manifest"]["input_manifest"]["sha256"])
    )
    for entry in expected["sources"]:
        assert sha256((root / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert reference["synthetic"] is True and reference["claim_ready"] is False
    assert reference["split"] == {
        "calibration_days": ["2025-01-01", "2025-01-02", "2025-01-03"],
        "validation_days": ["2025-01-04"],
        "test_days": ["2025-01-05"],
    }


def test_committed_native_manifest_and_insufficient_coverage_keep_their_exact_scope():
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    manifest = reference["native_run_manifest"]
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert sha256((canonical + "\n").encode()).hexdigest() == reference["native_run_manifest_file_sha256"]
    unhashed = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(unhashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert sha256(canonical.encode()).hexdigest() == manifest["manifest_sha256"]
    assert manifest["producer"]["source"] == {
        "git_branch": "codex/hmm-regime-layer",
        "git_commit": "0c8d505f9c419c3d6d12a155be6297b296ee08b3",
        "git_dirty": False,
    }
    assert manifest["status"] == "completed" and manifest["claim_ready"] is False
    assert manifest["generator_manifest_sha256"] == reference["native_source_manifest"]["manifest_sha256"]
    assert manifest["study_report"]["sha256"] == "8ca302a740480a105ec2ec5c05fcd578c661a686ca79113988712fdf42634006"
    assert reference["models"]["primary"]["attempts"] == reference["models"]["cadence_250ms"]["attempts"] == 40
    assert [row["variant"] for row in reference["results"]] == [
        "baseline",
        "observe",
        "policy",
        "hard_active",
        "cadence_250ms",
    ]
    for row in reference["results"]:
        assert row["net_marked_pnl_quote_rational"] is None and row["unpriced_inventory_ns"] > 0
    coverage = reference["comparison_coverage"]
    assert coverage["eligible_period_count"] == coverage["period_count"] == 2
    assert coverage["block_minutes"] == [30, 5, 60]
    assert coverage["all_intervals"] is None and coverage["unavailable_reason"]


def independent_daily_rows(config):
    raw = [row for kind, row in synthetic_rows(config) if kind == "raw"]
    start = (
        int(datetime.combine(date.fromisoformat(config.start_day), datetime.min.time(), timezone.utc).timestamp())
        * SECOND
    )
    result = []
    for day in range(config.days):
        origin = day * (config.seconds_per_day + 2) * SECOND
        values = json.loads(json.dumps(raw[:2]))
        for value in values:
            value["data"]["_capture"]["recvWallNs"] += day * DAY
            value["data"]["_capture"]["streamEpoch"] = day
            value["data"]["_capture"]["syncEpoch"] = day
        values.extend(
            json.loads(
                json.dumps(
                    [value for value in raw[2:] if (value["data"]["_capture"]["recvWallNs"] - start) // DAY == day]
                )
            )
        )
        for index, value in enumerate(values):
            capture = value["data"]["_capture"]
            if index >= 2:
                capture["recvMonotonicNs"] -= origin
            capture["recvSeq"] = index
            value["ts_local"] = capture["recvWallNs"] / SECOND
        result.append(values)
    return result


def test_daily_export_matches_independent_calendar_receipt_and_payload_oracle(tmp_path):
    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    report = generate_synthetic_sources(tmp_path / "sources", config)
    expected = independent_daily_rows(config)
    assert report["schema_version"] == "lob_sim.synthetic_regime_sources.v1"
    assert not report["claim_ready"] and report["synthetic"]
    assert report["manifest_sha256"] == identity({k: v for k, v in report.items() if k != "manifest_sha256"})
    assert len(report["sources"]) == config.days
    for day, entry in enumerate(report["sources"]):
        path = tmp_path / "sources" / entry["path"]
        actual = [json.loads(line) for line in path.read_text().splitlines()]
        assert actual == expected[day]
        assert entry["utc_day"] == (date.fromisoformat(config.start_day) + timedelta(days=day)).isoformat()
        assert entry["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert entry["records"] == len(actual)
        assert all("state" not in value["data"] and "truth" not in value["data"] for value in actual)
        capture = [value["data"]["_capture"] for value in actual]
        assert [value["recvSeq"] for value in capture] == list(range(len(actual)))
        assert all(type(value["recvMonotonicNs"]) is int for value in capture)
        assert len({value["recvWallNs"] - value["recvMonotonicNs"] for value in capture}) == 1
        assert capture[0]["recvMonotonicNs"] == 0
        assert max(value["recvMonotonicNs"] for value in capture) < 61 * SECOND


def test_daily_export_never_mutates_generator_rows_or_rewrites_market_payloads(tmp_path, monkeypatch):
    import lob_sim.regime.synthetic as module

    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    original = list(synthetic_rows(config))
    before = json.dumps(original, sort_keys=True)
    monkeypatch.setattr(module, "synthetic_rows", lambda _: iter(original))
    generate_synthetic_sources(tmp_path / "sources", config)
    assert json.dumps(original, sort_keys=True) == before


def test_native_daily_extraction_does_not_manufacture_complete_days_or_transitions(tmp_path):
    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    root = tmp_path / "sources"
    report = generate_synthetic_sources(root, config)
    paths = tuple(root / entry["path"] for entry in report["sources"])
    collection = extract_feature_collection(
        paths,
        tmp_path / "features",
        load_config(".env.example", inherit_environment=False),
        symbol="BTCUSDT",
        spec=FeatureSpec(),
    )
    split = collection_split(tmp_path / "features")
    assert not split.claim_ready
    assert split.calibration_days == ("2025-01-01",)
    assert split.validation_days == ("2025-01-02",)
    assert split.test_days == ("2025-01-03",)
    for entry in collection["sources"]:
        assert len(entry["wall_spans"]) == 1
        span = entry["wall_spans"][0]
        assert span["wall_offset_ns"] is not None
        assert 60 * SECOND <= span["last_wall_ns"] - span["first_wall_ns"] < 61 * SECOND


def test_daily_sources_repeat_identically_and_existing_evidence_is_never_replaced(tmp_path):
    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    left = generate_synthetic_sources(tmp_path / "left", config)
    right = generate_synthetic_sources(tmp_path / "right", config)
    assert left == right
    before = (tmp_path / "left/manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        generate_synthetic_sources(tmp_path / "left", config)
    assert (tmp_path / "left/manifest.json").read_bytes() == before


@pytest.mark.parametrize("cutoff", (5, -1))
def test_silent_early_generator_end_is_not_published_as_complete(tmp_path, monkeypatch, cutoff):
    import lob_sim.regime.synthetic as module

    config = SyntheticTapeConfig(days=3, seconds_per_day=60)
    rows = list(synthetic_rows(config))[:cutoff]
    monkeypatch.setattr(module, "synthetic_rows", lambda _: iter(rows))
    root = tmp_path / "short"
    with pytest.raises(ValueError, match="completion"):
        generate_synthetic_sources(root, config)
    assert not (root / "manifest.json").exists()
    assert (root / "_INCOMPLETE.json").exists()
    assert list(root.glob("*.partial"))


@pytest.mark.parametrize("fault", ("generator", "fsync"))
def test_failed_daily_generation_preserves_visible_partial_and_never_publishes_manifest(tmp_path, monkeypatch, fault):
    import lob_sim.regime.synthetic as module

    original = module.synthetic_rows
    if fault == "generator":

        def broken(config):
            for index, item in enumerate(original(config)):
                if index == 5:
                    raise OSError("injected generation failure")
                yield item

        monkeypatch.setattr(module, "synthetic_rows", broken)
    else:
        real_fsync = module.os.fsync
        calls = 0

        def broken_fsync(_):
            nonlocal calls
            calls += 1
            if calls == 2:  # Marker succeeds; fail the first actual raw writer.
                raise OSError("injected fsync failure")
            real_fsync(_)

        monkeypatch.setattr(module.os, "fsync", broken_fsync)
    root = tmp_path / "failed"
    with pytest.raises(OSError, match="injected"):
        generate_synthetic_sources(root, SyntheticTapeConfig(days=3, seconds_per_day=60))
    assert not (root / "manifest.json").exists()
    assert list(root.glob("*.partial"))
    with pytest.raises(FileExistsError):
        generate_synthetic_sources(root, SyntheticTapeConfig(days=3, seconds_per_day=60))


def test_failed_manifest_publication_keeps_finalized_sources_visibly_incomplete(tmp_path, monkeypatch):
    import lob_sim.regime.synthetic as module

    original = module.publish_json

    def fail_manifest(path, value):
        if path.name == "manifest.json":
            raise OSError("injected manifest failure")
        original(path, value)

    monkeypatch.setattr(module, "publish_json", fail_manifest)
    root = tmp_path / "unpublished"
    with pytest.raises(OSError, match="injected manifest"):
        generate_synthetic_sources(root, SyntheticTapeConfig(days=3, seconds_per_day=60))
    assert len(list(root.glob("*.ndjson"))) == 3
    assert (root / "_INCOMPLETE.json").exists()
    assert not (root / "manifest.json").exists()


@pytest.mark.parametrize(
    "arguments",
    (
        [],
        ["--synthetic-demo", "--input", "external.ndjson"],
        ["--input", "external.ndjson", "--synthetic-days", "5"],
        ["--synthetic-demo", "--symbol", "ETHUSDT"],
    ),
)
def test_cli_rejects_ambiguous_or_non_demo_generator_options_before_outputs(tmp_path, arguments):
    import experiments.run_hmm_regime_study as module

    root = tmp_path / "never"
    with pytest.raises(SystemExit) as failure:
        module.main(["--out-dir", str(root), *arguments])
    assert failure.value.code == 2
    assert not root.exists()


def test_cli_demo_records_native_report_parents_common_workload_and_failures(tmp_path, monkeypatch, capsys):
    import experiments.run_hmm_regime_study as module
    from lob_sim.regime.dataset import publish_json

    calls = []

    def study(inputs, directory, cfg, **kwargs):
        directory.mkdir()
        calls.append((inputs, cfg, kwargs))
        report = {
            "status": "incomplete",
            "registry_sha256": "a" * 64,
            "claim_reason": "diagnostic only",
            "code_identity": {"sha256": "b" * 64},
            "results": [],
            "comparisons": [],
            "failures": [{"stage": "fit", "error": "injected no valid candidate"}],
        }
        publish_json(directory / "study_report.json", report)
        return report

    monkeypatch.setattr(module, "run_regime_study", study)
    root = tmp_path / "demo"
    code = module.main(
        [
            "--synthetic-demo",
            "--out-dir",
            str(root),
            "--synthetic-days",
            "3",
            "--synthetic-seconds-per-day",
            "60",
            "--restarts",
            "1",
            "--max-iterations",
            "80",
        ]
    )
    assert code == 1 and "FAILED fit" in capsys.readouterr().out
    inputs, cfg, kwargs = calls[0]
    base = load_config(".env.example", inherit_environment=False)
    assert len(inputs) == 3 and all(path.is_file() for path in inputs)
    assert str(cfg.mm_order_qty) == "0.010" and cfg.mm_requote_ms == 500
    assert cfg.mm_max_position == base.mm_max_position
    assert cfg.sim_latency_mode == base.sim_latency_mode and cfg.fill_assumption == base.fill_assumption
    assert kwargs["fit_config"].state_counts == (2, 3, 4, 5)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "incomplete" and not manifest["claim_ready"]
    assert (root / "_INCOMPLETE.json").exists()
    assert manifest["study_report"]["sha256"] == sha256((root / "study/study_report.json").read_bytes()).hexdigest()
    assert manifest["manifest_sha256"] == identity({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    with pytest.raises(FileExistsError):
        module.main(["--synthetic-demo", "--out-dir", str(root)])
