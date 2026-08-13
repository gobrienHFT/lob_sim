from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from lob_sim.binance.ws import ReceiveIdentity, StreamConsumerError, _run_stream, parse_agg_trade
from lob_sim.book.types import SymbolSpec
from lob_sim.config import load_config


def test_parse_agg_trade_accepts_raw_trade_payload() -> None:
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))

    event = parse_agg_trade(
        "BTCUSDT",
        spec,
        {
            "e": "trade",
            "E": 1780500088697,
            "T": 1780500088697,
            "s": "BTCUSDT",
            "p": "66240.10",
            "q": "0.061",
            "m": False,
            "t": 7721648663,
        },
    )

    assert event.symbol == "BTCUSDT"
    assert event.price_tick == 662401
    assert event.qty_lots == 61
    assert event.buyer_is_maker is False
    assert event.ts_local == 1780500088.697


@pytest.mark.parametrize("field", ["recvSeq", "recvMonotonicNs", "streamEpoch", "syncEpoch"])
def test_parse_agg_trade_rejects_non_integral_capture_identity(field: str) -> None:
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))
    payload = {
        "e": "aggTrade",
        "E": 1780500088697,
        "T": 1780500088697,
        "p": "66240.10",
        "q": "0.061",
        "m": False,
        "_capture": {
            "recvSeq": 1,
            "recvMonotonicNs": 2,
            "streamEpoch": 1,
            "syncEpoch": 1,
        },
    }
    payload["_capture"][field] = 1.5

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        parse_agg_trade("BTCUSDT", spec, payload)


class _SocketContext:
    def __init__(self, socket: object) -> None:
        self.socket = socket

    async def __aenter__(self) -> object:
        return self.socket

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingSocket:
    async def recv(self) -> str:
        raise OSError("simulated disconnect")


class _MessageSocket:
    def __init__(self, message: str) -> None:
        self.message = message

    async def recv(self) -> str:
        return self.message


def _run_test_stream(
    monkeypatch: pytest.MonkeyPatch,
    socket: object,
    *,
    callback: Any,
    on_failure: Any,
    next_receive_seq: Any = None,
) -> None:
    monkeypatch.setattr(
        "lob_sim.binance.ws.websockets.connect",
        lambda *_args, **_kwargs: _SocketContext(socket),
    )
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))
    stop_event = asyncio.Event()

    async def scenario() -> None:
        await _run_stream(
            symbol="BTCUSDT",
            spec=spec,
            config=load_config(".env.example"),
            url="wss://example.invalid/stream",
            route="market",
            expected_event_type="aggTrade",
            parser=parse_agg_trade,
            callback=callback,
            stop_event=stop_event,
            on_failure=on_failure,
            next_receive_seq=next_receive_seq,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("socket", "expected_kind", "expected_reason", "expected_recv_seq"),
    [
        (_FailingSocket(), "disconnect", "OSError", None),
        (_MessageSocket("{"), "parse_failure", "JSONDecodeError", 41),
    ],
)
def test_stream_records_failure_boundary_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    socket: object,
    expected_kind: str,
    expected_reason: str,
    expected_recv_seq: int | None,
) -> None:
    failures: list[tuple[int, str, str, int | None]] = []
    stop_event: asyncio.Event | None = None

    async def callback(_event: object, _raw: dict) -> None:
        return

    async def on_failure(
        epoch: int,
        kind: str,
        reason: str,
        receipt: ReceiveIdentity | None,
    ) -> None:
        failures.append((epoch, kind, reason, receipt.recv_seq if receipt is not None else None))
        assert stop_event is not None
        stop_event.set()

    monkeypatch.setattr(
        "lob_sim.binance.ws.websockets.connect",
        lambda *_args, **_kwargs: _SocketContext(socket),
    )
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))
    stop_event = asyncio.Event()

    async def scenario() -> None:
        await _run_stream(
            symbol="BTCUSDT",
            spec=spec,
            config=load_config(".env.example"),
            url="wss://example.invalid/stream",
            route="market",
            expected_event_type="aggTrade",
            parser=parse_agg_trade,
            callback=callback,
            stop_event=stop_event,
            on_failure=on_failure,
            next_receive_seq=iter([41]).__next__,
        )

    asyncio.run(scenario())

    assert failures == [(1, expected_kind, expected_reason, expected_recv_seq)]


def test_stream_assigns_receive_identity_before_parsing_and_preserves_it_for_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = json.dumps(
        {
            "e": "aggTrade",
            "E": 1_780_500_088_697,
            "T": 1_780_500_088_697,
            "p": "66240.10",
            "q": "0.061",
            "m": False,
            "a": 1,
        }
    )
    monkeypatch.setattr(
        "lob_sim.binance.ws.websockets.connect",
        lambda *_args, **_kwargs: _SocketContext(_MessageSocket(message)),
    )
    spec = SymbolSpec(symbol="BTCUSDT", tick_size=Decimal("0.10"), step_size=Decimal("0.001"))
    stop_event = asyncio.Event()
    observations: list[tuple[int | None, int]] = []

    async def callback(event: Any, raw: dict) -> None:
        observations.append((event.receive_seq, int(raw["_capture"]["recvSeq"])))
        stop_event.set()

    async def scenario() -> None:
        await _run_stream(
            symbol="BTCUSDT",
            spec=spec,
            config=load_config(".env.example"),
            url="wss://example.invalid/stream",
            route="market",
            expected_event_type="aggTrade",
            parser=parse_agg_trade,
            callback=callback,
            stop_event=stop_event,
            next_receive_seq=iter([73]).__next__,
        )

    asyncio.run(scenario())

    assert observations == [(73, 73)]


def test_stream_consumer_failure_stops_capture_instead_of_reconnecting(monkeypatch: pytest.MonkeyPatch) -> None:
    message = json.dumps(
        {
            "e": "aggTrade",
            "E": 1_780_500_088_697,
            "T": 1_780_500_088_697,
            "p": "66240.10",
            "q": "0.061",
            "m": False,
            "a": 1,
        }
    )
    failures: list[tuple[int, str, str]] = []

    async def callback(_event: object, _raw: dict) -> None:
        raise OSError("simulated writer failure")

    async def on_failure(
        epoch: int,
        kind: str,
        reason: str,
        _receipt: ReceiveIdentity | None,
    ) -> None:
        failures.append((epoch, kind, reason))

    with pytest.raises(StreamConsumerError) as exc_info:
        _run_test_stream(
            monkeypatch,
            _MessageSocket(message),
            callback=callback,
            on_failure=on_failure,
        )

    assert isinstance(exc_info.value.__cause__, OSError)
    assert failures == []
