from __future__ import annotations

import hashlib
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from binance_futures import BinanceHTTPError, BinanceFuturesPublic
from breakouts import BreakoutRow, levels_from_klines
from cmc_movers import fetch_cmc_movers
from convexity_scoring import CONVEXITY_SCORE_COLUMNS, apply_convexity_model
from crime_scoring import LIFECYCLE_SCORE_COLUMNS, apply_lifecycle_model
from external_markets import (
    fetch_coinbase_spot_bases,
    fetch_dwf_labs_portfolio_members,
    fetch_external_crime_metrics,
    normalize_base_asset,
)
from pnl import PnLDashboardResult, build_pnl_dashboard_data
from short_squeeze_scoring import SHORT_SQUEEZE_SCORE_COLUMNS, apply_short_squeeze_model
from screener import ScreenerData, build_screener_data

APP_DIR = Path(__file__).resolve().parent
USD_LIKE_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP"}
RANGE_PRESETS = ("7D", "30D", "90D", "YTD", "1Y", "3Y", "ITD", "Custom")
TRADFI_ALWAYS_INCLUDE_TYPES = {"COMMODITY", "EQUITY"}
DEFAULT_CRIME_EXCLUDED_BASES = (
    "BTC,ETH,XRP,BNB,SOL,DOGE,ADA,TRX,LINK,LTC,BCH,DOT,AVAX,TON,SHIB,HBAR,XLM,ETC,ICP,NEAR,APT,ARB,OP"
)
DEFAULT_CRIME_FORCE_SYMBOLS = "RAVEUSDT,FIGHTUSDT,CHIPUSDT,SIRENUSDT,STOUSDT,HIGHUSDT,RIVERUSDT,PIPPINUSDT"
MM_PROXIMITY_COLUMNS = [
    "mm_proximity_score",
    "mm_proximity_maker",
    "mm_proximity_note",
    "mm_proximity_source",
]
MM_PROXIMITY_TEXT_COLUMNS = {"mm_proximity_maker", "mm_proximity_note", "mm_proximity_source"}
INVENTORY_TRANSFER_COLUMNS = [
    "spot_volume_to_mcap_pct",
    "perp_volume_to_mcap_pct",
    "oi_to_market_cap_pct",
    "inventory_sponsor_mismatch_score",
    "inventory_transfer_risk_score",
    "inventory_transfer_risk_flag",
    "inventory_transfer_note",
]
INVENTORY_TRANSFER_TEXT_COLUMNS = {"inventory_transfer_note"}
CMC_MOVER_COLUMNS = [
    "cmc_name",
    "cmc_rank_1h",
    "cmc_rank_24h",
    "cmc_pct_1h",
    "cmc_pct_24h",
    "cmc_market_cap_usd",
    "cmc_volume_24h",
    "cmc_volume_to_mcap_pct",
    "cmc_mover_score",
    "cmc_mover_label",
]
CMC_MOVER_TEXT_COLUMNS = {"cmc_name", "cmc_mover_label"}
DWF_LABS_CATEGORY_URL = "https://www.coingecko.com/en/categories/dwf-labs-portfolio"
DWF_LABS_PORTFOLIO_COLUMNS = [
    "dwf_labs_portfolio",
    "dwf_labs_portfolio_score",
    "dwf_labs_portfolio_rank",
    "dwf_labs_portfolio_note",
]


def _load_local_env() -> None:
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _env_value(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _key_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_usd(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _format_pct(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.2f}%"


def _format_pnl(value: float, currency: str | None) -> str:
    if currency in (None, "USD-like"):
        return _format_usd(value)
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.2f} {currency}"


def _display_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Return only available display columns so optional enrichments cannot crash a table."""
    source = df.loc[:, ~df.columns.duplicated()].copy()
    existing_columns: list[str] = []
    seen: set[str] = set()
    for column in columns:
        if column in source.columns and column not in seen:
            existing_columns.append(column)
            seen.add(column)
    if not existing_columns:
        return source
    return source.loc[:, existing_columns].copy()


def _period_label(label: str, complete: bool) -> str:
    return label if complete else f"{label}*"


def _open_position_count(account: dict[str, Any]) -> int:
    count = 0
    for position in account.get("positions", []):
        if abs(_safe_float(position.get("positionAmt"))) > 0:
            count += 1
    return count


def _client(*, api_key: str = "", api_secret: str = "") -> BinanceFuturesPublic:
    return BinanceFuturesPublic(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        requests_per_second=REQUESTS_PER_SECOND,
        retries=RETRIES,
        api_key=api_key,
        api_secret=api_secret,
        recv_window=BINANCE_RECV_WINDOW,
    )


def _period_complete(result: PnLDashboardResult, start_dt: datetime) -> bool:
    if result.coverage_start is None:
        return False
    return result.coverage_start.to_pydatetime() <= start_dt


def _range_bounds(
    preset: str,
    *,
    coverage_start: pd.Timestamp,
    coverage_end: pd.Timestamp,
) -> tuple[datetime, datetime]:
    end_dt = coverage_end.to_pydatetime()
    if preset == "7D":
        return end_dt - timedelta(days=6), end_dt
    if preset == "30D":
        return end_dt - timedelta(days=29), end_dt
    if preset == "90D":
        return end_dt - timedelta(days=89), end_dt
    if preset == "YTD":
        return datetime(end_dt.year, 1, 1, tzinfo=timezone.utc), end_dt
    if preset == "1Y":
        return end_dt - timedelta(days=365), end_dt
    if preset == "3Y":
        return end_dt - timedelta(days=365 * 3), end_dt
    if preset == "ITD":
        return coverage_start.to_pydatetime(), end_dt
    return coverage_start.to_pydatetime(), end_dt


def _to_utc_date(dt: datetime | pd.Timestamp) -> date:
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


def _date_to_utc(dt: date, *, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def _filter_income_frame(df: pd.DataFrame, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    mask = (df["time"] >= pd.Timestamp(start_dt)) & (df["time"] <= pd.Timestamp(end_dt))
    return df.loc[mask].copy()


def _filter_daily_frame(df: pd.DataFrame, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    mask = (df["date"] >= pd.Timestamp(start_dt).normalize()) & (df["date"] <= pd.Timestamp(end_dt).normalize())
    return df.loc[mask].copy()


def _complete_daily_frame(df: pd.DataFrame, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    idx = pd.date_range(
        pd.Timestamp(start_dt).normalize(),
        pd.Timestamp(end_dt).normalize(),
        freq="D",
        tz="UTC",
    )
    if df.empty:
        filled = pd.DataFrame({"date": idx})
        filled["net_pnl"] = 0.0
        filled["positive_pnl"] = 0.0
        filled["negative_pnl"] = 0.0
        filled["events"] = 0
        filled["cumulative_pnl"] = 0.0
        return filled

    reindexed = df.set_index("date").reindex(idx, fill_value=0.0)
    reindexed.index.name = "date"
    filled = reindexed.reset_index()
    filled["events"] = filled["events"].astype(int)
    filled["cumulative_pnl"] = filled["net_pnl"].cumsum()
    return filled


def _metric_period_total(daily_df: pd.DataFrame, start_dt: datetime) -> float:
    if daily_df.empty:
        return 0.0
    mask = daily_df["date"] >= pd.Timestamp(start_dt).normalize()
    return float(daily_df.loc[mask, "net_pnl"].sum())


def _baseline_balance(result: PnLDashboardResult) -> float:
    balances = result.current_balances_df
    if balances.empty:
        return max(abs(_safe_float(result.account.get("totalWalletBalance"))), 1.0)
    if result.headline_currency in (None, "USD-like"):
        usd_like = balances[balances["asset"].isin(USD_LIKE_ASSETS)]
        if not usd_like.empty:
            return max(abs(float(usd_like["wallet_balance"].sum())), 1.0)
    if result.headline_currency and result.headline_currency != "USD-like":
        match = balances[balances["asset"] == result.headline_currency]
        if not match.empty:
            return max(abs(float(match["wallet_balance"].sum())), 1.0)
    return max(abs(_safe_float(result.account.get("totalWalletBalance"))), 1.0)


def _build_period_stats(
    income_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    *,
    baseline_balance: float,
) -> dict[str, float]:
    net_pnl = float(income_df["income"].sum()) if not income_df.empty else 0.0
    total_profit = float(income_df.loc[income_df["income"] > 0, "income"].sum()) if not income_df.empty else 0.0
    total_loss = abs(float(income_df.loc[income_df["income"] < 0, "income"].sum())) if not income_df.empty else 0.0
    winning_days = int((daily_df["net_pnl"] > 0).sum()) if not daily_df.empty else 0
    losing_days = int((daily_df["net_pnl"] < 0).sum()) if not daily_df.empty else 0
    breakeven_days = int((daily_df["net_pnl"] == 0).sum()) if not daily_df.empty else 0
    avg_profit = total_profit / winning_days if winning_days else 0.0
    avg_loss = total_loss / losing_days if losing_days else 0.0
    profit_loss_ratio = avg_profit / avg_loss if avg_loss else 0.0
    best_day = float(daily_df["net_pnl"].max()) if not daily_df.empty else 0.0
    worst_day = float(daily_df["net_pnl"].min()) if not daily_df.empty else 0.0
    event_count = int(len(income_df))
    active_assets = int(income_df["asset"].nunique()) if not income_df.empty else 0
    active_symbols = int(income_df.loc[income_df["symbol"] != "", "symbol"].nunique()) if not income_df.empty else 0
    return_pct = (net_pnl / baseline_balance * 100.0) if baseline_balance else 0.0
    max_drawdown = 0.0
    if not daily_df.empty:
        cumulative = daily_df["cumulative_pnl"]
        drawdown = cumulative - cumulative.cummax()
        max_drawdown = abs(float(drawdown.min()))

    return {
        "net_pnl": net_pnl,
        "total_profit": total_profit,
        "total_loss": total_loss,
        "winning_days": winning_days,
        "losing_days": losing_days,
        "breakeven_days": breakeven_days,
        "avg_profit": avg_profit,
        "avg_loss": avg_loss,
        "profit_loss_ratio": profit_loss_ratio,
        "best_day": best_day,
        "worst_day": worst_day,
        "event_count": event_count,
        "active_assets": active_assets,
        "active_symbols": active_symbols,
        "return_pct": return_pct,
        "max_drawdown": max_drawdown,
    }


def _credential_diagnostics(api_key: str, api_secret: str) -> list[str]:
    notes: list[str] = []
    if not api_key:
        notes.append("`BINANCE_API_KEY` is empty.")
    if not api_secret:
        notes.append("`BINANCE_API_SECRET` is empty.")
    if api_key and len(api_key) < 32:
        notes.append("The API key length looks unusually short.")
    if api_secret and len(api_secret) not in (64, 128):
        notes.append(f"The API secret length is {len(api_secret)}, which is unusual for a Binance HMAC secret.")
    return notes


def _render_stat_grid(stats: dict[str, float], currency: str | None) -> None:
    row_one = st.columns(4)
    row_one[0].metric("Selected net PnL", _format_pnl(stats["net_pnl"], currency))
    row_one[1].metric("Selected return", _format_pct(stats["return_pct"]))
    row_one[2].metric("Winning days", str(int(stats["winning_days"])))
    row_one[3].metric("Losing days", str(int(stats["losing_days"])))

    row_two = st.columns(4)
    row_two[0].metric("Total profit", _format_pnl(stats["total_profit"], currency))
    row_two[1].metric("Total loss", _format_pnl(-stats["total_loss"], currency))
    row_two[2].metric("Avg profit day", _format_pnl(stats["avg_profit"], currency))
    row_two[3].metric("Avg loss day", _format_pnl(-stats["avg_loss"], currency))

    row_three = st.columns(4)
    row_three[0].metric("Best day", _format_pnl(stats["best_day"], currency))
    row_three[1].metric("Worst day", _format_pnl(stats["worst_day"], currency))
    row_three[2].metric("Profit/loss ratio", f"{stats['profit_loss_ratio']:.2f}")
    row_three[3].metric("Max drawdown", _format_pnl(-stats["max_drawdown"], currency))

    row_four = st.columns(4)
    row_four[0].metric("Breakeven days", str(int(stats["breakeven_days"])))
    row_four[1].metric("Income events", str(int(stats["event_count"])))
    row_four[2].metric("Active assets", str(int(stats["active_assets"])))
    row_four[3].metric("Active symbols", str(int(stats["active_symbols"])))


def _closed_daily_close_series(klines: list[list[Any]]) -> pd.Series:
    if len(klines) < 2:
        return pd.Series(dtype=float)

    frame = pd.DataFrame(klines[:-1], columns=list(range(len(klines[0]))))
    close_series = pd.Series(
        pd.to_numeric(frame[4], errors="coerce").values,
        index=pd.to_datetime(frame[0], unit="ms", utc=True),
    ).dropna()
    close_series = close_series[~close_series.index.duplicated(keep="last")]
    return close_series.sort_index()


def _latest_btc_correlation(symbol: str, symbol_klines: list[list[Any]], btc_klines: list[list[Any]]) -> tuple[float, int]:
    if symbol.upper() == "BTCUSDT":
        btc_close = _closed_daily_close_series(btc_klines)
        btc_returns = btc_close.pct_change().dropna()
        return 1.0, min(180, len(btc_returns))

    symbol_close = _closed_daily_close_series(symbol_klines)
    btc_close = _closed_daily_close_series(btc_klines)
    if symbol_close.empty or btc_close.empty:
        return float("nan"), 0

    returns = pd.concat(
        [
            symbol_close.rename("symbol").pct_change(),
            btc_close.rename("btc").pct_change(),
        ],
        axis=1,
        join="inner",
    ).dropna()

    if len(returns) < 2:
        return float("nan"), int(len(returns))

    window_days = min(180, int(len(returns)))
    rolling_corr = returns["symbol"].rolling(window_days).corr(returns["btc"]).dropna()
    if rolling_corr.empty:
        return float("nan"), window_days
    return float(rolling_corr.iloc[-1]), window_days


def _funding_rate_to_pct(value: Any) -> float:
    try:
        rate = float(value)
    except Exception:
        return float("nan")
    if math.isnan(rate):
        return float("nan")
    return rate * 100.0


def _annualized_funding_pct(value: Any, *, interval_hours: int = 8) -> float:
    try:
        rate = float(value)
    except Exception:
        return float("nan")
    if math.isnan(rate):
        return float("nan")
    periods_per_day = 24.0 / max(1.0, float(interval_hours))
    return rate * periods_per_day * 365.0 * 100.0


def _share_to_pct(value: Any) -> float:
    try:
        share = float(value)
    except Exception:
        return float("nan")
    if math.isnan(share):
        return float("nan")
    return share * 100.0


def _float_nan(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float("nan")
    return parsed if not math.isnan(parsed) else float("nan")


def _pct_change(current: Any, previous: Any) -> float:
    current_f = _float_nan(current)
    previous_f = _float_nan(previous)
    if math.isnan(current_f) or math.isnan(previous_f) or abs(previous_f) < 1e-12:
        return float("nan")
    return (current_f / previous_f - 1.0) * 100.0


def _hourly_market_stats(klines: list[list[Any]]) -> dict[str, float]:
    if len(klines) < 4:
        return {
            "hour_return_pct": float("nan"),
            "hour_return_z": float("nan"),
            "day_return_pct": float("nan"),
            "hour_quote_volume": float("nan"),
            "hour_volume_multiple": float("nan"),
            "hour_trade_count_multiple": float("nan"),
            "hour_upper_wick_pct": float("nan"),
            "hour_close_location_pct": float("nan"),
        }

    closed_only = klines[:-1]
    opens = [_float_nan(row[1]) for row in closed_only if len(row) > 8]
    closes = [_float_nan(row[4]) for row in closed_only if len(row) > 7]
    highs = [_float_nan(row[2]) for row in closed_only if len(row) > 8]
    lows = [_float_nan(row[3]) for row in closed_only if len(row) > 8]
    quote_volumes = [_float_nan(row[7]) for row in closed_only if len(row) > 7]
    trade_counts = [_float_nan(row[8]) for row in closed_only if len(row) > 8]
    if len(closes) < 3 or len(quote_volumes) < 2 or len(trade_counts) < 2:
        return {
            "hour_return_pct": float("nan"),
            "hour_return_z": float("nan"),
            "day_return_pct": float("nan"),
            "hour_quote_volume": float("nan"),
            "hour_volume_multiple": float("nan"),
            "hour_trade_count_multiple": float("nan"),
            "hour_upper_wick_pct": float("nan"),
            "hour_close_location_pct": float("nan"),
        }

    return_series = pd.Series(closes, dtype="float64").pct_change().dropna()
    if return_series.empty:
        hour_return_pct = float("nan")
        hour_return_z = float("nan")
    else:
        latest_return = float(return_series.iloc[-1])
        baseline_returns = return_series.iloc[:-1].tail(48)
        if baseline_returns.empty:
            baseline_returns = return_series.tail(48)
        baseline_mean = float(baseline_returns.mean()) if not baseline_returns.empty else 0.0
        baseline_std = float(baseline_returns.std(ddof=0)) if len(baseline_returns) > 1 else float("nan")
        hour_return_pct = latest_return * 100.0
        if math.isnan(baseline_std) or baseline_std < 1e-12:
            hour_return_z = float("nan")
        else:
            hour_return_z = (latest_return - baseline_mean) / baseline_std

    if len(closes) >= 25 and not math.isnan(closes[-1]) and not math.isnan(closes[-25]) and abs(closes[-25]) >= 1e-12:
        day_return_pct = (closes[-1] / closes[-25] - 1.0) * 100.0
    else:
        day_return_pct = float("nan")

    latest_hour_quote_volume = quote_volumes[-1]
    volume_baseline = pd.Series(quote_volumes[:-1], dtype="float64").tail(24)
    baseline_avg_quote_volume = float(volume_baseline.mean()) if not volume_baseline.empty else float("nan")
    if math.isnan(latest_hour_quote_volume) or math.isnan(baseline_avg_quote_volume) or baseline_avg_quote_volume < 1e-12:
        hour_volume_multiple = float("nan")
    else:
        hour_volume_multiple = latest_hour_quote_volume / baseline_avg_quote_volume

    latest_trade_count = trade_counts[-1]
    trade_baseline = pd.Series(trade_counts[:-1], dtype="float64").tail(24)
    baseline_avg_trade_count = float(trade_baseline.mean()) if not trade_baseline.empty else float("nan")
    if math.isnan(latest_trade_count) or math.isnan(baseline_avg_trade_count) or baseline_avg_trade_count < 1e-12:
        hour_trade_count_multiple = float("nan")
    else:
        hour_trade_count_multiple = latest_trade_count / baseline_avg_trade_count

    latest_open = opens[-1] if opens else float("nan")
    latest_high = highs[-1] if highs else float("nan")
    latest_low = lows[-1] if lows else float("nan")
    latest_close = closes[-1]
    hour_range = latest_high - latest_low if not math.isnan(latest_high) and not math.isnan(latest_low) else float("nan")
    if math.isnan(hour_range) or hour_range < 1e-12:
        hour_upper_wick_pct = float("nan")
        hour_close_location_pct = float("nan")
    else:
        hour_upper_wick_pct = max(0.0, (latest_high - latest_close) / hour_range * 100.0)
        hour_close_location_pct = min(100.0, max(0.0, (latest_close - latest_low) / hour_range * 100.0))

    return {
        "hour_return_pct": hour_return_pct,
        "hour_return_z": hour_return_z,
        "day_return_pct": day_return_pct,
        "hour_quote_volume": latest_hour_quote_volume,
        "hour_volume_multiple": hour_volume_multiple,
        "hour_trade_count_multiple": hour_trade_count_multiple,
        "hour_upper_wick_pct": hour_upper_wick_pct,
        "hour_close_location_pct": hour_close_location_pct,
    }


def _daily_quote_volume_multiple(klines: list[list[Any]], quote_volume_24h: float) -> float:
    """Compare current 24h perp volume with the recent closed daily baseline."""
    current_volume = _float_nan(quote_volume_24h)
    if math.isnan(current_volume) or current_volume <= 0:
        return float("nan")

    closed_only = klines[:-1] if len(klines) > 1 else []
    quote_volumes: list[float] = []
    for row in closed_only:
        if len(row) <= 7:
            continue
        quote_volume = _float_nan(row[7])
        if not math.isnan(quote_volume) and quote_volume > 0:
            quote_volumes.append(quote_volume)
    if len(quote_volumes) < 5:
        return float("nan")

    baseline = pd.Series(quote_volumes[-20:], dtype="float64").median()
    if math.isnan(float(baseline)) or float(baseline) <= 0:
        return float("nan")
    return current_volume / float(baseline)


def _distance_to_level_pct(level: float, last_price: float) -> float:
    if math.isnan(level) or last_price <= 0:
        return float("nan")
    return max(0.0, (level / last_price - 1.0) * 100.0)


def _depth_stress(depth_snapshot: dict[str, Any], quote_volume_24h: float) -> dict[str, float]:
    bids = depth_snapshot.get("bids", []) if isinstance(depth_snapshot, dict) else []
    asks = depth_snapshot.get("asks", []) if isinstance(depth_snapshot, dict) else []
    if not bids or not asks:
        return {
            "ask_depth_1pct_usdt": float("nan"),
            "ask_depth_to_24h_volume_pct": float("nan"),
        }

    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return {
            "ask_depth_1pct_usdt": float("nan"),
            "ask_depth_to_24h_volume_pct": float("nan"),
        }
    if best_bid <= 0 or best_ask <= 0:
        return {
            "ask_depth_1pct_usdt": float("nan"),
            "ask_depth_to_24h_volume_pct": float("nan"),
        }

    mid_price = (best_bid + best_ask) / 2.0
    ask_limit = mid_price * 1.01
    ask_depth_1pct_usdt = 0.0
    for row in asks:
        try:
            price = float(row[0])
            qty = float(row[1])
        except Exception:
            continue
        if price > ask_limit:
            break
        ask_depth_1pct_usdt += price * qty

    if quote_volume_24h <= 0:
        ask_depth_to_24h_volume_pct = float("nan")
    else:
        ask_depth_to_24h_volume_pct = ask_depth_1pct_usdt / quote_volume_24h * 100.0

    return {
        "ask_depth_1pct_usdt": ask_depth_1pct_usdt,
        "ask_depth_to_24h_volume_pct": ask_depth_to_24h_volume_pct,
    }


def _percentile_score(series: pd.Series, *, ascending: bool = True, positive_only: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if positive_only:
        numeric = numeric.where(numeric > 0)
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(0.0, index=series.index)
    if positive_only and float(valid.max()) <= 0:
        return pd.Series(0.0, index=series.index)
    if float(valid.max()) - float(valid.min()) < 1e-12:
        if positive_only:
            return numeric.notna().astype(float).reindex(series.index).fillna(0.0) * 100.0
        return pd.Series(0.0, index=series.index)
    ranked = valid.rank(pct=True, ascending=ascending) * 100.0
    return ranked.reindex(series.index).fillna(0.0)


def _linear_score(series: pd.Series, *, low: float, high: float, invert: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if high <= low:
        return pd.Series(0.0, index=series.index)
    scaled = ((numeric - low) / (high - low) * 100.0).clip(lower=0.0, upper=100.0)
    if invert:
        scaled = 100.0 - scaled
    return scaled.fillna(0.0)


def _log_ratio_score(series: pd.Series, *, low: float = 2.0, high: float = 80.0) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").where(lambda values: values > 0)
    low_log = math.log10(max(low, 1e-12))
    high_log = math.log10(max(high, low + 1e-12))
    scored = ((numeric.map(math.log10) - low_log) / (high_log - low_log) * 100.0).clip(lower=0.0, upper=100.0)
    return scored.fillna(0.0)


def _band_score(series: pd.Series, *, low: float, sweet_low: float, sweet_high: float, high: float) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if sweet_low <= low or high <= sweet_high:
        return pd.Series(0.0, index=series.index)
    left = ((numeric - low) / (sweet_low - low) * 100.0).clip(lower=0.0, upper=100.0)
    right = ((high - numeric) / (high - sweet_high) * 100.0).clip(lower=0.0, upper=100.0)
    scored = pd.Series(100.0, index=series.index)
    scored = scored.where(numeric >= sweet_low, other=left)
    scored = scored.where(numeric <= sweet_high, other=right)
    return scored.fillna(0.0).clip(lower=0.0, upper=100.0)


def _apply_crime_pump_scores(all_df: pd.DataFrame) -> pd.DataFrame:
    if all_df.empty:
        return all_df

    velocity_score = _percentile_score(all_df["hour_return_z"], positive_only=True)
    day_momo_score = _percentile_score(all_df["day_return_pct"], positive_only=True)
    volume_score = _percentile_score(all_df["hour_volume_multiple"], positive_only=True)
    trade_count_score = _percentile_score(all_df["hour_trade_count_multiple"], positive_only=True)
    oi_score = _percentile_score(all_df["oi_delta_pct"], positive_only=True)
    oi_turnover_score = _percentile_score(all_df["oi_to_24h_volume_pct"], positive_only=True)
    oi_fade_score = _percentile_score(-pd.to_numeric(all_df["oi_delta_pct"], errors="coerce"), positive_only=True)
    funding_score = _percentile_score(all_df["carry_funding_pct"], positive_only=True)
    premium_score = _percentile_score(all_df["premium_index_pct"], positive_only=True)
    basis_score = _percentile_score(all_df["basis_rate_pct"], positive_only=True)
    taker_score = _percentile_score(all_df["taker_buy_sell_ratio"], positive_only=True)
    divergence_score = _percentile_score(all_df["crowd_top_position_divergence_pct"], positive_only=True)
    account_divergence_score = _percentile_score(all_df["crowd_top_account_divergence_pct"], positive_only=True)
    thinness_score = _percentile_score(all_df["ask_depth_to_24h_volume_pct"], ascending=False, positive_only=True)
    low_quote_volume_score = _percentile_score(all_df["quote_volume_24h"], ascending=False, positive_only=True)
    low_abs_depth_score = _percentile_score(all_df["ask_depth_1pct_usdt"], ascending=False, positive_only=True)
    high_quote_volume_score = _percentile_score(all_df["quote_volume_24h"], positive_only=True)
    high_abs_depth_score = _percentile_score(all_df["ask_depth_1pct_usdt"], positive_only=True)
    coinbase_spot_score = _percentile_score(all_df["coinbase_to_perp_volume_pct"], positive_only=True)
    coinbase_share_score = _percentile_score(all_df["coinbase_volume_share_pct"], positive_only=True)
    spot_support_score = _percentile_score(all_df["spot_to_perp_volume_pct"], positive_only=True)
    venue_concentration_rank_score = _percentile_score(all_df["venue_concentration_score"], positive_only=True)
    venue_hhi_score = pd.to_numeric(
        all_df.get("venue_hhi_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    trio_lane_score = pd.to_numeric(
        all_df.get("binance_bitget_gate_share_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    emfx_lane_score = pd.to_numeric(
        all_df.get("emfx_lane_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    cb_depth_volume_score = _linear_score(all_df["coinbase_depth_to_volume_pct"], low=0.0, high=1.0)
    cb_depth_perp_score = _linear_score(all_df["coinbase_depth_to_perp_volume_pct"], low=0.0, high=0.75)
    cb_tight_spread_score = _linear_score(all_df["coinbase_bid_ask_spread_pct"], low=0.0, high=0.60, invert=True)
    cb_share_direct_score = _linear_score(all_df["coinbase_volume_share_pct"], low=0.0, high=30.0)
    binance_share_direct_score = _linear_score(
        all_df.get("binance_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=5.0,
        high=45.0,
    )
    bitget_share_direct_score = _linear_score(
        all_df.get("bitget_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=3.0,
        high=35.0,
    )
    gate_share_direct_score = _linear_score(
        all_df.get("gate_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=2.0,
        high=25.0,
    )
    krw_share_direct_score = _linear_score(
        all_df.get("krw_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=2.0,
        high=35.0,
    )
    try_share_direct_score = _linear_score(
        all_df.get("try_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=1.0,
        high=20.0,
    )
    spot_mcap_score = _linear_score(all_df["spot_volume_to_mcap_pct"], low=25.0, high=250.0)
    perp_mcap_score = _linear_score(all_df["perp_volume_to_mcap_pct"], low=50.0, high=500.0)
    oi_mcap_score = _linear_score(all_df["oi_to_market_cap_pct"], low=3.0, high=40.0)
    cmc_mover_score = pd.to_numeric(
        all_df.get("cmc_mover_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    cmc_volume_mcap_score = _linear_score(
        all_df.get("cmc_volume_to_mcap_pct", pd.Series(float("nan"), index=all_df.index)),
        low=25.0,
        high=300.0,
    )
    cex_dex_score = pd.to_numeric(
        all_df.get("cex_dex_volume_ratio_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    cex_share_direct_score = _linear_score(
        all_df.get("cex_volume_share_pct", pd.Series(float("nan"), index=all_df.index)),
        low=60.0,
        high=98.0,
    )
    locked_supply_score = _percentile_score(all_df["locked_supply_pct"], positive_only=True)
    fdv_float_gap_score = _percentile_score(all_df["fdv_to_market_cap"], positive_only=True)
    holder_concentration_score = _percentile_score(all_df["holder_concentration_score"], positive_only=True)
    low_holder_count_score = _percentile_score(all_df["holder_count"], ascending=False, positive_only=True)
    close_near_high_score = _percentile_score(all_df["hour_close_location_pct"], positive_only=True)
    upper_wick_score = _percentile_score(all_df["hour_upper_wick_pct"], positive_only=True)
    major_excluded = all_df["crime_excluded_major"].fillna(False).astype(bool)
    mm_proximity_score = pd.to_numeric(all_df["mm_proximity_score"], errors="coerce").fillna(0.0).clip(
        lower=0.0,
        upper=100.0,
    )
    dwf_portfolio_score = pd.to_numeric(
        all_df.get("dwf_labs_portfolio_score", pd.Series(0.0, index=all_df.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    coinbase_listed_score = all_df["coinbase_spot_listed"].fillna(False).astype(bool).astype(float) * 100.0
    cb_imbalance = pd.to_numeric(all_df["coinbase_book_imbalance_pct"], errors="coerce")
    cb_book_balance_score = (100.0 - (cb_imbalance - 50.0).abs() * 2.0).clip(lower=0.0, upper=100.0).fillna(0.0)
    cb_bid_skew_score = ((cb_imbalance - 50.0) * 2.0).clip(lower=0.0, upper=100.0).fillna(0.0)
    cb_ask_skew_score = ((50.0 - cb_imbalance) * 2.0).clip(lower=0.0, upper=100.0).fillna(0.0)
    cb_depth_gap_score = _linear_score(all_df["coinbase_depth_to_perp_volume_pct"], low=0.0, high=0.25, invert=True).where(
        all_df["coinbase_spot_listed"].astype(bool),
        other=0.0,
    )

    all_df["crime_carry_stress_score"] = (funding_score + premium_score + basis_score) / 3.0
    all_df["crime_microstructure_score"] = low_quote_volume_score * 0.55 + low_abs_depth_score * 0.45
    all_df["crime_largecap_penalty_score"] = high_quote_volume_score * 0.60 + high_abs_depth_score * 0.40
    all_df["crime_coinbase_lane_score"] = (
        coinbase_spot_score * 0.42
        + coinbase_share_score * 0.42
        + all_df["coinbase_spot_listed"].astype(bool).astype(float) * 16.0
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_owner_circle_score"] = (
        holder_concentration_score * 0.34
        + locked_supply_score * 0.24
        + fdv_float_gap_score * 0.18
        + low_holder_count_score * 0.10
        + venue_concentration_rank_score * 0.09
        + venue_hhi_score * 0.05
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_spot_impulse_score"] = (
        spot_support_score * 0.55
        + all_df["crime_coinbase_lane_score"] * 0.35
        + venue_concentration_rank_score * 0.08
        + cex_dex_score * 0.12
        + trio_lane_score * 0.16
        + emfx_lane_score * 0.10
        + venue_hhi_score * 0.08
        + krw_share_direct_score * 0.04
        + try_share_direct_score * 0.03
        + cmc_volume_mcap_score * 0.08
        + cmc_mover_score * 0.04
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_supply_control_score"] = (
        all_df["crime_owner_circle_score"]
    )
    all_df["mm_presence_score"] = (
        coinbase_listed_score * 0.10
        + cb_depth_volume_score * 0.27
        + cb_depth_perp_score * 0.20
        + cb_tight_spread_score * 0.18
        + cb_book_balance_score * 0.13
        + cb_share_direct_score * 0.07
        + all_df["crime_coinbase_lane_score"] * 0.05
        + mm_proximity_score * 0.08
        + dwf_portfolio_score * 0.06
    ).clip(lower=0.0, upper=100.0)
    all_df["mm_bid_support_score"] = (
        coinbase_listed_score * 0.08
        + cb_depth_volume_score * 0.24
        + cb_depth_perp_score * 0.22
        + cb_tight_spread_score * 0.10
        + cb_bid_skew_score * 0.24
        + all_df["crime_coinbase_lane_score"] * 0.12
        + mm_proximity_score * 0.06
        + dwf_portfolio_score * 0.04
    ).clip(lower=0.0, upper=100.0)
    all_df["mm_withdrawal_risk_score"] = (
        (100.0 - all_df["mm_presence_score"]) * 0.30
        + cb_ask_skew_score * 0.08
        + all_df["crime_owner_circle_score"] * 0.23
        + venue_concentration_rank_score * 0.14
        + upper_wick_score * 0.10
        + oi_fade_score * 0.08
        + all_df["crime_carry_stress_score"] * 0.07
    ).where(all_df["coinbase_spot_listed"].astype(bool), other=0.0).clip(lower=0.0, upper=100.0)
    all_df["inventory_sponsor_mismatch_score"] = (
        all_df["crime_coinbase_lane_score"] * 0.20
        + spot_mcap_score * 0.18
        + perp_mcap_score * 0.18
        + cb_depth_gap_score * 0.16
        + cex_dex_score * 0.12
        + mm_proximity_score * 0.16
        + dwf_portfolio_score * 0.10
        + venue_concentration_rank_score * 0.08
        + trio_lane_score * 0.10
        + emfx_lane_score * 0.06
        + venue_hhi_score * 0.04
    ).clip(lower=0.0, upper=100.0)
    all_df["inventory_transfer_risk_score"] = (
        all_df["crime_owner_circle_score"] * 0.24
        + all_df["inventory_sponsor_mismatch_score"] * 0.18
        + mm_proximity_score * 0.18
        + dwf_portfolio_score * 0.12
        + spot_mcap_score * 0.12
        + perp_mcap_score * 0.10
        + holder_concentration_score * 0.08
        + locked_supply_score * 0.06
        + oi_mcap_score * 0.04
        + cex_dex_score * 0.06
        + trio_lane_score * 0.08
        + emfx_lane_score * 0.06
    ).where(~major_excluded, other=0.0).clip(lower=0.0, upper=100.0)
    all_df["inventory_transfer_risk_flag"] = (
        (all_df["inventory_transfer_risk_score"] >= 65.0)
        & (
            (mm_proximity_score >= 55.0)
            | (dwf_portfolio_score >= 70.0)
            | (all_df["crime_owner_circle_score"] >= 55.0)
            | (all_df["holder_concentration_score"] >= 60.0)
        )
        & (
            (all_df["inventory_sponsor_mismatch_score"] >= 55.0)
            | (spot_mcap_score >= 70.0)
            | (perp_mcap_score >= 70.0)
        )
    )
    all_df["crime_mechanics_score"] = (
        all_df["crime_microstructure_score"] * 0.20
        + all_df["crime_coinbase_lane_score"] * 0.15
        + all_df["crime_spot_impulse_score"] * 0.12
        + all_df["crime_owner_circle_score"] * 0.17
        + all_df["mm_presence_score"] * 0.10
        + all_df["mm_bid_support_score"] * 0.06
        + mm_proximity_score * 0.08
        + dwf_portfolio_score * 0.07
        + all_df["inventory_transfer_risk_score"] * 0.06
        + cex_dex_score * 0.06
        + trio_lane_score * 0.08
        + emfx_lane_score * 0.05
        + venue_hhi_score * 0.04
        + cmc_mover_score * 0.06
        + velocity_score * 0.12
        + day_momo_score * 0.10
        + oi_score * 0.10
        + taker_score * 0.04
        - all_df["crime_largecap_penalty_score"] * 0.24
        - all_df["mm_withdrawal_risk_score"] * 0.05
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_pump_score"] = (
        velocity_score * 0.14
        + day_momo_score * 0.12
        + volume_score * 0.10
        + trade_count_score * 0.08
        + oi_score * 0.14
        + all_df["crime_carry_stress_score"] * 0.11
        + taker_score * 0.10
        + divergence_score * 0.06
        + account_divergence_score * 0.03
        + thinness_score * 0.06
        + all_df["crime_microstructure_score"] * 0.07
        + all_df["crime_spot_impulse_score"] * 0.04
        + all_df["crime_supply_control_score"] * 0.03
        + all_df["mm_presence_score"] * 0.04
        + cex_share_direct_score * 0.02
        + trio_lane_score * 0.05
        + emfx_lane_score * 0.03
        + mm_proximity_score * 0.03
        + dwf_portfolio_score * 0.03
        + all_df["inventory_transfer_risk_score"] * 0.03
        + cmc_mover_score * 0.04
        + close_near_high_score * 0.04
        - all_df["crime_largecap_penalty_score"] * 0.18
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_ignition_score"] = (
        velocity_score * 0.17
        + day_momo_score * 0.15
        + volume_score * 0.14
        + trade_count_score * 0.12
        + oi_score * 0.15
        + taker_score * 0.10
        + close_near_high_score * 0.09
        + thinness_score * 0.04
        + all_df["crime_microstructure_score"] * 0.06
        + all_df["crime_spot_impulse_score"] * 0.03
        + all_df["mm_bid_support_score"] * 0.03
        + cex_dex_score * 0.04
        + trio_lane_score * 0.04
        + emfx_lane_score * 0.03
        + mm_proximity_score * 0.03
        + dwf_portfolio_score * 0.03
        + cmc_mover_score * 0.06
        - all_df["crime_largecap_penalty_score"] * 0.14
    ).clip(lower=0.0, upper=100.0)
    all_df["crime_exhaustion_score"] = (
        upper_wick_score * 0.24
        + all_df["crime_carry_stress_score"] * 0.22
        + divergence_score * 0.12
        + account_divergence_score * 0.08
        + oi_fade_score * 0.14
        + volume_score * 0.08
        + day_momo_score * 0.06
        + oi_turnover_score * 0.06
    )
    all_df["crime_eligible"] = (
        ~major_excluded
        & (
            (all_df["crime_microstructure_score"] >= 45.0)
            | (all_df["crime_mechanics_score"] >= 50.0)
            | (
                (all_df["crime_coinbase_lane_score"] >= 55.0)
                & (all_df["crime_owner_circle_score"] >= 45.0)
            )
            | (
                (all_df["crime_spot_impulse_score"] >= 55.0)
                & (all_df["day_return_pct"] >= 8.0)
            )
            | (
                (all_df["mm_presence_score"] >= 55.0)
                & (all_df["crime_owner_circle_score"] >= 35.0)
                & (all_df["day_return_pct"] >= 5.0)
            )
            | (
                (all_df["mm_bid_support_score"] >= 60.0)
                & (all_df["crime_spot_impulse_score"] >= 40.0)
            )
            | (
                (mm_proximity_score >= 70.0)
                & (all_df["coinbase_spot_listed"].astype(bool))
                & (all_df["day_return_pct"] >= 3.0)
            )
            | (
                (dwf_portfolio_score >= 70.0)
                & (
                    (all_df["day_return_pct"] >= 1.5)
                    | (all_df["convexity_seed_score"] >= 35.0)
                    | (all_df["crime_spot_impulse_score"] >= 35.0)
                )
            )
            | (
                (all_df["inventory_transfer_risk_score"] >= 68.0)
                & (all_df["day_return_pct"] >= 3.0)
            )
            | (
                (cex_dex_score >= 65.0)
                & (
                    (all_df["day_return_pct"] >= 3.0)
                    | (spot_mcap_score >= 45.0)
                    | (perp_mcap_score >= 45.0)
                )
            )
            | (
                (trio_lane_score >= 60.0)
                & (cex_dex_score >= 55.0)
                & (
                    (all_df["day_return_pct"] >= 3.0)
                    | (spot_mcap_score >= 45.0)
                    | (all_df["crime_spot_impulse_score"] >= 45.0)
                )
            )
            | (
                (emfx_lane_score >= 55.0)
                & (
                    (all_df["day_return_pct"] >= 3.0)
                    | (spot_mcap_score >= 45.0)
                    | (all_df["crime_mechanics_score"] >= 45.0)
                )
            )
            | (
                (venue_hhi_score >= 65.0)
                & (cex_dex_score >= 55.0)
                & (
                    (all_df["crime_spot_impulse_score"] >= 45.0)
                    | (all_df["crime_mechanics_score"] >= 45.0)
                )
            )
            | (
                (cmc_mover_score >= 55.0)
                & (
                    (low_quote_volume_score >= 25.0)
                    | (all_df["crime_largecap_penalty_score"] <= 65.0)
                )
            )
            | (
                (low_quote_volume_score >= 35.0)
                & (low_abs_depth_score >= 35.0)
            )
        )
    )

    all_df["crime_pump_flag"] = (
        all_df["crime_eligible"]
        & (all_df["crime_pump_score"] >= 70.0)
        & (all_df["hour_return_pct"] >= 4.0)
        & (all_df["oi_delta_pct"] >= 2.0)
        & (all_df["taker_buy_sell_ratio"] >= 1.15)
    )
    all_df["squeeze_risk_flag"] = (
        all_df["crime_eligible"]
        & (all_df["hour_return_pct"] >= 3.0)
        & (all_df["oi_delta_pct"] >= 3.0)
        & (all_df["taker_buy_sell_ratio"] >= 1.20)
        & (all_df["long_short_account_ratio"] >= 1.10)
        & (all_df["crowd_top_position_divergence_pct"] >= 5.0)
    )
    all_df["ignition_setup_flag"] = (
        all_df["crime_eligible"]
        & (all_df["crime_ignition_score"] >= 68.0)
        & (all_df["hour_return_pct"] >= 3.0)
        & (all_df["day_return_pct"] >= 12.0)
        & (all_df["oi_delta_pct"] >= 2.0)
        & (all_df["taker_buy_sell_ratio"] >= 1.05)
        & (all_df["hour_trade_count_multiple"] >= 1.40)
        & (all_df["hour_close_location_pct"] >= 65.0)
    )
    all_df["exhaustion_flag"] = (
        (all_df["crime_exhaustion_score"] >= 68.0)
        & (all_df["hour_upper_wick_pct"] >= 30.0)
        & ((all_df["oi_delta_pct"] <= 0.0) | (all_df["carry_funding_pct"] >= 0.02))
    )
    all_df["blowoff_risk_flag"] = (
        all_df["crime_eligible"]
        & (all_df["crime_exhaustion_score"] >= 75.0)
        & (all_df["hour_volume_multiple"] >= 2.5)
        & ((all_df["carry_funding_pct"] >= 0.02) | (all_df["basis_rate_pct"] >= 0.10))
        & (all_df["ask_depth_to_24h_volume_pct"] <= 0.60)
    )

    def _inventory_transfer_note(row: pd.Series) -> str:
        triggers: list[str] = []
        if _safe_float(row.get("mm_proximity_score")) >= 70.0:
            maker = str(row.get("mm_proximity_maker", "")).strip()
            triggers.append(f"{maker} proximity" if maker else "MM proximity")
        if bool(row.get("dwf_labs_portfolio")):
            triggers.append("DWF Labs portfolio")
        if _safe_float(row.get("crime_owner_circle_score")) >= 55.0:
            triggers.append("controlled holder/float proxy")
        if _safe_float(row.get("inventory_sponsor_mismatch_score")) >= 60.0:
            triggers.append("sponsor/depth mismatch")
        if _safe_float(row.get("spot_volume_to_mcap_pct")) >= 100.0:
            triggers.append("spot volume > market cap")
        if _safe_float(row.get("cex_to_dex_volume_ratio")) >= 10.0:
            triggers.append("CEX volume dominates DEX")
        if _safe_float(row.get("binance_bitget_gate_share_pct")) >= 50.0:
            triggers.append("Binance/Bitget/Gate lane dominates")
        if _safe_float(row.get("emfx_volume_share_pct")) >= 12.0:
            triggers.append("EMFX quote lane active")
        if _safe_float(row.get("try_volume_share_pct")) >= 4.0:
            triggers.append("TRY quote lane active")
        if _safe_float(row.get("perp_volume_to_mcap_pct")) >= 250.0:
            triggers.append("perp volume extreme vs mcap")
        if _safe_float(row.get("oi_to_market_cap_pct")) >= 20.0:
            triggers.append("OI large vs mcap")
        if _safe_float(row.get("top_venue_volume_share_pct")) >= 45.0:
            triggers.append("single venue dominates volume")
        if _safe_float(row.get("venue_hhi_score")) >= 65.0:
            triggers.append("venue concentration extreme")
        if _safe_float(row.get("coinbase_depth_to_perp_volume_pct")) > 0 and _safe_float(row.get("coinbase_depth_to_perp_volume_pct")) <= 0.05:
            triggers.append("tiny visible CB depth vs perp flow")
        if not triggers:
            return "No strong OTC/inventory-transfer fingerprint."
        return " | ".join(triggers[:5])

    all_df["inventory_transfer_note"] = all_df.apply(_inventory_transfer_note, axis=1)
    return all_df


def _score_trade_buckets(all_df: pd.DataFrame) -> pd.DataFrame:
    if all_df.empty:
        all_df["trade_bucket"] = pd.Series(dtype="object")
        all_df["trade_bucket_score"] = pd.Series(dtype="float64")
        all_df["trade_bucket_note"] = pd.Series(dtype="object")
        return all_df

    # Several upstream enrichers add optional columns depending on scan mode.
    # Copy once here so bucket writes do not operate on a fragmented dataframe.
    all_df = all_df.copy()
    try:
        all_df._consolidate_inplace()
    except Exception:
        pass

    numeric_cols = [
        "crime_ignition_score",
        "crime_exhaustion_score",
        "oi_delta_pct",
        "hour_trade_count_multiple",
        "hour_volume_multiple",
        "taker_buy_sell_ratio",
        "hour_close_location_pct",
        "hour_upper_wick_pct",
        "carry_funding_pct",
        "crowd_top_position_divergence_pct",
        "day_return_pct",
        "basis_rate_pct",
        "crime_mechanics_score",
        "crime_spot_impulse_score",
        "crime_supply_control_score",
        "mm_presence_score",
        "mm_bid_support_score",
        "mm_withdrawal_risk_score",
        "mm_proximity_score",
        "inventory_transfer_risk_score",
        "inventory_sponsor_mismatch_score",
        "float_trap_score",
        "ignition_score_v2",
        "perp_pressure_score",
        "venue_support_score",
        "exit_fragility_score",
        "crime_pump_score_v2",
        "convexity_seed_score",
        "large_cap_stabilizer",
        "coinbase_volume_share_pct",
        "binance_volume_share_pct",
        "bitget_volume_share_pct",
        "gate_volume_share_pct",
        "upbit_volume_share_pct",
        "krw_volume_share_pct",
        "try_volume_share_pct",
        "emfx_volume_share_pct",
        "kraken_volume_share_pct",
        "perp_volume_to_mcap_pct",
        "oi_to_market_cap_pct",
        "hour_return_pct",
        "cmc_mover_score",
        "cmc_pct_1h",
        "cmc_pct_24h",
        "cmc_volume_to_mcap_pct",
        "cex_volume_share_pct",
        "cex_to_dex_volume_ratio",
        "cex_dex_volume_ratio_score",
        "binance_bitget_gate_share_pct",
        "binance_bitget_gate_share_score",
        "venue_hhi",
        "venue_hhi_score",
        "emfx_lane_score",
        "funding_flip_score",
        "short_crowding_score",
        "breakout_pressure_score",
        "runway_score",
        "short_squeeze_score",
        "last_settled_funding_pct",
        "prior_settled_funding_pct",
        "funding_flip_delta_pct",
        "upside_to_ath_pct",
        "ath_price",
        "ath_multiple",
        "ath_upside_pct",
        "coingecko_ath_usd",
        "coingecko_ath_change_pct",
        "convexity_float_score",
        "convexity_sponsor_score",
        "convexity_preignition_score",
        "convexity_expansion_score",
        "convexity_squeeze_score",
        "convexity_runway_score",
        "convexity_late_penalty",
        "trend_confluence_score",
        "spot_flow_confluence_score",
        "perp_squeeze_confluence_score",
        "float_control_confluence_score",
        "mm_sponsor_confluence_score",
        "ath_runway_confluence_score",
        "convexity_confluence_score",
        "convexity_confluence_count",
        "dwf_labs_portfolio_score",
        "valuation_trap_score",
        "short_liquidation_fuel_score",
        "spot_control_score",
        "squeeze_machine_score",
        "convexity_entry_score",
        "convexity_score",
        "daily_quote_volume_multiple",
        "distance_to_high_5d_pct",
        "distance_to_high_20d_pct",
        "distance_to_high_90d_pct",
    ]
    for col in numeric_cols:
        all_df[col] = pd.to_numeric(all_df[col], errors="coerce")

    long_breakout = all_df["broke_high_20d"] | all_df["broke_high_5d"] | all_df["broke_high_90d"]
    short_breakout = all_df["broke_low_20d"] | all_df["broke_low_5d"] | all_df["broke_low_90d"]
    major_excluded = all_df["crime_excluded_major"].fillna(False).astype(bool)
    coinbase_lane = all_df["coinbase_lane_flag"].fillna(False).astype(bool)
    owner_controlled = all_df["owner_controlled_flag"].fillna(False).astype(bool)
    perp_heavy = all_df["perp_heavy_flag"].fillna(False).astype(bool)
    early_convexity = all_df["early_convexity_flag"].fillna(False).astype(bool)
    prime_convexity = all_df["convexity_prime_flag"].fillna(False).astype(bool)
    pre_pump_candidate = all_df["pre_pump_candidate_flag"].fillna(False).astype(bool)
    convexity_chase_risk = all_df["convexity_chase_risk_flag"].fillna(False).astype(bool)
    too_late_convexity = all_df["convexity_too_late_flag"].fillna(False).astype(bool)
    trend_confluence = all_df["trend_confluence_flag"].fillna(False).astype(bool)
    spot_flow_confluence = all_df["spot_flow_confluence_flag"].fillna(False).astype(bool)
    perp_squeeze_confluence = all_df["perp_squeeze_confluence_flag"].fillna(False).astype(bool)
    float_control_confluence = all_df["float_control_confluence_flag"].fillna(False).astype(bool)
    mm_sponsor_confluence = all_df["mm_sponsor_confluence_flag"].fillna(False).astype(bool)
    ath_runway_confluence = all_df["ath_runway_confluence_flag"].fillna(False).astype(bool)
    squeeze_machine = all_df["squeeze_machine_flag"].fillna(False).astype(bool)
    ath_runway_20x = all_df["ath_runway_20x_flag"].fillna(False).astype(bool)
    confluence_count = all_df["convexity_confluence_count"].fillna(0.0)
    confluence_score = all_df["convexity_confluence_score"].fillna(0.0)
    early_float_signal = (
        owner_controlled
        | float_control_confluence
        | (all_df["float_trap_score"] >= 42.0)
        | (all_df["crime_owner_circle_score"] >= 42.0)
        | (all_df["crime_supply_control_score"] >= 45.0)
        | (all_df["inventory_transfer_risk_score"] >= 45.0)
    )
    early_venue_signal = (
        coinbase_lane
        | spot_flow_confluence
        | mm_sponsor_confluence
        | (all_df["venue_support_score"] >= 32.0)
        | (all_df["mm_presence_score"] >= 45.0)
        | (all_df["mm_bid_support_score"] >= 42.0)
        | (all_df["mm_proximity_score"] >= 55.0)
        | (all_df["krw_volume_share_pct"] >= 12.0)
        | (all_df["upbit_volume_share_pct"] >= 12.0)
        | (all_df["kraken_volume_share_pct"] >= 8.0)
        | (all_df["cex_dex_volume_ratio_score"] >= 65.0)
        | (all_df["cex_to_dex_volume_ratio"] >= 10.0)
        | (all_df["binance_bitget_gate_share_pct"] >= 45.0)
        | (all_df["emfx_volume_share_pct"] >= 10.0)
        | (all_df["venue_hhi_score"] >= 60.0)
    )
    early_perp_signal = (
        perp_heavy
        | perp_squeeze_confluence
        | (all_df["perp_pressure_score"] >= 42.0)
        | (all_df["oi_delta_pct"] >= 1.0)
        | (all_df["perp_volume_to_mcap_pct"] >= 150.0)
        | (all_df["oi_to_market_cap_pct"] >= 8.0)
    )
    early_ignition_signal = (
        all_df["setup_ready_flag"].fillna(False).astype(bool)
        | trend_confluence
        | (all_df["ignition_score_v2"].between(18.0, 75.0, inclusive="both"))
        | (all_df["crime_pump_score_v2"].between(25.0, 72.0, inclusive="both"))
        | (all_df["day_return_pct"].between(3.0, 65.0, inclusive="both"))
        | (all_df["cmc_mover_score"] >= 45.0)
        | (all_df["cmc_pct_24h"].between(15.0, 160.0, inclusive="both"))
        | (all_df["convexity_seed_score"] >= 45.0)
        | (all_df["convexity_preignition_score"] >= 35.0)
        | ((confluence_score >= 45.0) & (confluence_count >= 3.0))
        | (all_df["daily_quote_volume_multiple"] >= 1.35)
        | long_breakout
    )
    not_late_stage = (
        (~all_df["blowoff_risk_flag"].fillna(False).astype(bool))
        & (
            (~all_df["blowoff_watch_flag"].fillna(False).astype(bool))
            | (all_df["exit_fragility_score"].fillna(0.0) < 60.0)
        )
        & (~all_df["unwind_risk_flag"].fillna(False).astype(bool))
        & (~all_df["exhaustion_flag"].fillna(False).astype(bool))
        & (all_df["exit_fragility_score"].fillna(0.0) < 78.0)
        & (all_df["crime_exhaustion_score"].fillna(0.0) < 74.0)
        & (all_df["large_cap_stabilizer"].fillna(0.0) <= 80.0)
        & (all_df["convexity_late_penalty"].fillna(0.0) < 72.0)
        & (~convexity_chase_risk | (all_df["convexity_late_penalty"].fillna(0.0) < 45.0))
    )

    convex_long_mask = (
        (~major_excluded)
        & not_late_stage
        & (~too_late_convexity)
        & (
            pre_pump_candidate
            | prime_convexity
            | early_convexity
            | squeeze_machine
            | (
                (all_df["convexity_entry_score"] >= 54.0)
                & (all_df["convexity_sponsor_score"] >= 50.0)
                & (all_df["convexity_float_score"] >= 38.0)
                & (all_df["convexity_preignition_score"] >= 34.0)
            )
            | all_df["setup_ready_flag"].fillna(False).astype(bool)
            | (early_float_signal & early_venue_signal & early_ignition_signal)
            | (
                (all_df["inventory_transfer_risk_score"] >= 42.0)
                & early_venue_signal
                & (
                    (all_df["ignition_score_v2"] >= 15.0)
                    | (all_df["convexity_preignition_score"] >= 30.0)
                )
            )
            | (
                early_perp_signal
                & early_venue_signal
                & (early_float_signal | (all_df["float_trap_score"] >= 30.0))
                & (
                    (all_df["ignition_score_v2"] >= 15.0)
                    | (all_df["convexity_preignition_score"] >= 30.0)
                )
            )
            | (
                (confluence_count >= 3.0)
                & (confluence_score >= 45.0)
                & (early_ignition_signal | (all_df["convexity_preignition_score"] >= 28.0))
                & (early_float_signal | ath_runway_confluence | ath_runway_20x)
                & (early_venue_signal | early_perp_signal)
            )
        )
        & (
            all_df["carry_funding_pct"].isna()
            | (all_df["carry_funding_pct"] <= 0.04)
        )
        & (
            all_df["crowd_top_position_divergence_pct"].isna()
            | (all_df["crowd_top_position_divergence_pct"] <= 30.0)
        )
        & (
            all_df["mm_withdrawal_risk_score"].isna()
            | (all_df["mm_withdrawal_risk_score"] <= 72.0)
        )
    )

    avoid_mask = (
        all_df["blowoff_risk_flag"]
        | all_df["exhaustion_flag"]
        | (
            (all_df["mm_withdrawal_risk_score"] >= 78.0)
            & (
                (all_df["crime_exhaustion_score"] >= 55.0)
                | (all_df["hour_upper_wick_pct"] >= 22.0)
                | (all_df["oi_delta_pct"] <= 0.0)
            )
        )
        | (
            (all_df["inventory_transfer_risk_score"] >= 78.0)
            & (all_df["mm_withdrawal_risk_score"] >= 65.0)
            & (
                (all_df["hour_upper_wick_pct"] >= 20.0)
                | (all_df["oi_delta_pct"] <= 0.0)
            )
        )
        | (
            (all_df["crime_exhaustion_score"] >= 72.0)
            & (
                (all_df["hour_upper_wick_pct"] >= 28.0)
                | (all_df["carry_funding_pct"] >= 0.02)
                | (all_df["basis_rate_pct"] >= 0.10)
            )
        )
        | too_late_convexity
        | (
            short_breakout
            & (all_df["oi_delta_pct"] <= 0.0)
        )
        | (
            convexity_chase_risk
            & (all_df["convexity_late_penalty"] >= 55.0)
        )
    )

    scalp_only_mask = (
        ~convex_long_mask
        & ~avoid_mask
        & (
            all_df["crime_pump_flag"]
            | all_df["squeeze_risk_flag"]
            | all_df["inventory_transfer_risk_flag"]
            | all_df["setup_ready_flag"]
            | all_df["active_squeeze_flag"]
            | pre_pump_candidate
            | early_convexity
            | (all_df["crime_pump_score"] >= 65.0)
            | (all_df["crime_pump_score_v2"] >= 65.0)
            | (all_df["crime_ignition_score"] >= 65.0)
            | ((all_df["hour_return_pct"] >= 3.0) & (all_df["oi_delta_pct"] >= 2.0))
        )
    )

    trade_bucket = pd.Series("Watch", index=all_df.index, dtype="object")
    trade_bucket.loc[convex_long_mask] = "Convex Long"
    trade_bucket.loc[scalp_only_mask] = "Scalp Only"
    trade_bucket.loc[avoid_mask] = "Avoid"

    convex_score = (
        all_df["convexity_entry_score"].fillna(all_df["convexity_score"]).fillna(0.0) * 0.72
        + confluence_score * 0.16
        + confluence_count * 3.0
        + all_df["squeeze_machine_score"].fillna(0.0) * 0.18
        + all_df["short_liquidation_fuel_score"].fillna(0.0) * 0.06
        + all_df["spot_control_score"].fillna(0.0) * 0.05
        + all_df["crime_pump_score_v2"].fillna(0.0) * 0.18
        + all_df["short_squeeze_score"].fillna(0.0) * 0.06
        + all_df["inventory_transfer_risk_score"].fillna(0.0) * 0.06
        + all_df["cmc_mover_score"].fillna(0.0) * 0.05
        + all_df["convexity_preignition_score"].fillna(0.0) * 0.08
        + all_df["ath_runway_confluence_score"].fillna(0.0) * 0.05
        + all_df["ath_multiple"].clip(lower=0.0, upper=50.0).fillna(0.0) * 0.30
        + pre_pump_candidate.astype(float) * 10.0
        + prime_convexity.astype(float) * 12.0
        + early_convexity.astype(float) * 8.0
        + ath_runway_20x.astype(float) * 6.0
        + squeeze_machine.astype(float) * 12.0
        + coinbase_lane.astype(float) * 4.0
        + owner_controlled.astype(float) * 5.0
        + perp_heavy.astype(float) * 4.0
        - all_df["convexity_late_penalty"].fillna(0.0) * 0.18
        - convexity_chase_risk.astype(float) * 10.0
        - too_late_convexity.astype(float) * 25.0
    )
    scalp_score = (
        all_df["crime_pump_score"].fillna(0.0) * 0.35
        + all_df["crime_pump_score_v2"].fillna(0.0) * 0.12
        + all_df["crime_mechanics_score"].fillna(0.0) * 0.12
        + all_df["crime_ignition_score"].fillna(0.0) * 0.18
        + all_df["ignition_score_v2"].fillna(0.0) * 0.08
        + all_df["convexity_score"].fillna(0.0) * 0.12
        + all_df["convexity_preignition_score"].fillna(0.0) * 0.06
        + all_df["mm_presence_score"].fillna(0.0) * 0.05
        + all_df["inventory_transfer_risk_score"].fillna(0.0) * 0.05
        + all_df["cmc_mover_score"].fillna(0.0) * 0.06
        + all_df["cex_dex_volume_ratio_score"].fillna(0.0) * 0.05
        + all_df["binance_bitget_gate_share_score"].fillna(0.0) * 0.08
        + all_df["emfx_lane_score"].fillna(0.0) * 0.04
        + confluence_score * 0.08
        + early_convexity.astype(float) * 5.0
        + all_df["hour_return_z"].clip(lower=0.0).fillna(0.0) * 6.0
        + all_df["oi_delta_pct"].clip(lower=0.0).fillna(0.0) * 3.0
        + all_df["taker_buy_sell_ratio"].clip(lower=0.0).fillna(0.0) * 6.0
        - all_df["crime_exhaustion_score"].fillna(0.0) * 0.10
    )
    avoid_score = (
        all_df["crime_exhaustion_score"].fillna(0.0) * 0.45
        + all_df["exit_fragility_score"].fillna(0.0) * 0.18
        + all_df["convexity_late_penalty"].fillna(0.0) * 0.28
        + all_df["hour_upper_wick_pct"].clip(lower=0.0).fillna(0.0) * 0.55
        + all_df["carry_funding_pct"].clip(lower=0.0).fillna(0.0) * 250.0
        + all_df["basis_rate_pct"].clip(lower=0.0).fillna(0.0) * 40.0
        + all_df["crowd_top_position_divergence_pct"].clip(lower=0.0).fillna(0.0) * 1.7
        + (-all_df["oi_delta_pct"]).clip(lower=0.0).fillna(0.0) * 3.5
        + all_df["mm_withdrawal_risk_score"].fillna(0.0) * 0.18
        + short_breakout.astype(float) * 10.0
    )

    trade_bucket_score = pd.Series(0.0, index=all_df.index, dtype="float64")
    trade_bucket_score.loc[trade_bucket == "Convex Long"] = convex_score
    trade_bucket_score.loc[trade_bucket == "Scalp Only"] = scalp_score
    trade_bucket_score.loc[trade_bucket == "Avoid"] = avoid_score
    trade_bucket_score.loc[trade_bucket == "Watch"] = convex_score * 0.5
    bucket_frame = pd.DataFrame(
        {
            "trade_bucket": trade_bucket,
            "trade_bucket_score": trade_bucket_score,
        },
        index=all_df.index,
    )
    all_df = pd.concat(
        [all_df.drop(columns=["trade_bucket", "trade_bucket_score"], errors="ignore"), bucket_frame],
        axis=1,
    ).copy()

    def _bucket_note(row: pd.Series) -> str:
        bucket = str(row.get("trade_bucket", "Watch"))
        triggers: list[str] = []
        if bool(row.get("broke_high_20d")):
            triggers.append("20D breakout")
        elif bool(row.get("broke_high_5d")):
            triggers.append("5D breakout")
        elif bool(row.get("broke_high_90d")):
            triggers.append("90D breakout")
        if pd.notna(row.get("crime_ignition_score")) and float(row["crime_ignition_score"]) >= 68.0:
            triggers.append("high ignition")
        if pd.notna(row.get("oi_delta_pct")) and float(row["oi_delta_pct"]) >= 2.0:
            triggers.append("OI expanding")
        if pd.notna(row.get("hour_trade_count_multiple")) and float(row["hour_trade_count_multiple"]) >= 1.40:
            triggers.append("trade count spike")
        if pd.notna(row.get("taker_buy_sell_ratio")) and float(row["taker_buy_sell_ratio"]) >= 1.05:
            triggers.append("taker buyers in control")
        if pd.notna(row.get("hour_close_location_pct")) and float(row["hour_close_location_pct"]) >= 65.0:
            triggers.append("strong hourly close")
        if pd.notna(row.get("float_trap_score")) and float(row["float_trap_score"]) >= 45.0:
            triggers.append("float trap")
        if pd.notna(row.get("convexity_score")) and float(row["convexity_score"]) >= 55.0:
            triggers.append("early convexity")
        if pd.notna(row.get("convexity_confluence_count")) and float(row["convexity_confluence_count"]) >= 3.0:
            note = str(row.get("convexity_confluence_note", "")).strip()
            triggers.append(note if note and note != "No multi-mechanic confluence yet." else "multi-mechanic confluence")
        if pd.notna(row.get("convexity_confluence_score")) and float(row["convexity_confluence_score"]) >= 55.0:
            triggers.append("mechanics confluence")
        if bool(row.get("dwf_labs_portfolio")):
            triggers.append("DWF Labs portfolio")
        if pd.notna(row.get("squeeze_machine_score")) and float(row["squeeze_machine_score"]) >= 55.0:
            triggers.append("float-control/perp-squeeze machine")
        if pd.notna(row.get("short_liquidation_fuel_score")) and float(row["short_liquidation_fuel_score"]) >= 55.0:
            triggers.append("short liquidation fuel")
        if pd.notna(row.get("spot_control_score")) and float(row["spot_control_score"]) >= 55.0:
            triggers.append("spot control")
        if pd.notna(row.get("valuation_trap_score")) and float(row["valuation_trap_score"]) >= 55.0:
            triggers.append("valuation trap")
        if bool(row.get("pre_pump_candidate_flag")):
            triggers.append("pre-pump candidate")
        if bool(row.get("convexity_prime_flag")):
            triggers.append("convexity prime")
        if bool(row.get("early_convexity_flag")):
            triggers.append("convexity active")
        if pd.notna(row.get("convexity_preignition_score")) and float(row["convexity_preignition_score"]) >= 45.0:
            triggers.append("pre-ignition pressure")
        if pd.notna(row.get("daily_quote_volume_multiple")) and float(row["daily_quote_volume_multiple"]) >= 1.75:
            triggers.append("daily volume expanding")
        if pd.notna(row.get("venue_support_score")) and float(row["venue_support_score"]) >= 35.0:
            triggers.append("venue support")
        if pd.notna(row.get("convexity_sponsor_score")) and float(row["convexity_sponsor_score"]) >= 50.0:
            triggers.append("sponsored spot")
        if pd.notna(row.get("convexity_expansion_score")) and float(row["convexity_expansion_score"]) >= 45.0:
            triggers.append("expansion readiness")
        if pd.notna(row.get("perp_pressure_score")) and float(row["perp_pressure_score"]) >= 42.0:
            triggers.append("perp fuel")
        if pd.notna(row.get("convexity_runway_score")) and float(row["convexity_runway_score"]) >= 45.0:
            triggers.append("runway open")
        if pd.notna(row.get("ath_multiple")) and float(row["ath_multiple"]) >= 20.0:
            triggers.append(f"{float(row['ath_multiple']):.1f}x from ATH")
        if pd.notna(row.get("crime_pump_score_v2")) and float(row["crime_pump_score_v2"]) >= 30.0:
            triggers.append("early v2 setup")
        if pd.notna(row.get("cmc_mover_score")) and float(row["cmc_mover_score"]) >= 55.0:
            label = str(row.get("cmc_mover_label", "")).strip()
            triggers.append(label if label else "CMC top mover")
        if pd.notna(row.get("cmc_volume_to_mcap_pct")) and float(row["cmc_volume_to_mcap_pct"]) >= 100.0:
            triggers.append("CMC vol/mcap extreme")
        if pd.notna(row.get("cex_to_dex_volume_ratio")) and float(row["cex_to_dex_volume_ratio"]) >= 10.0:
            triggers.append("CEX >> DEX flow")
        elif pd.notna(row.get("cex_dex_volume_ratio_score")) and float(row["cex_dex_volume_ratio_score"]) >= 65.0:
            triggers.append("CEX/DEX skew")
        if pd.notna(row.get("binance_bitget_gate_share_pct")) and float(row["binance_bitget_gate_share_pct"]) >= 45.0:
            triggers.append("Binance/Bitget/Gate lane")
        if pd.notna(row.get("krw_volume_share_pct")) and float(row["krw_volume_share_pct"]) >= 12.0:
            triggers.append("KRW spot lane")
        if pd.notna(row.get("try_volume_share_pct")) and float(row["try_volume_share_pct"]) >= 4.0:
            triggers.append("TRY spot lane")
        if pd.notna(row.get("emfx_volume_share_pct")) and float(row["emfx_volume_share_pct"]) >= 10.0:
            triggers.append("EMFX lane")
        if pd.notna(row.get("venue_hhi_score")) and float(row["venue_hhi_score"]) >= 60.0:
            triggers.append("venue concentration extreme")
        if pd.notna(row.get("mm_presence_score")) and float(row["mm_presence_score"]) >= 55.0:
            triggers.append("MM present on CB spot")
        if pd.notna(row.get("mm_bid_support_score")) and float(row["mm_bid_support_score"]) >= 55.0:
            triggers.append("CB bid support")
        if pd.notna(row.get("mm_withdrawal_risk_score")) and float(row["mm_withdrawal_risk_score"]) >= 72.0:
            triggers.append("MM pull-risk")
        if pd.notna(row.get("mm_proximity_score")) and float(row["mm_proximity_score"]) >= 70.0:
            maker = str(row.get("mm_proximity_maker", "")).strip()
            triggers.append(f"{maker} proximity" if maker else "MM proximity")
        if pd.notna(row.get("inventory_transfer_risk_score")) and float(row["inventory_transfer_risk_score"]) >= 65.0:
            triggers.append("OTC inventory-transfer risk")
        if bool(row.get("setup_ready_flag")):
            triggers.append("setup ready")
        if bool(row.get("funding_flip_up_flag")):
            triggers.append("funding flipped up")
        if bool(row.get("fresh_flip_flag")):
            triggers.append("fresh short squeeze")
        if bool(row.get("active_short_squeeze_flag")):
            triggers.append("active short squeeze")
        if bool(row.get("squeeze_chase_flag")):
            triggers.append("squeeze chase risk")
        if pd.notna(row.get("short_squeeze_score")) and float(row["short_squeeze_score"]) >= 65.0:
            triggers.append("short squeeze")
        if pd.notna(row.get("upside_to_ath_pct")) and float(row["upside_to_ath_pct"]) >= 50.0:
            triggers.append("ATH runway")
        if bool(row.get("active_squeeze_flag")):
            triggers.append("active squeeze")
        if bool(row.get("blowoff_watch_flag")):
            triggers.append("blowoff watch")
        if bool(row.get("unwind_risk_flag")):
            triggers.append("unwind risk")
        if bool(row.get("convexity_too_late_flag")):
            triggers.append("too late")
        if bool(row.get("convexity_chase_risk_flag")):
            triggers.append("chase risk")
        if pd.notna(row.get("crime_exhaustion_score")) and float(row["crime_exhaustion_score"]) >= 68.0:
            triggers.append("exhaustion elevated")
        if pd.notna(row.get("hour_upper_wick_pct")) and float(row["hour_upper_wick_pct"]) >= 30.0:
            triggers.append("big upper wick")
        if pd.notna(row.get("carry_funding_pct")) and float(row["carry_funding_pct"]) >= 0.02:
            triggers.append("hot funding")
        if pd.notna(row.get("crowd_top_position_divergence_pct")) and float(row["crowd_top_position_divergence_pct"]) >= 5.0:
            triggers.append("crowd ahead of top traders")
        if bool(row.get("blowoff_risk_flag")):
            triggers.append("blowoff risk")
        if bool(row.get("squeeze_risk_flag")):
            triggers.append("crowded squeeze")

        if not triggers:
            return "No strong classification signal yet."
        if bucket == "Convex Long":
            return " | ".join(triggers[:4])
        if bucket == "Scalp Only":
            return " | ".join(triggers[:5])
        if bucket == "Avoid":
            return " | ".join(triggers[:5])
        return " | ".join(triggers[:3])

    trade_bucket_note = all_df.apply(_bucket_note, axis=1)
    return pd.concat(
        [
            all_df.drop(columns=["trade_bucket_note"], errors="ignore"),
            pd.DataFrame({"trade_bucket_note": trade_bucket_note}, index=all_df.index),
        ],
        axis=1,
    ).copy()


def _coerce_funding_interval_hours(value: Any) -> int:
    try:
        hours = int(float(value))
    except Exception:
        return 8
    return max(1, hours)


def _funding_countdown_hours(next_funding_time_ms: Any) -> float:
    try:
        next_ms = int(float(next_funding_time_ms))
    except Exception:
        return float("nan")
    remaining_ms = next_ms - int(_utc_now().timestamp() * 1000)
    return max(0.0, remaining_ms / (1000.0 * 60.0 * 60.0))


def _latest_premium_index_rate(snapshot: dict[str, Any]) -> float:
    mark_price = _safe_float(snapshot.get("markPrice"))
    index_price = _safe_float(snapshot.get("indexPrice"))
    if math.isnan(mark_price) or math.isnan(index_price) or abs(index_price) < 1e-12:
        return float("nan")
    return (mark_price - index_price) / index_price


def _basis_from_mark_price_snapshot(snapshot: dict[str, Any]) -> tuple[float, float]:
    mark_price = _safe_float(snapshot.get("markPrice"))
    index_price = _safe_float(snapshot.get("indexPrice"))
    if math.isnan(mark_price) or math.isnan(index_price) or abs(index_price) < 1e-12:
        return float("nan"), float("nan")
    basis_usdt = mark_price - index_price
    basis_rate_pct = basis_usdt / index_price * 100.0
    return basis_rate_pct, basis_usdt


def _safe_public_fetch(default: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def _empty_hourly_stats() -> dict[str, float]:
    return {
        "hour_return_pct": float("nan"),
        "hour_return_z": float("nan"),
        "day_return_pct": float("nan"),
        "hour_quote_volume": float("nan"),
        "hour_volume_multiple": float("nan"),
        "hour_trade_count_multiple": float("nan"),
        "hour_upper_wick_pct": float("nan"),
        "hour_close_location_pct": float("nan"),
    }


def _clip_funding_rate(rate: float, cap_rate: float | None, floor_rate: float | None) -> float:
    if math.isnan(rate):
        return float("nan")
    if cap_rate is not None:
        rate = min(rate, cap_rate)
    if floor_rate is not None:
        rate = max(rate, floor_rate)
    return rate


def _kline_interval_ms(interval: str) -> int:
    lookup = {
        "1m": 60_000,
        "3m": 3 * 60_000,
        "5m": 5 * 60_000,
        "15m": 15 * 60_000,
        "30m": 30 * 60_000,
        "1h": 60 * 60_000,
        "2h": 2 * 60 * 60_000,
        "4h": 4 * 60 * 60_000,
    }
    return lookup.get(interval, 5 * 60_000)


def _select_premium_kline_interval(lookback_ms: int) -> str:
    for interval in ("5m", "15m", "30m", "1h", "2h", "4h"):
        if lookback_ms / _kline_interval_ms(interval) <= 1400:
            return interval
    return "4h"


def _premium_segments_from_klines(klines: list[list[Any]]) -> list[tuple[int, int, float]]:
    segments: list[tuple[int, int, float]] = []
    for row in klines:
        if len(row) < 7:
            continue
        try:
            start_ms = int(row[0])
            end_ms = int(row[6]) + 1
            rate = float(row[4])
        except Exception:
            continue
        if math.isnan(rate) or end_ms <= start_ms:
            continue
        segments.append((start_ms, end_ms, rate))
    return segments


def _weighted_premium_average(
    segments: list[tuple[int, int, float]],
    *,
    window_start_ms: int,
    window_end_ms: int,
    tail_rate: float | None = None,
    tail_start_ms: int | None = None,
    tail_end_ms: int | None = None,
) -> float:
    weighted_sum = 0.0
    weight_ms = 0

    for start_ms, end_ms, rate in segments:
        overlap_start = max(start_ms, window_start_ms)
        overlap_end = min(end_ms, window_end_ms)
        if overlap_end <= overlap_start:
            continue
        duration_ms = overlap_end - overlap_start
        weighted_sum += rate * duration_ms
        weight_ms += duration_ms

    if tail_rate is not None and tail_start_ms is not None and tail_end_ms is not None and not math.isnan(tail_rate):
        overlap_start = max(tail_start_ms, window_start_ms)
        overlap_end = min(tail_end_ms, window_end_ms)
        if overlap_end > overlap_start:
            duration_ms = overlap_end - overlap_start
            weighted_sum += tail_rate * duration_ms
            weight_ms += duration_ms

    if weight_ms <= 0:
        return float("nan")
    return weighted_sum / float(weight_ms)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    q = min(max(q, 0.0), 1.0)
    ranked = sorted(values)
    position = q * (len(ranked) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ranked[lower])
    blend = position - lower
    return float(ranked[lower] * (1.0 - blend) + ranked[upper] * blend)


def _estimate_next_funding_rate(
    *,
    funding_snapshot: dict[str, Any],
    interval_hours: int,
    cap_rate: float | None,
    floor_rate: float | None,
    funding_history: list[dict[str, Any]],
    premium_klines: list[list[Any]],
    websocket_snapshot: dict[str, Any] | None,
) -> dict[str, float]:
    try:
        next_funding_ms = int(float(funding_snapshot.get("nextFundingTime")))
    except Exception:
        return {
            "predicted_rate": float("nan"),
            "predicted_low_rate": float("nan"),
            "predicted_high_rate": float("nan"),
            "predicted_band_rate": float("nan"),
            "predicted_mae_rate": float("nan"),
            "latest_premium_rate": float("nan"),
            "window_elapsed_pct": float("nan"),
            "backtest_count": 0.0,
        }

    now_ms = int(_utc_now().timestamp() * 1000)
    interval_ms = max(1, int(interval_hours)) * 60 * 60 * 1000
    window_start_ms = max(0, next_funding_ms - interval_ms)
    elapsed_ms = min(max(0, now_ms - window_start_ms), interval_ms)
    window_elapsed_pct = elapsed_ms / interval_ms * 100.0 if interval_ms else float("nan")

    segments = _premium_segments_from_klines(premium_klines)
    latest_premium_rate = _latest_premium_index_rate(funding_snapshot)
    tail_rate = latest_premium_rate
    tail_end_ms = min(now_ms, next_funding_ms)
    last_closed_segment_end_ms = window_start_ms
    closed_segments = [segment for segment in segments if segment[1] <= tail_end_ms]
    if closed_segments:
        last_closed_segment_end_ms = max(end_ms for _, end_ms, _ in closed_segments)

    if websocket_snapshot:
        try:
            latest_ws_rate = float(websocket_snapshot.get("latest_premium_rate"))
            if not math.isnan(latest_ws_rate):
                latest_premium_rate = latest_ws_rate
        except Exception:
            pass
        try:
            avg_ws_rate = float(websocket_snapshot.get("avg_premium_rate"))
            if not math.isnan(avg_ws_rate):
                tail_rate = avg_ws_rate
        except Exception:
            tail_rate = latest_premium_rate

    try:
        interest_rate = float(funding_snapshot.get("interestRate"))
    except Exception:
        interest_rate = 0.0

    current_avg_premium_rate = _weighted_premium_average(
        closed_segments,
        window_start_ms=window_start_ms,
        window_end_ms=tail_end_ms,
        tail_rate=tail_rate,
        tail_start_ms=max(window_start_ms, last_closed_segment_end_ms),
        tail_end_ms=tail_end_ms,
    )
    raw_rate = _clip_funding_rate(current_avg_premium_rate + interest_rate, cap_rate, floor_rate)

    errors: list[float] = []
    for item in funding_history:
        try:
            funding_time_ms = int(float(item.get("fundingTime")))
            actual_rate = float(item.get("fundingRate"))
        except Exception:
            continue
        historical_avg_premium = _weighted_premium_average(
            segments,
            window_start_ms=max(0, funding_time_ms - interval_ms),
            window_end_ms=funding_time_ms,
        )
        historical_pred_rate = _clip_funding_rate(historical_avg_premium + interest_rate, cap_rate, floor_rate)
        if math.isnan(historical_pred_rate):
            continue
        errors.append(actual_rate - historical_pred_rate)

    backtest_count = len(errors)
    if errors:
        bias_rate = float(sum(errors) / backtest_count)
        abs_errors = [abs(value) for value in errors]
        mae_rate = float(sum(abs_errors) / backtest_count)
        band_rate = max(mae_rate, _quantile(abs_errors, 0.80))
    else:
        bias_rate = 0.0
        mae_rate = float("nan")
        band_rate = float("nan")

    calibrated_rate = _clip_funding_rate(raw_rate + bias_rate, cap_rate, floor_rate)
    if math.isnan(calibrated_rate) or math.isnan(band_rate):
        low_rate = float("nan")
        high_rate = float("nan")
    else:
        low_rate = _clip_funding_rate(calibrated_rate - band_rate, cap_rate, floor_rate)
        high_rate = _clip_funding_rate(calibrated_rate + band_rate, cap_rate, floor_rate)

    return {
        "predicted_rate": calibrated_rate,
        "predicted_low_rate": low_rate,
        "predicted_high_rate": high_rate,
        "predicted_band_rate": band_rate,
        "predicted_mae_rate": mae_rate,
        "latest_premium_rate": latest_premium_rate,
        "window_elapsed_pct": window_elapsed_pct,
        "backtest_count": int(backtest_count),
    }


def _crossed_above(level: float, observed_high: float) -> bool:
    return not math.isnan(level) and observed_high > level


def _crossed_below(level: float, observed_low: float) -> bool:
    return not math.isnan(level) and observed_low < level


_load_local_env()

BASE_URL = _env_value("BINANCE_FAPI_BASE", default="https://fapi.binance.com")
TIMEOUT = int(_env_value("HTTP_TIMEOUT", default="12"))
REQUESTS_PER_SECOND = float(
    _env_value("REQUESTS_PER_SECOND", "REQUESTS_PER_SEC", "RATE_LIMIT_REQ_PER_SEC", default="4.0")
)
RETRIES = int(_env_value("HTTP_RETRIES", "RETRIES", default="2"))
MAX_SYMBOLS_TO_SCAN = int(_env_value("MAX_SYMBOLS_TO_SCAN", "LEVELS_MAX_SYMBOLS", default="18"))
FAST_MAX_SYMBOLS = int(_env_value("FAST_MAX_SYMBOLS", default="12"))
DEEP_MAX_TOTAL_SYMBOLS_TO_SCAN = int(_env_value("DEEP_MAX_TOTAL_SYMBOLS_TO_SCAN", default="28"))
CRIME_SYMBOLS_TO_SCAN = int(_env_value("CRIME_SYMBOLS_TO_SCAN", default="10"))
DAILY_KLINE_LIMIT = int(_env_value("DAILY_KLINE_LIMIT", default="1500"))
INCLUDE_TRADFI_BREAKOUTS = _env_value("INCLUDE_TRADFI_BREAKOUTS", default="0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ENABLE_MODELED_FUNDING = _env_value("ENABLE_MODELED_FUNDING", default="0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALWAYS_SCAN_SYMBOLS = tuple(
    symbol.strip().upper()
    for symbol in _env_value(
        "ALWAYS_SCAN_SYMBOLS",
        "FORCE_SCAN_SYMBOLS",
        default="COPPERUSDT,XAUUSDT,XAGUSDT,XPTUSDT,XPDUSDT,CLUSDT,NATGASUSDT,NVDAUSDT,GOOGLUSDT,TSLAUSDT,INTCUSDT,HOODUSDT,MSTRUSDT,AMZNUSDT,CRCLUSDT,COINUSDT,PLTRUSDT,PAYPUSDT,METAUSDT,EWYUSDT,EWJUSDT",
    ).split(",")
    if symbol.strip()
)

BINANCE_API_KEY = _env_value("BINANCE_API_KEY", default="")
BINANCE_API_SECRET = _env_value("BINANCE_API_SECRET", default="")
BINANCE_RECV_WINDOW = int(_env_value("BINANCE_RECV_WINDOW", "BINANCE_RECV_WINDOW_MS", default="5000"))
PNL_RECENT_DAYS = int(_env_value("PNL_RECENT_DAYS", default="90"))
PNL_MAX_EXPORT_FETCHES = int(_env_value("PNL_EXPORT_MAX_FETCHES", "PNL_EXPORT_MAX_YEARS", default="5"))
PNL_CACHE_DIR = _env_value("PNL_CACHE_DIR", default=str(APP_DIR / ".cache" / "binance_income"))
PNL_BENCHMARKS = [s.strip().upper() for s in _env_value("PNL_BENCHMARKS", default="BTCUSDT,BNBUSDT").split(",") if s.strip()]
FUNDING_BACKTEST_WINDOWS = int(_env_value("FUNDING_BACKTEST_WINDOWS", default="4"))
FUNDING_STREAM_SAMPLE_SECONDS = float(_env_value("FUNDING_STREAM_SAMPLE_SECONDS", default="1.0"))
LONG_SHORT_RATIO_PERIOD = _env_value("LONG_SHORT_RATIO_PERIOD", default="1h")
CRIME_PUMP_PERIOD = _env_value("CRIME_PUMP_PERIOD", default="1h")
CRIME_DEPTH_LIMIT = int(_env_value("CRIME_DEPTH_LIMIT", default="50"))
CRIME_HOURLY_LOOKBACK = int(_env_value("CRIME_HOURLY_LOOKBACK", default="50"))
CRIME_MIN_QUOTE_VOLUME = float(_env_value("CRIME_MIN_QUOTE_VOLUME", default="2500000"))
CRIME_EXTERNAL_SYMBOLS_TO_SCAN = int(_env_value("CRIME_EXTERNAL_SYMBOLS_TO_SCAN", default="6"))
DEEP_EXTERNAL_SYMBOLS_TO_SCAN = int(_env_value("DEEP_EXTERNAL_SYMBOLS_TO_SCAN", default="4"))
PRECONVEX_SYMBOLS_TO_SCAN = int(_env_value("PRECONVEX_SYMBOLS_TO_SCAN", default="8"))
ATH_RUNWAY_SYMBOLS_TO_SCAN = int(_env_value("ATH_RUNWAY_SYMBOLS_TO_SCAN", default="25"))
DEEP_ATH_SYMBOLS_TO_SCAN = int(_env_value("DEEP_ATH_SYMBOLS_TO_SCAN", default="4"))
FULL_ATH_MAX_SYMBOLS_TO_SCAN = int(_env_value("FULL_ATH_MAX_SYMBOLS_TO_SCAN", default="35"))
FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN = int(_env_value("FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN", default="4"))
CRIME_EXCLUDED_BASE_ASSETS = {
    item.strip().upper()
    for item in _env_value("CRIME_EXCLUDED_BASE_ASSETS", default=DEFAULT_CRIME_EXCLUDED_BASES).split(",")
    if item.strip()
}
CRIME_FORCE_SYMBOLS = tuple(
    symbol.strip().upper()
    for symbol in _env_value("CRIME_FORCE_SYMBOLS", default=DEFAULT_CRIME_FORCE_SYMBOLS).split(",")
    if symbol.strip()
)
CRIME_MM_PROXIMITY_PATH = _env_value(
    "CRIME_MM_PROXIMITY_PATH",
    default=str(APP_DIR / "crime_mm_proximity.csv"),
)
CRIME_MM_PROXIMITY_SIGNALS = _env_value("CRIME_MM_PROXIMITY_SIGNALS", default="")
COINMARKETCAP_API_KEY = _env_value("COINMARKETCAP_API_KEY", "CMC_API_KEY", default="")
CMC_MOVERS_LIMIT = int(_env_value("CMC_MOVERS_LIMIT", default="200"))
CMC_MOVER_SYMBOLS_TO_SCAN = int(_env_value("CMC_MOVER_SYMBOLS_TO_SCAN", default="8"))
DWF_PORTFOLIO_SYMBOLS_TO_SCAN = int(_env_value("DWF_PORTFOLIO_SYMBOLS_TO_SCAN", default="8"))

st.set_page_config(page_title="Binance Breakouts + PnL", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #0b1020 0%, #111827 100%); color: #f3f4f6; }
    h1, h2, h3, p, label, .stMarkdown, .stCaption { color: #f9fafb !important; }
    .card { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=1800, show_spinner=False)
def load_benchmark_history(symbols: tuple[str, ...], start_ms: int, end_ms: int) -> pd.DataFrame:
    client = _client()
    day_ms = 24 * 60 * 60 * 1000
    frames: list[pd.DataFrame] = []

    for symbol in symbols:
        cursor = int(start_ms)
        rows: list[list[Any]] = []
        while cursor <= end_ms:
            batch = client.klines(
                symbol,
                interval="1d",
                limit=1500,
                start_time=cursor,
                end_time=end_ms,
            )
            if not batch:
                break
            rows.extend(batch)
            next_cursor = int(batch[-1][0]) + day_ms
            if next_cursor <= cursor or len(batch) < 1500:
                break
            cursor = next_cursor

        if not rows:
            continue

        frame = pd.DataFrame(rows, columns=list(range(len(rows[0]))))
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(frame[0], unit="ms", utc=True),
                symbol: pd.to_numeric(frame[4], errors="coerce"),
            }
        ).dropna()
        frames.append(frame.drop_duplicates(subset=["date"]).sort_values("date"))

    if not frames:
        return pd.DataFrame(columns=["date", *symbols])

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="outer")
    return merged.sort_values("date").reset_index(drop=True)


def _build_benchmark_comparison(
    result: PnLDashboardResult,
    daily_df: pd.DataFrame,
    *,
    benchmark_symbols: list[str],
) -> pd.DataFrame:
    if daily_df.empty or not benchmark_symbols:
        return pd.DataFrame(columns=["date", "Cumulative PnL %"])

    start_ms = int(daily_df["date"].min().timestamp() * 1000)
    end_ms = int(daily_df["date"].max().timestamp() * 1000)
    benchmark_df = load_benchmark_history(tuple(benchmark_symbols), start_ms, end_ms)
    baseline = _baseline_balance(result)
    compare_df = pd.DataFrame({"date": daily_df["date"], "Cumulative PnL %": daily_df["cumulative_pnl"] / baseline * 100.0})

    if benchmark_df.empty:
        return compare_df

    merged = compare_df.merge(benchmark_df, on="date", how="left").sort_values("date").ffill()
    for symbol in benchmark_symbols:
        if symbol not in merged.columns:
            continue
        first_valid = merged[symbol].dropna()
        if first_valid.empty:
            continue
        start_close = float(first_valid.iloc[0])
        if abs(start_close) < 1e-12:
            continue
        merged[f"{symbol} %"] = (merged[symbol] / start_close - 1.0) * 100.0

    keep_cols = ["date", "Cumulative PnL %"] + [f"{symbol} %" for symbol in benchmark_symbols if f"{symbol} %" in merged.columns]
    return merged[keep_cols]


EXTERNAL_CRIME_COLUMNS = [
    *DWF_LABS_PORTFOLIO_COLUMNS,
    "coinbase_spot_listed",
    "coinbase_spot_quote_volume_24h",
    "binance_spot_quote_volume_24h",
    "coingecko_total_volume_24h",
    "coingecko_coinbase_volume_24h",
    "coingecko_cex_volume_24h",
    "coingecko_dex_volume_24h",
    "kraken_spot_quote_volume_24h",
    "upbit_spot_quote_volume_24h",
    "upbit_krw_quote_volume_24h",
    "try_spot_quote_volume_24h",
    "emfx_spot_quote_volume_24h",
    "coinbase_volume_share_pct",
    "binance_volume_share_pct",
    "bitget_volume_share_pct",
    "gate_volume_share_pct",
    "cex_volume_share_pct",
    "kraken_volume_share_pct",
    "upbit_volume_share_pct",
    "krw_volume_share_pct",
    "try_volume_share_pct",
    "emfx_volume_share_pct",
    "dex_volume_share_pct",
    "cex_to_dex_volume_ratio",
    "cex_dex_volume_ratio_score",
    "binance_bitget_gate_share_pct",
    "binance_bitget_gate_share_score",
    "coinbase_bid_depth_2pct_usd",
    "coinbase_ask_depth_2pct_usd",
    "coinbase_total_depth_2pct_usd",
    "coinbase_book_imbalance_pct",
    "coinbase_depth_to_volume_pct",
    "coinbase_depth_to_perp_volume_pct",
    "top_venue",
    "top_venue_volume_24h",
    "top_venue_volume_share_pct",
    "top3_venue_volume_share_pct",
    "venue_hhi",
    "venue_hhi_score",
    "venue_count",
    "cex_venue_count",
    "dex_venue_count",
    "coinbase_bid_ask_spread_pct",
    "spot_external_quote_volume_24h",
    "spot_to_perp_volume_pct",
    "coinbase_to_perp_volume_pct",
    "coingecko_id",
    "coingecko_ath_usd",
    "coingecko_ath_change_pct",
    "coingecko_ath_date",
    "market_cap_usd",
    "fdv_usd",
    "fdv_to_market_cap",
    "circulating_supply_pct",
    "locked_supply_pct",
    "token_platform",
    "token_contract",
    "top10_holder_pct",
    "owner_holder_pct",
    "creator_holder_pct",
    "holder_count",
    "holder_source",
    "holder_concentration_score",
    "venue_concentration_score",
    "emfx_lane_score",
    "crime_coinbase_lane_score",
    "crime_owner_circle_score",
    "crime_spot_impulse_score",
    "crime_supply_control_score",
    *INVENTORY_TRANSFER_COLUMNS,
    "ath_price",
    "ath_multiple",
    "ath_upside_pct",
    "ath_source",
    "ath_runway_20x_flag",
]
EXTERNAL_CRIME_BOOL_COLUMNS = {
    "dwf_labs_portfolio",
    "coinbase_spot_listed",
    "inventory_transfer_risk_flag",
    "ath_runway_20x_flag",
}
EXTERNAL_CRIME_TEXT_COLUMNS = {
    "dwf_labs_portfolio_note",
    "coingecko_id",
    "coingecko_ath_date",
    "token_platform",
    "token_contract",
    "holder_source",
    "top_venue",
    "ath_source",
    *INVENTORY_TRANSFER_TEXT_COLUMNS,
}


@st.cache_data(ttl=900, show_spinner=False)
def load_external_crime_metrics_cached(base_assets: tuple[str, ...]) -> pd.DataFrame:
    rows = fetch_external_crime_metrics(list(base_assets))
    if not rows:
        return pd.DataFrame(columns=["normalized_base_asset", *EXTERNAL_CRIME_COLUMNS])
    return pd.DataFrame([row.__dict__ for row in rows])


@st.cache_data(ttl=1800, show_spinner=False)
def load_dwf_labs_portfolio_cached() -> pd.DataFrame:
    rows = fetch_dwf_labs_portfolio_members()
    if not rows:
        return pd.DataFrame(columns=["normalized_base_asset", *DWF_LABS_PORTFOLIO_COLUMNS])
    portfolio = pd.DataFrame(rows)
    if "normalized_base_asset" not in portfolio.columns:
        return pd.DataFrame(columns=["normalized_base_asset", *DWF_LABS_PORTFOLIO_COLUMNS])
    portfolio["normalized_base_asset"] = portfolio["normalized_base_asset"].map(normalize_base_asset)
    portfolio = portfolio[portfolio["normalized_base_asset"].astype(str).str.len() > 0].copy()
    portfolio["dwf_labs_portfolio"] = True
    portfolio["dwf_labs_portfolio_rank"] = pd.to_numeric(portfolio.get("rank"), errors="coerce")
    portfolio["dwf_labs_portfolio_score"] = (
        95.0 - portfolio["dwf_labs_portfolio_rank"].fillna(110.0).clip(lower=1.0, upper=150.0) * 0.12
    ).clip(lower=70.0, upper=95.0)
    portfolio["dwf_labs_portfolio_note"] = portfolio["dwf_labs_portfolio_rank"].apply(
        lambda rank: (
            f"DWF Labs CoinGecko portfolio member #{int(rank)} ({DWF_LABS_CATEGORY_URL})"
            if pd.notna(rank)
            else f"DWF Labs CoinGecko portfolio member ({DWF_LABS_CATEGORY_URL})"
        )
    )
    return (
        portfolio.sort_values(["dwf_labs_portfolio_rank", "normalized_base_asset"], ascending=[True, True])
        .drop_duplicates(subset=["normalized_base_asset"], keep="first")
        [["normalized_base_asset", *DWF_LABS_PORTFOLIO_COLUMNS]]
        .reset_index(drop=True)
    )


@st.cache_data(ttl=900, show_spinner=False)
def load_coinbase_spot_bases_cached() -> set[str]:
    return fetch_coinbase_spot_bases()


@st.cache_data(ttl=300, show_spinner=False)
def load_cmc_movers_cached(api_key_fingerprint: str, _api_key: str, limit: int) -> pd.DataFrame:
    # The fingerprint gives Streamlit a safe cache key without storing the raw CMC key in the hash.
    _ = api_key_fingerprint
    rows = fetch_cmc_movers(_api_key, limit=limit)
    if not rows:
        return pd.DataFrame(columns=["normalized_base_asset", *CMC_MOVER_COLUMNS])

    movers = pd.DataFrame([row.__dict__ for row in rows])
    movers["normalized_base_asset"] = movers["base_asset"].map(normalize_base_asset)
    movers = movers[movers["normalized_base_asset"].astype(str).str.len() > 0].copy()
    if movers.empty:
        return pd.DataFrame(columns=["normalized_base_asset", *CMC_MOVER_COLUMNS])

    movers["cmc_mover_score"] = pd.to_numeric(
        movers["cmc_mover_score"],
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    for col in CMC_MOVER_COLUMNS:
        if col not in movers.columns:
            movers[col] = "" if col in CMC_MOVER_TEXT_COLUMNS else float("nan")

    return (
        movers.sort_values("cmc_mover_score", ascending=False)
        .drop_duplicates(subset=["normalized_base_asset"], keep="first")
        [["normalized_base_asset", *CMC_MOVER_COLUMNS]]
    )


def _load_cmc_movers_for_scan(deep_scan: bool) -> pd.DataFrame:
    if not deep_scan or not COINMARKETCAP_API_KEY:
        return pd.DataFrame(columns=["normalized_base_asset", *CMC_MOVER_COLUMNS])
    return load_cmc_movers_cached(
        _key_fingerprint(COINMARKETCAP_API_KEY),
        COINMARKETCAP_API_KEY,
        CMC_MOVERS_LIMIT,
    )


def _empty_cmc_mover_columns(all_df: pd.DataFrame) -> pd.DataFrame:
    for col in CMC_MOVER_COLUMNS:
        if col not in all_df.columns:
            if col in CMC_MOVER_TEXT_COLUMNS:
                all_df[col] = ""
            elif col == "cmc_mover_score":
                all_df[col] = 0.0
            else:
                all_df[col] = float("nan")
    return all_df


def _apply_cmc_mover_metrics(all_df: pd.DataFrame, cmc_movers_df: pd.DataFrame) -> pd.DataFrame:
    all_df = _empty_cmc_mover_columns(all_df)
    if all_df.empty or cmc_movers_df.empty or "normalized_base_asset" not in cmc_movers_df.columns:
        return all_df

    merge_cols = ["normalized_base_asset", *[col for col in CMC_MOVER_COLUMNS if col in cmc_movers_df.columns]]
    merged = all_df[["symbol", "normalized_base_asset"]].merge(
        cmc_movers_df[merge_cols],
        on="normalized_base_asset",
        how="left",
    )
    merged = merged.set_index("symbol")
    for col in CMC_MOVER_COLUMNS:
        if col not in merged.columns:
            continue
        mapped = all_df["symbol"].map(merged[col])
        if col in CMC_MOVER_TEXT_COLUMNS:
            mapped = mapped.fillna("").astype(str)
        elif col == "cmc_mover_score":
            mapped = pd.to_numeric(mapped, errors="coerce").fillna(0.0).clip(lower=0.0, upper=100.0)
        else:
            mapped = pd.to_numeric(mapped, errors="coerce")
        all_df[col] = mapped
    return all_df


def _empty_mm_proximity_columns(all_df: pd.DataFrame) -> pd.DataFrame:
    for col in MM_PROXIMITY_COLUMNS:
        if col not in all_df.columns:
            all_df[col] = "" if col in MM_PROXIMITY_TEXT_COLUMNS else 0.0
    return all_df


def _parse_mm_proximity_env(raw_signals: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for raw_entry in str(raw_signals or "").split(";"):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split("|")]
        if not parts or not parts[0]:
            continue
        rows.append(
            {
                "base_asset": parts[0].upper(),
                "mm_proximity_maker": parts[1] if len(parts) > 1 else "",
                "mm_proximity_score": _safe_float(parts[2]) if len(parts) > 2 else 50.0,
                "mm_proximity_note": parts[3] if len(parts) > 3 else "Manual MM/social graph signal.",
                "mm_proximity_source": parts[4] if len(parts) > 4 else "",
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def load_mm_proximity_signals_cached(path: str, raw_signals: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    csv_path = Path(path)
    if csv_path.exists():
        try:
            frames.append(pd.read_csv(csv_path))
        except Exception:
            pass

    env_df = _parse_mm_proximity_env(raw_signals)
    if not env_df.empty:
        frames.append(env_df)

    if not frames:
        return pd.DataFrame(columns=["normalized_base_asset", *MM_PROXIMITY_COLUMNS])

    signals = pd.concat(frames, ignore_index=True)
    if "base_asset" not in signals.columns:
        return pd.DataFrame(columns=["normalized_base_asset", *MM_PROXIMITY_COLUMNS])

    signals["normalized_base_asset"] = signals["base_asset"].map(normalize_base_asset)
    rename_map = {
        "score": "mm_proximity_score",
        "maker": "mm_proximity_maker",
        "market_maker": "mm_proximity_maker",
        "note": "mm_proximity_note",
        "source": "mm_proximity_source",
        "source_url": "mm_proximity_source",
    }
    signals = signals.rename(columns={key: value for key, value in rename_map.items() if key in signals.columns})
    if "mm_proximity_score" not in signals.columns:
        signals["mm_proximity_score"] = 0.0
    signals["mm_proximity_score"] = pd.to_numeric(
        signals["mm_proximity_score"],
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0, upper=100.0)
    for col in MM_PROXIMITY_TEXT_COLUMNS:
        if col not in signals.columns:
            signals[col] = ""
        signals[col] = signals[col].fillna("").astype(str)

    signals = signals[signals["normalized_base_asset"].astype(str).str.len() > 0].copy()
    if signals.empty:
        return pd.DataFrame(columns=["normalized_base_asset", *MM_PROXIMITY_COLUMNS])
    return (
        signals.sort_values("mm_proximity_score", ascending=False)
        .drop_duplicates(subset=["normalized_base_asset"], keep="first")
        [["normalized_base_asset", *MM_PROXIMITY_COLUMNS]]
    )


def _apply_mm_proximity_signals(all_df: pd.DataFrame) -> pd.DataFrame:
    all_df = _empty_mm_proximity_columns(all_df)
    if all_df.empty:
        return all_df

    signals = load_mm_proximity_signals_cached(CRIME_MM_PROXIMITY_PATH, CRIME_MM_PROXIMITY_SIGNALS)
    if signals.empty:
        return all_df

    merged = all_df[["symbol", "normalized_base_asset"]].merge(signals, on="normalized_base_asset", how="left")
    merged = merged.set_index("symbol")
    for col in MM_PROXIMITY_COLUMNS:
        mapped = all_df["symbol"].map(merged[col]) if col in merged.columns else None
        if mapped is None:
            continue
        if col in MM_PROXIMITY_TEXT_COLUMNS:
            all_df[col] = mapped.fillna("").astype(str)
        else:
            all_df[col] = pd.to_numeric(mapped, errors="coerce").fillna(0.0).clip(lower=0.0, upper=100.0)
    return all_df


def _apply_dwf_mm_proximity_signal(all_df: pd.DataFrame) -> pd.DataFrame:
    if all_df.empty or "dwf_labs_portfolio_score" not in all_df.columns:
        return all_df
    all_df = _empty_mm_proximity_columns(all_df)
    dwf_score = pd.to_numeric(all_df["dwf_labs_portfolio_score"], errors="coerce").fillna(0.0).clip(
        lower=0.0,
        upper=100.0,
    )
    existing_mm_score = pd.to_numeric(all_df["mm_proximity_score"], errors="coerce").fillna(0.0)
    dwf_better = dwf_score > existing_mm_score
    if not bool(dwf_better.any()):
        return all_df
    all_df.loc[dwf_better, "mm_proximity_score"] = dwf_score[dwf_better]
    all_df.loc[dwf_better, "mm_proximity_maker"] = "DWF Labs"
    all_df.loc[dwf_better, "mm_proximity_note"] = all_df.loc[dwf_better, "dwf_labs_portfolio_note"].fillna(
        "DWF Labs CoinGecko portfolio member."
    )
    all_df.loc[dwf_better, "mm_proximity_source"] = DWF_LABS_CATEGORY_URL
    return all_df


def _empty_external_crime_columns(all_df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in EXTERNAL_CRIME_COLUMNS if col not in all_df.columns]
    if not missing:
        return all_df

    defaults: dict[str, Any] = {}
    for col in missing:
        if col in EXTERNAL_CRIME_BOOL_COLUMNS:
            defaults[col] = False
        elif col in EXTERNAL_CRIME_TEXT_COLUMNS:
            defaults[col] = ""
        else:
            defaults[col] = float("nan")

    # Add the sparse external-market schema in one concat to avoid fragmenting the
    # scan dataframe when public enrichment is skipped or only partly available.
    return pd.concat([all_df, pd.DataFrame(defaults, index=all_df.index)], axis=1).copy()


def _apply_external_crime_metrics(all_df: pd.DataFrame, crime_symbols: set[str]) -> pd.DataFrame:
    all_df = _empty_external_crime_columns(all_df)
    if all_df.empty or not crime_symbols:
        return all_df

    target_df = all_df[all_df["symbol"].isin(crime_symbols)].copy()
    base_assets = tuple(
        sorted(
            {
                str(base)
                for base in target_df["normalized_base_asset"].dropna().astype(str)
                if str(base).strip()
            }
        )
    )
    if not base_assets:
        return all_df

    external_df = load_external_crime_metrics_cached(base_assets)
    if external_df.empty or "normalized_base_asset" not in external_df.columns:
        return all_df

    merge_cols = ["normalized_base_asset", *[col for col in EXTERNAL_CRIME_COLUMNS if col in external_df.columns]]
    merged = target_df[["symbol", "normalized_base_asset", "quote_volume_24h"]].merge(
        external_df[merge_cols],
        on="normalized_base_asset",
        how="left",
    )
    merged = merged.set_index("symbol")
    for col in EXTERNAL_CRIME_COLUMNS:
        if col in merged.columns:
            mapped = all_df["symbol"].map(merged[col])
            if col in EXTERNAL_CRIME_BOOL_COLUMNS:
                mapped = mapped.where(mapped.notna(), False).astype(bool)
            elif col in EXTERNAL_CRIME_TEXT_COLUMNS:
                mapped = mapped.fillna("").astype(str)
            else:
                mapped = pd.to_numeric(mapped, errors="coerce")
            all_df.loc[all_df["symbol"].isin(merged.index), col] = mapped

    all_df = _apply_dwf_mm_proximity_signal(all_df)

    coinbase_direct = pd.to_numeric(all_df["coinbase_spot_quote_volume_24h"], errors="coerce")
    coinbase_cg = pd.to_numeric(all_df["coingecko_coinbase_volume_24h"], errors="coerce")
    coinbase = pd.concat([coinbase_direct, coinbase_cg], axis=1).max(axis=1)
    all_df["coinbase_spot_quote_volume_24h"] = coinbase
    binance_spot = pd.to_numeric(all_df["binance_spot_quote_volume_24h"], errors="coerce")
    coingecko_total = pd.to_numeric(all_df["coingecko_total_volume_24h"], errors="coerce")
    direct_sum = coinbase.fillna(0.0) + binance_spot.fillna(0.0)
    has_any_spot = coinbase.notna() | binance_spot.notna() | coingecko_total.notna()
    all_df["spot_external_quote_volume_24h"] = pd.concat([direct_sum, coingecko_total], axis=1).max(axis=1).where(
        has_any_spot,
        other=float("nan"),
    )

    perp_volume = pd.to_numeric(all_df["quote_volume_24h"], errors="coerce")
    valid_perp = perp_volume > 0
    all_df["spot_to_perp_volume_pct"] = (
        all_df["spot_external_quote_volume_24h"] / perp_volume * 100.0
    ).where(valid_perp, other=float("nan"))
    all_df["coinbase_to_perp_volume_pct"] = (coinbase / perp_volume * 100.0).where(valid_perp, other=float("nan"))
    cb_depth = pd.to_numeric(all_df["coinbase_total_depth_2pct_usd"], errors="coerce")
    all_df["coinbase_depth_to_perp_volume_pct"] = (cb_depth / perp_volume * 100.0).where(
        valid_perp,
        other=float("nan"),
    )
    cex_volume = pd.to_numeric(all_df["coingecko_cex_volume_24h"], errors="coerce")
    dex_volume = pd.to_numeric(all_df["coingecko_dex_volume_24h"], errors="coerce")
    cex_dex_total = cex_volume.fillna(0.0) + dex_volume.fillna(0.0)
    has_cex_dex_total = cex_dex_total > 0
    all_df["cex_volume_share_pct"] = (cex_volume / cex_dex_total * 100.0).where(
        has_cex_dex_total,
        other=float("nan"),
    )
    dex_denominator = dex_volume.where(dex_volume > 0)
    all_df["cex_to_dex_volume_ratio"] = (cex_volume / dex_denominator).where(
        cex_volume > 0,
        other=float("nan"),
    )
    all_df.loc[(cex_volume > 0) & ~(dex_volume > 0), "cex_to_dex_volume_ratio"] = 999.0
    ratio_score = _log_ratio_score(all_df["cex_to_dex_volume_ratio"], low=2.0, high=80.0)
    cex_share_score = _linear_score(all_df["cex_volume_share_pct"], low=60.0, high=98.0)
    all_df["cex_dex_volume_ratio_score"] = (
        ratio_score * 0.72
        + cex_share_score * 0.28
    ).where(has_cex_dex_total, other=0.0).clip(lower=0.0, upper=100.0)

    market_cap = pd.to_numeric(all_df["market_cap_usd"], errors="coerce")
    valid_market_cap = market_cap > 0
    all_df["spot_volume_to_mcap_pct"] = (
        all_df["spot_external_quote_volume_24h"] / market_cap * 100.0
    ).where(valid_market_cap, other=float("nan"))
    all_df["perp_volume_to_mcap_pct"] = (perp_volume / market_cap * 100.0).where(
        valid_market_cap,
        other=float("nan"),
    )
    oi_value = pd.to_numeric(all_df["oi_value_usdt"], errors="coerce")
    all_df["oi_to_market_cap_pct"] = (oi_value / market_cap * 100.0).where(valid_market_cap, other=float("nan"))

    top10 = pd.to_numeric(all_df["top10_holder_pct"], errors="coerce")
    owner = pd.to_numeric(all_df["owner_holder_pct"], errors="coerce")
    creator = pd.to_numeric(all_df["creator_holder_pct"], errors="coerce")
    all_df["holder_concentration_score"] = pd.concat(
        [
            top10.clip(lower=0.0, upper=100.0),
            owner.clip(lower=0.0, upper=100.0) * 1.5,
            creator.clip(lower=0.0, upper=100.0) * 1.5,
        ],
        axis=1,
    ).max(axis=1)
    all_df["venue_hhi_score"] = _linear_score(all_df["venue_hhi"], low=900.0, high=4_500.0)
    all_df["binance_bitget_gate_share_score"] = _linear_score(
        all_df["binance_bitget_gate_share_pct"],
        low=20.0,
        high=85.0,
    )
    all_df["emfx_lane_score"] = (
        _linear_score(all_df["emfx_volume_share_pct"], low=4.0, high=40.0) * 0.62
        + _linear_score(all_df["krw_volume_share_pct"], low=3.0, high=35.0) * 0.23
        + _linear_score(all_df["try_volume_share_pct"], low=1.0, high=20.0) * 0.15
    ).clip(lower=0.0, upper=100.0)
    top_venue_share = pd.to_numeric(all_df["top_venue_volume_share_pct"], errors="coerce")
    top3_share = pd.to_numeric(all_df["top3_venue_volume_share_pct"], errors="coerce")
    all_df["venue_concentration_score"] = pd.concat(
        [
            top_venue_share,
            top3_share * 0.75,
            all_df["venue_hhi_score"],
            all_df["binance_bitget_gate_share_score"],
        ],
        axis=1,
    ).max(axis=1)
    return all_df


def _apply_ath_runway(all_df: pd.DataFrame) -> pd.DataFrame:
    if all_df.empty:
        return all_df

    for col in ("coingecko_ath_usd", "ath_scanned", "last_price", "upside_to_ath_pct"):
        if col not in all_df.columns:
            all_df[col] = float("nan")

    cg_ath = pd.to_numeric(all_df["coingecko_ath_usd"], errors="coerce")
    scanned_ath = pd.to_numeric(all_df["ath_scanned"], errors="coerce")
    last_price = pd.to_numeric(all_df["last_price"], errors="coerce")

    valid_cg = (cg_ath > 0) & (last_price > 0)
    valid_scanned = (scanned_ath > 0) & (last_price > 0)
    ath_price = cg_ath.where(valid_cg, other=scanned_ath.where(valid_scanned, other=float("nan")))
    ath_source = pd.Series("Unavailable", index=all_df.index, dtype="object")
    ath_source = ath_source.where(~valid_scanned, other="Binance scanned history")
    ath_source = ath_source.where(~valid_cg, other="CoinGecko")

    multiple = (ath_price / last_price).where((ath_price > 0) & (last_price > 0), other=float("nan"))
    upside_pct = ((multiple - 1.0) * 100.0).where(multiple.notna(), other=float("nan"))
    all_df["ath_price"] = ath_price
    all_df["ath_multiple"] = multiple
    all_df["ath_upside_pct"] = upside_pct
    all_df["ath_source"] = ath_source
    all_df["ath_runway_20x_flag"] = multiple >= 20.0

    # Prefer external lifetime ATH for runway scoring when available, but keep the
    # Binance scanned-history fallback so fast scans still remain useful.
    all_df["upside_to_ath_pct"] = upside_pct.where(upside_pct.notna(), other=all_df["upside_to_ath_pct"])
    return all_df


@st.cache_data(ttl=60)
def run_scan(refresh_nonce: int, scan_mode: str = "Fast") -> tuple[pd.DataFrame, pd.DataFrame]:
    _ = (refresh_nonce, scan_mode)
    normalized_scan_mode = str(scan_mode).strip().lower()
    full_ath_scan = normalized_scan_mode in {"full ath", "full ath runway", "ath"}
    deep_scan = normalized_scan_mode in {"deep", "full ath", "full ath runway", "ath"}
    scan_max_symbols = FULL_ATH_MAX_SYMBOLS_TO_SCAN if full_ath_scan else MAX_SYMBOLS_TO_SCAN if deep_scan else FAST_MAX_SYMBOLS
    crime_symbol_limit = CRIME_SYMBOLS_TO_SCAN if deep_scan else 0
    modeled_funding_enabled = ENABLE_MODELED_FUNDING and deep_scan and not full_ath_scan
    client = _client()
    symbol_meta = {s.symbol: s for s in client.perpetual_usdt_symbols()}
    symbols = {symbol: meta.base_asset for symbol, meta in symbol_meta.items()}
    tradfi_symbols = {
        symbol for symbol, meta in symbol_meta.items() if meta.underlying_type in TRADFI_ALWAYS_INCLUDE_TYPES
    }
    ticker = pd.DataFrame(client.ticker_24hr())
    if ticker.empty:
        return pd.DataFrame(), pd.DataFrame()

    ticker["symbol"] = ticker["symbol"].astype(str).str.upper()
    ticker = ticker[ticker["symbol"].isin(symbols.keys())].copy()
    if not INCLUDE_TRADFI_BREAKOUTS:
        ticker = ticker[
            ticker["symbol"].map(
                lambda symbol: (symbol_meta.get(str(symbol)).underlying_type if symbol_meta.get(str(symbol)) else "")
                not in TRADFI_ALWAYS_INCLUDE_TYPES
            )
        ].copy()

    for col in ("lastPrice", "highPrice", "lowPrice", "quoteVolume", "priceChangePercent", "count"):
        ticker[col] = pd.to_numeric(ticker.get(col), errors="coerce")

    ticker = ticker.dropna(subset=["lastPrice", "highPrice", "lowPrice", "quoteVolume"])
    ticker["base_asset"] = ticker["symbol"].map(symbols)
    ticker["normalized_base_asset"] = ticker["base_asset"].map(normalize_base_asset)
    ticker["crime_excluded_major"] = ticker["normalized_base_asset"].isin(CRIME_EXCLUDED_BASE_ASSETS)
    cmc_movers_df = _load_cmc_movers_for_scan(deep_scan)
    if cmc_movers_df.empty:
        ticker["cmc_mover_seed_score"] = 0.0
    else:
        cmc_seed = cmc_movers_df.set_index("normalized_base_asset")["cmc_mover_score"]
        ticker["cmc_mover_seed_score"] = (
            ticker["normalized_base_asset"].map(cmc_seed).fillna(0.0).astype("float64")
        )
    dwf_portfolio_df = load_dwf_labs_portfolio_cached() if deep_scan else pd.DataFrame()
    if dwf_portfolio_df.empty:
        ticker["dwf_labs_portfolio_seed"] = False
        ticker["dwf_labs_portfolio_score_seed"] = 0.0
        ticker["dwf_labs_portfolio_rank_seed"] = float("nan")
        ticker["dwf_labs_portfolio_note_seed"] = ""
    else:
        dwf_index = dwf_portfolio_df.set_index("normalized_base_asset")
        ticker["dwf_labs_portfolio_seed"] = ticker["normalized_base_asset"].isin(dwf_index.index)
        ticker["dwf_labs_portfolio_score_seed"] = (
            ticker["normalized_base_asset"]
            .map(dwf_index["dwf_labs_portfolio_score"])
            .fillna(0.0)
            .astype("float64")
        )
        ticker["dwf_labs_portfolio_rank_seed"] = pd.to_numeric(
            ticker["normalized_base_asset"].map(dwf_index["dwf_labs_portfolio_rank"]),
            errors="coerce",
        )
        ticker["dwf_labs_portfolio_note_seed"] = (
            ticker["normalized_base_asset"].map(dwf_index["dwf_labs_portfolio_note"]).fillna("").astype(str)
        )
    coinbase_spot_bases = load_coinbase_spot_bases_cached() if deep_scan else set()
    ticker["coinbase_spot_seed"] = ticker["normalized_base_asset"].isin(coinbase_spot_bases)
    ticker["range_24h_pct"] = (ticker["highPrice"] / ticker["lowPrice"] - 1.0) * 100.0
    ticker["crime_seed_score"] = (
        _percentile_score(ticker["priceChangePercent"], positive_only=True) * 0.42
        + _percentile_score(ticker["range_24h_pct"], positive_only=True) * 0.22
        + _percentile_score(ticker["quoteVolume"], positive_only=True) * 0.18
        + _percentile_score(ticker["count"], positive_only=True) * 0.10
        + _percentile_score(ticker["quoteVolume"], ascending=False, positive_only=True) * 0.08
        + ticker["cmc_mover_seed_score"].fillna(0.0) * 0.16
        + ticker["dwf_labs_portfolio_score_seed"].fillna(0.0) * 0.12
        + ticker["coinbase_spot_seed"].astype(float) * 12.0
    )
    ticker["convexity_seed_score"] = (
        _band_score(ticker["priceChangePercent"], low=-8.0, sweet_low=2.0, sweet_high=45.0, high=120.0) * 0.30
        + _linear_score(ticker["range_24h_pct"], low=4.0, high=55.0) * 0.12
        + _percentile_score(ticker["quoteVolume"], positive_only=True) * 0.13
        + _percentile_score(ticker["count"], positive_only=True) * 0.13
        + _percentile_score(ticker["quoteVolume"], ascending=False, positive_only=True) * 0.08
        + ticker["cmc_mover_seed_score"].fillna(0.0) * 0.13
        + ticker["dwf_labs_portfolio_score_seed"].fillna(0.0) * 0.16
        + ticker["coinbase_spot_seed"].astype(float) * 11.0
    ).clip(lower=0.0, upper=100.0)
    ticker = ticker.sort_values("quoteVolume", ascending=False)

    top_ticker = ticker.head(scan_max_symbols)
    crime_pool = ticker[
        (~ticker["crime_excluded_major"])
        & (ticker["quoteVolume"] >= CRIME_MIN_QUOTE_VOLUME)
        & (
            (ticker["priceChangePercent"].fillna(0.0) > 0.0)
            | ticker["coinbase_spot_seed"]
            | (ticker["cmc_mover_seed_score"] >= 35.0)
            | ticker["dwf_labs_portfolio_seed"]
        )
    ].copy()
    crime_ticker = crime_pool.sort_values(
        ["crime_seed_score", "priceChangePercent", "quoteVolume", "symbol"],
        ascending=[False, False, False, True],
    ).head(crime_symbol_limit)
    preconvex_ticker = ticker[
        (~ticker["crime_excluded_major"])
        & (ticker["quoteVolume"] >= CRIME_MIN_QUOTE_VOLUME * 0.40)
        & (
            (ticker["convexity_seed_score"] >= 45.0)
            | ticker["coinbase_spot_seed"]
            | (ticker["cmc_mover_seed_score"] >= 35.0)
            | ticker["dwf_labs_portfolio_seed"]
        )
    ].sort_values(
        ["convexity_seed_score", "priceChangePercent", "quoteVolume", "symbol"],
        ascending=[False, False, False, True],
    ).head(max(0, PRECONVEX_SYMBOLS_TO_SCAN))
    forced_crime_ticker = ticker[
        ticker["symbol"].isin(CRIME_FORCE_SYMBOLS)
        & (~ticker["crime_excluded_major"])
    ].copy()
    dwf_ticker = ticker[
        ticker["dwf_labs_portfolio_seed"]
        & (~ticker["crime_excluded_major"])
    ].sort_values(
        ["convexity_seed_score", "crime_seed_score", "priceChangePercent", "quoteVolume", "dwf_labs_portfolio_score_seed", "symbol"],
        ascending=[False, False, False, False, False, True],
    ).head(max(0, DWF_PORTFOLIO_SYMBOLS_TO_SCAN))
    ath_runway_budget = (
        max(0, FULL_ATH_MAX_SYMBOLS_TO_SCAN)
        if full_ath_scan
        else max(0, min(ATH_RUNWAY_SYMBOLS_TO_SCAN, DEEP_ATH_SYMBOLS_TO_SCAN))
    )
    ath_external_budget = (
        max(0, FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN)
        if full_ath_scan
        else max(0, min(ATH_RUNWAY_SYMBOLS_TO_SCAN, DEEP_ATH_SYMBOLS_TO_SCAN))
    )
    ath_runway_ticker = ticker[(~ticker["crime_excluded_major"])].sort_values(
        ["convexity_seed_score", "quoteVolume", "symbol"],
        ascending=[False, False, True],
    ).head(ath_runway_budget)
    ath_external_ticker = ath_runway_ticker.head(ath_external_budget)
    cmc_crime_ticker = ticker[
        (ticker["cmc_mover_seed_score"] >= 45.0)
        & (~ticker["crime_excluded_major"])
    ].sort_values(
        ["cmc_mover_seed_score", "priceChangePercent", "quoteVolume", "symbol"],
        ascending=[False, False, False, True],
    ).head(max(0, CMC_MOVER_SYMBOLS_TO_SCAN))
    if not deep_scan:
        crime_ticker = ticker.iloc[0:0].copy()
        preconvex_ticker = ticker.iloc[0:0].copy()
        forced_crime_ticker = ticker.iloc[0:0].copy()
        dwf_ticker = ticker.iloc[0:0].copy()
        ath_runway_ticker = ticker.iloc[0:0].copy()
        ath_external_ticker = ticker.iloc[0:0].copy()
        cmc_crime_ticker = ticker.iloc[0:0].copy()
    forced_symbols = {
        symbol
        for symbol in set(ALWAYS_SCAN_SYMBOLS)
        if symbol in symbol_meta and (INCLUDE_TRADFI_BREAKOUTS or symbol_meta[symbol].underlying_type not in TRADFI_ALWAYS_INCLUDE_TYPES)
    }
    if INCLUDE_TRADFI_BREAKOUTS:
        forced_symbols |= tradfi_symbols
    forced_ticker = ticker[ticker["symbol"].isin(forced_symbols)]
    selected_ticker = (
        pd.concat(
            [
                top_ticker,
                crime_ticker,
                preconvex_ticker,
                ath_runway_ticker,
                cmc_crime_ticker,
                dwf_ticker,
                forced_crime_ticker,
                forced_ticker,
            ],
            ignore_index=True,
        )
        .drop_duplicates(subset=["symbol"], keep="first")
    )
    if full_ath_scan:
        forced_full_mask = selected_ticker["symbol"].isin(set(CRIME_FORCE_SYMBOLS) | set(forced_symbols))
        forced_selected = selected_ticker[forced_full_mask].copy().head(max(0, FULL_ATH_MAX_SYMBOLS_TO_SCAN))
        remaining_budget = max(0, FULL_ATH_MAX_SYMBOLS_TO_SCAN - len(forced_selected))
        ranked_selected = selected_ticker[~forced_full_mask].sort_values(
            ["convexity_seed_score", "crime_seed_score", "dwf_labs_portfolio_score_seed", "quoteVolume", "symbol"],
            ascending=[False, False, False, False, True],
        ).head(remaining_budget)
        ticker = (
            pd.concat([forced_selected, ranked_selected], ignore_index=True)
            .drop_duplicates(subset=["symbol"], keep="first")
            .sort_values(["convexity_seed_score", "dwf_labs_portfolio_score_seed", "quoteVolume", "symbol"], ascending=[False, False, False, True])
            .reset_index(drop=True)
        )
    elif deep_scan:
        forced_deep_mask = selected_ticker["symbol"].isin(set(CRIME_FORCE_SYMBOLS) | set(forced_symbols))
        forced_selected = selected_ticker[forced_deep_mask].copy().head(max(0, DEEP_MAX_TOTAL_SYMBOLS_TO_SCAN))
        remaining_budget = max(0, DEEP_MAX_TOTAL_SYMBOLS_TO_SCAN - len(forced_selected))
        ranked_selected = selected_ticker[~forced_deep_mask].sort_values(
            ["crime_seed_score", "convexity_seed_score", "dwf_labs_portfolio_score_seed", "quoteVolume", "symbol"],
            ascending=[False, False, False, False, True],
        ).head(remaining_budget)
        ticker = (
            pd.concat([forced_selected, ranked_selected], ignore_index=True)
            .drop_duplicates(subset=["symbol"], keep="first")
            .sort_values(["crime_seed_score", "convexity_seed_score", "dwf_labs_portfolio_score_seed", "quoteVolume", "symbol"], ascending=[False, False, False, False, True])
            .reset_index(drop=True)
        )
    else:
        ticker = (
            selected_ticker.sort_values("quoteVolume", ascending=False)
            .reset_index(drop=True)
        )
    crime_symbols = set(
        pd.concat([crime_ticker, preconvex_ticker, cmc_crime_ticker, dwf_ticker, forced_crime_ticker], ignore_index=True)
        .drop_duplicates(subset=["symbol"], keep="first")
        .head(max(0, crime_symbol_limit + len(preconvex_ticker) + len(cmc_crime_ticker) + len(dwf_ticker) + len(forced_crime_ticker)))
        ["symbol"]
        .astype(str)
    )
    external_candidates = (
        pd.concat(
            [
                crime_ticker.head(max(0, CRIME_EXTERNAL_SYMBOLS_TO_SCAN)),
                preconvex_ticker.head(max(0, PRECONVEX_SYMBOLS_TO_SCAN)),
                ath_external_ticker,
                cmc_crime_ticker.head(max(0, CMC_MOVER_SYMBOLS_TO_SCAN)),
                dwf_ticker.head(max(0, DWF_PORTFOLIO_SYMBOLS_TO_SCAN)),
                forced_crime_ticker,
            ],
            ignore_index=True,
        )
        .drop_duplicates(subset=["symbol"], keep="first")
    )
    if full_ath_scan:
        forced_external_mask = external_candidates["symbol"].isin(set(CRIME_FORCE_SYMBOLS) | set(forced_symbols))
        forced_external = external_candidates[forced_external_mask].copy().head(max(0, FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN))
        external_remaining = max(0, FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN - len(forced_external))
        external_candidates = pd.concat(
            [
                forced_external,
                external_candidates[~forced_external_mask].head(external_remaining),
            ],
            ignore_index=True,
        ).drop_duplicates(subset=["symbol"], keep="first")
    elif deep_scan:
        forced_external_mask = external_candidates["symbol"].isin(set(CRIME_FORCE_SYMBOLS) | set(forced_symbols))
        forced_external = external_candidates[forced_external_mask].copy().head(max(0, DEEP_EXTERNAL_SYMBOLS_TO_SCAN))
        external_remaining = max(0, DEEP_EXTERNAL_SYMBOLS_TO_SCAN - len(forced_external))
        external_candidates = pd.concat(
            [
                forced_external,
                external_candidates[~forced_external_mask].head(external_remaining),
            ],
            ignore_index=True,
        ).drop_duplicates(subset=["symbol"], keep="first")
    external_crime_symbols = set(external_candidates["symbol"].astype(str))

    rows: list[BreakoutRow] = []
    now_ms = int(_utc_now().timestamp() * 1000)
    btc_klines = _safe_public_fetch([], client.klines_1d, "BTCUSDT", limit=DAILY_KLINE_LIMIT)
    websocket_snapshots = (
        _safe_public_fetch(
            {},
            client.mark_price_stream_snapshot,
            ticker["symbol"].astype(str).str.upper().tolist(),
            sample_seconds=FUNDING_STREAM_SAMPLE_SECONDS,
            update_speed="1s",
        )
        if modeled_funding_enabled
        else {}
    )
    funding_info_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in _safe_public_fetch([], client.funding_info)
        if str(item.get("symbol", "")).upper() in symbols
    }
    funding_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in _safe_public_fetch([], client.mark_price)
        if str(item.get("symbol", "")).upper() in symbols
    }
    for _, t in ticker.iterrows():
        symbol = str(t["symbol"])
        compute_crime_detail = symbol in crime_symbols
        klines = _safe_public_fetch([], client.klines_1d, symbol, limit=DAILY_KLINE_LIMIT)
        hourly_klines = (
            _safe_public_fetch([], client.klines, symbol, interval="1h", limit=max(10, CRIME_HOURLY_LOOKBACK))
            if compute_crime_detail
            else []
        )
        levels = levels_from_klines(klines)
        corr_to_btc, corr_window_days = _latest_btc_correlation(symbol, klines, btc_klines)
        funding_snapshot = funding_by_symbol.get(symbol, {})
        funding_info_snapshot = funding_info_by_symbol.get(symbol, {})
        interval_hours = _coerce_funding_interval_hours(funding_info_snapshot.get("fundingIntervalHours"))
        cap_rate = None
        floor_rate = None
        if "adjustedFundingRateCap" in funding_info_snapshot:
            try:
                cap_rate = float(funding_info_snapshot.get("adjustedFundingRateCap"))
            except Exception:
                cap_rate = None
        if "adjustedFundingRateFloor" in funding_info_snapshot:
            try:
                floor_rate = float(funding_info_snapshot.get("adjustedFundingRateFloor"))
            except Exception:
                floor_rate = None

        funding_pct = _funding_rate_to_pct(funding_snapshot.get("lastFundingRate"))
        annualized_funding_pct = _annualized_funding_pct(
            funding_snapshot.get("lastFundingRate"),
            interval_hours=interval_hours,
        )
        try:
            next_funding_ms = int(float(funding_snapshot.get("nextFundingTime")))
        except Exception:
            next_funding_ms = now_ms + interval_hours * 60 * 60 * 1000
        current_window_start_ms = max(0, next_funding_ms - interval_hours * 60 * 60 * 1000)
        elapsed_ms = min(max(0, now_ms - current_window_start_ms), interval_hours * 60 * 60 * 1000)
        window_elapsed_pct = (
            elapsed_ms / (interval_hours * 60 * 60 * 1000) * 100.0 if interval_hours > 0 else float("nan")
        )
        funding_history = _safe_public_fetch(
            [],
            client.funding_rate_history,
            symbol,
            limit=max(3, FUNDING_BACKTEST_WINDOWS if modeled_funding_enabled else 3),
        )
        last_settled_funding_pct = (
            _funding_rate_to_pct(funding_history[-1].get("fundingRate"))
            if funding_history
            else float("nan")
        )
        prior_settled_funding_pct = (
            _funding_rate_to_pct(funding_history[-2].get("fundingRate"))
            if len(funding_history) >= 2
            else float("nan")
        )
        if modeled_funding_enabled:
            history_window_start_ms = current_window_start_ms
            if funding_history:
                try:
                    history_window_start_ms = min(
                        current_window_start_ms,
                        int(float(funding_history[0].get("fundingTime"))) - interval_hours * 60 * 60 * 1000,
                    )
                except Exception:
                    history_window_start_ms = current_window_start_ms

            premium_lookback_ms = max(5 * 60 * 1000, now_ms - history_window_start_ms)
            premium_interval = _select_premium_kline_interval(premium_lookback_ms)
            premium_limit = max(
                50,
                min(1500, int(math.ceil(premium_lookback_ms / _kline_interval_ms(premium_interval))) + 5),
            )
            premium_klines = _safe_public_fetch(
                [],
                client.premium_index_klines,
                symbol,
                interval=premium_interval,
                limit=premium_limit,
                start_time=history_window_start_ms,
                end_time=now_ms,
            )
            funding_model = _estimate_next_funding_rate(
                funding_snapshot=funding_snapshot,
                interval_hours=interval_hours,
                cap_rate=cap_rate,
                floor_rate=floor_rate,
                funding_history=funding_history,
                premium_klines=premium_klines,
                websocket_snapshot=websocket_snapshots.get(symbol),
            )
            predicted_funding_rate = funding_model["predicted_rate"]
            predicted_funding_pct = _funding_rate_to_pct(predicted_funding_rate)
            predicted_annualized_funding_pct = _annualized_funding_pct(
                predicted_funding_rate,
                interval_hours=interval_hours,
            )
            predicted_low_pct = _funding_rate_to_pct(funding_model["predicted_low_rate"])
            predicted_high_pct = _funding_rate_to_pct(funding_model["predicted_high_rate"])
            predicted_band_pct = _funding_rate_to_pct(funding_model["predicted_band_rate"])
            predicted_mae_pct = _funding_rate_to_pct(funding_model["predicted_mae_rate"])
            funding_window_elapsed_pct = funding_model["window_elapsed_pct"]
            predicted_backtest_count = int(funding_model["backtest_count"])
            latest_premium_pct = _funding_rate_to_pct(funding_model["latest_premium_rate"])
        else:
            predicted_funding_pct = funding_pct
            predicted_annualized_funding_pct = annualized_funding_pct
            predicted_low_pct = float("nan")
            predicted_high_pct = float("nan")
            predicted_band_pct = float("nan")
            predicted_mae_pct = float("nan")
            funding_window_elapsed_pct = window_elapsed_pct
            predicted_backtest_count = 0
            latest_premium_pct = _funding_rate_to_pct(_latest_premium_index_rate(funding_snapshot))
        effective_funding_pct = predicted_funding_pct if not math.isnan(predicted_funding_pct) else funding_pct
        funding_flip_delta_pct = (
            effective_funding_pct - last_settled_funding_pct
            if not math.isnan(effective_funding_pct) and not math.isnan(last_settled_funding_pct)
            else float("nan")
        )
        long_short_rows = _safe_public_fetch(
            [],
            client.global_long_short_account_ratio,
            symbol,
            period=LONG_SHORT_RATIO_PERIOD,
            limit=1,
        )
        long_short_snapshot = long_short_rows[-1] if long_short_rows else {}
        try:
            long_short_account_ratio = float(long_short_snapshot.get("longShortRatio"))
        except Exception:
            long_short_account_ratio = float("nan")
        long_account_pct = _share_to_pct(long_short_snapshot.get("longAccount"))
        short_account_pct = _share_to_pct(long_short_snapshot.get("shortAccount"))
        hourly_stats = _hourly_market_stats(hourly_klines) if compute_crime_detail else _empty_hourly_stats()

        oi_rows = (
            _safe_public_fetch(
                [],
                client.open_interest_statistics,
                symbol,
                period=CRIME_PUMP_PERIOD,
                limit=2,
            )
            if compute_crime_detail
            else []
        )
        latest_oi_row = oi_rows[-1] if oi_rows else {}
        previous_oi_row = oi_rows[-2] if len(oi_rows) >= 2 else {}
        oi_value_usdt = _float_nan(latest_oi_row.get("sumOpenInterestValue"))
        oi_delta_pct = _pct_change(
            latest_oi_row.get("sumOpenInterestValue"),
            previous_oi_row.get("sumOpenInterestValue"),
        )

        taker_rows = (
            _safe_public_fetch(
                [],
                client.taker_buy_sell_volume,
                symbol,
                period=CRIME_PUMP_PERIOD,
                limit=1,
            )
            if compute_crime_detail
            else []
        )
        taker_snapshot = taker_rows[-1] if taker_rows else {}
        taker_buy_sell_ratio = _float_nan(taker_snapshot.get("buySellRatio"))
        taker_buy_vol = _float_nan(taker_snapshot.get("buyVol"))
        taker_sell_vol = _float_nan(taker_snapshot.get("sellVol"))
        taker_total_vol = taker_buy_vol + taker_sell_vol
        if math.isnan(taker_buy_vol) or math.isnan(taker_sell_vol) or taker_total_vol <= 0:
            taker_buy_share_pct = float("nan")
        else:
            taker_buy_share_pct = taker_buy_vol / taker_total_vol * 100.0

        top_position_rows = (
            _safe_public_fetch(
                [],
                client.top_trader_long_short_position_ratio,
                symbol,
                period=CRIME_PUMP_PERIOD,
                limit=1,
            )
            if compute_crime_detail
            else []
        )
        top_position_snapshot = top_position_rows[-1] if top_position_rows else {}
        top_trader_position_ratio = _float_nan(top_position_snapshot.get("longShortRatio"))
        top_trader_long_position_pct = _share_to_pct(top_position_snapshot.get("longAccount"))
        top_trader_short_position_pct = _share_to_pct(top_position_snapshot.get("shortAccount"))

        top_account_rows = (
            _safe_public_fetch(
                [],
                client.top_trader_long_short_account_ratio,
                symbol,
                period=CRIME_PUMP_PERIOD,
                limit=1,
            )
            if compute_crime_detail
            else []
        )
        top_account_snapshot = top_account_rows[-1] if top_account_rows else {}
        top_trader_account_ratio = _float_nan(top_account_snapshot.get("longShortRatio"))
        top_trader_long_account_pct = _share_to_pct(top_account_snapshot.get("longAccount"))
        top_trader_short_account_pct = _share_to_pct(top_account_snapshot.get("shortAccount"))

        if math.isnan(long_account_pct) or math.isnan(top_trader_long_position_pct):
            crowd_top_position_divergence_pct = float("nan")
        else:
            crowd_top_position_divergence_pct = long_account_pct - top_trader_long_position_pct
        if math.isnan(long_account_pct) or math.isnan(top_trader_long_account_pct):
            crowd_top_account_divergence_pct = float("nan")
        else:
            crowd_top_account_divergence_pct = long_account_pct - top_trader_long_account_pct

        if compute_crime_detail:
            try:
                basis_rows = client.basis(
                    symbol,
                    contract_type="PERPETUAL",
                    period=CRIME_PUMP_PERIOD,
                    limit=1,
                )
                basis_snapshot = basis_rows[-1] if basis_rows else {}
                if basis_snapshot:
                    basis_rate_pct = _funding_rate_to_pct(basis_snapshot.get("basisRate"))
                    basis_usdt = _float_nan(basis_snapshot.get("basis"))
                else:
                    basis_rate_pct, basis_usdt = _basis_from_mark_price_snapshot(funding_snapshot)
            except BinanceHTTPError:
                basis_rate_pct, basis_usdt = _basis_from_mark_price_snapshot(funding_snapshot)
            except Exception:
                basis_rate_pct, basis_usdt = _basis_from_mark_price_snapshot(funding_snapshot)
            depth_snapshot = _safe_public_fetch({}, client.depth, symbol, limit=CRIME_DEPTH_LIMIT)
            depth_stats = _depth_stress(depth_snapshot, float(t["quoteVolume"]))
        else:
            basis_rate_pct, basis_usdt = _basis_from_mark_price_snapshot(funding_snapshot)
            depth_stats = {
                "ask_depth_1pct_usdt": float("nan"),
                "ask_depth_to_24h_volume_pct": float("nan"),
            }
        if math.isnan(oi_value_usdt) or float(t["quoteVolume"]) <= 0:
            oi_to_24h_volume_pct = float("nan")
        else:
            oi_to_24h_volume_pct = oi_value_usdt / float(t["quoteVolume"]) * 100.0

        high_24h = float(t["highPrice"])
        low_24h = float(t["lowPrice"])
        last_price = float(t["lastPrice"])
        quote_volume_24h = float(t["quoteVolume"])
        daily_quote_volume_multiple = _daily_quote_volume_multiple(klines, quote_volume_24h)
        upside_to_ath_pct = (
            max(0.0, levels.ath_scanned / last_price - 1.0) * 100.0
            if not math.isnan(levels.ath_scanned) and last_price > 0
            else float("nan")
        )
        distance_to_high_5d_pct = _distance_to_level_pct(levels.high_5d, last_price)
        distance_to_high_20d_pct = _distance_to_level_pct(levels.high_20d, last_price)
        distance_to_high_90d_pct = _distance_to_level_pct(levels.high_90d, last_price)
        rows.append(
            BreakoutRow(
                symbol=symbol,
                base_asset=symbols[symbol],
                market_type=symbol_meta[symbol].underlying_type or "CRYPTO",
                last_price=last_price,
                quote_volume_24h=quote_volume_24h,
                history_days=max(0, len(klines) - 1),
                corr_to_btc_6m=corr_to_btc,
                corr_window_days=corr_window_days,
                high_24h=high_24h,
                low_24h=low_24h,
                carry_funding_pct=funding_pct,
                carry_funding_annualized_pct=annualized_funding_pct,
                long_carry_pct=-funding_pct,
                long_carry_annualized_pct=-annualized_funding_pct,
                funding_interval_hours=interval_hours,
                funding_countdown_hours=_funding_countdown_hours(funding_snapshot.get("nextFundingTime")),
                premium_index_pct=latest_premium_pct,
                predicted_funding_pct=predicted_funding_pct,
                predicted_funding_annualized_pct=predicted_annualized_funding_pct,
                predicted_long_carry_pct=-predicted_funding_pct,
                predicted_long_carry_annualized_pct=-predicted_annualized_funding_pct,
                predicted_funding_low_pct=predicted_low_pct,
                predicted_funding_high_pct=predicted_high_pct,
                predicted_funding_band_pct=predicted_band_pct,
                predicted_funding_backtest_mae_pct=predicted_mae_pct,
                predicted_funding_backtest_count=predicted_backtest_count,
                funding_window_elapsed_pct=funding_window_elapsed_pct,
                last_settled_funding_pct=last_settled_funding_pct,
                prior_settled_funding_pct=prior_settled_funding_pct,
                funding_flip_delta_pct=funding_flip_delta_pct,
                long_short_account_ratio=long_short_account_ratio,
                long_account_pct=long_account_pct,
                short_account_pct=short_account_pct,
                hour_return_pct=hourly_stats["hour_return_pct"],
                hour_return_z=hourly_stats["hour_return_z"],
                day_return_pct=hourly_stats["day_return_pct"],
                daily_quote_volume_multiple=daily_quote_volume_multiple,
                hour_quote_volume=hourly_stats["hour_quote_volume"],
                hour_volume_multiple=hourly_stats["hour_volume_multiple"],
                hour_trade_count_multiple=hourly_stats["hour_trade_count_multiple"],
                hour_upper_wick_pct=hourly_stats["hour_upper_wick_pct"],
                hour_close_location_pct=hourly_stats["hour_close_location_pct"],
                oi_value_usdt=oi_value_usdt,
                oi_delta_pct=oi_delta_pct,
                oi_to_24h_volume_pct=oi_to_24h_volume_pct,
                taker_buy_sell_ratio=taker_buy_sell_ratio,
                taker_buy_share_pct=taker_buy_share_pct,
                top_trader_position_ratio=top_trader_position_ratio,
                top_trader_long_position_pct=top_trader_long_position_pct,
                top_trader_short_position_pct=top_trader_short_position_pct,
                top_trader_account_ratio=top_trader_account_ratio,
                top_trader_long_account_pct=top_trader_long_account_pct,
                top_trader_short_account_pct=top_trader_short_account_pct,
                crowd_top_position_divergence_pct=crowd_top_position_divergence_pct,
                crowd_top_account_divergence_pct=crowd_top_account_divergence_pct,
                basis_rate_pct=basis_rate_pct,
                basis_usdt=basis_usdt,
                ask_depth_1pct_usdt=depth_stats["ask_depth_1pct_usdt"],
                ask_depth_to_24h_volume_pct=depth_stats["ask_depth_to_24h_volume_pct"],
                crime_carry_stress_score=float("nan"),
                crime_pump_score=float("nan"),
                crime_ignition_score=float("nan"),
                crime_exhaustion_score=float("nan"),
                crime_pump_flag=False,
                ignition_setup_flag=False,
                exhaustion_flag=False,
                squeeze_risk_flag=False,
                blowoff_risk_flag=False,
                high_5d=levels.high_5d,
                low_5d=levels.low_5d,
                high_20d=levels.high_20d,
                low_20d=levels.low_20d,
                high_90d=levels.high_90d,
                low_90d=levels.low_90d,
                high_180d=levels.high_180d,
                low_180d=levels.low_180d,
                ath_scanned=levels.ath_scanned,
                upside_to_ath_pct=upside_to_ath_pct,
                distance_to_high_5d_pct=distance_to_high_5d_pct,
                distance_to_high_20d_pct=distance_to_high_20d_pct,
                distance_to_high_90d_pct=distance_to_high_90d_pct,
                broke_high_5d=_crossed_above(levels.high_5d, high_24h),
                broke_low_5d=_crossed_below(levels.low_5d, low_24h),
                broke_high_20d=_crossed_above(levels.high_20d, high_24h),
                broke_low_20d=_crossed_below(levels.low_20d, low_24h),
                broke_high_90d=_crossed_above(levels.high_90d, high_24h),
                broke_high_180d=_crossed_above(levels.high_180d, high_24h),
                broke_low_90d=_crossed_below(levels.low_90d, low_24h),
                broke_low_180d=_crossed_below(levels.low_180d, low_24h),
            )
        )

    if not rows:
        empty_cols = list(BreakoutRow.__annotations__.keys())
        empty_cols.extend(
            [
                "normalized_base_asset",
                "crime_excluded_major",
                "crime_seed_score",
                "convexity_seed_score",
                "price_change_24h_pct",
                "range_24h_pct",
                "crime_microstructure_score",
                "crime_largecap_penalty_score",
                "crime_eligible",
                "crime_mechanics_score",
                *MM_PROXIMITY_COLUMNS,
                *CMC_MOVER_COLUMNS,
                "mm_presence_score",
                "mm_bid_support_score",
                "mm_withdrawal_risk_score",
                *EXTERNAL_CRIME_COLUMNS,
                *LIFECYCLE_SCORE_COLUMNS,
                *CONVEXITY_SCORE_COLUMNS,
                *SHORT_SQUEEZE_SCORE_COLUMNS,
                "trade_bucket",
                "trade_bucket_score",
                "trade_bucket_note",
            ]
        )
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(columns=empty_cols)

    all_df = pd.DataFrame([r.__dict__ for r in rows]).sort_values("symbol")
    ticker_meta = ticker.set_index("symbol")
    all_df["normalized_base_asset"] = all_df["base_asset"].map(normalize_base_asset)
    all_df["crime_excluded_major"] = all_df["normalized_base_asset"].isin(CRIME_EXCLUDED_BASE_ASSETS)
    all_df["crime_seed_score"] = all_df["symbol"].map(ticker_meta["crime_seed_score"]).astype("float64")
    all_df["convexity_seed_score"] = all_df["symbol"].map(ticker_meta["convexity_seed_score"]).astype("float64")
    all_df["price_change_24h_pct"] = all_df["symbol"].map(ticker_meta["priceChangePercent"]).astype("float64")
    all_df["range_24h_pct"] = all_df["symbol"].map(ticker_meta["range_24h_pct"]).astype("float64")
    all_df["dwf_labs_portfolio"] = all_df["symbol"].map(ticker_meta["dwf_labs_portfolio_seed"]).fillna(False).astype(bool)
    all_df["dwf_labs_portfolio_score"] = (
        all_df["symbol"].map(ticker_meta["dwf_labs_portfolio_score_seed"]).fillna(0.0).astype("float64")
    )
    all_df["dwf_labs_portfolio_rank"] = pd.to_numeric(
        all_df["symbol"].map(ticker_meta["dwf_labs_portfolio_rank_seed"]),
        errors="coerce",
    )
    all_df["dwf_labs_portfolio_note"] = (
        all_df["symbol"].map(ticker_meta["dwf_labs_portfolio_note_seed"]).fillna("").astype(str)
    )
    all_df = _apply_mm_proximity_signals(all_df)
    all_df = _apply_dwf_mm_proximity_signal(all_df)
    all_df = _apply_cmc_mover_metrics(all_df, cmc_movers_df)
    all_df = _apply_external_crime_metrics(all_df, external_crime_symbols if deep_scan else set())
    all_df = _apply_ath_runway(all_df)
    all_df = _apply_crime_pump_scores(all_df)
    all_df = apply_lifecycle_model(all_df)
    all_df = apply_short_squeeze_model(all_df)
    all_df = apply_convexity_model(all_df)
    all_df = _score_trade_buckets(all_df)
    highs_df = all_df[(all_df["broke_high_90d"]) | (all_df["broke_high_180d"])].copy()
    highs_df = highs_df.sort_values(
        ["broke_high_180d", "broke_high_90d", "carry_funding_pct", "symbol"],
        ascending=[False, False, False, True],
    )
    return highs_df, all_df


@st.cache_data(ttl=120, show_spinner=False)
def load_pnl_dashboard_cached(api_key_fingerprint: str, refresh_nonce: int) -> PnLDashboardResult:
    _ = (api_key_fingerprint, refresh_nonce)
    client = _client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
    return build_pnl_dashboard_data(
        client=client,
        api_key=BINANCE_API_KEY,
        cache_root=PNL_CACHE_DIR,
        recent_lookback_days=PNL_RECENT_DAYS,
        max_export_year_fetches=PNL_MAX_EXPORT_FETCHES,
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_screener_cached(refresh_nonce: int) -> ScreenerData:
    _ = refresh_nonce
    return build_screener_data(_client())


def render_breakout_dashboard() -> None:
    st.title("Binance USDT Perp Breakout Dashboard")
    st.caption("Single-click scan for 5D/20D/90D/180D highs and lows plus funding/carry, crowding, and crime-pump diagnostics.")
    scan_mode = st.radio(
        "Scan Mode",
        ("Fast", "Deep", "Full ATH"),
        horizontal=True,
        help=(
            "Fast scans the top crypto perps with Binance est funding and breakout data first. "
            "Deep adds heavier crime-pump diagnostics. Full ATH also scans the wider non-major perp universe for 20x+ ATH runway, "
            f"capped at {FULL_ATH_MAX_SYMBOLS_TO_SCAN} symbols and {FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN} external enrichments to avoid hammering public APIs."
        ),
        key="breakout_scan_mode",
    )

    if st.button("Scan now", type="primary", key="scan_breakouts"):
        st.session_state["breakout_refresh_nonce"] = st.session_state.get("breakout_refresh_nonce", 0) + 1
        spinner_label = (
            "Running full ATH runway scan with throttled external enrichment..."
            if scan_mode == "Full ATH"
            else
            "Running deep Binance scan with crime-pump diagnostics..."
            if scan_mode == "Deep"
            else "Running fast Binance scan with breakout and est-funding data..."
        )
        try:
            with st.spinner(spinner_label):
                highs_df, all_df = run_scan(st.session_state["breakout_refresh_nonce"], scan_mode)
        except Exception as exc:
            st.error(
                "Scan failed before results were available. This is usually a transient Binance/CoinGecko/GoPlus "
                "API issue or a column-shape regression; the app caught it instead of leaving the page stuck."
            )
            st.exception(exc)
            return

        breakout_column_config = {
            "trade_bucket": st.column_config.TextColumn(
                "Setup Bucket",
                help="Fast trade-triage label derived from breakout, ignition, crowding, carry, and exhaustion signals.",
            ),
            "trade_bucket_score": st.column_config.NumberColumn(
                "Bucket Score",
                format="%.1f",
                help="Ranking score inside each bucket. Higher is better for Convex Long / Scalp Only; higher means more toxic for Avoid.",
            ),
            "trade_bucket_note": st.column_config.TextColumn(
                "Bucket Note",
                help="Short explanation of why the coin landed in that bucket.",
            ),
            "quote_volume_24h": st.column_config.NumberColumn(
                "24H Quote Vol",
                format="$%.0f",
                help="24-hour quote volume used as a rough size/liquidity proxy.",
            ),
            "crime_microstructure_score": st.column_config.NumberColumn(
                "Crime Micro",
                format="%.1f",
                help="Higher means thinner/smaller tape and therefore more plausible as a true crime-pump candidate.",
            ),
            "crime_largecap_penalty_score": st.column_config.NumberColumn(
                "Large-Cap Penalty",
                format="%.1f",
                help="Higher means the symbol behaves more like a major liquid tape and should rank lower in crime-pump views.",
            ),
            "crime_eligible": st.column_config.CheckboxColumn(
                "Crime Eligible",
                help="Only thinner/smaller names should make the crime tape. Large liquid majors are filtered out here.",
            ),
            "crime_mechanics_score": st.column_config.NumberColumn(
                "Crime Mechanics",
                format="%.1f",
                help="Composite of thin tape, spot venue support, supply concentration proxies, velocity, OI expansion, and taker pressure.",
            ),
            "float_trap_score": st.column_config.NumberColumn(
                "Float Trap",
                format="%.1f",
                help="Controlled-float quality score from holder/supply concentration, FDV gap, holder count, sponsor mismatch, and OTC inventory-risk proxies.",
            ),
            "convexity_float_score": st.column_config.NumberColumn(
                "Convex Float",
                format="%.1f",
                help="Early convexity score for controlled float and bad float quality.",
            ),
            "convexity_sponsor_score": st.column_config.NumberColumn(
                "Convex Sponsor",
                format="%.1f",
                help="How strong the spot sponsorship looks from venue concentration, CEX-over-DEX, venue cluster dominance, and EMFX lanes.",
            ),
            "convexity_preignition_score": st.column_config.NumberColumn(
                "Pre-Ignition",
                format="%.1f",
                help="Entry-quality pressure before the move is fully obvious: daily volume lift, near-breakout structure, constructive returns, and early OI/trade expansion.",
            ),
            "convexity_expansion_score": st.column_config.NumberColumn(
                "Convex Expand",
                format="%.1f",
                help="Whether the tape is beginning to expand through volume, trade count, OI growth, and breakouts without already being too late.",
            ),
            "convexity_squeeze_score": st.column_config.NumberColumn(
                "Convex Squeeze",
                format="%.1f",
                help="Optional squeeze fuel score using crowding, funding flips, and OI structure while preferring cooler carry.",
            ),
            "convexity_runway_score": st.column_config.NumberColumn(
                "Convex Runway",
                format="%.1f",
                help="Remaining asymmetry from scanned ATH runway, listing age, and smaller-cap structure.",
            ),
            "convexity_late_penalty": st.column_config.NumberColumn(
                "Late Penalty",
                format="%.1f",
                help="Penalty for already-late structures: exhaustion, hot funding, upper wicks, blowoff behavior, and unwind risk.",
            ),
            "convexity_entry_score": st.column_config.NumberColumn(
                "Entry Score",
                format="%.1f",
                help="The main entry-quality convexity score. This is the score used to prefer early asymmetric setups over late heat.",
            ),
            "convexity_score": st.column_config.NumberColumn(
                "Convexity",
                format="%.1f",
                help="Early-phase convexity score prioritizing controlled float, sponsored spot, expansion readiness, squeeze optionality, and runway while penalizing late-stage heat.",
            ),
            "pre_pump_candidate_flag": st.column_config.CheckboxColumn(
                "Pre-Pump",
                help="True when the setup has sponsor/float/pre-ignition confluence but is not yet in chase-risk territory.",
            ),
            "early_convexity_flag": st.column_config.CheckboxColumn(
                "Early Convex",
                help="True when the setup looks structurally sponsored and still early enough to matter.",
            ),
            "convexity_prime_flag": st.column_config.CheckboxColumn(
                "Convex Prime",
                help="Best-in-class early convexity candidates.",
            ),
            "convexity_chase_risk_flag": st.column_config.CheckboxColumn(
                "Chase Risk",
                help="True when the same setup is likely too hot for clean early positioning.",
            ),
            "convexity_too_late_flag": st.column_config.CheckboxColumn(
                "Convex Too Late",
                help="Names whose convexity profile is already too stretched or too late.",
            ),
            "convexity_summary": st.column_config.TextColumn("Convex Summary"),
            "convexity_top_factors": st.column_config.TextColumn("Convex Factors"),
            "convexity_offsets": st.column_config.TextColumn("Convex Offsets"),
            "convexity_seed_score": st.column_config.NumberColumn("Convex Seed", format="%.1f"),
            "trend_confluence_score": st.column_config.NumberColumn(
                "Trend Conf.",
                format="%.1f",
                help="Breakout-stack, near-breakout, daily volume lift, hourly volume, and close-quality confluence.",
            ),
            "spot_flow_confluence_score": st.column_config.NumberColumn(
                "Spot Flow Conf.",
                format="%.1f",
                help="CEX/DEX skew, Binance/Bitget/Gate dominance, EMFX/KRW/TRY lanes, spot volume versus market cap, and venue concentration.",
            ),
            "perp_squeeze_confluence_score": st.column_config.NumberColumn(
                "Perp Sqz Conf.",
                format="%.1f",
                help="Funding-flip, short crowding, OI growth, OI versus market cap, taker buying, and cool carry confluence.",
            ),
            "float_control_confluence_score": st.column_config.NumberColumn(
                "Float Ctrl Conf.",
                format="%.1f",
                help="Controlled-float confluence from holder concentration, unreleased supply, FDV/MCap, low holders, and inventory-risk proxies.",
            ),
            "mm_sponsor_confluence_score": st.column_config.NumberColumn(
                "MM Sponsor Conf.",
                format="%.1f",
                help="Coinbase depth/spread/bid support plus manual MM-proximity hints. This is a liquidity-sponsor proxy, not proof of a specific desk.",
            ),
            "ath_runway_confluence_score": st.column_config.NumberColumn(
                "ATH Runway Conf.",
                format="%.1f",
                help="ATH multiple/runway confluence, preferring large remaining upside while avoiding mega-cap stabilization.",
            ),
            "convexity_confluence_score": st.column_config.NumberColumn(
                "Mechanics Conf.",
                format="%.1f",
                help="Weighted confluence score across trend, spot flow, perp squeeze, float control, MM/sponsor, and ATH runway mechanics.",
            ),
            "convexity_confluence_count": st.column_config.NumberColumn(
                "Conf. Count",
                format="%d",
                help="How many independent mechanics are active: trend, spot flow, perp squeeze, float control, MM/sponsor, ATH runway.",
            ),
            "valuation_trap_score": st.column_config.NumberColumn(
                "Valuation Trap",
                format="%.1f",
                help="Scores the 'obviously overvalued but tiny real float' setup using FDV/MCap, locked supply, holder concentration, and volume versus market cap.",
            ),
            "short_liquidation_fuel_score": st.column_config.NumberColumn(
                "Short Fuel",
                format="%.1f",
                help="Scores forced-buy fuel from short-account skew, funding flip, short-crowding, OI/MCap, OI growth, and perp pressure.",
            ),
            "spot_control_score": st.column_config.NumberColumn(
                "Spot Control",
                format="%.1f",
                help="Scores CEX/spot sponsorship and possible liquidity-control proxies from venue support, CEX/DEX skew, MM presence, venue concentration, and bid support.",
            ),
            "squeeze_machine_score": st.column_config.NumberColumn(
                "Squeeze Machine",
                format="%.1f",
                help="Lifecycle score for controlled float + CEX spot support + perp short fuel + early upward pressure, penalized for late/distribution risk.",
            ),
            "trend_confluence_flag": st.column_config.CheckboxColumn("Trend Conf."),
            "spot_flow_confluence_flag": st.column_config.CheckboxColumn("Spot Flow Conf."),
            "perp_squeeze_confluence_flag": st.column_config.CheckboxColumn("Perp Sqz Conf."),
            "float_control_confluence_flag": st.column_config.CheckboxColumn("Float Ctrl Conf."),
            "mm_sponsor_confluence_flag": st.column_config.CheckboxColumn("MM Sponsor Conf."),
            "ath_runway_confluence_flag": st.column_config.CheckboxColumn("ATH Runway Conf."),
            "squeeze_machine_flag": st.column_config.CheckboxColumn("Sqz Machine"),
            "convexity_confluence_note": st.column_config.TextColumn(
                "Confluence Note",
                help="Plain-English summary of which mechanics are lining up.",
            ),
            "daily_quote_volume_multiple": st.column_config.NumberColumn("Daily Vol x", format="%.2f"),
            "distance_to_high_5d_pct": st.column_config.NumberColumn("Dist 5D High", format="%.2f%%"),
            "distance_to_high_20d_pct": st.column_config.NumberColumn("Dist 20D High", format="%.2f%%"),
            "distance_to_high_90d_pct": st.column_config.NumberColumn("Dist 90D High", format="%.2f%%"),
            "ignition_score_v2": st.column_config.NumberColumn(
                "Ignition v2",
                format="%.1f",
                help="Lifecycle launch score from breakouts, return z-score, volume/trade-count expansion, taker pressure, OI expansion, and spot/MM support.",
            ),
            "perp_pressure_score": st.column_config.NumberColumn(
                "Perp Pressure",
                format="%.1f",
                help="Derivative crowding score from OI, funding, premium/basis, long-short crowding, and perp volume versus market cap.",
            ),
            "venue_support_score": st.column_config.NumberColumn(
                "Venue Support",
                format="%.1f",
                help="Spot/MM support score using Coinbase depth/spread/volume, venue breadth/concentration, Upbit/KRW, Kraken, and MM pull-risk as an offset.",
            ),
            "exit_fragility_score": st.column_config.NumberColumn(
                "Exit Fragility",
                format="%.1f",
                help="How violently the move can fail if support fades: MM pull risk, upper wick, weak close, thin ask depth, sponsor mismatch, OTC inventory risk, and exhaustion.",
            ),
            "crime_pump_score_v2": st.column_config.NumberColumn(
                "Crime Pump Score v2",
                format="%.1f",
                help="Lifecycle score: Float Trap 27%, Ignition 23%, Perp Pressure 22%, Venue Support 16%, Exit Fragility 12%, minus Large-Cap Stabilizer 20%.",
            ),
            "funding_flip_up_flag": st.column_config.CheckboxColumn(
                "Funding Flipped",
                help="True when the most recent settled funding was negative and the live/model funding has flipped positive by a meaningful amount.",
            ),
            "fresh_flip_flag": st.column_config.CheckboxColumn("Fresh Flip"),
            "active_short_squeeze_flag": st.column_config.CheckboxColumn("Active Short Sqz"),
            "squeeze_chase_flag": st.column_config.CheckboxColumn("Short Sqz Chase"),
            "funding_flip_score": st.column_config.NumberColumn(
                "Funding Flip",
                format="%.1f",
                help="Scores how cleanly funding swung from negative to positive using the last settled funding, current estimated/model funding, premium, and basis.",
            ),
            "short_crowding_score": st.column_config.NumberColumn(
                "Short Crowding",
                format="%.1f",
                help="Scores whether there was still real short fuel in the tape using account ratios, short share, and OI crowding.",
            ),
            "breakout_pressure_score": st.column_config.NumberColumn(
                "Breakout Pressure",
                format="%.1f",
                help="Confirms the squeeze with breakout stack, returns, volume/trade expansion, taker aggression, and close quality.",
            ),
            "runway_score": st.column_config.NumberColumn(
                "ATH Runway",
                format="%.1f",
                help="Scores remaining upside to the max scanned daily high, plus listing age and size stabilization.",
            ),
            "short_squeeze_score": st.column_config.NumberColumn(
                "Short Squeeze",
                format="%.1f",
                help="Funding-flip / short-squeeze composite: funding flip, short crowding, breakout pressure, ATH runway, minus chase penalty.",
            ),
            "last_settled_funding_pct": st.column_config.NumberColumn(
                "Prev Funding",
                format="%.4f%%",
                help="Most recent settled Binance funding rate before the current live estimate/model.",
            ),
            "prior_settled_funding_pct": st.column_config.NumberColumn(
                "Prior Funding",
                format="%.4f%%",
                help="Funding rate from one settlement before Prev Funding.",
            ),
            "funding_flip_delta_pct": st.column_config.NumberColumn(
                "Flip Delta",
                format="%.4f%%",
                help="Current effective funding minus the most recent settled funding. Positive means the tape is repricing toward longs paying shorts.",
            ),
            "ath_scanned": st.column_config.NumberColumn(
                "Scanned ATH",
                format="%.6f",
                help="Maximum scanned daily high from the loaded Binance history window. This is a scanned-history proxy, not guaranteed full lifetime ATH.",
            ),
            "coingecko_ath_usd": st.column_config.NumberColumn(
                "CG ATH",
                format="$%.6f",
                help="CoinGecko lifetime ATH when the asset can be matched and enriched in Deep/Full ATH mode.",
            ),
            "coingecko_ath_change_pct": st.column_config.NumberColumn(
                "CG ATH Drawdown",
                format="%.1f%%",
                help="CoinGecko reported percent change from ATH. Negative means price is below ATH.",
            ),
            "coingecko_ath_date": st.column_config.TextColumn("CG ATH Date"),
            "ath_price": st.column_config.NumberColumn(
                "ATH Price",
                format="%.6f",
                help="Best available ATH price: CoinGecko lifetime ATH when present, otherwise Binance scanned-history ATH.",
            ),
            "ath_multiple": st.column_config.NumberColumn(
                "X to ATH",
                format="%.2fx",
                help="ATH Price divided by current last price. 20x means the coin is at least 20x below its ATH.",
            ),
            "ath_upside_pct": st.column_config.NumberColumn(
                "ATH Upside",
                format="%.1f%%",
                help="Remaining upside from last price to the best available ATH.",
            ),
            "ath_source": st.column_config.TextColumn(
                "ATH Source",
                help="CoinGecko when lifetime ATH is available; otherwise Binance scanned daily history.",
            ),
            "ath_runway_20x_flag": st.column_config.CheckboxColumn(
                "20x From ATH",
                help="True when current price is at least 20x below the best available ATH.",
            ),
            "upside_to_ath_pct": st.column_config.NumberColumn(
                "Upside to ATH",
                format="%.1f%%",
                help="Remaining upside from last price to the scanned ATH proxy.",
            ),
            "breakout_stack_count": st.column_config.NumberColumn(
                "Breakout Stack",
                format="%d",
                help="How many of the 5D, 20D, 90D, and 180D highs broke in the latest move.",
            ),
            "short_squeeze_summary": st.column_config.TextColumn("Squeeze Summary"),
            "short_squeeze_top_factors": st.column_config.TextColumn("Squeeze Factors"),
            "short_squeeze_offsets": st.column_config.TextColumn("Squeeze Offsets"),
            "cmc_mover_score": st.column_config.NumberColumn(
                "CMC Mover",
                format="%.1f",
                help="Optional CoinMarketCap top-mover signal from 1H/24H rank, velocity, and volume-to-market-cap. Requires COINMARKETCAP_API_KEY or CMC_API_KEY.",
            ),
            "cmc_mover_label": st.column_config.TextColumn(
                "CMC Label",
                help="Why CMC ranked it: 1H rank, 24H rank, and extreme CMC volume-to-market-cap notes.",
            ),
            "cmc_rank_1h": st.column_config.NumberColumn("CMC 1H Rank", format="%.0f"),
            "cmc_rank_24h": st.column_config.NumberColumn("CMC 24H Rank", format="%.0f"),
            "cmc_pct_1h": st.column_config.NumberColumn("CMC 1H %", format="%.2f%%"),
            "cmc_pct_24h": st.column_config.NumberColumn("CMC 24H %", format="%.2f%%"),
            "cmc_market_cap_usd": st.column_config.NumberColumn("CMC Mkt Cap", format="$%.0f"),
            "cmc_volume_24h": st.column_config.NumberColumn("CMC 24H Vol", format="$%.0f"),
            "cmc_volume_to_mcap_pct": st.column_config.NumberColumn("CMC Vol/MCap", format="%.1f%%"),
            "cmc_name": st.column_config.TextColumn("CMC Name"),
            "setup_ready_flag": st.column_config.CheckboxColumn("Setup Ready"),
            "active_squeeze_flag": st.column_config.CheckboxColumn("Active Squeeze"),
            "blowoff_watch_flag": st.column_config.CheckboxColumn("Blowoff Watch"),
            "unwind_risk_flag": st.column_config.CheckboxColumn("Unwind Risk"),
            "coinbase_lane_flag": st.column_config.CheckboxColumn("Coinbase Lane"),
            "owner_controlled_flag": st.column_config.CheckboxColumn("Owner Controlled"),
            "perp_heavy_flag": st.column_config.CheckboxColumn("Perp Heavy"),
            "why_flagged_summary": st.column_config.TextColumn("Why Summary"),
            "why_flagged_top_factors": st.column_config.TextColumn("Top Factors"),
            "why_flagged_offsets": st.column_config.TextColumn("Offsets"),
            "crime_spot_impulse_score": st.column_config.NumberColumn(
                "Spot Impulse",
                format="%.1f",
                help="Scores external spot support, including Coinbase/Binance spot volume relative to Binance perp volume.",
            ),
            "crime_supply_control_score": st.column_config.NumberColumn(
                "Supply Control",
                format="%.1f",
                help="Proxy for float/holder concentration using CoinGecko supply data and GoPlus holder concentration when available.",
            ),
            "crime_coinbase_lane_score": st.column_config.NumberColumn(
                "Coinbase Lane",
                format="%.1f",
                help="Scores Coinbase-listed spot support, Coinbase volume share, and Coinbase spot volume versus Binance perp volume.",
            ),
            "crime_owner_circle_score": st.column_config.NumberColumn(
                "Owner-Circle",
                format="%.1f",
                help="Scores holder concentration, unreleased supply/FDV gap, low holder count, and venue concentration proxies.",
            ),
            "mm_presence_score": st.column_config.NumberColumn(
                "MM Presence",
                format="%.1f",
                help="Coinbase spot liquidity-sponsor proxy: tight spread, 2% order-book depth, balanced quoting, and Coinbase spot share.",
            ),
            "mm_bid_support_score": st.column_config.NumberColumn(
                "CB Bid Support",
                format="%.1f",
                help="Scores whether Coinbase spot bids are unusually supportive versus asks and volume. Useful for sponsored squeeze mechanics.",
            ),
            "mm_withdrawal_risk_score": st.column_config.NumberColumn(
                "MM Pull Risk",
                format="%.1f",
                help="Higher means the Coinbase liquidity sponsor looks weak, one-sided, or easier to pull while owner/venue concentration risk is high.",
            ),
            "mm_proximity_score": st.column_config.NumberColumn(
                "MM Proximity",
                format="%.1f",
                help="Manual social-graph / relationship signal. Use for founder/MM proximity, venue sponsorship, advisor links, or credible desk breadcrumbs.",
            ),
            "dwf_labs_portfolio": st.column_config.CheckboxColumn(
                "DWF Labs",
                help="True when CoinGecko currently lists the asset in the DWF Labs portfolio category.",
            ),
            "dwf_labs_portfolio_score": st.column_config.NumberColumn(
                "DWF Score",
                format="%.1f",
                help="Sponsor-proximity score from CoinGecko's DWF Labs portfolio category. Confluence, not proof of misconduct.",
            ),
            "dwf_labs_portfolio_rank": st.column_config.NumberColumn("DWF Rank", format="%.0f"),
            "dwf_labs_portfolio_note": st.column_config.TextColumn("DWF Note"),
            "mm_proximity_maker": st.column_config.TextColumn(
                "MM Hint",
                help="Market maker, venue, desk, or relationship label behind the manual proximity signal.",
            ),
            "mm_proximity_note": st.column_config.TextColumn(
                "MM Note",
                help="Human-readable note for the manual proximity signal. Treat as confluence, not proof.",
            ),
            "mm_proximity_source": st.column_config.TextColumn(
                "MM Source",
                help="Optional URL for the social graph / market maker relationship breadcrumb.",
            ),
            "inventory_transfer_risk_score": st.column_config.NumberColumn(
                "OTC Inv Risk",
                format="%.1f",
                help="Proxy for off-exchange inventory transfer risk: MM proximity, controlled float, venue concentration, volume-to-market-cap pressure, and visible-depth mismatch.",
            ),
            "inventory_sponsor_mismatch_score": st.column_config.NumberColumn(
                "Sponsor Mismatch",
                format="%.1f",
                help="Higher when spot/perp volume is huge versus market cap but public Coinbase depth looks small or concentrated.",
            ),
            "inventory_transfer_risk_flag": st.column_config.CheckboxColumn(
                "OTC Inv Flag",
                help="True when the off-exchange inventory-transfer fingerprint crosses the configured composite threshold.",
            ),
            "inventory_transfer_note": st.column_config.TextColumn(
                "OTC Inv Note",
                help="Short explanation of the inventory-transfer risk fingerprint. It is not proof of off-exchange token swaps.",
            ),
            "spot_volume_to_mcap_pct": st.column_config.NumberColumn(
                "Spot Vol/MCap",
                format="%.1f%%",
                help="External spot volume divided by CoinGecko market cap. Very high values can indicate aggressive inventory rotation.",
            ),
            "perp_volume_to_mcap_pct": st.column_config.NumberColumn(
                "Perp Vol/MCap",
                format="%.1f%%",
                help="Binance futures 24h volume divided by CoinGecko market cap.",
            ),
            "oi_to_market_cap_pct": st.column_config.NumberColumn(
                "OI/MCap",
                format="%.1f%%",
                help="Binance futures open interest notional divided by CoinGecko market cap.",
            ),
            "venue_concentration_score": st.column_config.NumberColumn(
                "Venue Conc.",
                format="%.1f",
                help="Higher when volume is dominated by one or a few venues.",
            ),
            "crime_excluded_major": st.column_config.CheckboxColumn(
                "Major Excluded",
                help="True when the base asset is in the configured major-exclusion list for crime-pump scans.",
            ),
            "crime_seed_score": st.column_config.NumberColumn(
                "Crime Seed",
                format="%.1f",
                help="Pre-scan ranking score used to choose which non-major symbols receive the expensive crime diagnostics.",
            ),
            "spot_external_quote_volume_24h": st.column_config.NumberColumn(
                "Ext Spot Vol",
                format="$%.0f",
                help="Coinbase plus Binance spot quote volume where public endpoints have the pair.",
            ),
            "coinbase_spot_quote_volume_24h": st.column_config.NumberColumn(
                "Coinbase Spot Vol",
                format="$%.0f",
                help="Coinbase public 24h spot quote volume across USD/USDC/USDT pairs when listed.",
            ),
            "coingecko_total_volume_24h": st.column_config.NumberColumn("CG Total Vol", format="$%.0f"),
            "coingecko_cex_volume_24h": st.column_config.NumberColumn(
                "CEX Vol",
                format="$%.0f",
                help="CoinGecko ticker volume classified as centralized-exchange volume.",
            ),
            "coingecko_dex_volume_24h": st.column_config.NumberColumn("DEX Vol", format="$%.0f"),
            "kraken_spot_quote_volume_24h": st.column_config.NumberColumn("Kraken Vol", format="$%.0f"),
            "upbit_spot_quote_volume_24h": st.column_config.NumberColumn("Upbit Vol", format="$%.0f"),
            "upbit_krw_quote_volume_24h": st.column_config.NumberColumn("Upbit KRW Vol", format="$%.0f"),
            "try_spot_quote_volume_24h": st.column_config.NumberColumn("TRY Vol", format="$%.0f"),
            "emfx_spot_quote_volume_24h": st.column_config.NumberColumn(
                "EMFX Vol",
                format="$%.0f",
                help="CoinGecko ticker volume quoted in EMFX lanes such as KRW and TRY.",
            ),
            "coinbase_volume_share_pct": st.column_config.NumberColumn("CB Vol Share", format="%.1f%%"),
            "binance_volume_share_pct": st.column_config.NumberColumn("Binance Share", format="%.1f%%"),
            "bitget_volume_share_pct": st.column_config.NumberColumn("Bitget Share", format="%.1f%%"),
            "gate_volume_share_pct": st.column_config.NumberColumn("Gate Share", format="%.1f%%"),
            "cex_volume_share_pct": st.column_config.NumberColumn(
                "CEX Share",
                format="%.1f%%",
                help="CEX volume divided by CEX plus DEX volume. High values mean spot activity is mostly centralized venues.",
            ),
            "cex_to_dex_volume_ratio": st.column_config.NumberColumn(
                "CEX/DEX",
                format="%.2f",
                help="CEX volume divided by DEX volume. Very high ratios can indicate venue-supported flow rather than organic on-chain liquidity.",
            ),
            "cex_dex_volume_ratio_score": st.column_config.NumberColumn(
                "CEX/DEX Score",
                format="%.1f",
                help="Log-scaled CEX/DEX dominance score. It is a pump-risk / squeeze-risk anomaly signal, not proof of misconduct.",
            ),
            "kraken_volume_share_pct": st.column_config.NumberColumn("Kraken Share", format="%.1f%%"),
            "upbit_volume_share_pct": st.column_config.NumberColumn("Upbit Share", format="%.1f%%"),
            "krw_volume_share_pct": st.column_config.NumberColumn("KRW Share", format="%.1f%%"),
            "try_volume_share_pct": st.column_config.NumberColumn("TRY Share", format="%.1f%%"),
            "emfx_volume_share_pct": st.column_config.NumberColumn(
                "EMFX Share",
                format="%.1f%%",
                help="Share of spot volume quoted in EMFX lanes such as KRW and TRY. Useful for spotting non-USD lane sponsorship.",
            ),
            "dex_volume_share_pct": st.column_config.NumberColumn("DEX Vol Share", format="%.1f%%"),
            "binance_bitget_gate_share_pct": st.column_config.NumberColumn(
                "B/B/G Share",
                format="%.1f%%",
                help="Combined CoinGecko spot-volume share from Binance, Bitget, and Gate.io. High values mean a small venue cluster owns the tape.",
            ),
            "binance_bitget_gate_share_score": st.column_config.NumberColumn(
                "B/B/G Score",
                format="%.1f",
                help="Scaled version of the Binance+Bitget+Gate share, used as a venue-cluster anomaly signal.",
            ),
            "top_venue": st.column_config.TextColumn("Top Venue"),
            "top_venue_volume_share_pct": st.column_config.NumberColumn("Top Venue %", format="%.1f%%"),
            "top3_venue_volume_share_pct": st.column_config.NumberColumn("Top3 Venue %", format="%.1f%%"),
            "venue_hhi": st.column_config.NumberColumn(
                "Venue HHI",
                format="%.0f",
                help="Herfindahl-style venue concentration index computed from CoinGecko ticker shares. Higher means fewer venues dominate the flow.",
            ),
            "venue_hhi_score": st.column_config.NumberColumn(
                "Venue HHI Score",
                format="%.1f",
                help="Scaled venue concentration score derived from the spot-volume HHI.",
            ),
            "venue_count": st.column_config.NumberColumn("Venues", format="%d"),
            "emfx_lane_score": st.column_config.NumberColumn(
                "EMFX Lane",
                format="%.1f",
                help="Composite KRW/TRY/EMFX quote-lane score. High values mean the move is being carried in non-USD quote lanes.",
            ),
            "coinbase_bid_ask_spread_pct": st.column_config.NumberColumn("CB Spread", format="%.2f%%"),
            "coinbase_bid_depth_2pct_usd": st.column_config.NumberColumn("CB Bid Depth 2%", format="$%.0f"),
            "coinbase_ask_depth_2pct_usd": st.column_config.NumberColumn("CB Ask Depth 2%", format="$%.0f"),
            "coinbase_total_depth_2pct_usd": st.column_config.NumberColumn(
                "CB Depth 2%",
                format="$%.0f",
                help="Live Coinbase Exchange level-2 bid plus ask notional within 2% of mid across USD/USDC/USDT products.",
            ),
            "coinbase_book_imbalance_pct": st.column_config.NumberColumn(
                "CB Book Bid %",
                format="%.1f%%",
                help="Coinbase 2% book imbalance: bid depth divided by total bid+ask depth. Above 50% means bid-heavy.",
            ),
            "coinbase_depth_to_volume_pct": st.column_config.NumberColumn(
                "CB Depth/CB Vol",
                format="%.2f%%",
                help="Coinbase 2% depth divided by Coinbase 24h spot volume. Higher suggests stickier sponsor liquidity.",
            ),
            "coinbase_depth_to_perp_volume_pct": st.column_config.NumberColumn(
                "CB Depth/Perp Vol",
                format="%.2f%%",
                help="Coinbase 2% spot depth divided by Binance futures 24h quote volume.",
            ),
            "binance_spot_quote_volume_24h": st.column_config.NumberColumn(
                "Binance Spot Vol",
                format="$%.0f",
                help="Binance public spot 24h quote volume for the normalized base asset.",
            ),
            "spot_to_perp_volume_pct": st.column_config.NumberColumn(
                "Spot/Perp Vol",
                format="%.1f%%",
                help="External spot volume divided by Binance futures 24h quote volume. Higher can indicate real spot sponsorship.",
            ),
            "coinbase_to_perp_volume_pct": st.column_config.NumberColumn(
                "CB/Perp Vol",
                format="%.1f%%",
                help="Coinbase spot volume divided by Binance futures 24h quote volume.",
            ),
            "coinbase_spot_listed": st.column_config.CheckboxColumn(
                "Coinbase Spot",
                help="Whether Coinbase Exchange has an online USD/USDC/USDT spot market for this base.",
            ),
            "market_cap_usd": st.column_config.NumberColumn("Mkt Cap", format="$%.0f"),
            "fdv_usd": st.column_config.NumberColumn("FDV", format="$%.0f"),
            "fdv_to_market_cap": st.column_config.NumberColumn("FDV/MCap", format="%.2f"),
            "circulating_supply_pct": st.column_config.NumberColumn("Circ Supply", format="%.1f%%"),
            "locked_supply_pct": st.column_config.NumberColumn(
                "Locked/Unrel.",
                format="%.1f%%",
                help="100 minus circulating supply share using CoinGecko total/max supply. This is a float proxy, not exact insider ownership.",
            ),
            "top10_holder_pct": st.column_config.NumberColumn(
                "Top10 Holders",
                format="%.1f%%",
                help="GoPlus top-10 holder concentration when an EVM contract is available.",
            ),
            "owner_holder_pct": st.column_config.NumberColumn("Owner Hold", format="%.1f%%"),
            "creator_holder_pct": st.column_config.NumberColumn("Creator Hold", format="%.1f%%"),
            "holder_count": st.column_config.NumberColumn("Holders", format="%.0f"),
            "market_type": st.column_config.TextColumn("Market Type"),
            "history_days": st.column_config.NumberColumn("History (D)", format="%d"),
            "corr_window_days": st.column_config.NumberColumn("Corr Window (D)", format="%d"),
            "corr_to_btc_6m": st.column_config.NumberColumn("Corr to BTC (Max 180D)", format="%.3f"),
            "funding_interval_hours": st.column_config.NumberColumn("Funding Int. (H)", format="%d"),
            "funding_countdown_hours": st.column_config.NumberColumn(
                "Next Funding In",
                format="%.2f h",
                help="Hours remaining until the next Binance funding settlement.",
            ),
            "carry_funding_pct": st.column_config.NumberColumn(
                "Est. Funding",
                format="%.4f%%",
                help="Binance's live estimated funding rate from the mark-price snapshot. Positive means longs pay shorts; negative means shorts pay longs.",
            ),
            "carry_funding_annualized_pct": st.column_config.NumberColumn(
                "Est. Funding Ann.",
                format="%.2f%%",
                help="Binance's live estimated funding rate annualized using the symbol's current settlement interval.",
            ),
            "long_carry_pct": st.column_config.NumberColumn(
                "Long Carry (Est.)",
                format="%.4f%%",
                help="Binance estimated funding from the long-position perspective. Positive means a long would receive funding; negative means a long would pay funding.",
            ),
            "long_carry_annualized_pct": st.column_config.NumberColumn(
                "Long Carry Ann. (Est.)",
                format="%.2f%%",
                help="Binance estimated long carry annualized using the symbol's current settlement interval.",
            ),
            "premium_index_pct": st.column_config.NumberColumn(
                "Premium Idx",
                format="%.4f%%",
                help="Latest premium snapshot, refined by websocket when available.",
            ),
            "predicted_funding_pct": st.column_config.NumberColumn(
                "Modeled Funding",
                format="%.4f%%",
                help="Our custom modeled funding estimate using the current premium window, a short websocket refinement, and recent settled funding backtests. This is not Binance's official estimate.",
            ),
            "predicted_funding_annualized_pct": st.column_config.NumberColumn(
                "Modeled Funding Ann.",
                format="%.2f%%",
                help="Our custom modeled funding estimate annualized using the symbol's current settlement interval.",
            ),
            "predicted_long_carry_pct": st.column_config.NumberColumn(
                "Modeled Long Carry",
                format="%.4f%%",
                help="Modeled next funding from the long-position perspective. Positive means a long would receive; negative means a long would pay.",
            ),
            "predicted_long_carry_annualized_pct": st.column_config.NumberColumn(
                "Modeled Long Carry Ann.",
                format="%.2f%%",
                help="Predicted long carry annualized using the symbol's current settlement interval.",
            ),
            "predicted_funding_low_pct": st.column_config.NumberColumn(
                "Model Low",
                format="%.4f%%",
                help="Lower bound of the predicted funding band using recent model error.",
            ),
            "predicted_funding_high_pct": st.column_config.NumberColumn(
                "Model High",
                format="%.4f%%",
                help="Upper bound of the predicted funding band using recent model error.",
            ),
            "predicted_funding_band_pct": st.column_config.NumberColumn(
                "Model Band +/-",
                format="%.4f%%",
                help="Radius of the displayed funding band based on recent out-of-sample model error.",
            ),
            "predicted_funding_backtest_mae_pct": st.column_config.NumberColumn(
                "Model MAE",
                format="%.4f%%",
                help="Mean absolute error of recent funding backtests for this symbol.",
            ),
            "predicted_funding_backtest_count": st.column_config.NumberColumn(
                "Model N",
                format="%d",
                help="Number of settled funding windows used for the recent backtest calibration.",
            ),
            "funding_window_elapsed_pct": st.column_config.NumberColumn(
                "Window Elapsed",
                format="%.0f%%",
                help="How much of the current funding window has elapsed. Higher values usually make the estimate more informative.",
            ),
            "long_short_account_ratio": st.column_config.NumberColumn(
                "L/S Acct Ratio",
                format="%.2f",
                help="Binance global long/short account ratio for this symbol. This is account-count based, not position-size based.",
            ),
            "long_account_pct": st.column_config.NumberColumn(
                "Long Accts",
                format="%.1f%%",
                help="Share of Binance accounts net long this symbol for the selected long/short ratio period.",
            ),
            "short_account_pct": st.column_config.NumberColumn(
                "Short Accts",
                format="%.1f%%",
                help="Share of Binance accounts net short this symbol for the selected long/short ratio period.",
            ),
            "hour_return_pct": st.column_config.NumberColumn("1H Return", format="%.2f%%"),
            "hour_return_z": st.column_config.NumberColumn(
                "1H Return Z",
                format="%.2f",
                help="Latest closed 1-hour return normalized by the recent hourly return distribution.",
            ),
            "day_return_pct": st.column_config.NumberColumn("24H Return", format="%.2f%%"),
            "hour_quote_volume": st.column_config.NumberColumn("1H Quote Vol", format="$%.0f"),
            "hour_volume_multiple": st.column_config.NumberColumn(
                "1H Vol x",
                format="%.2fx",
                help="Latest closed 1-hour quote volume versus the prior 24-hour average hourly quote volume.",
            ),
            "hour_trade_count_multiple": st.column_config.NumberColumn(
                "1H Trades x",
                format="%.2fx",
                help="Latest closed 1-hour trade count versus the prior 24-hour average hourly trade count.",
            ),
            "hour_upper_wick_pct": st.column_config.NumberColumn(
                "Upper Wick",
                format="%.1f%%",
                help="Upper-wick size as a share of the latest closed hourly candle range. Higher often means blowoff/exhaustion.",
            ),
            "hour_close_location_pct": st.column_config.NumberColumn(
                "Close in Range",
                format="%.1f%%",
                help="Where the latest closed hourly candle finished inside its range. Near 100% means it closed near the high.",
            ),
            "oi_value_usdt": st.column_config.NumberColumn("OI Value", format="$%.0f"),
            "oi_delta_pct": st.column_config.NumberColumn(
                "OI Delta",
                format="%.2f%%",
                help="Change in open-interest notional value over the crime-pump period.",
            ),
            "oi_to_24h_volume_pct": st.column_config.NumberColumn(
                "OI / 24H Vol",
                format="%.2f%%",
                help="Open-interest notional divided by 24-hour quote volume.",
            ),
            "taker_buy_sell_ratio": st.column_config.NumberColumn(
                "Taker B/S",
                format="%.2f",
                help="Aggressive buy volume divided by aggressive sell volume over the crime-pump period.",
            ),
            "taker_buy_share_pct": st.column_config.NumberColumn("Taker Buy %", format="%.1f%%"),
            "top_trader_position_ratio": st.column_config.NumberColumn(
                "Top Pos L/S",
                format="%.2f",
                help="Top-trader long/short ratio based on positions.",
            ),
            "top_trader_long_position_pct": st.column_config.NumberColumn("Top Pos Long %", format="%.1f%%"),
            "top_trader_account_ratio": st.column_config.NumberColumn(
                "Top Acct L/S",
                format="%.2f",
                help="Top-trader long/short ratio based on accounts.",
            ),
            "top_trader_long_account_pct": st.column_config.NumberColumn("Top Acct Long %", format="%.1f%%"),
            "crowd_top_position_divergence_pct": st.column_config.NumberColumn(
                "Crowd-Top Div",
                format="%.1f pp",
                help="Global long-account share minus top-trader long-position share. Positive means crowd is more long than top traders.",
            ),
            "crowd_top_account_divergence_pct": st.column_config.NumberColumn(
                "Crowd-Top Acct Div",
                format="%.1f pp",
                help="Global long-account share minus top-trader long-account share.",
            ),
            "basis_rate_pct": st.column_config.NumberColumn(
                "Basis Rate",
                format="%.3f%%",
                help="Perpetual basis rate over the selected crime-pump period.",
            ),
            "basis_usdt": st.column_config.NumberColumn("Basis", format="%.4f"),
            "ask_depth_1pct_usdt": st.column_config.NumberColumn(
                "Ask Depth 1%",
                format="$%.0f",
                help="Visible ask-side notional within 1% above the mid price.",
            ),
            "ask_depth_to_24h_volume_pct": st.column_config.NumberColumn(
                "Ask Depth / 24H Vol",
                format="%.2f%%",
                help="Visible ask-side 1% depth divided by 24-hour quote volume. Lower means a thinner book.",
            ),
            "crime_carry_stress_score": st.column_config.NumberColumn("Carry Stress", format="%.1f"),
            "crime_pump_score": st.column_config.NumberColumn(
                "Crime Pump Score",
                format="%.1f",
                help="Composite stress score using 1H velocity, volume spike, OI expansion, carry/basis stress, taker aggression, crowd divergence, and book thinness.",
            ),
            "crime_ignition_score": st.column_config.NumberColumn(
                "Ignition Score",
                format="%.1f",
                help="Earlier-stage squeeze/ignition score emphasizing momentum, OI expansion, taker aggression, trade-count expansion, and close-near-high behavior.",
            ),
            "crime_exhaustion_score": st.column_config.NumberColumn(
                "Exhaustion Score",
                format="%.1f",
                help="Later-stage blowoff score emphasizing wickiness, carry stress, crowding, OI fade, and leverage saturation.",
            ),
        }
        scan_mode_label = str(scan_mode)
        if scan_mode_label == "Full ATH":
            universe_label = (
                f"up to {FULL_ATH_MAX_SYMBOLS_TO_SCAN} non-major ATH-runway candidates; "
                f"top {FULL_ATH_EXTERNAL_SYMBOLS_TO_SCAN} externally enriched"
            )
        elif scan_mode_label == "Deep":
            universe_label = (
                f"capped at {DEEP_MAX_TOTAL_SYMBOLS_TO_SCAN} total symbols; "
                f"top {DEEP_EXTERNAL_SYMBOLS_TO_SCAN} externally enriched; "
                f"includes top {MAX_SYMBOLS_TO_SCAN} volume perps plus up to "
                f"{min(ATH_RUNWAY_SYMBOLS_TO_SCAN, DEEP_ATH_SYMBOLS_TO_SCAN)} ATH-runway candidates"
            )
        else:
            universe_label = f"top {FAST_MAX_SYMBOLS} crypto USDT perps by 24h quote volume"
        st.caption(
            f"Last scan: {_now_utc()} | Scan mode: {scan_mode_label} | "
            f"Universe scanned: {universe_label} | "
            f"Modeled funding: {'enabled' if ENABLE_MODELED_FUNDING and scan_mode_label == 'Deep' else 'off'} | "
            f"L/S ratio period: {LONG_SHORT_RATIO_PERIOD} (global Binance account ratio) | "
            f"Crime-pump period: {CRIME_PUMP_PERIOD}"
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Pairs scanned", int(len(all_df)))
        c2.metric("5D high breaks", int(all_df["broke_high_5d"].sum()) if not all_df.empty else 0)
        c3.metric("20D high breaks", int(all_df["broke_high_20d"].sum()) if not all_df.empty else 0)
        c4.metric("90D high breaks", int(all_df["broke_high_90d"].sum()) if not all_df.empty else 0)
        c5.metric("180D high breaks", int(all_df["broke_high_180d"].sum()) if not all_df.empty else 0)

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("5D low breaks", int(all_df["broke_low_5d"].sum()) if not all_df.empty else 0)
        d2.metric("20D low breaks", int(all_df["broke_low_20d"].sum()) if not all_df.empty else 0)
        d3.metric("90D low breaks", int(all_df["broke_low_90d"].sum()) if not all_df.empty else 0)
        d4.metric("180D low breaks", int(all_df["broke_low_180d"].sum()) if not all_df.empty else 0)

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Crime candidates", int(all_df["crime_pump_flag"].sum()) if not all_df.empty else 0)
        e2.metric("Ignition setups", int(all_df["ignition_setup_flag"].sum()) if not all_df.empty else 0)
        e3.metric("Exhaustion flags", int(all_df["exhaustion_flag"].sum()) if not all_df.empty else 0)
        median_pump_score = float(all_df["crime_pump_score"].median()) if not all_df.empty else 0.0
        e4.metric("Median pump score", f"{median_pump_score:.1f}")

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Pre-pump", int(all_df["pre_pump_candidate_flag"].sum()) if not all_df.empty else 0)
        f2.metric("Convex prime", int(all_df["convexity_prime_flag"].sum()) if not all_df.empty else 0)
        f3.metric("Chase risk", int(all_df["convexity_chase_risk_flag"].sum()) if not all_df.empty else 0)
        median_convexity = float(all_df["convexity_entry_score"].median()) if not all_df.empty else 0.0
        f4.metric("Median entry score", f"{median_convexity:.1f}")

        g1, g2, g3, g4 = st.columns(4)
        runway_20x_count = int(all_df["ath_runway_20x_flag"].fillna(False).astype(bool).sum()) if not all_df.empty else 0
        max_ath_multiple = float(pd.to_numeric(all_df["ath_multiple"], errors="coerce").max()) if not all_df.empty else float("nan")
        confluence_median = float(pd.to_numeric(all_df["convexity_confluence_score"], errors="coerce").median()) if not all_df.empty else 0.0
        multi_mech_count = int((pd.to_numeric(all_df["convexity_confluence_count"], errors="coerce").fillna(0) >= 4).sum()) if not all_df.empty else 0
        machine_count = int(all_df["squeeze_machine_flag"].fillna(False).astype(bool).sum()) if not all_df.empty else 0
        g1.metric("20x ATH runway", runway_20x_count)
        g2.metric("Max ATH runway", f"{max_ath_multiple:.1f}x" if math.isfinite(max_ath_multiple) else "n/a")
        g3.metric("Squeeze machines", machine_count)
        g4.metric("Median confluence", f"{confluence_median:.1f}")

        st.subheader("Trade Buckets")
        st.caption(
            "Quick triage bucket for each coin. Convex Long now prioritizes early asymmetric setups: controlled float, sponsored spot, "
            "real expansion, some squeeze fuel, and enough runway left that the move can still become absurd. "
            "Scalp Only catches hot but less durable momentum, and Avoid marks late / crowded / deteriorating structures."
        )
        bucket_cols = [
            "symbol",
            "base_asset",
            "trade_bucket",
            "trade_bucket_score",
            "trade_bucket_note",
            "pre_pump_candidate_flag",
            "convexity_score",
            "convexity_entry_score",
            "convexity_prime_flag",
            "early_convexity_flag",
            "convexity_chase_risk_flag",
            "convexity_too_late_flag",
            "convexity_summary",
            "convexity_confluence_score",
            "convexity_confluence_count",
            "convexity_confluence_note",
            "squeeze_machine_flag",
            "squeeze_machine_score",
            "short_liquidation_fuel_score",
            "spot_control_score",
            "valuation_trap_score",
            "trend_confluence_score",
            "spot_flow_confluence_score",
            "perp_squeeze_confluence_score",
            "float_control_confluence_score",
            "mm_sponsor_confluence_score",
            "ath_runway_confluence_score",
            "last_price",
            "ath_multiple",
            "ath_price",
            "ath_source",
            "ath_runway_20x_flag",
            "convexity_float_score",
            "convexity_sponsor_score",
            "convexity_preignition_score",
            "convexity_expansion_score",
            "convexity_squeeze_score",
            "convexity_runway_score",
            "convexity_late_penalty",
            "convexity_seed_score",
            "crime_ignition_score",
            "crime_pump_score",
            "crime_exhaustion_score",
            "carry_funding_pct",
            "predicted_funding_pct",
            "oi_delta_pct",
            "daily_quote_volume_multiple",
            "hour_trade_count_multiple",
            "taker_buy_sell_ratio",
            "hour_close_location_pct",
            "crowd_top_position_divergence_pct",
            "broke_high_5d",
            "broke_high_20d",
            "broke_high_90d",
            "distance_to_high_5d_pct",
            "distance_to_high_20d_pct",
            "distance_to_high_90d_pct",
        ]
        convex_col, scalp_col, avoid_col = st.columns(3)

        convex_df = all_df[all_df["trade_bucket"] == "Convex Long"].sort_values(
            [
                "pre_pump_candidate_flag",
                "convexity_prime_flag",
                "early_convexity_flag",
                "convexity_confluence_count",
                "convexity_confluence_score",
                "trade_bucket_score",
                "convexity_entry_score",
                "symbol",
            ],
            ascending=[False, False, False, False, False, False, False, True],
        )
        scalp_df = all_df[all_df["trade_bucket"] == "Scalp Only"].sort_values(
            ["pre_pump_candidate_flag", "early_convexity_flag", "trade_bucket_score", "convexity_entry_score", "crime_pump_score", "symbol"],
            ascending=[False, False, False, False, False, True],
        )
        avoid_df = all_df[all_df["trade_bucket"] == "Avoid"].sort_values(
            ["convexity_too_late_flag", "trade_bucket_score", "crime_exhaustion_score", "symbol"],
            ascending=[False, False, False, True],
        )

        convex_col.subheader("Convex Long")
        if convex_df.empty:
            convex_col.info("No coins are clearing the cleaner convex-long filter in this scan.")
        else:
            convex_col.dataframe(
                _display_frame(convex_df, bucket_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )

        scalp_col.subheader("Scalp Only")
        if scalp_df.empty:
            scalp_col.info("No hot-but-fragile momentum names in this scan.")
        else:
            scalp_col.dataframe(
                _display_frame(scalp_df, bucket_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )

        avoid_col.subheader("Avoid")
        if avoid_df.empty:
            avoid_col.info("No late / toxic structures are currently flagged.")
        else:
            avoid_col.dataframe(
                _display_frame(avoid_df, bucket_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )

        st.subheader("Pairs that broke 90D/180D highs in the last 24h")
        if highs_df.empty:
            st.info("No 90D/180D upside breakouts detected in the scanned universe.")
        else:
            display_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "market_type",
                "last_price",
                "funding_countdown_hours",
                "carry_funding_pct",
                "carry_funding_annualized_pct",
                "long_short_account_ratio",
                "long_account_pct",
                "short_account_pct",
                "predicted_funding_pct",
                "predicted_funding_annualized_pct",
                "predicted_funding_low_pct",
                "predicted_funding_high_pct",
                "funding_window_elapsed_pct",
                "predicted_funding_backtest_mae_pct",
                "corr_window_days",
                "corr_to_btc_6m",
                "high_24h",
                "high_5d",
                "high_20d",
                "high_90d",
                "high_180d",
                "broke_high_5d",
                "broke_high_20d",
                "broke_high_90d",
                "broke_high_180d",
            ]
            st.dataframe(
                _display_frame(highs_df, display_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )

        screener_tabs = st.tabs(
            [
                "5D Highs",
                "5D Lows",
                "20D Highs",
                "20D Lows",
                "Carry / Funding",
                "Funding Flipped // Short Squeeze",
                "20x ATH Runway",
                "Crime Pump",
            ]
        )

        short_window_cols = [
            "symbol",
            "base_asset",
            "trade_bucket",
            "trade_bucket_score",
            "market_type",
            "last_price",
            "funding_interval_hours",
            "funding_countdown_hours",
            "carry_funding_pct",
            "carry_funding_annualized_pct",
            "long_carry_pct",
            "long_carry_annualized_pct",
            "long_short_account_ratio",
            "long_account_pct",
            "short_account_pct",
            "premium_index_pct",
            "predicted_funding_pct",
            "predicted_funding_annualized_pct",
            "predicted_funding_low_pct",
            "predicted_funding_high_pct",
            "predicted_funding_band_pct",
            "predicted_funding_backtest_mae_pct",
            "funding_window_elapsed_pct",
            "corr_to_btc_6m",
            "high_24h",
            "low_24h",
            "high_5d",
            "low_5d",
            "high_20d",
            "low_20d",
            "broke_high_5d",
            "broke_low_5d",
            "broke_high_20d",
            "broke_low_20d",
        ]

        with screener_tabs[0]:
            highs_5d_df = all_df[all_df["broke_high_5d"]].sort_values(
                ["carry_funding_pct", "symbol"],
                ascending=[False, True],
            )
            if highs_5d_df.empty:
                st.info("No 5D upside breakouts detected in the scanned universe.")
            else:
                st.dataframe(
                    _display_frame(highs_5d_df, short_window_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

        with screener_tabs[1]:
            lows_5d_df = all_df[all_df["broke_low_5d"]].sort_values(
                ["carry_funding_pct", "symbol"],
                ascending=[True, True],
            )
            if lows_5d_df.empty:
                st.info("No 5D downside breakouts detected in the scanned universe.")
            else:
                st.dataframe(
                    _display_frame(lows_5d_df, short_window_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

        with screener_tabs[2]:
            highs_20d_df = all_df[all_df["broke_high_20d"]].sort_values(
                ["carry_funding_pct", "symbol"],
                ascending=[False, True],
            )
            if highs_20d_df.empty:
                st.info("No 20D upside breakouts detected in the scanned universe.")
            else:
                st.dataframe(
                    _display_frame(highs_20d_df, short_window_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

        with screener_tabs[3]:
            lows_20d_df = all_df[all_df["broke_low_20d"]].sort_values(
                ["carry_funding_pct", "symbol"],
                ascending=[True, True],
            )
            if lows_20d_df.empty:
                st.info("No 20D downside breakouts detected in the scanned universe.")
            else:
                st.dataframe(
                    _display_frame(lows_20d_df, short_window_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

        with screener_tabs[4]:
            st.caption(
                "Est. Funding is Binance's live estimate from the mark-price snapshot and matches the funding number shown in Binance's UI. "
                "Positive means a short should receive and a long should pay; negative means a short should pay and a long should receive. "
                "Modeled Funding is our separate reconstruction/backtest model, kept here as a secondary comparison only. "
                "L/S Acct Ratio is Binance's global account-count ratio for the symbol."
            )
            funding_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "market_type",
                "last_price",
                "funding_interval_hours",
                "funding_countdown_hours",
                "carry_funding_pct",
                "carry_funding_annualized_pct",
                "long_carry_pct",
                "long_carry_annualized_pct",
                "long_short_account_ratio",
                "long_account_pct",
                "short_account_pct",
                "premium_index_pct",
                "predicted_funding_pct",
                "predicted_funding_annualized_pct",
                "predicted_long_carry_pct",
                "predicted_long_carry_annualized_pct",
                "predicted_funding_low_pct",
                "predicted_funding_high_pct",
                "predicted_funding_band_pct",
                "predicted_funding_backtest_mae_pct",
                "predicted_funding_backtest_count",
                "funding_window_elapsed_pct",
                "corr_to_btc_6m",
                "broke_high_5d",
                "broke_low_5d",
                "broke_high_20d",
                "broke_low_20d",
                "broke_high_90d",
                "broke_low_90d",
            ]
            top_carry, bottom_carry = st.columns(2)
            top_carry.subheader("Best est. carry for shorts")
            top_carry.dataframe(
                _display_frame(all_df.sort_values(["carry_funding_pct", "symbol"], ascending=[False, True]).head(20), funding_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )
            bottom_carry.subheader("Best est. carry for longs")
            bottom_carry.dataframe(
                _display_frame(all_df.sort_values(["carry_funding_pct", "symbol"], ascending=[True, True]).head(20), funding_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )
            crowded_long, crowded_short = st.columns(2)
            crowded_long.subheader("Most long-skewed accounts")
            crowded_long.dataframe(
                _display_frame(all_df.sort_values(["long_short_account_ratio", "symbol"], ascending=[False, True]).head(20), funding_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )
            crowded_short.subheader("Most short-skewed accounts")
            crowded_short.dataframe(
                _display_frame(all_df.sort_values(["long_short_account_ratio", "symbol"], ascending=[True, True]).head(20), funding_cols),
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )
            with st.expander("Show full carry / funding table"):
                st.dataframe(
                    _display_frame(all_df.sort_values(["carry_funding_pct", "symbol"], ascending=[False, True]), funding_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

        with screener_tabs[5]:
            st.caption(
                "This tab hunts the exact shape of move where funding was recently negative, flips positive, "
                "and price confirms with stacked upside breakouts, rising OI, taker buyers, and still-meaningful short crowding. "
                "Scanned ATH is the max daily high in the loaded Binance history window, so treat it as a history-window proxy rather than guaranteed full lifetime ATH."
            )
            squeeze_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "short_squeeze_score",
                "funding_flip_score",
                "short_crowding_score",
                "breakout_pressure_score",
                "runway_score",
                "funding_flip_up_flag",
                "fresh_flip_flag",
                "active_short_squeeze_flag",
                "squeeze_chase_flag",
                "short_squeeze_summary",
                "short_squeeze_top_factors",
                "short_squeeze_offsets",
                "last_price",
                "ath_scanned",
                "upside_to_ath_pct",
                "carry_funding_pct",
                "predicted_funding_pct",
                "last_settled_funding_pct",
                "prior_settled_funding_pct",
                "funding_flip_delta_pct",
                "premium_index_pct",
                "basis_rate_pct",
                "long_short_account_ratio",
                "long_account_pct",
                "short_account_pct",
                "top_trader_position_ratio",
                "top_trader_account_ratio",
                "oi_value_usdt",
                "oi_delta_pct",
                "oi_to_24h_volume_pct",
                "hour_return_pct",
                "hour_return_z",
                "day_return_pct",
                "hour_volume_multiple",
                "hour_trade_count_multiple",
                "taker_buy_sell_ratio",
                "taker_buy_share_pct",
                "hour_close_location_pct",
                "hour_upper_wick_pct",
                "breakout_stack_count",
                "high_5d",
                "high_20d",
                "high_90d",
                "high_180d",
                "broke_high_5d",
                "broke_high_20d",
                "broke_high_90d",
                "broke_high_180d",
            ]
            squeeze_view_df = all_df[
                (all_df["funding_flip_up_flag"])
                | (all_df["breakout_stack_count"] >= 1)
                | (all_df["short_squeeze_score"] >= 35.0)
            ].copy()
            squeeze_ranked_df = squeeze_view_df.sort_values(
                [
                    "active_short_squeeze_flag",
                    "fresh_flip_flag",
                    "funding_flip_up_flag",
                    "short_squeeze_score",
                    "funding_flip_score",
                    "breakout_stack_count",
                    "upside_to_ath_pct",
                    "symbol",
                ],
                ascending=[False, False, False, False, False, False, False, True],
            )
            fresh_flip_df = squeeze_ranked_df[squeeze_ranked_df["fresh_flip_flag"]].copy()
            active_short_df = squeeze_ranked_df[squeeze_ranked_df["active_short_squeeze_flag"]].copy()
            chase_df = squeeze_ranked_df[squeeze_ranked_df["squeeze_chase_flag"]].copy()

            sq1, sq2, sq3, sq4 = st.columns(4)
            sq1.metric("Funding flipped now", int(all_df["funding_flip_up_flag"].sum()) if not all_df.empty else 0)
            sq2.metric("Fresh flips", int(all_df["fresh_flip_flag"].sum()) if not all_df.empty else 0)
            sq3.metric("Active short squeezes", int(all_df["active_short_squeeze_flag"].sum()) if not all_df.empty else 0)
            sq4.metric("Short squeeze median", f"{float(squeeze_ranked_df['short_squeeze_score'].median()) if not squeeze_ranked_df.empty else 0.0:.1f}")

            flip_col, active_col, chase_col = st.columns(3)
            flip_col.subheader("Fresh Flip")
            if fresh_flip_df.empty:
                flip_col.info("No fresh funding-flip setups right now.")
            else:
                flip_col.dataframe(
                    _display_frame(fresh_flip_df.head(15), squeeze_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            active_col.subheader("Active Short Squeeze")
            if active_short_df.empty:
                active_col.info("No active short squeezes in this scan.")
            else:
                active_col.dataframe(
                    _display_frame(active_short_df.head(15), squeeze_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            chase_col.subheader("Chase Risk")
            if chase_df.empty:
                chase_col.info("No overextended squeeze names are currently flagged.")
            else:
                chase_col.dataframe(
                    _display_frame(chase_df.head(15), squeeze_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            st.markdown("#### Ranked Short Squeeze Tape")
            if squeeze_ranked_df.empty:
                st.info("No funding-flip / short-squeeze candidates in the scanned universe.")
            else:
                st.dataframe(
                    _display_frame(squeeze_ranked_df.head(30), squeeze_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            with st.expander("Show full funding-flip / short-squeeze table"):
                if squeeze_ranked_df.empty:
                    st.info("No short-squeeze rows to show.")
                else:
                    st.dataframe(
                        _display_frame(squeeze_ranked_df, squeeze_cols),
                        use_container_width=True,
                        hide_index=True,
                        column_config=breakout_column_config,
                    )

        with screener_tabs[6]:
            st.caption(
                "Ranks coins trading at least 20x below their best available ATH. "
                "Deep mode enriches the strongest runway candidates with CoinGecko lifetime ATH where possible; "
                f"Full ATH scans up to {FULL_ATH_MAX_SYMBOLS_TO_SCAN} wider non-major Binance perp candidates and "
                "falls back to Binance scanned-history ATH when external ATH is unavailable."
            )
            ath_runway_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "trade_bucket_note",
                "last_price",
                "ath_price",
                "ath_multiple",
                "ath_upside_pct",
                "ath_source",
                "coingecko_ath_usd",
                "coingecko_ath_change_pct",
                "coingecko_ath_date",
                "ath_scanned",
                "ath_runway_20x_flag",
                "convexity_score",
                "convexity_entry_score",
                "convexity_confluence_score",
                "convexity_confluence_count",
                "convexity_confluence_note",
                "squeeze_machine_flag",
                "squeeze_machine_score",
                "short_liquidation_fuel_score",
                "spot_control_score",
                "valuation_trap_score",
                "pre_pump_candidate_flag",
                "convexity_prime_flag",
                "early_convexity_flag",
                "convexity_chase_risk_flag",
                "convexity_too_late_flag",
                "trend_confluence_score",
                "spot_flow_confluence_score",
                "perp_squeeze_confluence_score",
                "float_control_confluence_score",
                "mm_sponsor_confluence_score",
                "ath_runway_confluence_score",
                "breakout_stack_count",
                "broke_high_5d",
                "broke_high_20d",
                "broke_high_90d",
                "broke_high_180d",
                "distance_to_high_5d_pct",
                "distance_to_high_20d_pct",
                "distance_to_high_90d_pct",
                "daily_quote_volume_multiple",
                "hour_volume_multiple",
                "hour_trade_count_multiple",
                "day_return_pct",
                "hour_return_pct",
                "carry_funding_pct",
                "funding_flip_up_flag",
                "short_crowding_score",
                "perp_pressure_score",
                "oi_delta_pct",
                "oi_to_market_cap_pct",
                "spot_flow_confluence_flag",
                "perp_squeeze_confluence_flag",
                "float_control_confluence_flag",
                "ath_runway_confluence_flag",
                "cex_to_dex_volume_ratio",
                "cex_volume_share_pct",
                "binance_bitget_gate_share_pct",
                "emfx_volume_share_pct",
                "krw_volume_share_pct",
                "try_volume_share_pct",
                "top_venue",
                "top_venue_volume_share_pct",
                "spot_volume_to_mcap_pct",
                "perp_volume_to_mcap_pct",
                "market_cap_usd",
                "fdv_to_market_cap",
                "locked_supply_pct",
                "top10_holder_pct",
                "holder_count",
            ]
            ath_multiple = pd.to_numeric(all_df["ath_multiple"], errors="coerce") if "ath_multiple" in all_df.columns else pd.Series(float("nan"), index=all_df.index)
            major_mask = all_df["crime_excluded_major"].fillna(False).astype(bool) if "crime_excluded_major" in all_df.columns else pd.Series(False, index=all_df.index)
            runway_df = all_df[(ath_multiple >= 20.0) & (~major_mask)].copy()
            runway_df = runway_df.sort_values(
                [
                    "ath_multiple",
                    "pre_pump_candidate_flag",
                    "convexity_prime_flag",
                    "convexity_confluence_score",
                    "convexity_entry_score",
                    "daily_quote_volume_multiple",
                    "symbol",
                ],
                ascending=[False, False, False, False, False, False, True],
            )
            ranked_runway_df = all_df[(ath_multiple >= 5.0) & (~major_mask)].copy().sort_values(
                [
                    "ath_multiple",
                    "convexity_confluence_score",
                    "convexity_entry_score",
                    "symbol",
                ],
                ascending=[False, False, False, True],
            )

            rw1, rw2, rw3, rw4 = st.columns(4)
            rw1.metric("20x+ runway names", int(len(runway_df)))
            rw2.metric("50x+ runway names", int(((ath_multiple.fillna(0.0) >= 50.0) & (~major_mask)).sum()))
            rw3.metric(
                "Best runway",
                f"{float(runway_df['ath_multiple'].max()):.1f}x" if not runway_df.empty else "n/a",
            )
            cg_backed = int((runway_df["ath_source"].astype(str) == "CoinGecko").sum()) if not runway_df.empty and "ath_source" in runway_df.columns else 0
            rw4.metric("CG-backed ATHs", cg_backed)

            st.markdown("#### 20x+ ATH Runway")
            if runway_df.empty:
                st.info("No non-major coins in the scanned universe are currently at least 20x below ATH.")
            else:
                st.dataframe(
                    _display_frame(runway_df, ath_runway_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            with st.expander("Show 5x+ ATH runway ranked table"):
                if ranked_runway_df.empty:
                    st.info("No 5x+ ATH runway rows in this scan.")
                else:
                    st.dataframe(
                        _display_frame(ranked_runway_df, ath_runway_cols),
                        use_container_width=True,
                        hide_index=True,
                        column_config=breakout_column_config,
                    )

        with screener_tabs[7]:
            if scan_mode == "Fast":
                st.info("Fast mode skips the heavier crime-pump fetches for speed. Switch to Deep scan for the full crime diagnostics.")
            st.caption(
                "Crime Pump flags try to catch the nastier perp squeezes: fast upside velocity, big 1H and 24H momentum, "
                "trade-count expansion, rising OI, aggressive taker buys, positive carry/basis, crowd-longing ahead of top traders, "
                "a thin ask book, external spot support, Coinbase spot liquidity-sponsor diagnostics, manual MM/social-graph confluence, and float/holder concentration proxies. "
                "Majors are excluded from this tab by default. "
                "Ignition looks for forceful closes near the hourly high; Exhaustion looks for upper wicks, carry stress, and OI fade."
            )
            crime_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "trade_bucket_note",
                "crime_eligible",
                "crime_excluded_major",
                "crime_mechanics_score",
                "crime_coinbase_lane_score",
                "crime_owner_circle_score",
                "mm_presence_score",
                "mm_bid_support_score",
                "mm_withdrawal_risk_score",
                "mm_proximity_score",
                "mm_proximity_maker",
                "dwf_labs_portfolio",
                "dwf_labs_portfolio_score",
                "dwf_labs_portfolio_rank",
                "mm_proximity_note",
                "mm_proximity_source",
                "inventory_transfer_risk_score",
                "inventory_sponsor_mismatch_score",
                "inventory_transfer_risk_flag",
                "inventory_transfer_note",
                "crime_microstructure_score",
                "crime_largecap_penalty_score",
                "crime_spot_impulse_score",
                "crime_supply_control_score",
                "market_type",
                "last_price",
                "float_trap_score",
                "perp_pressure_score",
                "venue_support_score",
                "exit_fragility_score",
                "crime_pump_score_v2",
                "squeeze_machine_flag",
                "squeeze_machine_score",
                "short_liquidation_fuel_score",
                "spot_control_score",
                "valuation_trap_score",
                "cmc_mover_score",
                "cmc_mover_label",
                "cmc_rank_1h",
                "cmc_rank_24h",
                "cmc_pct_1h",
                "cmc_pct_24h",
                "cmc_name",
                "cmc_market_cap_usd",
                "cmc_volume_24h",
                "cmc_volume_to_mcap_pct",
                "setup_ready_flag",
                "active_squeeze_flag",
                "blowoff_watch_flag",
                "unwind_risk_flag",
                "why_flagged_summary",
                "why_flagged_top_factors",
                "why_flagged_offsets",
                "convexity_score",
                "convexity_entry_score",
                "pre_pump_candidate_flag",
                "convexity_prime_flag",
                "early_convexity_flag",
                "convexity_chase_risk_flag",
                "convexity_too_late_flag",
                "convexity_summary",
                "convexity_confluence_score",
                "convexity_confluence_count",
                "convexity_confluence_note",
                "squeeze_machine_flag",
                "squeeze_machine_score",
                "short_liquidation_fuel_score",
                "spot_control_score",
                "valuation_trap_score",
                "trend_confluence_score",
                "spot_flow_confluence_score",
                "perp_squeeze_confluence_score",
                "float_control_confluence_score",
                "mm_sponsor_confluence_score",
                "ath_runway_confluence_score",
                "convexity_float_score",
                "convexity_sponsor_score",
                "convexity_preignition_score",
                "convexity_expansion_score",
                "convexity_squeeze_score",
                "convexity_runway_score",
                "convexity_late_penalty",
                "convexity_seed_score",
                "ath_multiple",
                "ath_price",
                "ath_upside_pct",
                "ath_source",
                "ath_runway_20x_flag",
                "crime_pump_score",
                "ignition_score_v2",
                "crime_ignition_score",
                "crime_exhaustion_score",
                "crime_pump_flag",
                "ignition_setup_flag",
                "exhaustion_flag",
                "squeeze_risk_flag",
                "blowoff_risk_flag",
                "hour_return_pct",
                "hour_return_z",
                "day_return_pct",
                "daily_quote_volume_multiple",
                "hour_volume_multiple",
                "hour_trade_count_multiple",
                "hour_upper_wick_pct",
                "hour_close_location_pct",
                "oi_value_usdt",
                "oi_delta_pct",
                "oi_to_24h_volume_pct",
                "oi_to_market_cap_pct",
                "carry_funding_pct",
                "predicted_funding_pct",
                "premium_index_pct",
                "basis_rate_pct",
                "crime_carry_stress_score",
                "taker_buy_sell_ratio",
                "taker_buy_share_pct",
                "long_short_account_ratio",
                "long_account_pct",
                "top_trader_position_ratio",
                "top_trader_long_position_pct",
                "top_trader_account_ratio",
                "top_trader_long_account_pct",
                "crowd_top_position_divergence_pct",
                "crowd_top_account_divergence_pct",
                "ask_depth_1pct_usdt",
                "ask_depth_to_24h_volume_pct",
                "coinbase_spot_listed",
                "spot_external_quote_volume_24h",
                "coinbase_spot_quote_volume_24h",
                "coingecko_total_volume_24h",
                "coingecko_cex_volume_24h",
                "kraken_spot_quote_volume_24h",
                "upbit_spot_quote_volume_24h",
                "upbit_krw_quote_volume_24h",
                "try_spot_quote_volume_24h",
                "emfx_spot_quote_volume_24h",
                "coingecko_dex_volume_24h",
                "cex_volume_share_pct",
                "cex_to_dex_volume_ratio",
                "cex_dex_volume_ratio_score",
                "coinbase_volume_share_pct",
                "binance_volume_share_pct",
                "bitget_volume_share_pct",
                "gate_volume_share_pct",
                "kraken_volume_share_pct",
                "upbit_volume_share_pct",
                "krw_volume_share_pct",
                "try_volume_share_pct",
                "emfx_volume_share_pct",
                "binance_bitget_gate_share_pct",
                "binance_bitget_gate_share_score",
                "top_venue",
                "top_venue_volume_share_pct",
                "top3_venue_volume_share_pct",
                "venue_hhi",
                "venue_hhi_score",
                "venue_count",
                "dex_volume_share_pct",
                "emfx_lane_score",
                "coinbase_bid_ask_spread_pct",
                "coinbase_bid_depth_2pct_usd",
                "coinbase_ask_depth_2pct_usd",
                "coinbase_total_depth_2pct_usd",
                "coinbase_book_imbalance_pct",
                "coinbase_depth_to_volume_pct",
                "coinbase_depth_to_perp_volume_pct",
                "spot_to_perp_volume_pct",
                "coinbase_to_perp_volume_pct",
                "spot_volume_to_mcap_pct",
                "perp_volume_to_mcap_pct",
                "market_cap_usd",
                "fdv_to_market_cap",
                "locked_supply_pct",
                "top10_holder_pct",
                "holder_count",
                "owner_holder_pct",
                "creator_holder_pct",
                "broke_high_5d",
                "broke_high_20d",
                "broke_high_90d",
                "distance_to_high_5d_pct",
                "distance_to_high_20d_pct",
                "distance_to_high_90d_pct",
            ]
            crime_bucket_cols = [
                "symbol",
                "base_asset",
                "trade_bucket",
                "trade_bucket_score",
                "trade_bucket_note",
                "crime_eligible",
                "crime_excluded_major",
                "crime_mechanics_score",
                "crime_coinbase_lane_score",
                "crime_owner_circle_score",
                "mm_presence_score",
                "mm_bid_support_score",
                "mm_withdrawal_risk_score",
                "mm_proximity_score",
                "mm_proximity_maker",
                "dwf_labs_portfolio",
                "dwf_labs_portfolio_score",
                "dwf_labs_portfolio_rank",
                "inventory_transfer_risk_score",
                "inventory_sponsor_mismatch_score",
                "inventory_transfer_risk_flag",
                "convexity_score",
                "convexity_entry_score",
                "pre_pump_candidate_flag",
                "convexity_prime_flag",
                "early_convexity_flag",
                "convexity_chase_risk_flag",
                "convexity_too_late_flag",
                "convexity_summary",
                "convexity_confluence_score",
                "convexity_confluence_count",
                "convexity_confluence_note",
                "trend_confluence_score",
                "spot_flow_confluence_score",
                "perp_squeeze_confluence_score",
                "float_control_confluence_score",
                "mm_sponsor_confluence_score",
                "ath_runway_confluence_score",
                "convexity_float_score",
                "convexity_sponsor_score",
                "convexity_preignition_score",
                "convexity_expansion_score",
                "convexity_squeeze_score",
                "convexity_runway_score",
                "convexity_late_penalty",
                "convexity_seed_score",
                "ath_multiple",
                "ath_price",
                "ath_upside_pct",
                "ath_source",
                "ath_runway_20x_flag",
                "float_trap_score",
                "perp_pressure_score",
                "venue_support_score",
                "exit_fragility_score",
                "crime_pump_score_v2",
                "cmc_mover_score",
                "cmc_mover_label",
                "cmc_pct_1h",
                "cmc_pct_24h",
                "cmc_volume_to_mcap_pct",
                "setup_ready_flag",
                "active_squeeze_flag",
                "blowoff_watch_flag",
                "unwind_risk_flag",
                "crime_microstructure_score",
                "crime_largecap_penalty_score",
                "crime_spot_impulse_score",
                "crime_supply_control_score",
                "last_price",
                "crime_ignition_score",
                "crime_pump_score",
                "crime_exhaustion_score",
                "oi_delta_pct",
                "daily_quote_volume_multiple",
                "hour_trade_count_multiple",
                "taker_buy_sell_ratio",
                "hour_close_location_pct",
                "carry_funding_pct",
                "crowd_top_position_divergence_pct",
                "coinbase_spot_listed",
                "coinbase_volume_share_pct",
                "coingecko_cex_volume_24h",
                "coingecko_dex_volume_24h",
                "cex_to_dex_volume_ratio",
                "cex_dex_volume_ratio_score",
                "binance_volume_share_pct",
                "bitget_volume_share_pct",
                "gate_volume_share_pct",
                "krw_volume_share_pct",
                "try_volume_share_pct",
                "emfx_volume_share_pct",
                "binance_bitget_gate_share_pct",
                "binance_bitget_gate_share_score",
                "top_venue_volume_share_pct",
                "venue_hhi",
                "venue_hhi_score",
                "coinbase_total_depth_2pct_usd",
                "coinbase_book_imbalance_pct",
                "coinbase_depth_to_volume_pct",
                "coinbase_depth_to_perp_volume_pct",
                "emfx_lane_score",
                "spot_to_perp_volume_pct",
                "spot_volume_to_mcap_pct",
                "perp_volume_to_mcap_pct",
                "oi_to_market_cap_pct",
                "locked_supply_pct",
                "top10_holder_pct",
                "holder_count",
                "broke_high_5d",
                "broke_high_20d",
                "broke_high_90d",
                "distance_to_high_5d_pct",
                "distance_to_high_20d_pct",
                "distance_to_high_90d_pct",
            ]
            st.markdown("#### Crime Pump Convexity")
            st.caption(
                "These are the same triage buckets, but now with explicit early-convexity ranking. Convex Long should be the names "
                "that still have real asymmetry left if the sponsor flow persists, not just whatever is already obviously ripping."
            )
            crime_convex_col, crime_scalp_col, crime_avoid_col = st.columns(3)
            candidate_view_df = all_df[
                (~all_df["crime_excluded_major"].fillna(False).astype(bool))
                & (
                    all_df["crime_eligible"].fillna(False).astype(bool)
                    | all_df["pre_pump_candidate_flag"].fillna(False).astype(bool)
                    | all_df["early_convexity_flag"].fillna(False).astype(bool)
                    | all_df["convexity_prime_flag"].fillna(False).astype(bool)
                    | all_df["squeeze_machine_flag"].fillna(False).astype(bool)
                    | (all_df["convexity_entry_score"].fillna(0.0) >= 42.0)
                    | (all_df["convexity_seed_score"].fillna(0.0) >= 55.0)
                    | (all_df["squeeze_machine_score"].fillna(0.0) >= 52.0)
                    | (
                        (all_df["convexity_confluence_count"].fillna(0.0) >= 3.0)
                        & (all_df["convexity_confluence_score"].fillna(0.0) >= 42.0)
                    )
                    | all_df["ath_runway_20x_flag"].fillna(False).astype(bool)
                )
            ].copy()
            crime_view_df = candidate_view_df.copy()
            radar_1, radar_2, radar_3, radar_4, radar_5 = st.columns(5)
            radar_1.metric("Pre-pump candidates", int(all_df["pre_pump_candidate_flag"].sum()) if not all_df.empty else 0)
            radar_2.metric("Convex prime", int(all_df["convexity_prime_flag"].sum()) if not all_df.empty else 0)
            radar_3.metric("Squeeze machines", int(all_df["squeeze_machine_flag"].sum()) if not all_df.empty else 0)
            radar_4.metric("Chase risk", int(all_df["convexity_chase_risk_flag"].sum()) if not all_df.empty else 0)
            radar_5.metric("Median entry score", f"{float(candidate_view_df['convexity_entry_score'].median()) if not candidate_view_df.empty else 0.0:.1f}")

            st.markdown("#### Pre-Pump Radar")
            top_convexity_df = candidate_view_df.sort_values(
                [
                    "pre_pump_candidate_flag",
                    "squeeze_machine_flag",
                    "convexity_prime_flag",
                    "early_convexity_flag",
                    "squeeze_machine_score",
                    "convexity_confluence_count",
                    "convexity_confluence_score",
                    "convexity_entry_score",
                    "short_liquidation_fuel_score",
                    "spot_control_score",
                    "convexity_preignition_score",
                    "convexity_sponsor_score",
                    "symbol",
                ],
                ascending=[False, False, False, False, False, False, False, False, False, False, False, False, True],
            )
            if top_convexity_df.empty:
                st.info("No symbols are showing strong pre-pump convexity right now.")
            else:
                st.dataframe(
                    _display_frame(top_convexity_df.head(20), crime_bucket_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            st.markdown("#### Crime Pump Buckets")
            crime_convex_df = crime_view_df[crime_view_df["trade_bucket"] == "Convex Long"].sort_values(
                [
                    "pre_pump_candidate_flag",
                    "squeeze_machine_flag",
                    "convexity_prime_flag",
                    "early_convexity_flag",
                    "squeeze_machine_score",
                    "convexity_confluence_count",
                    "convexity_confluence_score",
                    "trade_bucket_score",
                    "convexity_entry_score",
                    "symbol",
                ],
                ascending=[False, False, False, False, False, False, False, False, False, True],
            )
            crime_scalp_df = crime_view_df[crime_view_df["trade_bucket"] == "Scalp Only"].sort_values(
                ["squeeze_machine_flag", "pre_pump_candidate_flag", "early_convexity_flag", "squeeze_machine_score", "trade_bucket_score", "convexity_entry_score", "crime_pump_score", "symbol"],
                ascending=[False, False, False, False, False, False, False, True],
            )
            crime_avoid_df = crime_view_df[crime_view_df["trade_bucket"] == "Avoid"].sort_values(
                ["convexity_too_late_flag", "blowoff_watch_flag", "trade_bucket_score", "exit_fragility_score", "symbol"],
                ascending=[False, False, False, False, True],
            )

            crime_convex_col.subheader("Convex Long")
            if crime_convex_df.empty:
                crime_convex_col.info("No cleaner convex-long crime candidates in this scan.")
            else:
                crime_convex_col.dataframe(
                    _display_frame(crime_convex_df, crime_bucket_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            crime_scalp_col.subheader("Scalp Only")
            if crime_scalp_df.empty:
                crime_scalp_col.info("No scalp-only crime names right now.")
            else:
                crime_scalp_col.dataframe(
                    _display_frame(crime_scalp_df, crime_bucket_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            crime_avoid_col.subheader("Avoid")
            if crime_avoid_df.empty:
                crime_avoid_col.info("No late / toxic crime names are currently flagged.")
            else:
                crime_avoid_col.dataframe(
                    _display_frame(crime_avoid_df, crime_bucket_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )

            st.markdown("#### Ranked Crime Tape")
            ranked_crime_df = crime_view_df.sort_values(
                [
                    "active_squeeze_flag",
                    "squeeze_machine_flag",
                    "pre_pump_candidate_flag",
                    "convexity_prime_flag",
                    "early_convexity_flag",
                    "squeeze_machine_score",
                    "convexity_entry_score",
                    "crime_pump_score_v2",
                    "short_liquidation_fuel_score",
                    "spot_control_score",
                    "convexity_preignition_score",
                    "cmc_mover_score",
                    "setup_ready_flag",
                    "ignition_score_v2",
                    "perp_pressure_score",
                    "venue_support_score",
                    "hour_return_z",
                    "symbol",
                ],
                ascending=[
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    True,
                ],
            )
            if ranked_crime_df.empty:
                st.info("No symbols are currently crossing the pre-pump / crime-structure filters after excluding majors.")
            else:
                st.dataframe(
                    _display_frame(ranked_crime_df.head(30), crime_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )
            flagged_crime_df = ranked_crime_df[
                ranked_crime_df["pre_pump_candidate_flag"]
                | ranked_crime_df["squeeze_machine_flag"]
                | ranked_crime_df["convexity_prime_flag"]
                | ranked_crime_df["early_convexity_flag"]
                | ranked_crime_df["convexity_chase_risk_flag"]
                | ranked_crime_df["convexity_too_late_flag"]
                | ranked_crime_df["active_squeeze_flag"]
                | ranked_crime_df["setup_ready_flag"]
                | ranked_crime_df["blowoff_watch_flag"]
                | ranked_crime_df["unwind_risk_flag"]
                | ranked_crime_df["crime_pump_flag"]
                | ranked_crime_df["ignition_setup_flag"]
                | ranked_crime_df["exhaustion_flag"]
                | ranked_crime_df["squeeze_risk_flag"]
                | ranked_crime_df["blowoff_risk_flag"]
            ]
            with st.expander("Show flagged crime-pump candidates only"):
                if flagged_crime_df.empty:
                    st.info("No symbols are crossing the current crime-pump, ignition, exhaustion, or squeeze/blowoff thresholds.")
                else:
                    st.dataframe(
                        _display_frame(flagged_crime_df, crime_cols),
                        use_container_width=True,
                        hide_index=True,
                        column_config=breakout_column_config,
                    )

        if INCLUDE_TRADFI_BREAKOUTS:
            st.subheader("Tracked commodities")
            tradfi_cols = [
                "symbol",
                "base_asset",
                "market_type",
                "history_days",
                "funding_interval_hours",
                "funding_countdown_hours",
                "carry_funding_pct",
                "carry_funding_annualized_pct",
                "long_short_account_ratio",
                "long_account_pct",
                "short_account_pct",
                "premium_index_pct",
                "predicted_funding_pct",
                "predicted_funding_annualized_pct",
                "predicted_funding_low_pct",
                "predicted_funding_high_pct",
                "predicted_funding_backtest_mae_pct",
                "predicted_funding_backtest_count",
                "funding_window_elapsed_pct",
                "corr_window_days",
                "last_price",
                "corr_to_btc_6m",
                "high_24h",
                "low_24h",
                "high_5d",
                "low_5d",
                "high_20d",
                "low_20d",
                "high_90d",
                "low_90d",
                "high_180d",
                "low_180d",
                "broke_high_5d",
                "broke_low_5d",
                "broke_high_20d",
                "broke_low_20d",
                "broke_high_90d",
                "broke_high_180d",
                "broke_low_90d",
                "broke_low_180d",
            ]
            commodity_df = all_df[all_df["market_type"] == "COMMODITY"].copy()
            if commodity_df.empty:
                st.info("Binance returned no TradFi commodity symbols in this scan.")
            else:
                commodity_df = commodity_df.sort_values(["symbol"]).copy()
                st.dataframe(
                    _display_frame(commodity_df, tradfi_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )
                st.caption(
                    "New listings can appear here before they have enough closed daily candles for the longer 90D/180D "
                    "levels or the full 180-day BTC correlation window."
                )

            st.subheader("Tracked equities")
            equity_df = all_df[all_df["market_type"] == "EQUITY"].copy()
            if equity_df.empty:
                st.info("Binance returned no TradFi equity symbols in this scan.")
            else:
                equity_df = equity_df.sort_values(["symbol"]).copy()
                st.dataframe(
                    _display_frame(equity_df, tradfi_cols),
                    use_container_width=True,
                    hide_index=True,
                    column_config=breakout_column_config,
                )
                st.caption("This section follows Binance's current TradFi equity listings, which today include symbols like NVDAUSDT and GOOGLUSDT.")

        st.subheader("Correlation to BTC")
        correlation_cols = [
            "symbol",
            "base_asset",
            "market_type",
            "history_days",
            "funding_interval_hours",
            "funding_countdown_hours",
            "carry_funding_pct",
            "carry_funding_annualized_pct",
            "long_short_account_ratio",
            "long_account_pct",
            "short_account_pct",
            "premium_index_pct",
            "predicted_funding_pct",
            "predicted_funding_annualized_pct",
            "predicted_funding_low_pct",
            "predicted_funding_high_pct",
            "predicted_funding_backtest_mae_pct",
            "predicted_funding_backtest_count",
            "funding_window_elapsed_pct",
            "corr_window_days",
            "corr_to_btc_6m",
            "last_price",
            "broke_high_5d",
            "broke_low_5d",
            "broke_high_20d",
            "broke_low_20d",
            "broke_high_90d",
            "broke_high_180d",
            "broke_low_90d",
            "broke_low_180d",
        ]
        correlation_df = all_df.sort_values(["corr_to_btc_6m", "symbol"], ascending=[False, True]).copy()
        st.dataframe(
            _display_frame(correlation_df, correlation_cols),
            use_container_width=True,
            hide_index=True,
            column_config=breakout_column_config,
        )

        with st.expander("Show full scanned table"):
            st.dataframe(
                all_df,
                use_container_width=True,
                hide_index=True,
                column_config=breakout_column_config,
            )
    else:
        st.markdown(
            '<div class="card">Click <b>Scan now</b> to fetch Binance data once and show 5D/20D/90D/180D breakouts plus live carry, modeled next funding, crowding, and the new crime-pump tab.</div>',
            unsafe_allow_html=True,
        )


def render_screener_dashboard() -> None:
    st.title("Cross-Asset Screener")
    st.caption(
        "Near-live cross-asset tape using Yahoo Finance for macro markets and Binance Futures for BTC. "
        "Venue delays and change calculations can differ by source."
    )

    if st.button("Refresh screener", type="primary", key="refresh_screener"):
        st.session_state["screener_requested"] = True
        st.session_state["screener_refresh_nonce"] = st.session_state.get("screener_refresh_nonce", 0) + 1

    if not st.session_state.get("screener_requested"):
        st.markdown(
            '<div class="card">Click <b>Refresh screener</b> to load the cross-asset tape for indices, '
            "volatility, commodities, rates, managed futures, and BTC.</div>",
            unsafe_allow_html=True,
        )
        return

    try:
        with st.spinner("Loading cross-asset screener..."):
            screener = load_screener_cached(st.session_state.get("screener_refresh_nonce", 0))
    except Exception as exc:
        st.error(f"Unable to load the screener right now: {exc}")
        return

    st.caption(f"Updated: {_now_utc()} | Sources: Yahoo Finance + Binance Futures")

    if screener.quotes_df.empty:
        st.warning("No screener assets could be loaded from the configured feeds.")
        if screener.errors:
            for error in screener.errors:
                st.write(f"- {error}")
        return

    quotes_df = screener.quotes_df.copy()
    quotes_df["price_sparkline"] = quotes_df["sparkline"]

    metrics = st.columns(4)
    metrics[0].metric("Assets loaded", str(int(len(quotes_df))))
    metrics[1].metric("Positive movers", str(int((quotes_df["change_pct"] > 0).sum())))
    metrics[2].metric("Negative movers", str(int((quotes_df["change_pct"] < 0).sum())))
    metrics[3].metric("Sources", str(int(quotes_df["source"].nunique())))

    st.subheader("Cross-asset tape")
    display_cols = [
        "code",
        "name",
        "category",
        "last_price",
        "change",
        "change_pct",
        "corr_window_days",
        "corr_to_btc",
        "corr_to_spx",
        "source",
        "price_sparkline",
    ]
    st.dataframe(
        _display_frame(quotes_df, display_cols),
        use_container_width=True,
        hide_index=True,
        column_config={
            "code": st.column_config.TextColumn("Ticker"),
            "name": st.column_config.TextColumn("Asset"),
            "category": st.column_config.TextColumn("Category"),
            "last_price": st.column_config.NumberColumn("Last", format="%.2f"),
            "change": st.column_config.NumberColumn("Change", format="%.2f"),
            "change_pct": st.column_config.NumberColumn("% Change", format="%.2f%%"),
            "corr_window_days": st.column_config.NumberColumn("Corr Window (D)", format="%d"),
            "corr_to_btc": st.column_config.NumberColumn("Corr to BTC", format="%.3f"),
            "corr_to_spx": st.column_config.NumberColumn("Corr to SPX", format="%.3f"),
            "source": st.column_config.TextColumn("Source"),
            "price_sparkline": st.column_config.LineChartColumn("Intraday"),
        },
    )

    if not screener.intraday_df.empty:
        st.subheader("Normalized intraday move")
        st.caption("Each line starts at 100 so you can compare intraday direction across very different markets.")
        st.line_chart(screener.intraday_df, use_container_width=True)

    if screener.errors:
        with st.expander("Unavailable screener assets"):
            for error in screener.errors:
                st.write(f"- {error}")


def render_pnl_dashboard() -> None:
    st.title("Binance Futures PnL Dashboard")
    st.caption(
        "Expanded analysis view with timeframe presets, a custom range control, cumulative charts, "
        "asset breakdowns, and detailed PnL stats."
    )

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        st.warning("Set BINANCE_API_KEY and BINANCE_API_SECRET in .env or your environment to enable PnL.")
        return

    if st.button("Load / refresh PnL", type="primary", key="load_pnl"):
        st.session_state["pnl_requested"] = True
        st.session_state["pnl_refresh_nonce"] = st.session_state.get("pnl_refresh_nonce", 0) + 1

    if not st.session_state.get("pnl_requested"):
        st.markdown(
            '<div class="card">Click <b>Load / refresh PnL</b> to fetch Binance futures account data for '
            "the richer PnL analysis view.</div>",
            unsafe_allow_html=True,
        )
        return

    try:
        with st.spinner("Loading Binance PnL data..."):
            result = load_pnl_dashboard_cached(
                _key_fingerprint(BINANCE_API_KEY),
                st.session_state.get("pnl_refresh_nonce", 0),
            )
    except BinanceHTTPError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"msg": str(exc.payload)}
        st.error(f"Binance rejected the PnL request: {payload}")
        if payload.get("code") == -1022:
            st.info(
                "Binance is rejecting the API key/secret pair before any PnL data is returned. "
                "That means the blocker is the credentials rather than the dashboard logic."
            )
            for note in _credential_diagnostics(BINANCE_API_KEY, BINANCE_API_SECRET):
                st.write(f"- {note}")
            st.write("- Re-copy the API secret from Binance and update `.env`.")
            st.write("- Confirm this API key belongs to the same Binance account shown in the website PnL page.")
            st.write("- Confirm Futures read permissions are enabled on that key.")
            st.write("- Restart Streamlit after updating `.env`.")
        return
    except Exception as exc:
        st.error(f"Unable to load Binance PnL right now: {exc}")
        return

    st.caption(
        f"Updated: {_now_utc()} | Recent signed income lookback: {PNL_RECENT_DAYS} days | "
        f"Annual exports cache: {PNL_CACHE_DIR}"
    )

    account = result.account
    wallet_balance = _safe_float(account.get("totalWalletBalance"))
    margin_balance = _safe_float(account.get("totalMarginBalance"))
    unrealized_profit = _safe_float(account.get("totalUnrealizedProfit"))
    available_balance = _safe_float(account.get("availableBalance"))

    header_row = st.columns(4)
    header_row[0].metric("Wallet balance", _format_usd(wallet_balance))
    header_row[1].metric("Margin balance", _format_usd(margin_balance))
    header_row[2].metric("Unrealized PnL", _format_usd(unrealized_profit))
    header_row[3].metric("Open positions", str(_open_position_count(account)))

    if result.coverage_start is None or result.coverage_end is None:
        st.warning("Binance returned no usable PnL history for this account yet.")
        return

    now_utc = _utc_now()
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    day7_start = now_utc - timedelta(days=6)
    day30_start = now_utc - timedelta(days=29)
    three_year_start = now_utc - timedelta(days=365 * 3)
    pnl_currency = result.headline_currency

    cards_a = st.columns(4)
    cards_a[0].metric("Available balance", _format_usd(available_balance))
    cards_a[1].metric("Today's PnL", _format_pnl(_metric_period_total(result.daily_df, today_start), pnl_currency))
    cards_a[2].metric("7D PnL", _format_pnl(_metric_period_total(result.daily_df, day7_start), pnl_currency))
    cards_a[3].metric("30D PnL", _format_pnl(_metric_period_total(result.daily_df, day30_start), pnl_currency))

    cards_b = st.columns(4)
    cards_b[0].metric(
        _period_label("YTD PnL", result.completeness["ytd"]),
        _format_pnl(result.period_totals["ytd"], pnl_currency),
    )
    cards_b[1].metric(
        _period_label("1Y PnL", result.completeness["one_year"]),
        _format_pnl(result.period_totals["one_year"], pnl_currency),
    )
    cards_b[2].metric(
        _period_label("3Y PnL", _period_complete(result, three_year_start)),
        _format_pnl(_metric_period_total(result.daily_df, three_year_start), pnl_currency),
    )
    cards_b[3].metric(
        _period_label("Lifetime (ITD) PnL", result.completeness["itd"]),
        _format_pnl(result.period_totals["itd"], pnl_currency),
    )

    controls = st.columns([2, 3])
    preset = controls[0].radio("Range", RANGE_PRESETS, horizontal=True, key="pnl_range")
    min_date = _to_utc_date(result.coverage_start)
    max_date = _to_utc_date(result.coverage_end)
    default_start, default_end = _range_bounds("1Y", coverage_start=result.coverage_start, coverage_end=result.coverage_end)
    slider_value = controls[1].slider(
        "Custom range",
        min_value=min_date,
        max_value=max_date,
        value=(_to_utc_date(default_start), _to_utc_date(default_end)),
        format="YYYY-MM-DD",
        key="pnl_range_slider",
    )

    if preset == "Custom":
        start_dt = _date_to_utc(slider_value[0])
        end_dt = _date_to_utc(slider_value[1], end_of_day=True)
    else:
        start_dt, end_dt = _range_bounds(preset, coverage_start=result.coverage_start, coverage_end=result.coverage_end)
        start_dt = max(start_dt, result.coverage_start.to_pydatetime())

    selected_income = _filter_income_frame(result.income_df, start_dt, end_dt)
    selected_daily = _complete_daily_frame(_filter_daily_frame(result.daily_df, start_dt, end_dt), start_dt, end_dt)
    baseline_balance = _baseline_balance(result)
    stats = _build_period_stats(selected_income, selected_daily, baseline_balance=baseline_balance)

    st.subheader("Profit and Loss Analysis")
    st.caption(
        f"Selected range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')} | "
        f"Coverage starts: {result.coverage_start.strftime('%Y-%m-%d')}"
    )
    _render_stat_grid(stats, pnl_currency)

    chart_cols = st.columns(2)
    chart_cols[0].subheader("Daily net PnL")
    daily_chart = selected_daily.rename(columns={"date": "Date", "net_pnl": "Daily net PnL"}).set_index("Date")
    chart_cols[0].bar_chart(daily_chart[["Daily net PnL"]], use_container_width=True)

    chart_cols[1].subheader("Cumulative PnL")
    cumulative_chart = selected_daily.rename(columns={"date": "Date", "cumulative_pnl": "Cumulative PnL"}).set_index("Date")
    chart_cols[1].line_chart(cumulative_chart[["Cumulative PnL"]], use_container_width=True)

    benchmark_options: list[str] = []
    for symbol in result.symbol_totals_df.get("symbol", pd.Series(dtype=str)).head(6).tolist():
        if symbol and symbol not in benchmark_options:
            benchmark_options.append(symbol)
    for symbol in PNL_BENCHMARKS:
        if symbol not in benchmark_options:
            benchmark_options.append(symbol)

    benchmark_symbols = st.multiselect(
        "Benchmarks",
        options=benchmark_options,
        default=[symbol for symbol in PNL_BENCHMARKS if symbol in benchmark_options][:2],
        key="pnl_benchmarks",
    )
    if benchmark_symbols:
        with st.spinner("Loading benchmark comparison..."):
            compare_df = _build_benchmark_comparison(result, selected_daily, benchmark_symbols=benchmark_symbols)
        if not compare_df.empty:
            st.subheader("Cumulative PnL % vs benchmarks")
            st.caption("PnL % uses current wallet balance as the baseline, so treat it as directional rather than an exact Binance website clone.")
            compare_chart = compare_df.rename(columns={"date": "Date"}).set_index("Date")
            st.line_chart(compare_chart, use_container_width=True)

    asset_cols = st.columns(2)
    asset_cols[0].subheader("Current asset balances")
    if result.current_balances_df.empty:
        asset_cols[0].info("No non-zero balances returned.")
    else:
        asset_cols[0].bar_chart(result.current_balances_df.set_index("asset")[["wallet_balance"]], use_container_width=True)
        asset_cols[0].dataframe(result.current_balances_df, use_container_width=True, hide_index=True)

    asset_cols[1].subheader("Selected-range PnL by asset")
    if selected_income.empty:
        asset_cols[1].info("No PnL rows in this range.")
    else:
        selected_asset_totals = (
            selected_income.groupby("asset", as_index=False)
            .agg(net_pnl=("income", "sum"), events=("income", "size"))
            .sort_values("net_pnl", ascending=False)
        )
        asset_cols[1].bar_chart(selected_asset_totals.set_index("asset")[["net_pnl"]], use_container_width=True)
        asset_cols[1].dataframe(selected_asset_totals, use_container_width=True, hide_index=True)

    tabs = st.tabs(["Income Types", "Symbols", "Transactions", "Coverage"])

    with tabs[0]:
        if selected_income.empty:
            st.info("No income rows in this range.")
        else:
            income_type_totals = (
                selected_income.groupby("incomeType", as_index=False)
                .agg(net_pnl=("income", "sum"), events=("income", "size"))
                .sort_values("net_pnl", ascending=False)
            )
            st.dataframe(income_type_totals, use_container_width=True, hide_index=True)

    with tabs[1]:
        if selected_income.empty:
            st.info("No symbol-level PnL rows in this range.")
        else:
            symbol_totals = (
                selected_income[selected_income["symbol"] != ""]
                .groupby("symbol", as_index=False)
                .agg(net_pnl=("income", "sum"), events=("income", "size"))
                .sort_values("net_pnl", ascending=False)
            )
            st.dataframe(symbol_totals, use_container_width=True, hide_index=True)

    with tabs[2]:
        st.dataframe(selected_income.sort_values("time", ascending=False), use_container_width=True, hide_index=True)

    with tabs[3]:
        for note in result.notes:
            st.write(f"- {note}")
        if not result.notes:
            st.write("- No additional coverage notes.")
        st.write(f"- Earliest loaded PnL event: {result.coverage_start.strftime('%Y-%m-%d')}")
        st.write(f"- Latest loaded PnL event: {result.coverage_end.strftime('%Y-%m-%d')}")
        st.write(f"- Current benchmark baseline: {_format_pnl(baseline_balance, pnl_currency)}")


st.caption("Switch between the breakout scanner, the new cross-asset screener, and the Binance PnL dashboard.")
dashboard_mode = st.radio("Dashboard", ("Breakouts", "Screener", "PnL"), horizontal=True)

if dashboard_mode == "Breakouts":
    render_breakout_dashboard()
elif dashboard_mode == "Screener":
    render_screener_dashboard()
else:
    render_pnl_dashboard()
