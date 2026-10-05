"""Independent cash-flow oracle, missing-mark and serialized-parent regressions."""

from __future__ import annotations

import copy
import json
import random
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from lob_sim.regime.diagnostics import inspect_run
from lob_sim.regime.economics import EconomicLedger, reconstruct_economics
from lob_sim.regime.risk import RegimeRiskAudit, iter_risk_rows
from lob_sim.regime.validation import canonical_json
from lob_sim.sim.export import iter_fill_audit_rows
from lob_sim.sim.runner import run_bounded_simulation
from test_hmm_dataset import cfg, tape
from test_hmm_execution import execution_tape
from test_hmm_observation import settings
from test_hmm_risk import row, write_rows


def boundary(time, inventory=0, *, mid=200, state=0, mark_life=100, k=2):
    result = row(time, k=k, inventory=inventory, state=state, active=state, mark_life=mark_life if mid else 0)
    result.update(tick_size="1", step_size="1", contract_multiplier="1", mid_twice_tick=mid)
    return result


def fill(subject, time, side, ticks, lots, *, fee="0", state=0, currency="USDT"):
    trade = {
        "symbol": "BTCUSDT",
        "side": side,
        "qty": str(lots),
        "price": str(ticks),
        "contract_multiplier": "1",
        "notional": str(ticks * lots),
        "fee": fee,
        "fee_currency": currency,
        "fill_source": "agg_trade",
        "order_id": "order",
    }
    execution = {
        **trade,
        "logical_ns": time,
        "qty_lots": lots,
        "pre_fill": boundary(time, state=state, k=len(subject.labels) - 3)["stage"],
    }
    subject.on_fill(trade, execution, time)
    return trade, execution


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("k", [2, 3, 4, 5])
def test_exact_ledger_matches_separate_batch_cash_flow_oracle(seed, k):
    rng = random.Random(seed)
    subject = EconomicLedger(["1", "1", "1"], k)
    subject.on_boundary(boundary(0))
    trades = []
    inventory = 0
    for time in range(1, 201):
        side, ticks, lots, state = (
            rng.choice(["bid", "ask"]),
            rng.randrange(70, 131),
            rng.randrange(1, 8),
            rng.randrange(k),
        )
        fee = str(Decimal(rng.randrange(-50, 101)) / 100)
        trade, _ = fill(subject, time, side, ticks, lots, fee=fee, state=state)
        trades.append(trade)
        inventory += lots if side == "bid" else -lots
        subject.on_boundary(boundary(time, inventory, mid=2 * rng.randrange(70, 131), state=state, k=k))
    # This oracle sums immutable transactions after the fact; it does not use
    # the online ledger's transition method or the core's average-cost logic.
    cash = sum((1 if t["side"] == "ask" else -1) * int(t["notional"]) for t in trades)
    fees = sum((Fraction(t["fee"]) for t in trades), Fraction(0))
    report = subject.summary()
    assert report["cash_tick_lots"] == cash
    assert Fraction(report["fees_quote_rational"]) == fees
    assert report["turnover_tick_lots"] == sum(int(t["notional"]) for t in trades)
    assert report["inventory_lots"] == inventory
    assert Fraction(report["net_marked_pnl_quote_rational"]) == cash - fees + inventory * Fraction(
        subject.previous["mid_twice_tick"], 2
    )
    for cells in report["conditioned"].values():
        assert sum(c["cash_tick_lots"] for c in cells.values()) == cash
        assert sum(c["turnover_tick_lots"] for c in cells.values()) == report["turnover_tick_lots"]
        assert sum((Fraction(c["fees_quote_rational"]) for c in cells.values()), Fraction(0)) == fees
        assert sum((Fraction(c["observed_net_delta_quote_rational"]) for c in cells.values()), Fraction(0)) == Fraction(
            report["net_marked_pnl_quote_rational"]
        )
        assert sum((Fraction(c["drawdown_extension_quote_rational"]) for c in cells.values()), Fraction(0)) == Fraction(
            report["observed_max_drawdown_quote_rational"]
        )


def test_partial_closes_and_reversals_do_not_depend_on_average_cost_rounding():
    subject = EconomicLedger(["1", "1", "1"], 2)
    subject.on_boundary(boundary(0))
    for time, side, price, qty, inventory in (
        (1, "bid", 100, 3, 3),
        (2, "ask", 110, 1, 2),
        (3, "ask", 90, 4, -2),
        (4, "bid", 80, 1, -1),
        (5, "bid", 120, 1, 0),
    ):
        fill(subject, time, side, price, qty, fee="0.125")
        subject.on_boundary(boundary(time, inventory, mid=2 * price))
    assert subject.summary()["gross_marked_pnl_quote_rational"] == "-30"
    assert subject.summary()["net_marked_pnl_quote_rational"] == "-245/8"
    assert subject.summary()["fees_quote_rational"] == "5/8"


def test_gap_never_erases_peak_or_assigns_unknown_path_to_a_later_state():
    subject = EconomicLedger(["1", "1", "1"], 2)
    subject.on_boundary(boundary(0))
    fill(subject, 1, "bid", 100, 1, fee="0.1")
    subject.on_boundary(boundary(1, 1))
    subject.on_boundary(boundary(2, 1, mid=240, state=1))  # Known peak +19.9.
    subject.on_boundary(boundary(3, 1, mid=None, state=1))
    assert subject.summary()["net_marked_pnl_quote_rational"] is None
    assert subject.summary()["last_observed_net_equity_quote_rational"] == "199/10"
    subject.on_boundary(boundary(5, 1, mid=160, state=1))  # Known loss -20.1.
    fill(subject, 6, "ask", 80, 1, fee="-0.01", state=1)
    subject.on_boundary(boundary(6, 0, mid=None, state=1))  # Flat is valued without a mark.
    report = subject.summary()
    assert report["unpriced_inventory_ns"] == 2
    assert report["gap_bridge_count"] == 1
    assert report["observed_max_drawdown_quote_rational"] == "40"
    assert report["net_marked_pnl_quote_rational"] == "-2009/100"
    assert report["paid_fees_quote_rational"] == "1/10" and report["rebates_quote_rational"] == "1/100"
    for cells in report["conditioned"].values():
        assert cells["UNATTRIBUTED"]["observed_net_delta_quote_rational"] == "-40"
        assert cells["STATE_1"]["max_observed_drawdown_quote_rational"] == "40"
        assert cells["STATE_0"]["observed_net_delta_quote_rational"] == "199/10"  # Prior holding label, not new state.


def test_expiry_without_an_intermediate_row_still_marks_gap_and_exact_unpriced_duration():
    subject = EconomicLedger(["1", "1", "1"], 2)
    subject.on_boundary(boundary(0))
    fill(subject, 1, "bid", 100, 1)
    subject.on_boundary(boundary(1, 1, mark_life=3))
    subject.on_boundary(boundary(10, 1, mid=180, state=1))
    assert subject.summary()["unpriced_inventory_ns"] == 6
    assert subject.summary()["conditioned"]["active"]["UNATTRIBUTED"]["observed_net_delta_quote_rational"] == "-10"


def test_flat_needs_no_mark_but_open_inventory_cannot_be_marked_as_zero():
    subject = EconomicLedger(["1", "1", "1"], 2)
    subject.on_boundary(boundary(0, mid=None))
    assert subject.summary()["net_marked_pnl_quote_rational"] == "0"
    fill(subject, 1, "bid", 100, 1, fee="0.25")
    subject.on_boundary(boundary(1, 1, mid=None))
    report = subject.summary()
    assert report["net_cash_quote_rational"] == "-401/4"
    assert report["net_marked_pnl_quote_rational"] is None
    assert report["last_observed_net_equity_quote_rational"] == "0"
    assert report["equity_known_boundary_count"] == 1


def test_missing_regime_does_not_hide_valid_book_equity():
    subject = EconomicLedger(["1", "1", "1"], 2)
    subject.on_boundary(boundary(0))
    fill(subject, 1, "bid", 100, 1)
    current = boundary(1, 1)
    current["stage"]["status"] = "INVALID_TRADES"
    subject.on_boundary(current)
    following = copy.deepcopy(current)
    following.update(logical_ns=2, mid_twice_tick=180)
    subject.on_boundary(following)
    assert subject.summary()["net_marked_pnl_quote_rational"] == "-10"
    assert subject.summary()["conditioned"]["active"]["UNAVAILABLE"]["observed_net_delta_quote_rational"] == "-10"


@pytest.mark.parametrize(
    "field,value",
    [
        ("fee_currency", "EUR"),
        ("price", "100.5"),
        ("qty", "0.5"),
        ("notional", "123"),
        ("contract_multiplier", "2"),
        ("side", "buy"),
        ("fee", "NaN"),
    ],
)
def test_invalid_fill_validation_is_atomic(field, value):
    subject = EconomicLedger(["1", "1", "1"], 2)
    trade, execution = fill(subject, 1, "bid", 100, 1)
    before = subject.summary()
    trade[field] = value
    with pytest.raises(ValueError):
        subject.on_fill(trade, execution, 2)
    assert subject.summary() == before


def test_future_execution_and_inventory_mismatch_fail_closed():
    subject = EconomicLedger(["1", "1", "1"], 2)
    trade, execution = fill(subject, 1, "bid", 100, 1)
    execution["logical_ns"] = 3
    with pytest.raises(ValueError, match="frozen execution"):
        subject.on_fill(trade, execution, 2)
    with pytest.raises(ValueError, match="conserve"):
        subject.on_boundary(boundary(2, 0))


def test_rational_economics_is_decimal_context_independent_and_bounded():
    subject = EconomicLedger(["1", "1", "1"], 5)
    subject.on_boundary(boundary(0))
    for time in range(1, 4001):
        fill(subject, time, "bid" if time % 2 else "ask", 100, 1, fee="0.001", state=time % 5)
        subject.on_boundary(boundary(time, time % 2, state=time % 5, k=5))
    expected = subject.summary()
    with localcontext() as context:
        context.prec = 2
        assert subject.summary() == expected
    assert len(subject.cells["active"]) == 8
    assert len(canonical_json(expected)) < 15_000
    assert "trades" not in vars(subject)


@pytest.fixture(scope="module")
def economic_bundle(tmp_path_factory):
    directory = tmp_path_factory.mktemp("economic_bundle")
    files, summary = run_bounded_simulation(
        replace(cfg(), hmm=settings(), record_dir=directory / "runs", mm_half_spread_bps=Decimal(0)),
        execution_tape(directory / "input.ndjson"),
    )
    return files, summary


def reconstruct(files, summary, *, risk_path=None, risk_summary=None):
    return reconstruct_economics(
        risk_path or files["regime_risk"],
        files["trades"],
        files["regime_execution"],
        risk_summary=risk_summary or summary["hmm_risk"],
        execution_summary=summary["hmm_execution"],
        fill_count=summary["fill_count"],
        fill_sha256=summary["audit_retention"]["fill_audit_sha256"],
    )


def test_completed_bundle_economics_matches_independent_transactions_and_core(economic_bundle):
    files, summary = economic_bundle
    report = reconstruct(files, summary)
    assert report == summary["hmm_economics"]
    trades = list(iter_fill_audit_rows(files["trades"]))
    cash = sum(((1 if t["side"] == "ask" else -1) * Fraction(t["notional"]) for t in trades), Fraction(0))
    fees = sum((Fraction(t["fee"]) for t in trades), Fraction(0))
    assert Fraction(report["cash_quote_rational"]) == cash
    assert Fraction(report["fees_quote_rational"]) == fees
    assert float(Fraction(report["net_marked_pnl_quote_rational"])) == pytest.approx(summary["total_pnl"])
    assert "Reconciled single-symbol scenario economics" in inspect_run(files["manifest"].parent)


@pytest.mark.parametrize("mutation", ["prefix", "inventory", "count"])
def test_self_consistent_risk_forgery_cannot_change_economic_ledger(economic_bundle, tmp_path, mutation):
    files, summary = economic_bundle
    rows = list(iter_risk_rows(files["regime_risk"]))
    target = next(r for r in rows if r["reason"] == "fill_accounted")
    if mutation == "prefix":
        # Preserve standalone same-count consistency while forging a prefix.
        count = target["fill_audit_count"]
        for r in rows:
            if r["fill_audit_count"] == count:
                r["fill_audit_sha256"] = "f" * 64
    elif mutation == "inventory":
        target["inventory_lots"] += 1
    else:
        target["fill_audit_count"] += 1
        # This creates a regression unless later same-count rows are changed.
        for r in rows[rows.index(target) + 1 :]:
            if r["fill_audit_count"] < target["fill_audit_count"]:
                r["fill_audit_count"] = target["fill_audit_count"]
                r["fill_audit_sha256"] = target["fill_audit_sha256"]
    forged = RegimeRiskAudit(summary["hmm_risk"]["model_sha256"], "BTCUSDT", 2)
    # Count forgery can also fail the risk stream's stricter prefix check.
    if mutation == "count":
        with pytest.raises(ValueError):
            for r in rows:
                forged.observe(r)
        return
    for r in rows:
        forged.observe(r)
    path = tmp_path / "forged.csv"
    write_rows(path, rows)
    with pytest.raises(ValueError, match="economic"):
        reconstruct(files, summary, risk_path=path, risk_summary=forged.summary())


def test_report_rejects_rehashed_economic_summary(economic_bundle, tmp_path):
    import shutil

    files, summary = economic_bundle
    copied = tmp_path / "run"
    shutil.copytree(files["manifest"].parent, copied)
    changed = copy.deepcopy(summary)
    changed["hmm_economics"]["fees_quote_rational"] = "0"
    (copied / "summary.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="economic summary"):
        inspect_run(copied)


def two_symbol_tape(path):
    execution_tape(path)
    original = [json.loads(line) for line in path.read_text().splitlines()]
    records = []
    for record in original:
        records.append(record)
        if record["symbol"] != "BTCUSDT":
            continue
        other = copy.deepcopy(record)
        other["symbol"] = "ETHUSDT"
        data = other["data"]
        if other["type"] == "exchangeInfo":
            # Keep the same tick-grid geometry at a different price/quantum.
            data["tickSize"] = str(Decimal(data["tickSize"]) * 2)
        for key in ("bids", "asks", "b", "a"):
            for level in data.get(key, []):
                level[0] = str(Decimal(level[0]) * 2)
        if "p" in data:
            data["p"] = str(Decimal(data["p"]) * 2)
        records.append(other)
    for seq, record in enumerate(records):
        record["data"]["_capture"]["recvSeq"] = seq
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def test_single_symbol_report_does_not_inherit_other_symbol_cash_or_global_pnl(tmp_path):
    path = two_symbol_tape(tmp_path / "two_symbols.ndjson")
    files, summary = run_bounded_simulation(
        replace(
            cfg(),
            symbols=("BTCUSDT", "ETHUSDT"),
            hmm=settings(),
            record_dir=tmp_path / "runs",
            mm_half_spread_bps=Decimal(0),
        ),
        path,
    )
    trades = list(iter_fill_audit_rows(files["trades"]))
    own = [t for t in trades if t["symbol"] == "BTCUSDT"]
    assert own and len(own) < len(trades)
    report = reconstruct(files, summary)
    cash = sum(((1 if t["side"] == "ask" else -1) * Fraction(t["notional"]) for t in own), Fraction(0))
    fees = sum((Fraction(t["fee"]) for t in own), Fraction(0))
    assert Fraction(report["cash_quote_rational"]) == cash
    assert Fraction(report["fees_quote_rational"]) == fees
    assert report["fill_count"] == len(own)
    assert report["parents"]["global_fill_count"] == len(trades)
    assert float(Fraction(report["net_marked_pnl_quote_rational"])) != pytest.approx(summary["total_pnl"])


def test_empty_execution_stream_still_has_known_flat_economics(tmp_path):
    files, summary = run_bounded_simulation(
        replace(cfg(), hmm=settings(), record_dir=tmp_path / "runs"), tape(tmp_path / "input.ndjson")
    )
    report = reconstruct(files, summary)
    assert report["fill_count"] == 0
    assert report["net_marked_pnl_quote_rational"] == "0"
    assert report["observed_max_drawdown_quote_rational"] == "0"
    assert report["quote_currency"] is None


def test_native_open_position_after_book_disconnect_is_not_falsely_valued(tmp_path):
    path = execution_tape(tmp_path / "gap.ndjson")
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records = [r for r in records if r["type"] != "aggTrade" or r["data"]["m"]]
    gap = copy.deepcopy(records[-1])
    gap["symbol"] = "BTCUSDT"
    gap["data"].update(event="disconnect", route="public")
    gap["data"]["_capture"].update(route="public", recvMonotonicNs=13_500_000_000)
    gap["data"]["_capture"]["recvWallNs"] -= 500_000_000
    gap["ts_local"] -= 0.5
    records.insert(-1, gap)
    for seq, record in enumerate(records):
        record["data"]["_capture"]["recvSeq"] = seq
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    files, summary = run_bounded_simulation(
        replace(cfg(), hmm=settings(), record_dir=tmp_path / "runs", mm_half_spread_bps=Decimal(0)), path
    )
    report = reconstruct(files, summary)
    assert report["fill_count"] and report["inventory_lots"]
    assert report["net_marked_pnl_quote_rational"] is None
    assert report["gross_marked_pnl_quote_rational"] is None
    assert report["unpriced_inventory_ns"] == 500_000_000


def test_economic_verification_failure_cannot_finalize_bundle(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("economic parent verification failed")

    monkeypatch.setattr("lob_sim.regime.economics.reconstruct_economics", fail)
    with pytest.raises(ValueError, match="economic parent"):
        run_bounded_simulation(
            replace(cfg(), hmm=settings(), record_dir=tmp_path / "runs"), tape(tmp_path / "input.ndjson")
        )
    incomplete = list((tmp_path / "runs").rglob("_INCOMPLETE.json"))
    assert len(incomplete) == 1
    assert not (incomplete[0].parent / "manifest.json").exists()
    assert not (incomplete[0].parent / "summary.json").exists()
