"""Protocol/access mechanics with hand-authored labels, NOT admitted data."""

from copy import deepcopy
from pathlib import Path

import pytest

from lob_sim.regime.validation import canonical_json, identity
from lob_sim.research.day_bundle import build_day_bundle
from lob_sim.research.evidence_io import read_json
from lob_sim.research.protocol import chronological_day_split
from lob_sim.research.registered_protocol import (
    FrozenProtocol,
    experiment_specification,
    load_protocol,
    register_protocol,
)
from test_research_capture_intervals import capture, cfg


def mechanical_payload():
    days = ["2025-01-" + str(i).zfill(2) for i in range(1, 11)]
    experiment = experiment_specification(cfg())
    data = {
        "schema_version": "lob_sim.frozen_real_market_registry.v1",
        "frozen": True,
        "day_bundle_sha256": "0" * 64,
        "day_report_sha256": "1" * 64,
        "sources": [
            {
                "capture_id": "mechanical_only",
                "input_sha256": "2" * 64,
                "report_sha256": "3" * 64,
                "clock_sha256": "4" * 64,
                "research_usable": False,
            }
        ],
        "eligible_days": days,
        "split": chronological_day_split(days).as_dict(),
        "experiment": experiment,
        "experiment_sha256": identity(experiment),
        "code_identity": {"mechanical_test_only": True},
    }
    data["registry_sha256"] = identity(data)
    return data


def protocol():
    return FrozenProtocol(canonical_json(mechanical_payload()))


def test_registered_specification_freezes_sources_features_fits_economics_and_scenarios():
    experiment = experiment_specification(cfg())
    assert experiment["fitting"]["state_counts"] == (2, 3, 4, 5)
    assert experiment["fitting"]["restarts"] == 10 and len(experiment["restart_seeds"]) == 40
    assert len({v["seed"] for v in experiment["restart_seeds"]}) == 40
    assert experiment["variants"] == ["baseline", "hmm_observe", "hmm_policy"]
    assert experiment["latency_ms"] == [0, 1, 5, 10, 25, 50]
    assert experiment["statistics"]["primary_block_minutes"] == 30
    assert "not measured" in experiment["latency_contract"]
    assert experiment["base_configuration"]["sim_markout_horizons_ms"] == [100, 1000, 5000, 30_000]
    assert {f["interval_ns"] * f["window_steps"] for f in experiment["features"]} == {10_000_000_000}
    assert [s["sim_fill_model"] for s in experiment["fill_scenarios"]] == ["trade", "depth", "depth"]
    for scenario in experiment["fill_scenarios"]:
        effective = scenario["effective_fill_assumption"]
        assert effective["depth_reductions_consume_queue"] != effective["agg_trades_consume_queue"]
    assert (
        experiment["fill_scenarios"][1]["passive_execution_equivalence_class"]
        == experiment["fill_scenarios"][2]["passive_execution_equivalence_class"]
    )
    assert len(experiment["hysteresis"]) == 3


def test_loaded_protocol_is_immutable_and_has_exact_whole_day_chronology():
    frozen = protocol()
    data = frozen.snapshot()
    assert len(data["split"]["calibration_days"]) == 6
    assert len(data["split"]["validation_days"]) == 2 and len(data["split"]["test_days"]) == 2
    data["experiment"]["latency_ms"].clear()
    assert frozen.snapshot()["experiment"]["latency_ms"] == [0, 1, 5, 10, 25, 50]


def test_bad_digest_or_role_cannot_inspect_or_open_test_partition(tmp_path, monkeypatch):
    frozen = protocol()

    def forbidden_open(*args, **kwargs):
        raise AssertionError("unauthorized partition was touched")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(ValueError, match="digest"):
        frozen.open_feature_day(tmp_path, "test", "2025-01-09", "BTCUSDT", registry_sha256="9" * 64)
    with pytest.raises(ValueError, match="partition"):
        frozen.open_feature_day(tmp_path, "calibration", "2025-01-09", "BTCUSDT", registry_sha256=frozen.digest)
    with pytest.raises(ValueError, match="digest"):
        load_protocol(tmp_path / "registry.json", registry_sha256="")


def test_correct_authorization_opens_only_the_physical_selected_day(tmp_path):
    frozen = protocol()
    (tmp_path / "test").mkdir()
    (tmp_path / "calibration").mkdir()
    (tmp_path / "test" / "2025-01-09__BTCUSDT.jsonl").write_bytes(b"test mechanics\n")
    with frozen.open_feature_day(tmp_path, "test", "2025-01-09", "BTCUSDT", registry_sha256=frozen.digest) as handle:
        assert handle.read() == b"test mechanics\n"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(frozen=1),
        lambda d: d["eligible_days"].pop(),
        lambda d: d["split"]["test_days"].append("2025-01-08"),
        lambda d: d["experiment"].update(latency_ms=[-1]),
        lambda d: d["sources"][0].update(clock_sha256="bad"),
    ],
)
def test_rehashed_but_inconsistent_protocol_cannot_be_loaded(mutation):
    data = deepcopy(mechanical_payload())
    mutation(data)
    data["registry_sha256"] = identity({k: v for k, v in data.items() if k != "registry_sha256"})
    with pytest.raises(ValueError):
        FrozenProtocol(canonical_json(data))


def test_synthetic_short_capture_cannot_register_a_real_market_study(tmp_path):
    source = capture(tmp_path)
    bundle = tmp_path / "days"
    build_day_bundle((source,), bundle, cfg())
    output = tmp_path / "registry.json"
    with pytest.raises(ValueError, match="representative-data blocker"):
        register_protocol(bundle, (source,), cfg(), output)
    assert not output.exists() and not read_json(bundle / "days.json")["ready_for_registration"]
