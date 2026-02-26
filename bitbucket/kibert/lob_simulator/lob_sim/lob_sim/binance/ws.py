from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable

import websockets

from ..config import Config
from ..book.types import AggTradeEvent, DepthUpdateEvent, SymbolSpec

logger = logging.getLogger(__name__)

DepthCallback = Callable[[DepthUpdateEvent, dict], Awaitable[None]]
TradeCallback = Callable[[AggTradeEvent, dict], Awaitable[None]]


def _parse_event_ts(payload: dict) -> float:
    ts = payload.get("E")
    if ts is None:
        return time.time()
    event_ts = float(ts)
    if event_ts > 1_000_000_000_000:
        return event_ts / 1000.0
    return event_ts


def parse_depth_update(symbol: str, spec: SymbolSpec, payload: dict) -> DepthUpdateEvent:
    bids = [(spec.price_to_tick(level[0]), spec.qty_to_lot(level[1])) for level in payload.get("b", [])]
    asks = [(spec.price_to_tick(level[0]), spec.qty_to_lot(level[1])) for level in payload.get("a", [])]
    return DepthUpdateEvent(
        symbol=symbol,
        first_update_id=int(payload["U"]),
        final_update_id=int(payload["u"]),
        prev_update_id=int(payload["pu"]),
        bids=bids,
        asks=asks,
        ts_local=_parse_event_ts(payload),
    )


def parse_agg_trade(symbol: str, spec: SymbolSpec, payload: dict) -> AggTradeEvent:
    return AggTradeEvent(
        symbol=symbol,
        price_tick=spec.price_to_tick(payload["p"]),
        qty_lots=spec.qty_to_lot(payload["q"]),
        buyer_is_maker=bool(payload.get("m")),
        ts_local=_parse_event_ts(payload),
    )


async def run_symbol_stream(
    symbol: str,
    spec: SymbolSpec,
    config: Config,
    on_depth: DepthCallback,
    on_trade: TradeCallback,
    stop_event: asyncio.Event,
) -> None:
    stream = f"{symbol.lower()}{config.depth_stream_suffix}/{symbol.lower()}{config.trade_stream_suffix}"
    url = f"{config.binance_fws_base}/stream?streams={stream}"
    backoff = 1.0
    while not stop_event.is_set():
        try:
            async with websockets.connect(
                url,
                ping_interval=config.ws_ping_interval,
                ping_timeout=config.ws_ping_timeout,
                max_size=2**20,
            ) as ws:
                backoff = 1.0
                while not stop_event.is_set():
                    try:
                        raw = await ws.recv()
                    except asyncio.TimeoutError:
                        continue
                    payload = json.loads(raw)
                    data = payload.get("data", payload)
                    event_type = data.get("e")
                    if event_type == "depthUpdate":
                        evt = parse_depth_update(symbol, spec, data)
                        await on_depth(evt, data)
                    elif event_type == "aggTrade":
                        evt = parse_agg_trade(symbol, spec, data)
                        await on_trade(evt, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Websocket for %s ended: %s", symbol, exc)
            await asyncio.sleep(min(config.ws_reconnect_max_sec, backoff))
            backoff = min(config.ws_reconnect_max_sec, backoff * 2.0)
