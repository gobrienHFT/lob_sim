"""Front-of-reader typed graph bindings; stops before any native case audit.

Parent admission is explicitly substituted to isolate this identity boundary.
This test is not evidence that the substitute graph is an admitted study.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
import lob_sim.research.study_reader as reader


class PassedPlanBoundary(Exception):
    pass


@pytest.fixture
def front_graph(tmp_path, monkeypatch):
    registered = {"code_identity": {"sha256": "code"}, "split": {"test_days": ["2025-01-09"]}}
    protocol = SimpleNamespace(snapshot=lambda: deepcopy(registered), digest="registry")
    models = {"all_selected_models_available": True, "models_sha256": "models"}
    views = {"views_sha256": "views"}
    cases = [{"symbol": "BTCUSDT", "interval_ns": 1_000_000_000, "fill_scenario": "conservative", "latency_ms": 0}]
    for name in ("verify_prepared", "verify_registered_models", "admitted_parents", "verify_views"):
        monkeypatch.setattr(reader, name, lambda *a, **k: {})
    monkeypatch.setattr(reader, "load_protocol", lambda *a, **k: protocol)
    monkeypatch.setattr(reader, "load_model_set", lambda *a, **k: models)
    monkeypatch.setattr(reader, "load_views", lambda *a, **k: views)
    monkeypatch.setattr(reader, "expected_cases", lambda *a, **k: deepcopy(cases))
    original_read = reader.read_json

    def read(path, **kwargs):
        if path.name == "test_access.json":
            raise PassedPlanBoundary("typed plan gate accepted")
        return original_read(path, **kwargs)

    monkeypatch.setattr(reader, "read_json", read)
    plan = {
        "registry_sha256": "registry",
        "models_sha256": "models",
        "views_sha256": "views",
        "cases": deepcopy(cases),
        "claim_ready": False,
    }
    cell = ["BTCUSDT", 1_000_000_000, "conservative", 0]
    manifest = {
        "schema_version": "lob_sim.registered_real_study.v1",
        "complete": True,
        "claim_ready": False,
        "registry_sha256": "registry",
        "models_sha256": "models",
        "views_sha256": "views",
        "code_identity": registered["code_identity"],
        "results": [{}],
        "comparisons": [
            {
                "cell": cell,
                "path": "comparisons/" + identity({"registry_sha256": "registry", "cell": tuple(cell)}) + ".json",
                "sha256": "unused",
            }
        ],
        "attempts_sha256": "unused",
        "plan_sha256": "unused",
        "scope": "isolated boundary control",
    }
    return tmp_path, plan, manifest


def verify(front_graph):
    root, plan, manifest = front_graph
    publish_json(root / "plan.json", plan)
    manifest["plan_sha256"] = file_sha256(root / "plan.json")
    manifest["study_sha256"] = identity({k: v for k, v in manifest.items() if k != "study_sha256"})
    publish_json(root / "manifest.json", manifest)
    missing = root / "intentionally_unread_parent"
    reader.verify_registered_study(
        root, (missing,), missing, missing, missing, missing, missing, None, registry_sha256="registry"
    )


def test_canonical_numeric_control_reaches_the_next_boundary(front_graph):
    with pytest.raises(PassedPlanBoundary):
        verify(front_graph)


@pytest.mark.parametrize("mutation", ["plan_latency", "plan_claim_flag", "comparison_latency"])
def test_rehashed_boolean_numeric_substitution_is_rejected_before_case_access(front_graph, mutation):
    _, plan, manifest = front_graph
    if mutation == "plan_latency":
        plan["cases"][0]["latency_ms"] = False
    elif mutation == "plan_claim_flag":
        plan["claim_ready"] = 0
    else:
        manifest["comparisons"][0]["cell"][3] = False
    with pytest.raises(ValueError, match="study plan differs|comparison cell/path differs"):
        verify(front_graph)
