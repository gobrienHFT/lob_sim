from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
import websocket


class BinanceHTTPError(RuntimeError):
    def __init__(self, status_code: int, payload: Any, url: str):
        self.status_code = int(status_code)
        self.payload = payload
        self.url = url
        super().__init__(f"Binance HTTP {self.status_code} on {url}: {payload}")


class BinanceAuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str
    base_asset: str
    quote_asset: str
    underlying_type: str


class BinanceFuturesPublic:
    """Small public-only client with gentle pacing to avoid bans."""

    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        timeout: int = 12,
        requests_per_second: float = 4.0,
        retries: int = 3,
        api_key: str | None = None,
        api_secret: str | None = None,
        recv_window: int = 5000,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = int(timeout)
        self.retries = int(max(1, retries))
        self.min_gap = 1.0 / max(0.5, float(requests_per_second))
        self._last_at = 0.0
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.recv_window = int(recv_window)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "simple-breakout-dashboard/1.0"})

    def _pace(self) -> None:
        now = time.monotonic()
        gap = now - self._last_at
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self._last_at = time.monotonic()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        absolute_url: bool = False,
    ) -> Any:
        url = path if absolute_url else f"{self.base_url}{path}"
        last_exc: Exception | None = None
        base_params = dict(params or {})

        for attempt in range(1, self.retries + 1):
            req_params = dict(base_params)
            headers: dict[str, str] = {}
            request_url = url
            request_params: dict[str, Any] | None = req_params
            if signed:
                if not self.api_key or not self.api_secret:
                    raise BinanceAuthenticationError("Binance API key/secret are required for signed futures endpoints.")
                req_params["recvWindow"] = int(req_params.get("recvWindow", self.recv_window))
                req_params["timestamp"] = int(time.time() * 1000)
                query_string = urlencode(req_params, doseq=True)
                signature = hmac.new(
                    self.api_secret.encode("utf-8"),
                    query_string.encode("ascii"),
                    hashlib.sha256,
                ).hexdigest()
                request_url = f"{url}?{query_string}&signature={signature}"
                request_params = None
                headers["X-MBX-APIKEY"] = self.api_key

            self._pace()
            try:
                response = self.session.request(
                    method=method.upper(),
                    url=request_url,
                    params=request_params,
                    headers=headers or None,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                if response.status_code == 200:
                    return response.json() if response.text else None

                payload: Any
                try:
                    payload = response.json()
                except Exception:
                    payload = {"text": response.text[:400]}

                if response.status_code in (418, 429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(8.0, 0.8 * (2 ** (attempt - 1))))
                    continue
                raise BinanceHTTPError(response.status_code, payload, url)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(min(8.0, 0.8 * (2 ** (attempt - 1))))
                else:
                    break

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Request failed without exception")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params, signed=True)

    def _signed_post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, params=params, signed=True)

    def _signed_delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, params=params, signed=True)

    def perpetual_usdt_symbols(self) -> list[FuturesSymbol]:
        info = self.exchange_info()
        rows: list[FuturesSymbol] = []
        allowed_contract_types = {"PERPETUAL", "TRADIFI_PERPETUAL"}
        for item in info.get("symbols", []):
            if str(item.get("contractType", "")).upper() not in allowed_contract_types:
                continue
            if str(item.get("status", "")).upper() != "TRADING":
                continue
            if str(item.get("quoteAsset", "")).upper() != "USDT":
                continue

            symbol = str(item.get("symbol", "")).upper()
            if symbol:
                rows.append(
                    FuturesSymbol(
                        symbol=symbol,
                        base_asset=str(item.get("baseAsset", "")).upper(),
                        quote_asset="USDT",
                        underlying_type=str(item.get("underlyingType", "")).upper(),
                    )
                )
        return rows

    def exchange_info(self) -> dict[str, Any]:
        data = self._get("/fapi/v1/exchangeInfo")
        return data if isinstance(data, dict) else {}

    def ticker_24hr(self) -> list[dict[str, Any]]:
        data = self._get("/fapi/v1/ticker/24hr")
        return data if isinstance(data, list) else []

    def mark_price(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        data = self._get("/fapi/v1/premiumIndex", params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    def funding_info(self) -> list[dict[str, Any]]:
        data = self._get("/fapi/v1/fundingInfo")
        return data if isinstance(data, list) else []

    def premium_index_klines(
        self,
        symbol: str,
        *,
        interval: str = "5m",
        limit: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval, "limit": int(limit)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/fapi/v1/premiumIndexKlines", params=params)
        return data if isinstance(data, list) else []

    def funding_rate_history(
        self,
        symbol: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": int(limit)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/fapi/v1/fundingRate", params=params)
        return data if isinstance(data, list) else []

    def global_long_short_account_ratio(
        self,
        symbol: str,
        *,
        period: str = "1h",
        limit: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/globalLongShortAccountRatio", params=params)
        return data if isinstance(data, list) else []

    def open_interest_statistics(
        self,
        symbol: str,
        *,
        period: str = "1h",
        limit: int = 2,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/openInterestHist", params=params)
        return data if isinstance(data, list) else []

    def taker_buy_sell_volume(
        self,
        symbol: str,
        *,
        period: str = "1h",
        limit: int = 2,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/takerlongshortRatio", params=params)
        return data if isinstance(data, list) else []

    def top_trader_long_short_position_ratio(
        self,
        symbol: str,
        *,
        period: str = "1h",
        limit: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/topLongShortPositionRatio", params=params)
        return data if isinstance(data, list) else []

    def top_trader_long_short_account_ratio(
        self,
        symbol: str,
        *,
        period: str = "1h",
        limit: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/topLongShortAccountRatio", params=params)
        return data if isinstance(data, list) else []

    def basis(
        self,
        pair: str,
        *,
        contract_type: str = "PERPETUAL",
        period: str = "1h",
        limit: int = 1,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "pair": pair.upper(),
            "contractType": str(contract_type).upper(),
            "period": str(period),
            "limit": int(limit),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/futures/data/basis", params=params)
        return data if isinstance(data, list) else []

    def depth(self, symbol: str, *, limit: int = 50) -> dict[str, Any]:
        data = self._get("/fapi/v1/depth", {"symbol": symbol.upper(), "limit": int(limit)})
        return data if isinstance(data, dict) else {}

    def mark_price_stream_snapshot(
        self,
        symbols: list[str],
        *,
        sample_seconds: float = 4.0,
        update_speed: str = "1s",
    ) -> dict[str, dict[str, Any]]:
        streams = [
            f"{str(symbol).lower()}@markPrice@{update_speed}"
            for symbol in symbols
            if str(symbol).strip()
        ]
        if not streams:
            return {}

        stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"
        snapshots: dict[str, dict[str, Any]] = {}
        ws = None
        deadline = time.monotonic() + max(0.5, float(sample_seconds))

        try:
            ws = websocket.create_connection(stream_url, timeout=self.timeout)
            while time.monotonic() < deadline:
                remaining = max(0.25, deadline - time.monotonic())
                ws.settimeout(min(self.timeout, remaining + 0.5))
                try:
                    raw_message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    break

                payload = json.loads(raw_message)
                data = payload.get("data", payload)
                symbol = str(data.get("s", "")).upper()
                if not symbol:
                    continue

                try:
                    mark_price = float(data.get("p"))
                    index_price = float(data.get("i"))
                except Exception:
                    continue
                if abs(index_price) < 1e-12:
                    continue

                premium_rate = (mark_price - index_price) / index_price
                event_time = int(data.get("E", 0) or 0)

                snapshot = snapshots.setdefault(
                    symbol,
                    {
                        "sample_count": 0,
                        "sum_premium_rate": 0.0,
                        "avg_premium_rate": float("nan"),
                        "latest_premium_rate": float("nan"),
                        "latest_event_time": 0,
                        "latest_funding_rate": "",
                        "next_funding_time": 0,
                    },
                )
                snapshot["sample_count"] += 1
                snapshot["sum_premium_rate"] += premium_rate
                snapshot["avg_premium_rate"] = snapshot["sum_premium_rate"] / snapshot["sample_count"]
                if event_time >= int(snapshot["latest_event_time"]):
                    snapshot["latest_premium_rate"] = premium_rate
                    snapshot["latest_event_time"] = event_time
                    snapshot["latest_funding_rate"] = data.get("r", "")
                    snapshot["next_funding_time"] = int(data.get("T", 0) or 0)
        except Exception:
            return {}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        return snapshots

    def klines(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        limit: int = 200,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": int(limit)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._get("/fapi/v1/klines", params)
        return data if isinstance(data, list) else []

    def klines_1d(
        self,
        symbol: str,
        limit: int = 200,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]:
        return self.klines(
            symbol,
            interval="1d",
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )

    def account_information_v3(self) -> dict[str, Any]:
        data = self._signed_get("/fapi/v3/account")
        return data if isinstance(data, dict) else {}

    def user_commission_rate(self, symbol: str) -> dict[str, Any]:
        data = self._signed_get("/fapi/v1/commissionRate", {"symbol": symbol.upper()})
        return data if isinstance(data, dict) else {}

    def position_information_v3(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        data = self._signed_get("/fapi/v3/positionRisk", params=params)
        return data if isinstance(data, list) else []

    def change_initial_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        data = self._signed_post(
            "/fapi/v1/leverage",
            {"symbol": symbol.upper(), "leverage": int(leverage)},
        )
        return data if isinstance(data, dict) else {}

    def change_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        data = self._signed_post(
            "/fapi/v1/marginType",
            {"symbol": symbol.upper(), "marginType": margin_type.upper()},
        )
        return data if isinstance(data, dict) else {}

    def new_futures_order(self, **params: Any) -> dict[str, Any]:
        data = self._signed_post("/fapi/v1/order", params=params)
        return data if isinstance(data, dict) else {}

    def new_futures_algo_order(self, **params: Any) -> dict[str, Any]:
        data = self._signed_post("/fapi/v1/algoOrder", params=params)
        return data if isinstance(data, dict) else {}

    def open_futures_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        data = self._signed_get("/fapi/v1/openOrders", params=params)
        return data if isinstance(data, list) else []

    def query_futures_order(
        self,
        symbol: str,
        *,
        order_id: int | str | None = None,
        orig_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        data = self._signed_get("/fapi/v1/order", params=params)
        return data if isinstance(data, dict) else {}

    def query_futures_algo_order(
        self,
        *,
        algo_id: int | str | None = None,
        client_algo_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if algo_id is not None:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        data = self._signed_get("/fapi/v1/algoOrder", params=params)
        return data if isinstance(data, dict) else {}

    def open_futures_algo_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        data = self._signed_get("/fapi/v1/openAlgoOrders", params=params)
        return data if isinstance(data, list) else []

    def cancel_all_futures_orders(self, symbol: str) -> dict[str, Any]:
        data = self._signed_delete("/fapi/v1/allOpenOrders", {"symbol": symbol.upper()})
        return data if isinstance(data, dict) else {}

    def cancel_all_futures_algo_orders(self, symbol: str) -> dict[str, Any]:
        data = self._signed_delete("/fapi/v1/algoOpenOrders", {"symbol": symbol.upper()})
        return data if isinstance(data, dict) else {}

    def income_history(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        income_type: str | None = None,
        page: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": int(limit)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if income_type:
            params["incomeType"] = str(income_type)
        if page is not None:
            params["page"] = int(page)
        data = self._signed_get("/fapi/v1/income", params=params)
        return data if isinstance(data, list) else []

    def request_income_download_id(self, start_time: int, end_time: int) -> str:
        data = self._signed_get(
            "/fapi/v1/income/asyn",
            {"startTime": int(start_time), "endTime": int(end_time)},
        )
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected income export response.")
        return str(data.get("downloadId", "")).strip()

    def income_download_status(self, download_id: str) -> dict[str, Any]:
        data = self._signed_get("/fapi/v1/income/asyn/id", {"downloadId": download_id})
        return data if isinstance(data, dict) else {}

    def wait_for_income_download_url(
        self,
        download_id: str,
        *,
        poll_attempts: int = 8,
        poll_interval_seconds: float = 2.5,
    ) -> str:
        for _ in range(max(1, int(poll_attempts))):
            status = self.income_download_status(download_id)
            url = str(status.get("url", "")).strip()
            if url:
                return url
            time.sleep(max(0.5, float(poll_interval_seconds)))
        raise RuntimeError("Timed out waiting for Binance income export download link.")

    def download_file_bytes(self, url: str) -> bytes:
        self._pace()
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        return response.content
