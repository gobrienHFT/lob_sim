from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import requests


BASE_URL = "https://pro-api.coinmarketcap.com"
TIMEOUT = 12


@dataclass(frozen=True)
class CmcMover:
    base_asset: str
    cmc_name: str
    cmc_rank_1h: float
    cmc_rank_24h: float
    cmc_pct_1h: float
    cmc_pct_24h: float
    cmc_market_cap_usd: float
    cmc_volume_24h: float
    cmc_volume_to_mcap_pct: float
    cmc_mover_score: float
    cmc_mover_label: str


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float("nan")
    return parsed if not math.isnan(parsed) else float("nan")


def _request(api_key: str, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if not api_key:
        return []
    try:
        response = requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
    except Exception:
        return []
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return data if isinstance(data, list) else []


def _listing_rows(api_key: str, sort: str, limit: int) -> list[dict[str, Any]]:
    return _request(
        api_key,
        "/v1/cryptocurrency/listings/latest",
        {
            "start": 1,
            "limit": max(1, min(int(limit), 5000)),
            "sort": sort,
            "sort_dir": "desc",
            "convert": "USD",
            "aux": "num_market_pairs,cmc_rank,date_added",
        },
    )


def _mover_from_row(row: dict[str, Any], rank_1h: float, rank_24h: float) -> CmcMover:
    quote = row.get("quote", {})
    usd = quote.get("USD", {}) if isinstance(quote, dict) else {}
    pct_1h = _safe_float(usd.get("percent_change_1h"))
    pct_24h = _safe_float(usd.get("percent_change_24h"))
    market_cap = _safe_float(usd.get("market_cap"))
    volume_24h = _safe_float(usd.get("volume_24h"))
    volume_to_mcap = volume_24h / market_cap * 100.0 if market_cap and not math.isnan(market_cap) else float("nan")
    rank_score = 0.0
    if not math.isnan(rank_1h) and rank_1h > 0:
        rank_score = max(rank_score, max(0.0, 100.0 - (rank_1h - 1.0) * 2.0))
    if not math.isnan(rank_24h) and rank_24h > 0:
        rank_score = max(rank_score, max(0.0, 100.0 - (rank_24h - 1.0) * 1.5))
    velocity_score = max(
        0.0 if math.isnan(pct_1h) else min(100.0, max(0.0, pct_1h / 20.0 * 100.0)),
        0.0 if math.isnan(pct_24h) else min(100.0, max(0.0, pct_24h / 150.0 * 100.0)),
    )
    volume_score = 0.0 if math.isnan(volume_to_mcap) else min(100.0, max(0.0, volume_to_mcap / 300.0 * 100.0))
    mover_score = min(100.0, rank_score * 0.45 + velocity_score * 0.35 + volume_score * 0.20)
    labels: list[str] = []
    if not math.isnan(rank_1h):
        labels.append(f"CMC 1H #{int(rank_1h)}")
    if not math.isnan(rank_24h):
        labels.append(f"CMC 24H #{int(rank_24h)}")
    if not math.isnan(volume_to_mcap) and volume_to_mcap >= 100.0:
        labels.append("vol/mcap extreme")
    return CmcMover(
        base_asset=str(row.get("symbol", "")).upper(),
        cmc_name=str(row.get("name", "")),
        cmc_rank_1h=rank_1h,
        cmc_rank_24h=rank_24h,
        cmc_pct_1h=pct_1h,
        cmc_pct_24h=pct_24h,
        cmc_market_cap_usd=market_cap,
        cmc_volume_24h=volume_24h,
        cmc_volume_to_mcap_pct=volume_to_mcap,
        cmc_mover_score=mover_score,
        cmc_mover_label=" | ".join(labels),
    )


def fetch_cmc_movers(api_key: str, *, limit: int = 200) -> list[CmcMover]:
    by_symbol: dict[str, dict[str, Any]] = {}
    ranks_1h: dict[str, float] = {}
    ranks_24h: dict[str, float] = {}

    for rank, row in enumerate(_listing_rows(api_key, "percent_change_1h", limit), start=1):
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        by_symbol.setdefault(symbol, row)
        ranks_1h[symbol] = float(rank)

    for rank, row in enumerate(_listing_rows(api_key, "percent_change_24h", limit), start=1):
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        by_symbol.setdefault(symbol, row)
        ranks_24h[symbol] = float(rank)

    movers: list[CmcMover] = []
    for symbol, row in by_symbol.items():
        movers.append(
            _mover_from_row(
                row,
                ranks_1h.get(symbol, float("nan")),
                ranks_24h.get(symbol, float("nan")),
            )
        )
    return movers
