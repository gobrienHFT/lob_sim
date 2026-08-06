from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from ..book.types import AggTradeEvent, DepthUpdateEvent, SymbolSpec
from ..config import Config

logger = logging.getLogger(__name__)

DepthCallback = Callable[[DepthUpdateEvent, dict], Awaitable[None]]
TradeCallback = Callable[[AggTradeEvent, dict], Awaitable[None]]
ConnectionCallback = Callable[[int], Awaitable[None]]


def _routed_base_url(base_url: str, route: str) -> str:
    """Return a Binance USD-M routed websocket base URL.

    Accepting an already-routed base keeps existing environment files usable while
    still allowing the collector to open independent public and market sessions.
    """

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


def parse_depth_update(
    symbol: str,
    spec: SymbolSpec,
    payload: dict,
    received_ts: float | None = None,
) -> DepthUpdateEvent:
    bids = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
        for level in payload.get("b", [])
    ]
    asks = [
        (spec.price_to_tick_exact(level[0]), spec.qty_to_lot_exact(level[1]))
        for level in payload.get("a", [])
    ]
    return DepthUpdateEvent(
        symbol=symbol,
        first_update_id=int(payload["U"]),
        final_update_id=int(payload["u"]),
        prev_update_id=int(payload["pu"]),
        bids=bids,
        asks=asks,
        # Strategy/replay causality is based on local receipt. Exchange E/T remain
        # untouched in the recorded raw payload.
        ts_local=time.time() if received_ts is None else float(received_ts),
        event_ts=float(payload["E"]) / 1000.0 if payload.get("E") is not None else None,
        transaction_ts=float(payload["T"]) / 1000.0 if payload.get("T") is not None else None,
    )


def parse_agg_trade(
    symbol: str,
    spec: SymbolSpec,
    payload: dict,
    received_ts: float | None = None,
) -> AggTradeEvent:
    return AggTradeEvent(
        symbol=symbol,
        price_tick=spec.price_to_tick_exact(payload["p"]),
        qty_lots=spec.qty_to_lot_exact(payload["q"]),
        buyer_is_maker=bool(payload.get("m")),
        ts_local=time.time() if received_ts is None else float(received_ts),
        aggregate_trade_id=int(payload["a"]) if payload.get("a") is not None else None,
        event_ts=float(payload["E"]) / 1000.0 if payload.get("E") is not None else None,
        transaction_ts=float(payload["T"]) / 1000.0 if payload.get("T") is not None else None,
    )


async def _recv_or_stop(
    ws: Any,
    stop_event: asyncio.Event,
) -> tuple[str | bytes, float, int] | None:
    recv_task = asyncio.create_task(ws.recv())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait({recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            recv_task.cancel()
            await asyncio.gather(recv_task, return_exceptions=True)
            return None
        raw_message = recv_task.result()
        received_monotonic_ns = time.monotonic_ns()
        received_ts = time.time()
        return raw_message, received_ts, received_monotonic_ns
    finally:
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
) -> None:
    backoff = 1.0
    stream_epoch = 0

    while not stop_event.is_set():
        try:
            async with websockets.connect(
                url,
                ping_interval=config.ws_ping_interval,
                ping_timeout=config.ws_ping_timeout,
                max_size=2**20,
            ) as ws:
                backoff = 1.0
                stream_epoch += 1
                if on_connect is not None:
                    await on_connect(stream_epoch)

                while not stop_event.is_set():
                    received = await _recv_or_stop(ws, stop_event)
                    if received is None:
                        return

                    raw_message, received_ts, received_monotonic_ns = received
                    payload = json.loads(raw_message)
                    raw_data = payload.get("data", payload)
                    if not isinstance(raw_data, dict):
                        raise TypeError(f"Unexpected websocket payload type: {type(raw_data)!r}")
                    if raw_data.get("e") != expected_event_type:
                        continue

                    # Copy rather than mutate json.loads output used by the parser.
                    data = dict(raw_data)
                    data["_capture"] = {
                        "recvMonotonicNs": received_monotonic_ns,
                        "streamEpoch": stream_epoch,
                        "route": route,
                    }
                    event = parser(symbol, spec, data, received_ts)
                    await callback(event, data)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError, TimeoutError) as exc:
            logger.warning("%s websocket for %s ended: %s", route, symbol, exc)
            await _wait_for_retry(stop_event, min(config.ws_reconnect_max_sec, backoff))
            backoff = min(config.ws_reconnect_max_sec, backoff * 2.0)


async def run_depth_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_depth: DepthCallback,
    stop_event: asyncio.Event,
    on_connect: ConnectionCallback | None = None,
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
    )


async def run_trade_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_trade: TradeCallback,
    stop_event: asyncio.Event,
    on_connect: ConnectionCallback | None = None,
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
    )


async def run_symbol_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_depth: DepthCallback,
    on_trade: TradeCallback,
    stop_event: asyncio.Event,
) -> None:
    """Backward-compatible wrapper using the required independent routes."""

    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(run_depth_stream(symbol, spec, config, on_depth, stop_event))
        task_group.create_task(run_trade_stream(symbol, spec, config, on_trade, stop_event))
