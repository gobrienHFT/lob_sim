from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from binance_futures import BinanceFuturesPublic

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_CORR_WINDOW_DAYS = 180


@dataclass(frozen=True)
class ScreenerAsset:
    code: str
    symbol: str
    name: str
    category: str
    provider: str


@dataclass(frozen=True)
class ScreenerData:
    quotes_df: pd.DataFrame
    intraday_df: pd.DataFrame
    errors: list[str]


DEFAULT_SCREENER_ASSETS: tuple[ScreenerAsset, ...] = (
    ScreenerAsset(code="SPX", symbol="^GSPC", name="S&P 500 Index", category="Index", provider="yahoo"),
    ScreenerAsset(code="NDX", symbol="^NDX", name="Nasdaq-100 Index", category="Index", provider="yahoo"),
    ScreenerAsset(code="VIX", symbol="^VIX", name="CBOE Volatility Index", category="Volatility", provider="yahoo"),
    ScreenerAsset(code="DXY", symbol="DX-Y.NYB", name="US Dollar Index", category="FX", provider="yahoo"),
    ScreenerAsset(code="CL", symbol="CL=F", name="WTI Crude Oil", category="Commodity", provider="yahoo"),
    ScreenerAsset(code="TLT", symbol="TLT", name="20+ Year Treasury Bond ETF", category="Rates", provider="yahoo"),
    ScreenerAsset(code="DBMF", symbol="DBMF", name="iMGP DBi Managed Futures Strategy ETF", category="Managed Futures", provider="yahoo"),
    ScreenerAsset(code="BTC", symbol="BTCUSDT", name="Bitcoin", category="Crypto", provider="binance"),
)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("nan")


def _empty_series() -> pd.Series:
    return pd.Series(dtype=float)


def _yahoo_chart(symbol: str, *, interval: str, range_value: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    response = requests.get(
        url,
        params={
            "interval": interval,
            "range": range_value,
            "includePrePost": "false",
            "events": "div,splits",
        },
        timeout=20,
        headers=YAHOO_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"Yahoo returned no data for {symbol}: {error}")
    return result[0]


def _yahoo_series(symbol: str, *, interval: str, range_value: str) -> pd.Series:
    result = _yahoo_chart(symbol, interval=interval, range_value=range_value)
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    if not timestamps or not closes:
        return _empty_series()

    length = min(len(timestamps), len(closes))
    series = pd.Series(
        pd.to_numeric(closes[:length], errors="coerce"),
        index=pd.to_datetime(timestamps[:length], unit="s", utc=True),
    ).dropna()
    if interval == "1d":
        series.index = pd.DatetimeIndex(series.index).normalize()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _yahoo_quote(asset: ScreenerAsset) -> tuple[dict[str, Any], pd.Series, pd.Series]:
    result = _yahoo_chart(asset.symbol, interval="1m", range_value="1d")
    meta = result.get("meta") or {}
    intraday = _yahoo_series(asset.symbol, interval="1m", range_value="1d")
    daily = _yahoo_series(asset.symbol, interval="1d", range_value="6mo")

    last_price = _safe_float(meta.get("regularMarketPrice"))
    if pd.isna(last_price) and not intraday.empty:
        last_price = float(intraday.iloc[-1])

    prev_close = _safe_float(meta.get("chartPreviousClose"))
    if pd.isna(prev_close):
        prev_close = _safe_float(meta.get("previousClose"))
    if pd.isna(prev_close) and len(daily) >= 2:
        prev_close = float(daily.iloc[-2])

    change = last_price - prev_close if not pd.isna(last_price) and not pd.isna(prev_close) else float("nan")
    change_pct = (change / prev_close * 100.0) if prev_close and not pd.isna(change) else float("nan")
    as_of = meta.get("regularMarketTime")
    as_of_dt = (
        datetime.fromtimestamp(int(as_of), tz=timezone.utc)
        if as_of not in (None, "")
        else (intraday.index[-1].to_pydatetime() if not intraday.empty else None)
    )

    row = {
        "code": asset.code,
        "name": asset.name,
        "category": asset.category,
        "source": "Yahoo Finance",
        "source_symbol": asset.symbol,
        "last_price": last_price,
        "change": change,
        "change_pct": change_pct,
        "history_days": max(0, len(daily)),
        "as_of": as_of_dt,
        "sparkline": intraday.tail(90).tolist(),
    }
    return row, intraday, daily


def _binance_intraday_series(client: BinanceFuturesPublic, symbol: str) -> pd.Series:
    rows = client.klines(symbol, interval="1m", limit=360)
    if not rows:
        return _empty_series()
    frame = pd.DataFrame(rows, columns=list(range(len(rows[0]))))
    series = pd.Series(
        pd.to_numeric(frame[4], errors="coerce").values,
        index=pd.to_datetime(frame[0], unit="ms", utc=True),
    ).dropna()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _binance_daily_series(client: BinanceFuturesPublic, symbol: str) -> pd.Series:
    rows = client.klines(symbol, interval="1d", limit=181)
    if len(rows) < 2:
        return _empty_series()
    closed_only = rows[:-1]
    frame = pd.DataFrame(closed_only, columns=list(range(len(closed_only[0]))))
    series = pd.Series(
        pd.to_numeric(frame[4], errors="coerce").values,
        index=pd.to_datetime(frame[0], unit="ms", utc=True),
    ).dropna()
    series.index = pd.DatetimeIndex(series.index).normalize()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _binance_quote(client: BinanceFuturesPublic, asset: ScreenerAsset) -> tuple[dict[str, Any], pd.Series, pd.Series]:
    payload = client._get("/fapi/v1/ticker/24hr", {"symbol": asset.symbol})
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(f"Binance returned no ticker payload for {asset.symbol}")

    intraday = _binance_intraday_series(client, asset.symbol)
    daily = _binance_daily_series(client, asset.symbol)

    last_price = _safe_float(payload.get("lastPrice"))
    change = _safe_float(payload.get("priceChange"))
    change_pct = _safe_float(payload.get("priceChangePercent"))
    close_time = payload.get("closeTime")
    as_of_dt = (
        datetime.fromtimestamp(int(close_time) / 1000, tz=timezone.utc)
        if close_time not in (None, "")
        else (intraday.index[-1].to_pydatetime() if not intraday.empty else None)
    )

    row = {
        "code": asset.code,
        "name": asset.name,
        "category": asset.category,
        "source": "Binance Futures",
        "source_symbol": asset.symbol,
        "last_price": last_price,
        "change": change,
        "change_pct": change_pct,
        "history_days": max(0, len(daily)),
        "as_of": as_of_dt,
        "sparkline": intraday.tail(90).tolist(),
    }
    return row, intraday, daily


def _rolling_corr(series: pd.Series, reference: pd.Series) -> tuple[float, int]:
    if series.empty or reference.empty:
        return float("nan"), 0

    aligned = pd.concat(
        [
            series.rename("series").pct_change(),
            reference.rename("reference").pct_change(),
        ],
        axis=1,
        join="inner",
    ).dropna()

    if len(aligned) < 2:
        return float("nan"), int(len(aligned))

    window_days = min(MAX_CORR_WINDOW_DAYS, int(len(aligned)))
    rolling_corr = aligned["series"].rolling(window_days).corr(aligned["reference"]).dropna()
    if rolling_corr.empty:
        return float("nan"), window_days
    return float(rolling_corr.iloc[-1]), window_days


def _build_intraday_compare(series_map: dict[str, pd.Series]) -> pd.DataFrame:
    usable = {code: series for code, series in series_map.items() if not series.empty}
    if not usable:
        return pd.DataFrame()

    merged = pd.concat(usable, axis=1).sort_index().ffill()
    normalized: dict[str, pd.Series] = {}
    for code in merged.columns:
        series = merged[code].dropna()
        if series.empty:
            continue
        base = float(series.iloc[0])
        if abs(base) < 1e-12:
            continue
        normalized[code] = (merged[code] / base) * 100.0

    if not normalized:
        return pd.DataFrame()
    return pd.DataFrame(normalized).dropna(how="all")


def build_screener_data(client: BinanceFuturesPublic) -> ScreenerData:
    rows: list[dict[str, Any]] = []
    intraday_map: dict[str, pd.Series] = {}
    daily_map: dict[str, pd.Series] = {}
    errors: list[str] = []

    for asset in DEFAULT_SCREENER_ASSETS:
        try:
            if asset.provider == "yahoo":
                row, intraday, daily = _yahoo_quote(asset)
            elif asset.provider == "binance":
                row, intraday, daily = _binance_quote(client, asset)
            else:
                raise RuntimeError(f"Unsupported provider: {asset.provider}")
        except Exception as exc:
            errors.append(f"{asset.code} ({asset.symbol}): {exc}")
            continue

        rows.append(row)
        intraday_map[asset.code] = intraday
        daily_map[asset.code] = daily

    if not rows:
        return ScreenerData(quotes_df=pd.DataFrame(), intraday_df=pd.DataFrame(), errors=errors)

    quotes_df = pd.DataFrame(rows)
    btc_series = daily_map.get("BTC", _empty_series())
    spx_series = daily_map.get("SPX", _empty_series())

    corr_to_btc: list[float] = []
    corr_to_spx: list[float] = []
    corr_window_days: list[int] = []
    for _, row in quotes_df.iterrows():
        code = str(row["code"])
        daily = daily_map.get(code, _empty_series())

        btc_corr, btc_window = (1.0, min(MAX_CORR_WINDOW_DAYS, max(0, len(daily) - 1))) if code == "BTC" else _rolling_corr(daily, btc_series)
        spx_corr, spx_window = (1.0, min(MAX_CORR_WINDOW_DAYS, max(0, len(daily) - 1))) if code == "SPX" else _rolling_corr(daily, spx_series)

        corr_to_btc.append(btc_corr)
        corr_to_spx.append(spx_corr)
        corr_window_days.append(max(btc_window, spx_window))

    quotes_df["corr_to_btc"] = corr_to_btc
    quotes_df["corr_to_spx"] = corr_to_spx
    quotes_df["corr_window_days"] = corr_window_days
    quotes_df = quotes_df.sort_values(["category", "code"]).reset_index(drop=True)

    intraday_df = _build_intraday_compare(intraday_map)
    return ScreenerData(quotes_df=quotes_df, intraday_df=intraday_df, errors=errors)
