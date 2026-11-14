"""Pure admission/geometry/resource oracles, not fabricated empirical days."""

from copy import deepcopy
from pathlib import Path

import pytest

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import canonical_json, identity
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.evidence_io import JSON_LIMIT, STUDY_JSON_LIMIT, read_json
from lob_sim.research.registered_protocol import FrozenProtocol
from lob_sim.research.study_units import expected_study_units
from lob_sim.research.study_views import load_views
from lob_sim.research.view_reader import verify_day_view
from test_research_capture_intervals import SECOND as S, WALL
from test_research_day_views import view
from test_research_registry import mechanical_payload


def test_units_follow_projected_utc_not_incomparable_machine_monotonic_origins():
    protocol = FrozenProtocol(canonical_json(mechanical_payload()))
    first_mono, day = 10**14, WALL + 8 * 86_400 * S
    parents = {}
    for sha, clock in (("a" * 64, CaptureClock(first_mono, day)), ("b" * 64, CaptureClock(0, day + 43_200 * S))):
        parents[sha] = (
            Path("unused"),
            {
                "research_usable": True,
                "clock": clock.as_dict(),
                "report_sha256": "3" * 64,
                "first_logical_ns": clock.origin_logical_ns,
                "last_logical_ns": clock.origin_logical_ns + 43_200 * S,
            },
        )
    units = expected_study_units(protocol, parents, registry_sha256=protocol.digest)
    assert [u["source_sha256"] for u in units] == ["a" * 64, "a" * 64, "b" * 64, "b" * 64]
    assert all(u["utc_day"] == "2025-01-09" for u in units)


def test_study_json_has_a_larger_fixed_budget_without_relaxing_parent_reads(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(canonical_json({"padding": "x" * JSON_LIMIT}), encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        read_json(path)
    assert len(read_json(path, maximum_bytes=STUDY_JSON_LIMIT)["padding"]) == JSON_LIMIT
    with pytest.raises(ValueError, match="size budget"):
        read_json(tmp_path / "never_opened", maximum_bytes=STUDY_JSON_LIMIT + 1)


def test_no_selected_models_or_invalid_digest_can_open_a_view_bundle(monkeypatch):
    protocol = FrozenProtocol(canonical_json(mechanical_payload()))

    def forbidden(*a, **k):
        raise AssertionError("precondition failed before any artifact access")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError, match="all frozen"):
        load_views(
            Path("never_opened"), protocol, {"all_selected_models_available": False}, registry_sha256=protocol.digest
        )
    with pytest.raises(ValueError, match="registry"):
        load_views(Path("never_opened"), protocol, {}, registry_sha256="9" * 64)


def test_empty_view_universe_cannot_be_rehashed_into_completed_research(tmp_path):
    protocol = FrozenProtocol(canonical_json(mechanical_payload()))
    models = {"all_selected_models_available": True, "models_sha256": "5" * 64}
    manifest = {
        "schema_version": "lob_sim.registered_replay_views.v1",
        "complete": True,
        "claim_ready": False,
        "registry_sha256": protocol.digest,
        "models_sha256": models["models_sha256"],
        "code_identity": protocol.snapshot()["code_identity"],
        "units": [],
    }
    manifest["views_sha256"] = identity(manifest)
    publish_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="completion/parents/content"):
        load_views(tmp_path, protocol, models, registry_sha256=protocol.digest)


def test_rehashed_view_contract_cannot_relabel_its_window_as_a_different_utc_day(tmp_path):
    source, path, entry = view(tmp_path)
    changed = deepcopy(entry)
    changed["contract"]["utc_day"] = "2025-01-02"
    changed["view_id"] = identity(changed["contract"])
    with pytest.raises(ValueError, match="geometry escapes"):
        verify_day_view(source, path, changed)
