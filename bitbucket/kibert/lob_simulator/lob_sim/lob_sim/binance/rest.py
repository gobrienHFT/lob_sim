from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

from ..config import Config
from ..util import TokenBucket

logger = logging.getLogger(__name__)


class BinanceRESTClient:
    def __init__(self, config: Config, session: Optional[aiohttp.ClientSession] = None):
        self._config = config
        self._session = session
        self._owns_session = session is None
        self._limit = TokenBucket(rate_per_second=config.rate_limit_req_per_sec)

    async def __aenter__(self) -> "BinanceRESTClient":
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self._config.http_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(self, path: str, params: Dict[str, Any] | None = None) -> dict:
        if self._session is None:
            raise RuntimeError("REST client session is not initialized")
        url = f"{self._config.binance_fapi_base.rstrip('/')}/{path.lstrip('/')}"
        retries = max(0, self._config.http_retries)
        last_error = None
        for attempt in range(retries + 1):
            await self._limit.acquire()
            try:
                async with self._session.get(url, params=params) as response:
                    if response.status == 429:
                        delay = 0.5 * (2**attempt)
                        await response.release()
                        if attempt < retries:
                            await asyncio.sleep(delay)
                            continue
                        text = await response.text()
                        raise RuntimeError(f"Rate limited from Binance: {text}")
                    if response.status >= 500:
                        text = await response.text()
                        if attempt < retries:
                            await asyncio.sleep(0.25 * (2**attempt))
                            continue
                        raise RuntimeError(f"Binance server error {response.status}: {text}")
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                    if not isinstance(payload, dict):
                        raise RuntimeError(f"Unexpected Binance response type: {type(payload)}")
                    return payload
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:  # noqa: PERF203
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"REST request failed: {exc}") from exc
            except Exception as exc:
                raise
        if last_error:
            raise RuntimeError(f"REST request failed after retries: {last_error}")
        raise RuntimeError("REST request failed for unknown reasons")

    async def get_exchange_info(self) -> dict:
        return await self._request("/fapi/v1/exchangeInfo")

    async def get_depth_snapshot(self, symbol: str, limit: int) -> dict:
        return await self._request(
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": str(limit)},
        )
