from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

import lob_sim.cli as cli
from lob_sim.binance.ws import depth_stream_url, parse_agg_trade, parse_depth_update, trade_stream_url
from lob_sim.book.types import AggTradeEvent, DepthUpdateEvent, SymbolSpec
from lob_sim.record.writer import NDJSONWriter


def _spec() -> SymbolSpec:
    return SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.1"), step_size=Decimal("0.001"))


def _stream_config():
    return SimpleNamespace(
        binance_fws_base="wss://fstream.binance.com/public",
        depth_stream_suffix="@depth@100ms",
        trade_stream_suffix="@aggTrade",
    )


def test_split_routes_and_receive_time_parsing_preserve_exchange_timestamps():
    config = _stream_config()
    assert depth_stream_url("BTCUSDT", config) == (
        "wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms"
    )
    assert trade_stream_url("BTCUSDT", config) == (
        "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
    )

    depth_raw = {
        "e": "depthUpdate",
        "E": 1_700_000_000_123,
        "T": 1_700_000_000_120,
        "U": 10,
        "u": 11,
        "pu": 9,
        "b": [["100.0", "0.010"]],
        "a": [],
    }
    trade_raw = {
        "e": "aggTrade",
        "E": 1_700_000_000_223,
        "T": 1_700_000_000_220,
        "p": "100.1",
        "q": "0.002",
        "m": True,
    }

    depth = parse_depth_update("BTCUSDT", _spec(), depth_raw, received_ts=42.25)
    trade = parse_agg_trade("BTCUSDT", _spec(), trade_raw, received_ts=42.50)

    assert depth.ts_local == 42.25
    assert trade.ts_local == 42.50
    assert depth_raw["E"] == 1_700_000_000_123
    assert depth_raw["T"] == 1_700_000_000_120
    assert trade_raw["E"] == 1_700_000_000_223
    assert trade_raw["T"] == 1_700_000_000_220


def test_capture_schema_v2_is_explicit():
    assert cli._capture_metadata() == {
        "schemaVersion": 2,
        "clock": "receive_time",
        "timestampUnit": "seconds",
        "exchangeTimestampUnit": "milliseconds",
        "eventMetadataField": "_capture",
        "routes": {"depth": "public", "aggTrade": "market"},
    }


def test_capture_writer_refuses_to_append_to_an_existing_session(tmp_path):
    path = tmp_path / "capture.ndjson"
    with NDJSONWriter(path):
        pass

    with pytest.raises(FileExistsError):
        NDJSONWriter(path)


def test_collector_buffers_before_snapshot_retries_and_marks_reconnect_epoch(monkeypatch):
    stop = asyncio.Event()
    first_snapshot_written = asyncio.Event()

    class Writer:
        def __init__(self) -> None:
            self.records = []
            self.snapshot_count = 0

        def write(self, record) -> None:
            self.records.append(record)
            if record.type == "snapshot":
                self.snapshot_count += 1
                if self.snapshot_count == 1:
                    first_snapshot_written.set()
                elif self.snapshot_count == 2:
                    stop.set()

    class Rest:
        def __init__(self) -> None:
            self.calls = 0

        async def get_depth_snapshot(self, symbol: str, limit: int) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary snapshot failure")
            snapshot_id = 100 if self.calls == 2 else 200
            return {
                "lastUpdateId": snapshot_id,
                "bids": [["100.0", "0.010"]],
                "asks": [["101.0", "0.010"]],
            }

    async def fake_depth_stream(
        symbol,
        spec,
        config,
        on_depth,
        stop_event,
        on_connect=None,
    ):
        assert on_connect is not None
        await on_connect(1)
        await on_depth(
            DepthUpdateEvent(symbol, 90, 110, 89, [(1000, 8)], [], 10.0),
            {
                "e": "depthUpdate",
                "E": 1_000,
                "T": 999,
                "U": 90,
                "u": 110,
                "pu": 89,
                "b": [["100.0", "0.008"]],
                "a": [],
                "_capture": {"recvMonotonicNs": 10, "streamEpoch": 1, "route": "public"},
            },
        )
        await first_snapshot_written.wait()

        await on_connect(2)
        await on_depth(
            DepthUpdateEvent(symbol, 190, 210, 189, [(1000, 7)], [], 20.0),
            {
                "e": "depthUpdate",
                "E": 2_000,
                "T": 1_999,
                "U": 190,
                "u": 210,
                "pu": 189,
                "b": [["100.0", "0.007"]],
                "a": [],
                "_capture": {"recvMonotonicNs": 20, "streamEpoch": 2, "route": "public"},
            },
        )
        await stop_event.wait()

    async def fake_trade_stream(symbol, spec, config, on_trade, stop_event, on_connect=None):
        await on_trade(
            AggTradeEvent(symbol, 1001, 1, True, 11.0),
            {
                "e": "aggTrade",
                "E": 1_100,
                "T": 1_099,
                "p": "100.1",
                "q": "0.001",
                "m": True,
                "_capture": {"recvMonotonicNs": 11, "streamEpoch": 1, "route": "market"},
            },
        )
        await stop_event.wait()

    monkeypatch.setattr(cli, "run_depth_stream", fake_depth_stream)
    monkeypatch.setattr(cli, "run_trade_stream", fake_trade_stream)

    writer = Writer()
    rest = Rest()
    config = SimpleNamespace(
        book_top_n=20,
        resync_on_gap=True,
        snapshot_limit=1000,
        ws_reconnect_max_sec=0.0,
    )
    sequence = iter(range(1, 100)).__next__

    asyncio.run(cli._collect_symbol("BTCUSDT", _spec(), config, rest, writer, stop, sequence))

    assert rest.calls == 3
    depth_records = [record for record in writer.records if record.type == "depthUpdate"]
    trade_records = [record for record in writer.records if record.type == "aggTrade"]
    snapshots = [record for record in writer.records if record.type == "snapshot"]

    # Stream-first: the buffered depth record is durable before the first snapshot.
    assert writer.records.index(depth_records[0]) < writer.records.index(snapshots[0])
    assert depth_records[0].data["E"] == 1_000
    assert depth_records[0].data["T"] == 999
    assert depth_records[0].ts_local == 10.0
    assert trade_records[0].data["E"] == 1_100
    assert trade_records[0].ts_local == 11.0

    assert depth_records[0].data["_capture"]["syncEpoch"] == 1
    assert snapshots[0].data["_capture"]["syncEpoch"] == 1
    assert snapshots[0].data["_capture"]["reason"] == "bootstrap"
    assert depth_records[1].data["_capture"]["syncEpoch"] == 2
    assert snapshots[1].data["_capture"]["syncEpoch"] == 2
    assert snapshots[1].data["_capture"]["reason"] == "reconnect"


def test_symbol_collector_propagates_stream_failures(monkeypatch):
    stop = asyncio.Event()

    async def failing_depth_stream(*args, **kwargs):
        raise RuntimeError("fatal callback failure")

    async def waiting_trade_stream(symbol, spec, config, on_trade, stop_event, on_connect=None):
        await stop_event.wait()

    monkeypatch.setattr(cli, "run_depth_stream", failing_depth_stream)
    monkeypatch.setattr(cli, "run_trade_stream", waiting_trade_stream)

    config = SimpleNamespace(
        book_top_n=20,
        resync_on_gap=True,
        snapshot_limit=1000,
        ws_reconnect_max_sec=0.0,
    )
    writer = SimpleNamespace(write=lambda record: None)
    rest = SimpleNamespace(get_depth_snapshot=None)

    with pytest.raises(ExceptionGroup) as exc_info:
        asyncio.run(cli._collect_symbol("BTCUSDT", _spec(), config, rest, writer, stop))

    assert any("fatal callback failure" in str(exc) for exc in exc_info.value.exceptions)
