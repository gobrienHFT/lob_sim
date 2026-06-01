from __future__ import annotations

import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.book.types import AggTradeEvent
from lob_sim.config import Config, load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.mm_strategy import QuoteTarget, StrategyDecision
from lob_sim.sim.orders import Order


def _build_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> Config:
    defaults = {
        "RECORD_DIR": str(tmp_path),
        "RECORD_GZIP": "0",
        "LOG_LEVEL": "ERROR",
        "RESYNC_ON_GAP": "1",
        "SIM_ORDER_LATENCY_MS": "0",
        "SIM_CANCEL_LATENCY_MS": "0",
        "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
        "MM_REQUOTE_MS": "250",
        "MM_ORDER_QTY": "0.001",
        "MM_MAX_POSITION": "1",
        "MM_HALF_SPREAD_BPS": "0.05",
        "MM_SKEW_BPS_PER_UNIT": "0",
        "MM_QUEUE_REPOST_LOTS": "2",
        "FEES_MAKER_BPS": "0",
        "FEES_TAKER_BPS": "0",
    }
    defaults.update({key: str(value) for key, value in overrides.items()})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return load_config(".env.example")


def _write_records(path: Path, records: list[NDJSONRecord]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def _qty(lots: int) -> str:
    return str(Decimal(lots) * Decimal("0.001"))


def _generated_records(seed: int, *, with_gap: bool = False) -> list[NDJSONRecord]:
    rng = random.Random(seed)
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001", "venue": "BINANCE_USDM"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.010")], [("100.2", "0.010")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 100, "pu": 94, "b": [["100.0", "0.010"]], "a": [["100.2", "0.010"]]},
        ),
    ]
    update_id = 100
    bid_lots = 10
    ask_lots = 10
    for index in range(1, 18):
        ts = 2.0 + index * 0.1
        prev = update_id + 9 if with_gap and index == 8 else update_id
        update_id += 1
        bid_lots = max(1, bid_lots + rng.choice([-2, -1, 0, 1, 2]))
        ask_lots = max(1, ask_lots + rng.choice([-2, -1, 0, 1, 2]))
        records.append(
            NDJSONRecord(
                ts_local=ts,
                symbol="BTCUSDT",
                type="depthUpdate",
                data={
                    "U": update_id,
                    "u": update_id,
                    "pu": prev,
                    "b": [["100.0", _qty(bid_lots)]],
                    "a": [["100.2", _qty(ask_lots)]],
                },
            )
        )
        if index % 4 == 0:
            records.append(
                NDJSONRecord(
                    ts_local=ts + 0.025,
                    symbol="BTCUSDT",
                    type="aggTrade",
                    data={"p": "100.0" if rng.random() < 0.5 else "100.2", "q": "0.002", "m": rng.random() < 0.5},
                )
            )
    return records


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_no_negative_lots(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if ("lots" in str(key) or str(key).endswith("_count")) and isinstance(child, (int, float, str, Decimal)):
                assert Decimal(str(child)) >= 0
            _assert_no_negative_lots(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_negative_lots(child)


def _run_generated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seed: int,
    config_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], SimulationEngine]:
    replay_path = _write_records(tmp_path / f"generated_{seed}.ndjson", _generated_records(seed))
    cfg = _build_config(monkeypatch, tmp_path, **(config_overrides or {}))
    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)
    return summary, engine.event_trace, engine


@pytest.mark.parametrize("seed", [3, 7, 11])
def test_seeded_generated_streams_respect_lot_and_lifecycle_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    summary, event_trace, _engine = _run_generated(tmp_path, monkeypatch, seed=seed)
    lifecycle = summary["order_lifecycle_counts"]

    assert all(isinstance(value, int) and value >= 0 for value in lifecycle.values())
    assert lifecycle["arrival_scheduled"] >= lifecycle["arrived"]
    assert lifecycle["arrived"] == summary["quote_count"]
    assert lifecycle["rested_after_arrival"] <= lifecycle["arrived"]
    assert lifecycle["immediate_fill_arrivals"] <= lifecycle["arrived"]
    assert lifecycle["expired_unfilled_arrivals"] <= lifecycle["arrived"]
    assert lifecycle["cancel_acknowledged"] <= lifecycle["cancel_requested"]
    assert lifecycle["self_trade_prevented"] <= lifecycle["arrived"]
    assert summary["fill_count"] == len(summary["fills"])
    assert summary["fill_count"] == sum(summary["fill_source_counts"].values())
    assert 0 <= summary["quote_fill_probability"] <= 1
    assert summary["fills_per_quote_request"] >= 0
    assert summary["fills_per_arrived_order"] >= 0
    assert "fill_rate" not in summary

    unique_filled_order_ids = {fill["order_id"] for fill in summary["fills"]}
    expected_probability = len(unique_filled_order_ids) / lifecycle["arrived"] if lifecycle["arrived"] else 0.0
    expected_per_quote = summary["fill_count"] / summary["quote_count"] if summary["quote_count"] else 0.0
    assert summary["quote_fill_probability"] == pytest.approx(expected_probability)
    assert summary["fills_per_quote_request"] == pytest.approx(expected_per_quote)

    _assert_no_negative_lots(summary)
    _assert_no_negative_lots(event_trace)


def test_seeded_generated_stream_summary_and_trace_hashes_are_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_summary, first_trace, _first_engine = _run_generated(tmp_path / "first", monkeypatch, seed=17)
    second_summary, second_trace, _second_engine = _run_generated(tmp_path / "second", monkeypatch, seed=17)

    assert _canonical_hash(first_summary) == _canonical_hash(second_summary)
    assert _canonical_hash(first_trace) == _canonical_hash(second_trace)


def test_seeded_gap_stream_records_gap_without_applying_bad_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = _write_records(tmp_path / "generated_gap.ndjson", _generated_records(23, with_gap=True))
    cfg = _build_config(monkeypatch, tmp_path, MM_ENABLED="0", RESYNC_ON_GAP="0")
    engine = SimulationEngine(cfg)
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["event_counts"]["book_gap_count"] >= 1
    assert summary["book_gap_count_by_symbol"] == {"BTCUSDT": summary["event_counts"]["book_gap_count"]}
    assert engine._syncers["BTCUSDT"].synced is False
    gap_index = next(
        index for index, record in enumerate(_generated_records(23, with_gap=True)) if record.data.get("pu") == 116
    )
    last_good = _generated_records(23, with_gap=True)[gap_index - 1]
    assert engine._books["BTCUSDT"].bids[1000] == int(Decimal(last_good.data["b"][0][1]) / Decimal("0.001"))
    assert engine._books["BTCUSDT"].asks[1002] == int(Decimal(last_good.data["a"][0][1]) / Decimal("0.001"))


class _SelfTradeProbeStrategy:
    def __init__(self) -> None:
        self.decisions = 0

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        _ = order
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        self.decisions += 1
        if self.decisions == 1:
            return StrategyDecision(
                quotes=[QuoteTarget("ask", "own_ask", price_tick=1002, qty_lots=1, refresh_key="own-ask")]
            )
        return StrategyDecision(
            quotes=[
                QuoteTarget("ask", "own_ask", price_tick=1002, qty_lots=1, refresh_key="own-ask"),
                QuoteTarget("bid", "crossing_bid", price_tick=1002, qty_lots=20, refresh_key="crossing-bid"),
            ]
        )


def test_seeded_strategy_stream_does_not_self_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = _write_records(tmp_path / "self_trade_probe.ndjson", _generated_records(31))
    cfg = _build_config(monkeypatch, tmp_path, MM_REQUOTE_MS="100")
    engine = SimulationEngine(cfg)
    engine.strategy = _SelfTradeProbeStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert summary["order_lifecycle_counts"]["self_trade_prevented"] >= 1
    assert all(fill["order_id"] != "own_ask" for fill in summary["fills"])
    assert not any(
        row["event_type"] == "fill" and row["details"].get("self_trade_prevented") is True for row in engine.event_trace
    )


class _CancelThenCrossCheckStrategy:
    def __init__(self) -> None:
        self.decisions = 0

    def observe_trade(self, _trade: AggTradeEvent) -> None:
        return

    def should_refresh(self, target: QuoteTarget, order: Order | None) -> bool:
        _ = target
        _ = order
        return False

    def propose(self, _book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        _ = inventory_qty
        self.decisions += 1
        if self.decisions == 1:
            return StrategyDecision(
                quotes=[
                    QuoteTarget("bid", "base_bid", price_tick=1000, qty_lots=1, refresh_key="bid"),
                    QuoteTarget("ask", "base_ask", price_tick=1002, qty_lots=1, refresh_key="ask"),
                ]
            )
        return StrategyDecision(quotes=[], reason="pull_quotes")


def test_seeded_stream_has_no_crossed_strategy_state_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_path = _write_records(tmp_path / "cancel_cross_check.ndjson", _generated_records(37))
    cfg = _build_config(monkeypatch, tmp_path, MM_REQUOTE_MS="100", SIM_CANCEL_LATENCY_MS="0")
    engine = SimulationEngine(cfg)
    engine.strategy = _CancelThenCrossCheckStrategy()
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert (
        summary["order_lifecycle_counts"]["cancel_acknowledged"]
        == summary["order_lifecycle_counts"]["cancel_requested"]
    )
    bids = engine.fill_model.get_orders("BTCUSDT", "bid")
    asks = engine.fill_model.get_orders("BTCUSDT", "ask")
    if bids and asks:
        assert max(order.price_tick for order in bids if order.price_tick is not None) < min(
            order.price_tick for order in asks if order.price_tick is not None
        )
