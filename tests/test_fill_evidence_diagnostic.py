"""Hand-calculated event/quantity weighting, missingness and fill-ID oracles."""

from copy import deepcopy
from fractions import Fraction
import json

import pytest

from experiments.audit_fill_evidence import counts


def sample():
    fills = [
        {
            "price": "100",
            "qty": "0.002",
            "maker": True,
            "fill_source": "agg_trade",
            "order_id": "A",
            "order_state_at_fill": "live",
        },
        {
            "price": "200",
            "qty": "0.001",
            "maker": True,
            "fill_source": "depth_update",
            "order_id": "B",
            "order_state_at_fill": "pending_cancel",
        },
        {
            "price": "80",
            "qty": "0.001",
            "maker": False,
            "fill_source": "taker_order",
            "order_id": "C",
            "order_state_at_fill": "live",
        },
    ]
    execution = [{**fill, "fill_id": i, "event_type": "fill"} for i, fill in enumerate(fills, 1)]
    execution += [
        {
            **fill,
            "fill_id": i,
            "event_type": "markout",
            "maker": None,
            "horizon_ms": 1000,
            "status": "resolved",
            "markout": value,
        }
        for i, (fill, value) in enumerate(zip(fills, ("2", "-2", "1000")), 1)
    ]
    return fills, execution


def test_maker_sample_weighting_excludes_takers_and_counts_pending_cancel():
    report = counts(*sample(), (1000,))
    assert report["fill_populations"] == {"maker": 2, "taker": 1}
    maker = report["horizons_ms"]["1000"]["maker"]
    assert Fraction(maker["event_weighted_mean_bps_rational"]) == 50
    assert Fraction(maker["quantity_weighted_mean_bps_rational"]) == 100
    assert Fraction(maker["quantity_minus_event_mean_bps_rational"]) == 50
    assert maker["missing_rate_rational"] == "0"
    assert sum(g["fill_events"] for g in report["groups"] if g["pending_cancel"]) == 1
    assert report["interval"] is None and report["effective_sample_size"] is None


def test_missing_and_invalidated_outcomes_are_not_zero_filled():
    fills, execution = sample()
    execution = execution[:3] + [{**execution[3], "status": "invalidated", "markout": None}]
    report = counts(fills, execution, (1000,))
    maker = report["horizons_ms"]["1000"]["maker"]
    assert (maker["resolved"], maker["invalidated"], maker["unresolved"]) == (0, 1, 1)
    assert maker["missing_rate_rational"] == "1"
    assert maker["event_weighted_mean_bps_rational"] is None
    assert maker["quantity_weighted_mean_bps_rational"] is None
    assert report["maker_estimand"] == "insufficient_evidence"


@pytest.mark.parametrize("mutation", ["identity", "future", "census"])
def test_exact_fill_identity_order_and_census_cannot_be_substituted(mutation):
    fills, execution = sample()
    execution = deepcopy(execution)
    if mutation == "identity":
        execution[-1]["order_id"] = "A"
    elif mutation == "future":
        execution.insert(0, execution.pop())
    else:
        execution.pop(2)
    with pytest.raises(ValueError):
        counts(fills, execution, (1000,))


def test_sample_report_cannot_use_artifacts_changed_after_native_audit(tmp_path, monkeypatch):
    import experiments.audit_fill_evidence as module

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in (
        "manifest.json",
        "summary.csv",
        "trades.csv",
        "markouts.csv",
        "event_trace.csv",
        "hmm_model.json",
        "regime_trace.csv",
        "regime_execution.csv",
        "regime_quotes.csv",
        "regime_risk.csv",
    ):
        (bundle / name).write_text("original")
    (bundle / "summary.json").write_text(json.dumps({"hmm_execution": {"horizons_ms": [1000]}}))
    source = tmp_path / "source.ndjson"
    source.write_text("source")

    def audit_then_change(*a, **k):
        (bundle / "trades.csv").write_text("changed after audit")
        return {"ok": True}

    monkeypatch.setattr(module, "audit_streaming_bundle", audit_then_change)
    monkeypatch.setattr(module, "iter_fill_audit_rows", lambda path: iter(()))
    monkeypatch.setattr(module, "iter_execution_rows", lambda path: iter(()))
    target = tmp_path / "must_not_publish.json"
    with pytest.raises(ValueError, match="changed"):
        module.audit(bundle, source, target, "synthetic_diagnostic")
    assert not target.exists()
