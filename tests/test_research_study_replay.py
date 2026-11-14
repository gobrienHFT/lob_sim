"""Actual bounded replay/audit mechanics on explicitly synthetic receipts.

The hand-specified model tests contracts, not recovered market regimes.
No production empirical-admission rule is disabled by these tests.
"""

from copy import deepcopy
from dataclasses import replace

import pytest

from lob_sim.audit.streaming_bundle import audit_streaming_bundle
from lob_sim.config import ConfigError
from lob_sim.regime.study import core_audit_hashes
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.replay_engine import ReplayWindow, research_config
from lob_sim.research.study_replay import run_study_replay
from lob_sim.research.study_run_reader import verify_case
from lob_sim.research.study_tables import publish_tables, reduce_tables
from lob_sim.sim.run_manifest import config_snapshot
from test_hmm_policy import policy_settings
from test_research_capture_intervals import cfg
from test_research_day_views import view


@pytest.fixture(scope="module")
def bounded_pair(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("research_bounded_pair")
    _, path, entry = view(tmp_path)
    window = ReplayWindow(**entry["contract"]["window"])
    root = tmp_path / "study"
    results = []
    for index, variant in enumerate(("baseline", "hmm_observe", "hmm_policy")):
        case = {"case_id": str(index) * 64, "variant": variant}
        setting = policy_settings()
        runtime = (
            None
            if variant == "baseline"
            else replace(setting, mode="observe", policy=None)
            if variant == "hmm_observe"
            else setting
        )
        configuration = research_config(
            cfg(),
            mm_enabled=True,
            hmm=runtime,
            mm_strategy_profile="hmm_regime_mm" if variant == "hmm_policy" else "research_mm",
            sim_latency_mode="fixed",
            sim_order_latency_ms=0.0,
            sim_cancel_latency_ms=0.0,
            record_dir=root / "runs" / case["case_id"],
        )
        files, summary = run_study_replay(configuration, path, window, minimum_free_disk_bytes=0)
        tables = (
            None
            if runtime is None
            else publish_tables(
                files["summary"].parent / "clock_periods.jsonl", reduce_tables(files, summary, entry["contract"])
            )
        )
        record = {
            "case": case,
            "configuration": config_snapshot(configuration),
            "run_directory": files["summary"].parent.relative_to(root).as_posix(),
            "artifacts": {k: {"path": p.name, "sha256": file_sha256(p)} for k, p in files.items()},
            "core_audits": core_audit_hashes(files),
            "tables": tables,
            "status": "completed",
        }
        unit = {"view": entry, "contract": entry["contract"]}
        results.append((root, record, case, configuration, path, unit))
    return results


def test_actual_baseline_observe_policy_export_and_independent_audit(bounded_pair):
    baseline, observed, policy = bounded_pair
    assert baseline[1]["core_audits"] == observed[1]["core_audits"]
    for result in bounded_pair:
        tables = verify_case(*result)
        assert tables is None if result[2]["variant"] == "baseline" else tables == ()
    # The core's ordinary policy validation remains strictly single-symbol.
    with pytest.raises(ConfigError, match="single configured symbol"):
        replace(cfg(), hmm=policy[3].hmm, mm_strategy_profile="hmm_regime_mm")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["configuration"].update(sim_order_latency_ms=50.0),
        lambda r: r["case"].update(variant="hmm_policy"),
        lambda r: r["artifacts"]["hmm_model"].update(path="../hmm_model.json"),
        lambda r: r["core_audits"].update(core_event_sha256="0" * 64),
        lambda r: r.update(run_directory="../external"),
    ],
)
def test_rehashed_case_binding_cannot_change_scenario_model_paths_or_core_parity(bounded_pair, mutation):
    root, original, case, configuration, path, unit = bounded_pair[1]
    changed = deepcopy(original)
    mutation(changed)
    with pytest.raises(ValueError):
        verify_case(root, changed, case, configuration, path, unit)


def test_relocated_input_is_checked_not_trusted(bounded_pair, tmp_path):
    root, record, _, _, path, _ = bounded_pair[0]
    bundle = root / record["run_directory"]
    assert audit_streaming_bundle(bundle, input_override=path)["ok"]
    bad = tmp_path / "not_the_view.json"
    bad.write_bytes(b"{}")
    assert not audit_streaming_bundle(bundle, input_override=bad)["ok"]


def test_disk_floor_failure_preserves_partial_audits_and_no_completion(tmp_path):
    _, path, entry = view(tmp_path)
    configuration = research_config(cfg(), mm_enabled=True, record_dir=tmp_path / "failed")
    with pytest.raises(RuntimeError, match="disk floor"):
        run_study_replay(
            configuration, path, ReplayWindow(**entry["contract"]["window"]), minimum_free_disk_bytes=2**63
        )
    assert list((tmp_path / "failed").rglob("_INCOMPLETE.json"))
    assert list((tmp_path / "failed").rglob("*.partial"))
    assert not list((tmp_path / "failed").rglob("manifest.json"))
