from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from ..book.types import AggTradeEvent, DepthUpdateEvent, SymbolSpec
from ..config import Config

logger = logging.getLogger(__name__)

DepthCallback = Callable[[DepthUpdateEvent, dict], Awaitable[None]]
TradeCallback = Callable[[AggTradeEvent, dict], Awaitable[None]]
ConnectionCallback = Callable[[int], Awaitable[None]]


@dataclass(frozen=True)
class ReceiveIdentity:
    """Identity assigned immediately after a websocket receipt."""

    recv_seq: int
    recv_wall_ts: float
    recv_monotonic_ns: int


StreamFailureCallback = Callable[[int, str, str, ReceiveIdentity | None], Awaitable[None]]


class StreamConsumerError(RuntimeError):
    """A downstream capture callback failed and the collector must stop."""


async def _call_consumer(callback: Callable[..., Awaitable[None]], *args: object) -> None:
    """Keep writer/consumer failures outside the reconnect error boundary."""

    try:
        await callback(*args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise StreamConsumerError("market-data consumer failed") from exc


def _stream_failure_kind(exc: BaseException, *, connected: bool) -> tuple[str, str]:
    if not connected:
        return "connect_failure", type(exc).__name__
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "parse_failure", type(exc).__name__
    return "disconnect", type(exc).__name__


def _parse_event_ts(payload: dict) -> float:
    ts = payload.get("E")
    if ts is None:
        return time.time()
    try:
        event_ts = float(ts)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid exchange event timestamp: {ts!r}") from exc
    if not math.isfinite(event_ts):
        raise ValueError(f"non-finite exchange event timestamp: {ts!r}")
    if event_ts > 1_000_000_000_000:
        return event_ts / 1000.0
    return event_ts


def _optional_exchange_ts(payload: dict, key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid exchange timestamp {key}={value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite exchange timestamp {key}={value!r}")
    return parsed / 1000.0 if parsed > 1_000_000_000_000 else parsed


def parse_depth_update(
    symbol: str,
    spec: SymbolSpec,
    payload: dict,
    received_ts: float | None = None,
) -> DepthUpdateEvent:
    bids = [(spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1])) for level in payload.get("b", [])]
    asks = [(spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1])) for level in payload.get("a", [])]
    capture = payload.get("_capture", {})
    return DepthUpdateEvent(
        symbol=symbol,
        first_update_id=int(payload["U"]),
        final_update_id=int(payload["u"]),
        # `pu` is mandatory for current USD-M streams.  Falling back to U is
        # retained solely for importing v1 fixtures that predate the field.
        prev_update_id=int(payload.get("pu", payload["U"])),
        bids=bids,
        asks=asks,
        # Explicit receipt time is the causal clock for live capture.  The
        # default retains legacy parser behaviour for old unit tests/fixtures.
        ts_local=_parse_event_ts(payload) if received_ts is None else float(received_ts),
        event_ts=_optional_exchange_ts(payload, "E"),
        transaction_ts=_optional_exchange_ts(payload, "T"),
        receive_seq=int(capture["recvSeq"]) if capture.get("recvSeq") is not None else None,
        receive_monotonic_ns=(int(capture["recvMonotonicNs"]) if capture.get("recvMonotonicNs") is not None else None),
        stream_epoch=int(capture["streamEpoch"]) if capture.get("streamEpoch") is not None else None,
        sync_epoch=int(capture["syncEpoch"]) if capture.get("syncEpoch") is not None else None,
    )


def parse_agg_trade(
    symbol: str,
    spec: SymbolSpec,
    payload: dict,
    received_ts: float | None = None,
) -> AggTradeEvent:
    capture = payload.get("_capture", {})
    return AggTradeEvent(
        symbol=symbol,
        price_tick=spec.price_to_tick_exact(payload["p"]),
        qty_lots=spec.qty_to_lot_exact(payload["q"]),
        buyer_is_maker=bool(payload.get("m")),
        ts_local=_parse_event_ts(payload) if received_ts is None else float(received_ts),
        aggregate_trade_id=int(payload["a"]) if payload.get("a") is not None else None,
        event_ts=_optional_exchange_ts(payload, "E"),
        transaction_ts=_optional_exchange_ts(payload, "T"),
        receive_seq=int(capture["recvSeq"]) if capture.get("recvSeq") is not None else None,
        receive_monotonic_ns=(int(capture["recvMonotonicNs"]) if capture.get("recvMonotonicNs") is not None else None),
        stream_epoch=int(capture["streamEpoch"]) if capture.get("streamEpoch") is not None else None,
        sync_epoch=int(capture["syncEpoch"]) if capture.get("syncEpoch") is not None else None,
    )


def _routed_base_url(base_url: str, route: str) -> str:
    """Return a USD-M routed websocket base while tolerating old env files."""

    root = base_url.rstrip("/")
    for existing_route in ("public", "market", "private"):
        suffix = f"/{existing_route}"
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return f"{root}/{route}"


def depth_stream_url(symbol: str, config: Config) -> str:
    stream = f"{symbol.lower()}{config.depth_stream_suffix}"
    return f"{_routed_base_url(config.binance_fws_base, 'public')}/stream?streams={stream}"


def trade_stream_url(symbol: str, config: Config) -> str:
    stream = f"{symbol.lower()}{config.trade_stream_suffix}"
    return f"{_routed_base_url(config.binance_fws_base, 'market')}/stream?streams={stream}"


async def _recv_or_stop(ws: Any, stop_event: asyncio.Event) -> tuple[str | bytes, float, int] | None:
    recv_task = asyncio.create_task(ws.recv())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait({recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            recv_task.cancel()
            await asyncio.gather(recv_task, return_exceptions=True)
            return None
        raw_message = recv_task.result()
        return raw_message, time.time(), time.monotonic_ns()
    finally:
        if not stop_task.done():
            stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


async def _wait_for_retry(stop_event: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        await asyncio.sleep(0)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return


async def _run_stream(
    *,
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    url: str,
    route: str,
    expected_event_type: str,
    parser: Callable[[str, SymbolSpec, dict, float | None], Any],
    callback: Callable[[Any, dict], Awaitable[None]],
    stop_event: asyncio.Event,
    on_connect: ConnectionCallback | None = None,
    on_failure: StreamFailureCallback | None = None,
    next_receive_seq: Callable[[], int] | None = None,
) -> None:
    backoff = 1.0
    stream_epoch = 0
    while not stop_event.is_set():
        active_epoch: int | None = None
        failure_receipt: ReceiveIdentity | None = None
        try:
            async with websockets.connect(
                url,
                ping_interval=config.ws_ping_interval,
                ping_timeout=config.ws_ping_timeout,
                max_size=2**20,
            ) as ws:
                backoff = 1.0
                stream_epoch += 1
                active_epoch = stream_epoch
                if on_connect is not None:
                    await _call_consumer(on_connect, stream_epoch)
                while not stop_event.is_set():
                    failure_receipt = None
                    received = await _recv_or_stop(ws, stop_event)
                    if received is None:
                        return
                    raw_message, received_ts, received_monotonic_ns = received
                    if next_receive_seq is not None:
                        failure_receipt = ReceiveIdentity(
                            recv_seq=next_receive_seq(),
                            recv_wall_ts=received_ts,
                            recv_monotonic_ns=received_monotonic_ns,
                        )
                    payload = json.loads(raw_message)
                    if not isinstance(payload, dict):
                        raise ValueError("websocket payload must be an object")
                    raw_data = payload.get("data", payload)
                    if not isinstance(raw_data, dict) or raw_data.get("e") != expected_event_type:
                        raise ValueError(f"unexpected websocket event; expected {expected_event_type}")
                    data = dict(raw_data)
                    capture = dict(data.get("_capture", {}))
                    capture.update(
                        {
                            "recvMonotonicNs": received_monotonic_ns,
                            "streamEpoch": stream_epoch,
                            "route": route,
                        }
                    )
                    if failure_receipt is not None:
                        capture["recvSeq"] = failure_receipt.recv_seq
                    data["_capture"] = capture
                    event = parser(symbol, spec, data, received_ts)
                    await _call_consumer(callback, event, data)
                    failure_receipt = None
        except StreamConsumerError:
            # Disk, backpressure, and downstream state failures must stop the
            # capture. Reconnecting would silently create an incomplete tape.
            raise
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            failure_kind, failure_reason = _stream_failure_kind(exc, connected=active_epoch is not None)
            logger.warning("%s websocket for %s ended: %s", route, symbol, exc)
            if on_failure is not None:
                await on_failure(active_epoch or stream_epoch, failure_kind, failure_reason, failure_receipt)
            await _wait_for_retry(stop_event, min(config.ws_reconnect_max_sec, backoff))
            backoff = min(config.ws_reconnect_max_sec, backoff * 2.0)


async def run_depth_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_depth: DepthCallback,
    stop_event: asyncio.Event,
    on_connect: ConnectionCallback | None = None,
    on_failure: StreamFailureCallback | None = None,
    next_receive_seq: Callable[[], int] | None = None,
) -> None:
    await _run_stream(
        symbol=symbol,
        spec=spec,
        config=config,
        url=depth_stream_url(symbol, config),
        route="public",
        expected_event_type="depthUpdate",
        parser=parse_depth_update,
        callback=on_depth,
        stop_event=stop_event,
        on_connect=on_connect,
        on_failure=on_failure,
        next_receive_seq=next_receive_seq,
    )


async def run_trade_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_trade: TradeCallback,
    stop_event: asyncio.Event,
    on_connect: ConnectionCallback | None = None,
    on_failure: StreamFailureCallback | None = None,
    next_receive_seq: Callable[[], int] | None = None,
) -> None:
    await _run_stream(
        symbol=symbol,
        spec=spec,
        config=config,
        url=trade_stream_url(symbol, config),
        route="market",
        expected_event_type="aggTrade",
        parser=parse_agg_trade,
        callback=on_trade,
        stop_event=stop_event,
        on_connect=on_connect,
        on_failure=on_failure,
        next_receive_seq=next_receive_seq,
    )


async def run_symbol_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_depth: DepthCallback,
    on_trade: TradeCallback,
    stop_event: asyncio.Event,
) -> None:
    """Backward-compatible wrapper using independent depth and trade routes."""

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(run_depth_stream(symbol, spec, config, on_depth, stop_event))
        task_group.create_task(run_trade_stream(symbol, spec, config, on_trade, stop_event))
