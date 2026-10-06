from __future__ import annotations

import csv
import json
from dataclasses import replace
from fractions import Fraction

import pytest

from lob_sim.regime.fit import FitConfig, FitResult
from lob_sim.regime.study import run_regime_study, core_audit_hashes, format_study_report
from lob_sim.regime.synthetic import SyntheticTapeConfig, generate_synthetic_sources, synthetic_rows
from lob_sim.regime.study_periods import risk_clock_periods, compare_risk_periods, PERIOD_NS
from lob_sim.regime.study_pnl import PNL_CONTRACT, read_pnl_periods
from test_hmm_dataset import cfg, SECOND, WALL
from test_hmm_risk import row


def independent_sources(tmp_path):
    config = SyntheticTapeConfig(days=3, seconds_per_day=180)
    raw = [item for kind, item in synthetic_rows(config) if kind == "raw"]
    headers = raw[:2]
    paths = []
    for day in range(3):
        rows = []
        for value in headers + [
            v for v in raw[2:] if (v["data"]["_capture"]["recvWallNs"] - WALL) // (86_400 * SECOND) == day
        ]:
            item = json.loads(json.dumps(value))
            capture = item["data"]["_capture"]
            if value in headers:
                capture["recvWallNs"] += day * 86_400 * SECOND
            else:
                capture["recvMonotonicNs"] -= day * 182 * SECOND
            capture["recvSeq"] = len(rows)
            item["ts_local"] = capture["recvWallNs"] / SECOND
            rows.append(item)
        path = tmp_path / f"native_{day}.ndjson"
        path.write_text("".join(json.dumps(value) + "\n" for value in rows))
        paths.append(path)
    return tuple(paths)


def study_cfg():
    # Exercise the entire export/parity path without generating hundreds of
    # megabytes of timer-driven quote audit in the fast correctness suite.
    # The registered production runner retains its caller's quote cadence.
    return replace(cfg(), symbols=("BTCUSDT",), mm_strategy_profile="research_mm", mm_requote_ms=500)


@pytest.fixture(scope="module")
def completed_study(tmp_path_factory):
    root = tmp_path_factory.mktemp("registered-study")
    source_root = root / "inputs"
    generated = generate_synthetic_sources(source_root, SyntheticTapeConfig(days=3, seconds_per_day=180))
    inputs = tuple(source_root / entry["path"] for entry in generated["sources"])
    directory = root / "study"
    report = run_regime_study(
        inputs,
        directory,
        study_cfg(),
        symbol="BTCUSDT",
        fit_config=FitConfig(restarts=1, max_iterations=80),
        bootstrap_replicates=25,
    )
    return directory, report


def test_registered_native_study_executes_all_variants_and_records_pairing(completed_study):
    directory, report = completed_study
    assert report["status"] == "completed", report["failures"]
    assert report["schema_version"] == "lob_sim.hmm_regime_study.v3"
    assert not report["claim_ready"]
    assert set(report["registered_variants"]) == {"baseline", "observe", "policy", "hard_active", "cadence_250ms"}
    assert report["split"]["calibration_days"] == ["2025-01-01"]
    assert report["split"]["validation_days"] == ["2025-01-02"]
    assert report["split"]["test_days"] == ["2025-01-03"]
    assert len(report["results"]) == 5
    assert len(report["comparisons"]) == 3
    baseline, observe = report["results"][:2]
    assert baseline["core_audits"] == observe["core_audits"]
    assert baseline["measurement_source"]
    assert "policy-benefit claim" in report["claim_reason"]
    registry = json.loads((directory / "registry.json").read_text())
    assert registry["frozen"] and registry["registry_sha256"] == report["registry_sha256"]
    for variant in registry["variants"]:
        contract = variant["config"]["bootstrap"]["execution_outcomes"]
        assert contract["schema_version"] == "lob_sim.hmm_clock_outcome_contract.v1"
        assert "signed_markout_100ms" in contract["ratio_units"]
        assert "not marked net PnL" in contract["scope"]
        assert variant["config"]["bootstrap"]["marked_pnl"] == PNL_CONTRACT
    for label in ("primary", "cadence_250ms"):
        fitted = report["fit_reports"][label]
        assert len(fitted["attempts"]) == 4
        assert fitted["training"]["days"] == ["2025-01-01"]
        assert fitted["validation"]["days"] == ["2025-01-02"]
    for comparison in report["comparisons"]:
        for sensitivities in comparison["risk_clock_comparison"].values():
            assert set(sensitivities) == {"30", "5", "60"}
            assert all(value["interval"] is None for value in sensitivities.values())
        outcomes = comparison["execution_clock_comparison"]
        assert "fees_quote" in outcomes["metrics"]
        assert "pending_cancel_fill_fraction" in outcomes["metrics"]
        for metric in outcomes["metrics"].values():
            assert set(metric["sensitivities"]) == {"30", "5", "60"}
            assert all(value["interval"] is None for value in metric["sensitivities"].values())
        pnl = comparison["pnl_clock_comparison"]
        assert pnl["contract"] == PNL_CONTRACT
        assert set(pnl["metrics"]) == {"gross_marked_delta_quote_rational", "net_marked_delta_quote_rational"}
        for sensitivities in pnl["metrics"].values():
            assert set(sensitivities) == {"30", "5", "60"}
            assert all(value["interval"] is None for value in sensitivities.values())
    assert "diagnostic" in format_study_report(report)
    assert "mean eligible-minute net PnL delta=" in format_study_report(report)
    assert "95% 30-minute-block interval=None" in format_study_report(report)


def test_real_run_bundle_is_streamed_and_contains_reproduction_parents(completed_study):
    directory, report = completed_study
    for result in report["results"]:
        run = directory / result["run_dir"]
        assert not (run / "_INCOMPLETE.json").exists()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["simulation_export"]["memory_bounded_by_tape_duration"]
        assert {"trades", "event_trace", "markouts", "manifest"}.issubset(result["artifact_sha256"])
        if result["variant"] != "baseline":
            assert summary["hmm_economics"]["memory_bounded_by_tape_duration"]
            assert (run / "regime_risk.csv").exists()
            from lob_sim.replay.inspection import file_sha256
            from lob_sim.regime.validation import identity

            path = directory / result["clock_outcomes_path"]
            assert path.name == "clock_outcomes.json"
            assert file_sha256(path) == result["artifact_sha256"]["clock_outcomes"]
            document = json.loads(path.read_text())
            digest = document.pop("report_sha256")
            assert digest == identity(document)
            assert document["variant_id"] == result["variant_id"]
            assert document["parents"] == summary["hmm_economics"]["parents"]
            assert document["source_sha256"] == result["source_sha256"]
            assert len(document["periods"]) == 2  # Initial partial UTC minute is not extrapolated.
            for period in document["periods"]:
                assert period["utc_start_ns"] >= document["wall_span"]["first_wall_ns"]
                assert period["utc_start_ns"] + period["period_ns"] <= document["wall_span"]["last_wall_ns"]
            pnl_path = directory / result["clock_pnl_path"]
            assert pnl_path.name == "clock_pnl.json"
            assert file_sha256(pnl_path) == result["artifact_sha256"]["clock_pnl"]
            pnl = json.loads(pnl_path.read_text())
            digest = pnl.pop("report_sha256")
            assert digest == identity(pnl)
            for key in ("source_sha256", "variant_id", "wall_span", "parents", "model_sha256", "grid"):
                assert pnl[key] == document[key]
            assert pnl["contract"] == PNL_CONTRACT
            assert not pnl["claim_ready"]
            assert [p["utc_start_ns"] for p in pnl["periods"]] == [p["utc_start_ns"] for p in document["periods"]]
            for equity, execution in zip(pnl["periods"], document["periods"], strict=True):
                if equity["fees_delta_quote_rational"] is not None:
                    assert Fraction(equity["fees_delta_quote_rational"]) == Fraction(
                        execution["additive"]["fees_quote"]
                    )


def test_native_pnl_periods_match_independent_batch_fill_prefix_valuation(completed_study):
    from lob_sim.sim.export import iter_fill_audit_rows

    directory, report = completed_study
    for result in report["results"]:
        if result["variant"] == "baseline":
            continue
        run = directory / result["run_dir"]
        document = json.loads((run / "clock_pnl.json").read_text())
        with (run / "regime_risk.csv").open(newline="", encoding="utf-8") as handle:
            boundaries = list(csv.DictReader(handle))
        fills = list(iter_fill_audit_rows(run / "trades.csv"))
        quantum = Fraction(1)
        for value in document["grid"]:
            quantum *= Fraction(value)

        def equity_left_limit(wall_time):
            logical = wall_time - document["wall_span"]["wall_offset_ns"]
            earlier = [row for row in boundaries if int(row["logical_ns"]) < logical]
            if not earlier:
                return None, None, None
            anchor = earlier[-1]
            prefix = fills[: int(anchor["fill_audit_count"])]
            cash = sum(((1 if f["side"] == "ask" else -1) * Fraction(f["notional"]) for f in prefix), Fraction(0))
            fees = sum((Fraction(f["fee"]) for f in prefix), Fraction(0))
            inventory = int(anchor["inventory_lots"])
            assert sum((1 if f["side"] == "bid" else -1) * Fraction(f["qty"]) for f in prefix) == inventory * Fraction(
                document["grid"][1]
            )
            if inventory and (
                not anchor["mid_twice_tick"] or not anchor["mark_until_ns"] or int(anchor["mark_until_ns"]) < logical
            ):
                return None, None, fees
            gross = cash + inventory * Fraction(anchor["mid_twice_tick"] or "0") / 2 * quantum
            return gross, gross - fees, fees

        for period in document["periods"]:
            a = equity_left_limit(period["utc_start_ns"])
            b = equity_left_limit(period["utc_start_ns"] + PERIOD_NS)
            if period["excluded_reason"] is None:
                assert Fraction(period["gross_marked_delta_quote_rational"]) == b[0] - a[0]
                assert Fraction(period["net_marked_delta_quote_rational"]) == b[1] - a[1]
            else:
                assert period["gross_marked_delta_quote_rational"] is period["net_marked_delta_quote_rational"] is None
            if a[2] is not None and b[2] is not None:
                assert Fraction(period["fees_delta_quote_rational"]) == b[2] - a[2]


def test_pnl_parent_reconciliation_and_publication_no_clobber(completed_study):
    from lob_sim.regime.dataset import publish_json

    directory, report = completed_study
    observe = next(r for r in report["results"] if r["variant"] == "observe")
    run = directory / observe["run_dir"]
    summary = json.loads((run / "summary.json").read_text())
    pnl_path = run / "clock_pnl.json"
    document = json.loads(pnl_path.read_text())
    # These metadata fields are passed unchanged to the sampler; its result
    # must still reconcile the complete independent economic parent, not just
    # agree with a caller's fill count or a convenient final PnL cell.
    changed = {**summary["hmm_economics"], "gap_bridge_count": summary["hmm_economics"]["gap_bridge_count"] + 1}
    with pytest.raises(ValueError, match="parent/clock reconciliation"):
        read_pnl_periods(
            run / "regime_risk.csv",
            run / "trades.csv",
            run / "regime_execution.csv",
            risk_summary=summary["hmm_risk"],
            execution_summary=summary["hmm_execution"],
            economic_summary=changed,
            risk_periods=document["periods"],
            span=document["wall_span"],
        )
    before = pnl_path.read_bytes()
    with pytest.raises(FileExistsError):
        publish_json(pnl_path, {"replacement": True})
    assert pnl_path.read_bytes() == before


def test_failed_fit_variants_are_not_hidden_and_registry_precedes_input_read(tmp_path, monkeypatch):
    inputs = independent_sources(tmp_path)
    directory = tmp_path / "failed"
    import lob_sim.regime.study as module

    original = module.extract_feature_collection

    def guarded(*args, **kwargs):
        registry = json.loads((directory / "registry.json").read_text())
        assert registry["frozen"] and len(registry["variants"]) == 5
        assert all(v["config"]["bootstrap"]["marked_pnl"] == PNL_CONTRACT for v in registry["variants"])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "extract_feature_collection", guarded)
    monkeypatch.setattr(
        module, "fit_candidates", lambda *args: FitResult(None, '{"status":"no_valid_candidate","attempts":[]}')
    )
    report = run_regime_study(inputs, directory, study_cfg(), symbol="BTCUSDT", bootstrap_replicates=10)
    assert report["status"] == "incomplete"
    assert len([failure for failure in report["failures"] if failure["stage"] == "fit"]) == 2
    assert {result["variant"] for result in report["results"]} == set(report["registered_variants"])
    assert [result["status"] for result in report["results"]] == ["completed"] + ["unavailable"] * 4
    assert report["comparisons"] == []


def test_formatter_keeps_historical_reports_without_inventing_clock_pnl():
    report = {
        "schema_version": "lob_sim.hmm_regime_study.v2",
        "status": "completed",
        "registry_sha256": "a" * 64,
        "claim_reason": "historical diagnostic",
        "results": [],
        "failures": [],
        "comparisons": [{"variant": "policy", "baseline": "baseline"}],
    }
    text = format_study_report(report)
    assert "marked-PnL analysis was not included in this report version" in text
    assert "mean eligible-minute net PnL delta=" not in text


@pytest.mark.parametrize(
    "changes",
    [
        {"sim_latency_mode": "empirical", "sim_latency_samples_ms": (1.0, 5.0)},
        {"mm_strategy_profile": "baseline"},
        {"mm_enabled": False},
        {"symbols": ("BTCUSDT", "ETHUSDT")},
    ],
)
def test_unpaired_execution_configs_are_rejected_before_creating_outputs(tmp_path, changes):
    with pytest.raises(ValueError):
        run_regime_study((), tmp_path / "never", replace(study_cfg(), **changes), symbol="BTCUSDT")
    assert not (tmp_path / "never").exists()


def test_core_hash_can_strip_diagnostics_but_not_trading_differences(tmp_path):
    path = tmp_path / "events.csv"
    trade, mark = tmp_path / "trades.csv", tmp_path / "markouts.csv"
    trade.write_text("trades unchanged")
    mark.write_text("markouts unchanged")
    files = {"event_trace": path, "trades": trade, "markouts": mark}

    def write(details, quantity="2"):
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("details", "qty_lots"))
            writer.writeheader()
            writer.writerow({"details": json.dumps(details), "qty_lots": quantity})

    write({"core": 42})
    expected = core_audit_hashes(files)
    write({"core": 42, "hmm": {"posterior": [1, 0]}, "hmm_request_id": "x"})
    assert core_audit_hashes(files) == expected
    write({"core": 43, "hmm": {"posterior": [1, 0]}})
    assert core_audit_hashes(files) != expected
    write({"core": 42}, "3")
    assert core_audit_hashes(files) != expected


def span(first=0, last=120):
    return {
        "wall_offset_ns": WALL,
        "clock_basis": "receive_nanoseconds",
        "first_wall_ns": WALL + first * SECOND,
        "last_wall_ns": WALL + last * SECOND,
    }


def boundary(time, inventory, *, epoch=0, until=1000, valid=True):
    item = row(time * SECOND, inventory=inventory, regime_life=until * SECOND, mark_life=until * SECOND)
    item["stage"]["epochs"] = [epoch, 0, 0]
    item["stage"]["status"] = "VALID" if valid else "INVALID_BOOK"
    return item


def test_integer_period_integrals_match_independent_dense_clock_oracle():
    rows = [boundary(0, 2), boundary(25, -4), boundary(60, 1), boundary(100, 3), boundary(120, 0)]
    actual = list(risk_clock_periods(iter(rows), span()))
    assert len(actual) == 2
    for index, period in enumerate(actual):
        dense = [
            next(value["inventory_lots"] for value in reversed(rows) if value["logical_ns"] <= time * SECOND)
            for time in range(index * 60, index * 60 + 60)
        ]
        assert period["absolute_inventory_lots_ns"] == sum(abs(value) for value in dense) * SECOND
        assert period["squared_inventory_lots_ns"] == sum(value * value for value in dense) * SECOND
        assert period["mean_absolute_inventory_lots"] == pytest.approx(sum(abs(value) for value in dense) / 60)
        assert period["inventory_variance_lots_squared"] == pytest.approx(
            sum(value * value for value in dense) / 60 - (sum(dense) / 60) ** 2
        )
        assert period["excluded_reason"] is None
        assert period["duration_ns"] == period["valid_ns"] == PERIOD_NS


@pytest.mark.parametrize("fault", ["epoch", "stale", "invalid"])
def test_period_invalidity_is_not_interpolated_away(fault):
    rows = [
        boundary(0, 2),
        boundary(45, -2, epoch=1 if fault == "epoch" else 0, valid=fault != "invalid"),
        boundary(120, 0),
    ]
    if fault == "stale":
        rows[0]["mark_until_ns"] = 30 * SECOND
    actual = list(risk_clock_periods(iter(rows), span()))
    assert actual[0]["excluded_reason"]
    assert actual[0]["mean_reserved_notional_quote"] is None
    paired = compare_risk_periods(actual, actual, "a" * 64, replicates=10)
    assert paired["mean_absolute_inventory_lots"]["30"]["interval"] is None


def test_partial_edges_are_omitted_and_later_actions_do_not_extend_capture():
    actual = list(risk_clock_periods(iter([boundary(30, 1), boundary(300, 2)]), span(30, 150)))
    assert len(actual) == 1
    assert actual[0]["utc_start_ns"] == WALL + 60 * SECOND
    assert actual[0]["duration_ns"] == 60 * SECOND


def test_moving_clock_and_mismatched_pair_grid_fail_closed():
    with pytest.raises(ValueError, match="stable"):
        list(risk_clock_periods(iter([]), {**span(), "wall_offset_ns": None}))
    actual = list(risk_clock_periods(iter([boundary(0, 1), boundary(120, 2)]), span()))
    with pytest.raises(ValueError, match="same UTC"):
        compare_risk_periods(actual, actual[1:], "a" * 64)
