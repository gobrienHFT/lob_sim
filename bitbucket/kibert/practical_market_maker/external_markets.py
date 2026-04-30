from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import requests


USER_AGENT = "crime-pump-dashboard/1.0"
TIMEOUT = 10
EMFX_QUOTES = {
    "KRW",
    "TRY",
    "IDR",
    "THB",
    "VND",
    "BRL",
    "ARS",
    "NGN",
    "UAH",
}

COINGECKO_PLATFORM_CHAIN_IDS = {
    "ethereum": "1",
    "binance-smart-chain": "56",
    "polygon-pos": "137",
    "arbitrum-one": "42161",
    "optimistic-ethereum": "10",
    "avalanche": "43114",
    "base": "8453",
}
DWF_LABS_CATEGORY_ID = "dwf-labs-portfolio"
DWF_LABS_CATEGORY_URL = "https://www.coingecko.com/en/categories/dwf-labs-portfolio"


@dataclass(frozen=True)
class ExternalCrimeMetrics:
    base_asset: str
    normalized_base_asset: str
    dwf_labs_portfolio: bool
    dwf_labs_portfolio_score: float
    dwf_labs_portfolio_rank: float
    dwf_labs_portfolio_note: str
    coinbase_spot_listed: bool
    coinbase_spot_quote_volume_24h: float
    binance_spot_quote_volume_24h: float
    coingecko_total_volume_24h: float
    coingecko_coinbase_volume_24h: float
    coingecko_cex_volume_24h: float
    coingecko_dex_volume_24h: float
    kraken_spot_quote_volume_24h: float
    upbit_spot_quote_volume_24h: float
    upbit_krw_quote_volume_24h: float
    try_spot_quote_volume_24h: float
    emfx_spot_quote_volume_24h: float
    coinbase_volume_share_pct: float
    binance_volume_share_pct: float
    bitget_volume_share_pct: float
    gate_volume_share_pct: float
    kraken_volume_share_pct: float
    upbit_volume_share_pct: float
    krw_volume_share_pct: float
    try_volume_share_pct: float
    emfx_volume_share_pct: float
    dex_volume_share_pct: float
    binance_bitget_gate_share_pct: float
    coinbase_bid_depth_2pct_usd: float
    coinbase_ask_depth_2pct_usd: float
    coinbase_total_depth_2pct_usd: float
    coinbase_book_imbalance_pct: float
    coinbase_depth_to_volume_pct: float
    top_venue: str
    top_venue_volume_24h: float
    top_venue_volume_share_pct: float
    top3_venue_volume_share_pct: float
    venue_hhi: float
    venue_count: int
    cex_venue_count: int
    dex_venue_count: int
    coinbase_bid_ask_spread_pct: float
    coingecko_id: str
    coingecko_ath_usd: float
    coingecko_ath_change_pct: float
    coingecko_ath_date: str
    market_cap_usd: float
    fdv_usd: float
    fdv_to_market_cap: float
    circulating_supply_pct: float
    locked_supply_pct: float
    token_platform: str
    token_contract: str
    top10_holder_pct: float
    owner_holder_pct: float
    creator_holder_pct: float
    holder_count: float
    holder_source: str


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float("nan")
    return parsed if not math.isnan(parsed) else float("nan")


def _json_get(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def normalize_base_asset(base_asset: str) -> str:
    base = str(base_asset or "").upper().strip()
    for prefix in ("1000000", "1000"):
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix) :]
    return base


def _coinbase_products(session: requests.Session) -> dict[str, list[str]]:
    data = _json_get(session, "https://api.exchange.coinbase.com/products")
    by_base: dict[str, list[str]] = {}
    if not isinstance(data, list):
        return by_base

    preferred_quotes = {"USD", "USDC", "USDT"}
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).lower() != "online":
            continue
        quote = str(item.get("quote_currency", "")).upper()
        if quote not in preferred_quotes:
            continue
        base = str(item.get("base_currency", "")).upper()
        product_id = str(item.get("id", "")).upper()
        if base and product_id:
            by_base.setdefault(base, []).append(product_id)
    return by_base


def fetch_coinbase_spot_bases() -> set[str]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return set(_coinbase_products(session).keys())


def _coinbase_quote_volume(session: requests.Session, product_ids: list[str]) -> float:
    quote_volume = 0.0
    found = False
    for product_id in product_ids[:4]:
        stats = _json_get(session, f"https://api.exchange.coinbase.com/products/{product_id}/stats")
        if not isinstance(stats, dict):
            continue
        base_volume = _safe_float(stats.get("volume"))
        last_price = _safe_float(stats.get("last"))
        if math.isnan(last_price):
            last_price = _safe_float(stats.get("open"))
        if math.isnan(base_volume) or math.isnan(last_price):
            continue
        quote_volume += base_volume * last_price
        found = True
        time.sleep(0.05)
    return quote_volume if found else float("nan")


def _coinbase_book_metrics(session: requests.Session, product_ids: list[str]) -> dict[str, float]:
    best_metrics: dict[str, float] = {
        "bid_depth_2pct": float("nan"),
        "ask_depth_2pct": float("nan"),
        "total_depth_2pct": float("nan"),
        "book_imbalance_pct": float("nan"),
        "depth_to_volume_pct": float("nan"),
        "spread_pct": float("nan"),
    }
    best_depth = -1.0
    for product_id in product_ids[:4]:
        book = _json_get(
            session,
            f"https://api.exchange.coinbase.com/products/{product_id}/book",
            {"level": 2},
        )
        if not isinstance(book, dict):
            continue
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            continue
        try:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
        except Exception:
            continue
        if best_bid <= 0 or best_ask <= 0:
            continue
        mid = (best_bid + best_ask) / 2.0
        bid_limit = mid * 0.98
        ask_limit = mid * 1.02
        bid_depth = 0.0
        ask_depth = 0.0
        for row in bids:
            try:
                price = float(row[0])
                size = float(row[1])
            except Exception:
                continue
            if price < bid_limit:
                break
            bid_depth += price * size
        for row in asks:
            try:
                price = float(row[0])
                size = float(row[1])
            except Exception:
                continue
            if price > ask_limit:
                break
            ask_depth += price * size
        total_depth = bid_depth + ask_depth
        if total_depth <= best_depth:
            continue
        volume = _coinbase_quote_volume(session, [product_id])
        best_depth = total_depth
        best_metrics = {
            "bid_depth_2pct": bid_depth,
            "ask_depth_2pct": ask_depth,
            "total_depth_2pct": total_depth,
            "book_imbalance_pct": bid_depth / total_depth * 100.0 if total_depth > 0 else float("nan"),
            "depth_to_volume_pct": total_depth / volume * 100.0 if not math.isnan(volume) and volume > 0 else float("nan"),
            "spread_pct": (best_ask - best_bid) / mid * 100.0,
        }
        time.sleep(0.05)
    return best_metrics


def _binance_spot_quote_volume(session: requests.Session, normalized_base: str) -> float:
    for quote in ("USDT", "USDC"):
        data = _json_get(
            session,
            "https://api.binance.com/api/v3/ticker/24hr",
            {"symbol": f"{normalized_base}{quote}"},
        )
        if isinstance(data, dict):
            quote_volume = _safe_float(data.get("quoteVolume"))
            if not math.isnan(quote_volume):
                return quote_volume
    return float("nan")


def _coingecko_coin(session: requests.Session, normalized_base: str) -> dict[str, Any]:
    search = _json_get(session, "https://api.coingecko.com/api/v3/search", {"query": normalized_base})
    coins = search.get("coins", []) if isinstance(search, dict) else []
    matches = [
        coin
        for coin in coins
        if str(coin.get("symbol", "")).upper() == normalized_base
    ]
    if not matches:
        return {}
    matches.sort(key=lambda coin: coin.get("market_cap_rank") or 10**9)
    coin_id = str(matches[0].get("id", ""))
    if not coin_id:
        return {}
    time.sleep(0.10)
    detail = _json_get(
        session,
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
    )
    return detail if isinstance(detail, dict) else {}


def _coingecko_category_market_rows(session: requests.Session, category_id: str, *, max_pages: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rank_offset = 0
    for page in range(1, max_pages + 1):
        data = _json_get(
            session,
            "https://api.coingecko.com/api/v3/coins/markets",
            {
                "vs_currency": "usd",
                "category": category_id,
                "order": "market_cap_desc",
                "per_page": 250,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        if not isinstance(data, list):
            break
        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            symbol = normalize_base_asset(str(item.get("symbol", "")).upper())
            coin_id = str(item.get("id", "") or "")
            if not symbol or not coin_id:
                continue
            rank = rank_offset + idx
            rows.append(
                {
                    "coingecko_id": coin_id,
                    "normalized_base_asset": symbol,
                    "name": str(item.get("name", "") or ""),
                    "rank": rank,
                    "market_cap_rank": item.get("market_cap_rank"),
                    "market_cap_usd": _safe_float(item.get("market_cap")),
                    "volume_24h_usd": _safe_float(item.get("total_volume")),
                }
            )
        if len(data) < 250:
            break
        rank_offset += len(data)
        time.sleep(0.12)
    return rows


def _category_lookup(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        coin_id = str(row.get("coingecko_id", "") or "")
        symbol = str(row.get("normalized_base_asset", "") or "")
        if coin_id and coin_id not in by_id:
            by_id[coin_id] = row
        if symbol and (symbol not in by_symbol or _safe_float(row.get("rank")) < _safe_float(by_symbol[symbol].get("rank"))):
            by_symbol[symbol] = row
    return by_id, by_symbol


def fetch_dwf_labs_portfolio_members() -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _coingecko_category_market_rows(session, DWF_LABS_CATEGORY_ID)


def _dwf_membership_score(rank: float) -> float:
    if math.isnan(rank):
        return 82.0
    # Category membership matters more than exact rank; rank just separates the
    # most visible DWF names from the long tail without letting majors dominate.
    return max(70.0, min(95.0, 95.0 - min(rank, 150.0) * 0.12))


def _market_value_usd(market_data: dict[str, Any], key: str) -> float:
    value = market_data.get(key)
    if isinstance(value, dict):
        return _safe_float(value.get("usd"))
    return _safe_float(value)


def _extract_contract(detail: dict[str, Any]) -> tuple[str, str]:
    platforms = detail.get("platforms", {}) if isinstance(detail, dict) else {}
    if not isinstance(platforms, dict):
        return "", ""
    for platform, chain_id in COINGECKO_PLATFORM_CHAIN_IDS.items():
        contract = str(platforms.get(platform, "") or "").strip()
        if contract and contract != "0x0000000000000000000000000000000000000000":
            return platform, contract
    return "", ""


def _is_dex_market(identifier: str, name: str) -> bool:
    text = f"{identifier} {name}".lower()
    dex_markers = (
        "uniswap",
        "pancake",
        "aerodrome",
        "raydium",
        "orca",
        "jupiter",
        "sushi",
        "curve",
        "balancer",
        "camelot",
        "trader joe",
        "traderjoe",
        "meteora",
        "pumpswap",
        "spookyswap",
        "quickswap",
        "velodrome",
        "clmm",
        "v2",
        "v3",
        "v4",
    )
    return any(marker in text for marker in dex_markers)


def _venue_matches(identifier: str, name: str, *, canonical: str) -> bool:
    lower_identifier = str(identifier or "").strip().lower()
    lower_name = str(name or "").strip().lower()
    if canonical == "binance":
        return "binance" in lower_identifier or "binance" in lower_name
    if canonical == "bitget":
        return "bitget" in lower_identifier or "bitget" in lower_name
    if canonical == "gate":
        return (
            lower_identifier in {"gate", "gate_io", "gate-io"}
            or "gate.io" in lower_name
            or lower_name.startswith("gate")
        )
    if canonical == "kraken":
        return "kraken" in lower_identifier or "kraken" in lower_name
    if canonical == "upbit":
        return "upbit" in lower_identifier or "upbit" in lower_name
    if canonical == "coinbase":
        return lower_identifier == "gdax" or "coinbase" in lower_name
    return False


def _empty_ticker_metrics() -> dict[str, Any]:
    return {
        "total_volume": float("nan"),
        "coinbase_volume": float("nan"),
        "cex_volume": float("nan"),
        "dex_volume": float("nan"),
        "kraken_volume": float("nan"),
        "upbit_volume": float("nan"),
        "upbit_krw_volume": float("nan"),
        "try_volume": float("nan"),
        "emfx_volume": float("nan"),
        "binance_volume": float("nan"),
        "bitget_volume": float("nan"),
        "gate_volume": float("nan"),
        "coinbase_share_pct": float("nan"),
        "binance_share_pct": float("nan"),
        "bitget_share_pct": float("nan"),
        "gate_share_pct": float("nan"),
        "kraken_share_pct": float("nan"),
        "upbit_share_pct": float("nan"),
        "krw_share_pct": float("nan"),
        "try_share_pct": float("nan"),
        "emfx_share_pct": float("nan"),
        "dex_share_pct": float("nan"),
        "binance_bitget_gate_share_pct": float("nan"),
        "top_venue": "",
        "top_venue_volume": float("nan"),
        "top_venue_share_pct": float("nan"),
        "top3_venue_share_pct": float("nan"),
        "venue_hhi": float("nan"),
        "venue_count": 0,
        "cex_venue_count": 0,
        "dex_venue_count": 0,
        "coinbase_spread_pct": float("nan"),
    }


def _summarize_tickers(tickers: list[dict[str, Any]]) -> dict[str, Any]:
    if not tickers:
        return _empty_ticker_metrics()

    venue_volumes: dict[str, float] = {}
    total_volume = 0.0
    coinbase_volume = 0.0
    binance_volume = 0.0
    bitget_volume = 0.0
    gate_volume = 0.0
    kraken_volume = 0.0
    upbit_volume = 0.0
    upbit_krw_volume = 0.0
    krw_volume = 0.0
    try_volume = 0.0
    emfx_volume = 0.0
    cex_volume = 0.0
    dex_volume = 0.0
    cex_venues: set[str] = set()
    dex_venues: set[str] = set()
    coinbase_spreads: list[float] = []

    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        if ticker.get("is_stale") or ticker.get("is_anomaly"):
            continue
        converted_volume = ticker.get("converted_volume", {})
        volume_usd = _safe_float(converted_volume.get("usd") if isinstance(converted_volume, dict) else None)
        if math.isnan(volume_usd) or volume_usd <= 0:
            continue

        market = ticker.get("market", {})
        venue_name = str(market.get("name", "") if isinstance(market, dict) else "").strip() or "Unknown"
        identifier = str(market.get("identifier", "") if isinstance(market, dict) else "").strip().lower()
        target = str(ticker.get("target", "")).upper()
        is_coinbase = _venue_matches(identifier, venue_name, canonical="coinbase")
        is_binance = _venue_matches(identifier, venue_name, canonical="binance")
        is_bitget = _venue_matches(identifier, venue_name, canonical="bitget")
        is_gate = _venue_matches(identifier, venue_name, canonical="gate")
        is_kraken = _venue_matches(identifier, venue_name, canonical="kraken")
        is_upbit = _venue_matches(identifier, venue_name, canonical="upbit")
        is_dex = _is_dex_market(identifier, venue_name)

        total_volume += volume_usd
        venue_volumes[venue_name] = venue_volumes.get(venue_name, 0.0) + volume_usd
        if is_coinbase:
            coinbase_volume += volume_usd
            spread = _safe_float(ticker.get("bid_ask_spread_percentage"))
            if not math.isnan(spread):
                coinbase_spreads.append(spread)
        if is_binance:
            binance_volume += volume_usd
        if is_bitget:
            bitget_volume += volume_usd
        if is_gate:
            gate_volume += volume_usd
        if is_kraken:
            kraken_volume += volume_usd
        if is_upbit:
            upbit_volume += volume_usd
            if target == "KRW":
                upbit_krw_volume += volume_usd
        if target == "KRW":
            krw_volume += volume_usd
        if target == "TRY":
            try_volume += volume_usd
        if target in EMFX_QUOTES:
            emfx_volume += volume_usd
        if is_dex:
            dex_volume += volume_usd
            dex_venues.add(venue_name)
        else:
            cex_volume += volume_usd
            cex_venues.add(venue_name)

    if total_volume <= 0:
        return _empty_ticker_metrics()

    ranked_venues = sorted(venue_volumes.items(), key=lambda item: item[1], reverse=True)
    top_venue, top_venue_volume = ranked_venues[0]
    top3_volume = sum(volume for _, volume in ranked_venues[:3])
    venue_hhi = sum(((volume / total_volume) * 100.0) ** 2 for volume in venue_volumes.values())
    coinbase_spread = min(coinbase_spreads) if coinbase_spreads else float("nan")
    binance_bitget_gate_volume = binance_volume + bitget_volume + gate_volume
    return {
        "total_volume": total_volume,
        "coinbase_volume": coinbase_volume if coinbase_volume > 0 else float("nan"),
        "cex_volume": cex_volume if cex_volume > 0 else float("nan"),
        "dex_volume": dex_volume if dex_volume > 0 else float("nan"),
        "kraken_volume": kraken_volume if kraken_volume > 0 else float("nan"),
        "upbit_volume": upbit_volume if upbit_volume > 0 else float("nan"),
        "upbit_krw_volume": upbit_krw_volume if upbit_krw_volume > 0 else float("nan"),
        "try_volume": try_volume if try_volume > 0 else float("nan"),
        "emfx_volume": emfx_volume if emfx_volume > 0 else float("nan"),
        "binance_volume": binance_volume if binance_volume > 0 else float("nan"),
        "bitget_volume": bitget_volume if bitget_volume > 0 else float("nan"),
        "gate_volume": gate_volume if gate_volume > 0 else float("nan"),
        "coinbase_share_pct": coinbase_volume / total_volume * 100.0,
        "binance_share_pct": binance_volume / total_volume * 100.0,
        "bitget_share_pct": bitget_volume / total_volume * 100.0,
        "gate_share_pct": gate_volume / total_volume * 100.0,
        "kraken_share_pct": kraken_volume / total_volume * 100.0,
        "upbit_share_pct": upbit_volume / total_volume * 100.0,
        "krw_share_pct": krw_volume / total_volume * 100.0,
        "try_share_pct": try_volume / total_volume * 100.0,
        "emfx_share_pct": emfx_volume / total_volume * 100.0,
        "dex_share_pct": dex_volume / total_volume * 100.0,
        "binance_bitget_gate_share_pct": binance_bitget_gate_volume / total_volume * 100.0,
        "top_venue": top_venue,
        "top_venue_volume": top_venue_volume,
        "top_venue_share_pct": top_venue_volume / total_volume * 100.0,
        "top3_venue_share_pct": top3_volume / total_volume * 100.0,
        "venue_hhi": venue_hhi,
        "venue_count": len(ranked_venues),
        "cex_venue_count": len(cex_venues),
        "dex_venue_count": len(dex_venues),
        "coinbase_spread_pct": coinbase_spread,
    }


def _coingecko_ticker_metrics(session: requests.Session, coin_id: str) -> dict[str, Any]:
    if not coin_id:
        return _empty_ticker_metrics()

    data = _json_get(
        session,
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/tickers",
        {"include_exchange_logo": "false", "depth": "false", "order": "volume_desc", "page": 1},
    )
    tickers = data.get("tickers", []) if isinstance(data, dict) else []
    return _summarize_tickers(tickers if isinstance(tickers, list) else [])


def _goplus_holder_metrics(session: requests.Session, platform: str, contract: str) -> tuple[float, float, float, str]:
    chain_id = COINGECKO_PLATFORM_CHAIN_IDS.get(platform)
    if not chain_id or not contract:
        return float("nan"), float("nan"), float("nan"), float("nan"), ""
    data = _json_get(
        session,
        f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
        {"contract_addresses": contract},
    )
    result = data.get("result", {}) if isinstance(data, dict) else {}
    token = result.get(contract.lower()) or result.get(contract) if isinstance(result, dict) else {}
    if not isinstance(token, dict):
        return float("nan"), float("nan"), float("nan"), float("nan"), ""

    top10 = _safe_float(token.get("top_10_holder_rate"))
    owner = _safe_float(token.get("owner_percent"))
    creator = _safe_float(token.get("creator_percent"))
    holder_count = _safe_float(token.get("holder_count"))

    def ratio_to_pct(value: float) -> float:
        if math.isnan(value):
            return float("nan")
        return value * 100.0 if value <= 1.0 else value

    return ratio_to_pct(top10), ratio_to_pct(owner), ratio_to_pct(creator), holder_count, f"GoPlus chain {chain_id}"


def fetch_external_crime_metrics(base_assets: list[str]) -> list[ExternalCrimeMetrics]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    coinbase_by_base = _coinbase_products(session)
    dwf_by_id, dwf_by_symbol = _category_lookup(_coingecko_category_market_rows(session, DWF_LABS_CATEGORY_ID))
    rows: list[ExternalCrimeMetrics] = []

    seen: set[str] = set()
    for raw_base in base_assets:
        base = str(raw_base or "").upper().strip()
        normalized = normalize_base_asset(base)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        coinbase_products = coinbase_by_base.get(normalized, [])
        coinbase_volume = _coinbase_quote_volume(session, coinbase_products) if coinbase_products else float("nan")
        coinbase_book = _coinbase_book_metrics(session, coinbase_products) if coinbase_products else {
            "bid_depth_2pct": float("nan"),
            "ask_depth_2pct": float("nan"),
            "total_depth_2pct": float("nan"),
            "book_imbalance_pct": float("nan"),
            "depth_to_volume_pct": float("nan"),
            "spread_pct": float("nan"),
        }
        binance_spot_volume = _binance_spot_quote_volume(session, normalized)

        detail = _coingecko_coin(session, normalized)
        coin_id = str(detail.get("id", "")) if isinstance(detail, dict) else ""
        dwf_member = dwf_by_id.get(coin_id) or dwf_by_symbol.get(normalized) or {}
        dwf_rank = _safe_float(dwf_member.get("rank")) if dwf_member else float("nan")
        dwf_flag = bool(dwf_member)
        dwf_score = _dwf_membership_score(dwf_rank) if dwf_flag else 0.0
        dwf_note = (
            f"DWF Labs CoinGecko portfolio member"
            + (f" #{int(dwf_rank)}" if not math.isnan(dwf_rank) else "")
            + f" ({DWF_LABS_CATEGORY_URL})"
            if dwf_flag
            else ""
        )
        ticker_metrics = _coingecko_ticker_metrics(session, coin_id)
        market_data = detail.get("market_data", {}) if isinstance(detail, dict) else {}
        market_cap = _market_value_usd(market_data, "market_cap") if isinstance(market_data, dict) else float("nan")
        fdv = _market_value_usd(market_data, "fully_diluted_valuation") if isinstance(market_data, dict) else float("nan")
        ath = _market_value_usd(market_data, "ath") if isinstance(market_data, dict) else float("nan")
        ath_change = _market_value_usd(market_data, "ath_change_percentage") if isinstance(market_data, dict) else float("nan")
        ath_date_value = market_data.get("ath_date", {}) if isinstance(market_data, dict) else {}
        ath_date = str(ath_date_value.get("usd", "") if isinstance(ath_date_value, dict) else "")
        circulating = _safe_float(market_data.get("circulating_supply")) if isinstance(market_data, dict) else float("nan")
        total_supply = _safe_float(market_data.get("total_supply")) if isinstance(market_data, dict) else float("nan")
        max_supply = _safe_float(market_data.get("max_supply")) if isinstance(market_data, dict) else float("nan")
        supply_denom = total_supply if not math.isnan(total_supply) and total_supply > 0 else max_supply
        if math.isnan(circulating) or math.isnan(supply_denom) or supply_denom <= 0:
            circulating_pct = float("nan")
            locked_pct = float("nan")
        else:
            circulating_pct = min(100.0, max(0.0, circulating / supply_denom * 100.0))
            locked_pct = max(0.0, 100.0 - circulating_pct)
        fdv_to_market_cap = fdv / market_cap if not math.isnan(fdv) and not math.isnan(market_cap) and market_cap > 0 else float("nan")

        platform, contract = _extract_contract(detail)
        top10, owner, creator, holder_count, holder_source = _goplus_holder_metrics(session, platform, contract)
        spread_candidates = [
            value
            for value in (ticker_metrics["coinbase_spread_pct"], coinbase_book["spread_pct"])
            if not math.isnan(value) and value >= 0
        ]
        coinbase_spread = min(spread_candidates) if spread_candidates else float("nan")

        rows.append(
            ExternalCrimeMetrics(
                base_asset=base,
                normalized_base_asset=normalized,
                dwf_labs_portfolio=dwf_flag,
                dwf_labs_portfolio_score=dwf_score,
                dwf_labs_portfolio_rank=dwf_rank,
                dwf_labs_portfolio_note=dwf_note,
                coinbase_spot_listed=bool(coinbase_products),
                coinbase_spot_quote_volume_24h=coinbase_volume,
                binance_spot_quote_volume_24h=binance_spot_volume,
                coingecko_total_volume_24h=ticker_metrics["total_volume"],
                coingecko_coinbase_volume_24h=ticker_metrics["coinbase_volume"],
                coingecko_cex_volume_24h=ticker_metrics["cex_volume"],
                coingecko_dex_volume_24h=ticker_metrics["dex_volume"],
                kraken_spot_quote_volume_24h=ticker_metrics["kraken_volume"],
                upbit_spot_quote_volume_24h=ticker_metrics["upbit_volume"],
                upbit_krw_quote_volume_24h=ticker_metrics["upbit_krw_volume"],
                try_spot_quote_volume_24h=ticker_metrics["try_volume"],
                emfx_spot_quote_volume_24h=ticker_metrics["emfx_volume"],
                coinbase_volume_share_pct=ticker_metrics["coinbase_share_pct"],
                binance_volume_share_pct=ticker_metrics["binance_share_pct"],
                bitget_volume_share_pct=ticker_metrics["bitget_share_pct"],
                gate_volume_share_pct=ticker_metrics["gate_share_pct"],
                kraken_volume_share_pct=ticker_metrics["kraken_share_pct"],
                upbit_volume_share_pct=ticker_metrics["upbit_share_pct"],
                krw_volume_share_pct=ticker_metrics["krw_share_pct"],
                try_volume_share_pct=ticker_metrics["try_share_pct"],
                emfx_volume_share_pct=ticker_metrics["emfx_share_pct"],
                dex_volume_share_pct=ticker_metrics["dex_share_pct"],
                binance_bitget_gate_share_pct=ticker_metrics["binance_bitget_gate_share_pct"],
                coinbase_bid_depth_2pct_usd=coinbase_book["bid_depth_2pct"],
                coinbase_ask_depth_2pct_usd=coinbase_book["ask_depth_2pct"],
                coinbase_total_depth_2pct_usd=coinbase_book["total_depth_2pct"],
                coinbase_book_imbalance_pct=coinbase_book["book_imbalance_pct"],
                coinbase_depth_to_volume_pct=coinbase_book["depth_to_volume_pct"],
                top_venue=ticker_metrics["top_venue"],
                top_venue_volume_24h=ticker_metrics["top_venue_volume"],
                top_venue_volume_share_pct=ticker_metrics["top_venue_share_pct"],
                top3_venue_volume_share_pct=ticker_metrics["top3_venue_share_pct"],
                venue_hhi=ticker_metrics["venue_hhi"],
                venue_count=int(ticker_metrics["venue_count"]),
                cex_venue_count=int(ticker_metrics["cex_venue_count"]),
                dex_venue_count=int(ticker_metrics["dex_venue_count"]),
                coinbase_bid_ask_spread_pct=coinbase_spread,
                coingecko_id=coin_id,
                coingecko_ath_usd=ath,
                coingecko_ath_change_pct=ath_change,
                coingecko_ath_date=ath_date,
                market_cap_usd=market_cap,
                fdv_usd=fdv,
                fdv_to_market_cap=fdv_to_market_cap,
                circulating_supply_pct=circulating_pct,
                locked_supply_pct=locked_pct,
                token_platform=platform,
                token_contract=contract,
                top10_holder_pct=top10,
                owner_holder_pct=owner,
                creator_holder_pct=creator,
                holder_count=holder_count,
                holder_source=holder_source,
            )
        )
        time.sleep(0.12)
    return rows
