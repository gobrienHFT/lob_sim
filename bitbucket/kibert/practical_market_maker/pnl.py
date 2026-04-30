from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from binance_futures import BinanceFuturesPublic

BINANCE_FUTURES_START_YEAR = 2019
MIN_WINDOW_MS = 60 * 60 * 1000
TRANSFER_INCOME_TYPES = {
    "TRANSFER",
    "INTERNAL_TRANSFER",
    "CROSS_COLLATERAL_TRANSFER",
    "COIN_SWAP_DEPOSIT",
    "COIN_SWAP_WITHDRAW",
    "STRATEGY_UMFUTURES_TRANSFER",
}
USD_LIKE_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP"}
INCOME_COLUMNS = ["time", "incomeType", "income", "asset", "symbol", "info", "tranId", "tradeId"]


@dataclass(frozen=True)
class PnLDashboardResult:
    account: dict[str, Any]
    income_df: pd.DataFrame
    weekly_df: pd.DataFrame
    daily_df: pd.DataFrame
    period_totals: dict[str, float]
    completeness: dict[str, bool]
    asset_totals_df: pd.DataFrame
    income_type_totals_df: pd.DataFrame
    symbol_totals_df: pd.DataFrame
    current_balances_df: pd.DataFrame
    notes: list[str]
    coverage_start: pd.Timestamp | None
    coverage_end: pd.Timestamp | None
    headline_currency: str | None


def build_pnl_dashboard_data(
    client: BinanceFuturesPublic,
    *,
    api_key: str,
    cache_root: str,
    now: datetime | None = None,
    recent_lookback_days: int = 90,
    max_export_year_fetches: int = 5,
) -> PnLDashboardResult:
    now_utc = now or datetime.now(timezone.utc)
    recent_cutoff = now_utc - timedelta(days=max(7, int(recent_lookback_days)))
    notes: list[str] = []

    account = client.account_information_v3()
    recent_df = _normalize_income_frame(
        pd.DataFrame(_fetch_income_window(client, _millis(recent_cutoff), _millis(now_utc)))
    )

    yearly_frames, available_years, missing_years, fetch_notes = _load_yearly_income_history(
        client=client,
        api_key=api_key,
        cache_root=cache_root,
        now_utc=now_utc,
        recent_cutoff=recent_cutoff,
        max_export_year_fetches=max_export_year_fetches,
    )
    notes.extend(fetch_notes)

    combined = _normalize_income_frame(pd.concat([recent_df, *yearly_frames], ignore_index=True))
    combined = _dedupe_income_frame(combined)
    pnl_df = combined[~combined["incomeType"].isin(TRANSFER_INCOME_TYPES)].copy()

    headline_df, headline_currency, asset_note = _headline_frame(pnl_df)
    if asset_note:
        notes.append(asset_note)

    week_start = _week_start(now_utc)
    ytd_start = datetime(now_utc.year, 1, 1, tzinfo=timezone.utc)
    one_year_start = now_utc - timedelta(days=365)
    if not headline_df.empty:
        coverage_start = headline_df["time"].min()
    elif not pnl_df.empty:
        coverage_start = pnl_df["time"].min()
    else:
        coverage_start = None

    period_totals = {
        "weekly": _sum_since(headline_df, week_start),
        "ytd": _sum_since(headline_df, ytd_start),
        "one_year": _sum_since(headline_df, one_year_start),
        "itd": float(headline_df["income"].sum()) if not headline_df.empty else 0.0,
    }
    completeness = {
        "weekly": True,
        "ytd": _period_is_complete(ytd_start, recent_cutoff, available_years),
        "one_year": _period_is_complete(one_year_start, recent_cutoff, available_years),
        "itd": _period_is_complete(
            datetime(BINANCE_FUTURES_START_YEAR, 1, 1, tzinfo=timezone.utc),
            recent_cutoff,
            available_years,
        ),
    }

    if not completeness["itd"]:
        notes.append(
            "Lifetime/ITD PnL is partial until the yearly Binance export cache covers every year back to 2019. "
            "Binance only allows 5 transaction export requests per month."
        )
    if missing_years:
        missing_label = ", ".join(str(year) for year in missing_years[:5])
        notes.append(f"Older yearly export coverage is still missing for: {missing_label}.")
    if headline_df.empty and not pnl_df.empty:
        notes.append("Headline totals are hidden because Binance returned multiple non-USD income assets.")
    if pnl_df.empty:
        notes.append("No Binance futures income records were returned for the current account.")

    weekly_df = _weekly_totals(headline_df).tail(16)
    daily_df = _daily_totals(headline_df)
    asset_totals_df = _aggregate_totals(pnl_df, "asset", "asset")
    income_type_totals_df = _aggregate_totals(pnl_df, "incomeType", "income_type")
    symbol_totals_df = _aggregate_totals(pnl_df[pnl_df["symbol"] != ""], "symbol", "symbol")
    current_balances_df = _current_balances(account)

    return PnLDashboardResult(
        account=account,
        income_df=pnl_df.sort_values("time", ascending=False).reset_index(drop=True),
        weekly_df=weekly_df,
        daily_df=daily_df,
        period_totals=period_totals,
        completeness=completeness,
        asset_totals_df=asset_totals_df,
        income_type_totals_df=income_type_totals_df,
        symbol_totals_df=symbol_totals_df,
        current_balances_df=current_balances_df,
        notes=notes,
        coverage_start=coverage_start,
        coverage_end=headline_df["time"].max() if not headline_df.empty else None,
        headline_currency=headline_currency,
    )


def _fetch_income_window(
    client: BinanceFuturesPublic,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    rows = client.income_history(start_time=start_ms, end_time=end_ms, limit=limit)
    if len(rows) < limit or (end_ms - start_ms) <= MIN_WINDOW_MS:
        return rows

    midpoint = start_ms + ((end_ms - start_ms) // 2)
    left = _fetch_income_window(client, start_ms, midpoint, limit=limit)
    right = _fetch_income_window(client, midpoint + 1, end_ms, limit=limit)
    return left + right


def _load_yearly_income_history(
    *,
    client: BinanceFuturesPublic,
    api_key: str,
    cache_root: str,
    now_utc: datetime,
    recent_cutoff: datetime,
    max_export_year_fetches: int,
) -> tuple[list[pd.DataFrame], set[int], list[int], list[str]]:
    notes: list[str] = []
    cache_dir = Path(cache_root).expanduser() / _api_key_fingerprint(api_key)
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_years = list(range(recent_cutoff.year, BINANCE_FUTURES_START_YEAR - 1, -1))
    refresh_candidates = [
        year
        for year in all_years
        if not _year_cache_covers(cache_dir, year, now_utc=now_utc, recent_cutoff=recent_cutoff)
    ]

    for year in refresh_candidates[: max(0, int(max_export_year_fetches))]:
        try:
            _refresh_year_cache(client, cache_dir, year, now_utc=now_utc)
        except Exception as exc:
            notes.append(f"Stopped refreshing Binance yearly exports at {year}: {exc}")
            break

    available_years = {
        year
        for year in all_years
        if _year_cache_covers(cache_dir, year, now_utc=now_utc, recent_cutoff=recent_cutoff)
    }
    missing_years = [year for year in all_years if year not in available_years]
    frames = [_read_year_cache(cache_dir, year) for year in sorted(available_years, reverse=True)]
    return frames, available_years, missing_years, notes


def _refresh_year_cache(client: BinanceFuturesPublic, cache_dir: Path, year: int, *, now_utc: datetime) -> None:
    start_dt = datetime(year, 1, 1, tzinfo=timezone.utc)
    end_dt = min(now_utc, datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(milliseconds=1))
    download_id = client.request_income_download_id(_millis(start_dt), _millis(end_dt))
    download_url = client.wait_for_income_download_url(download_id)
    raw_payload = client.download_file_bytes(download_url)
    df = _parse_export_payload(raw_payload)

    csv_path = cache_dir / f"{year}.csv"
    meta_path = cache_dir / f"{year}.meta.json"
    df.to_csv(csv_path, index=False)
    meta_path.write_text(json.dumps({"fetched_through_ms": _millis(end_dt)}), encoding="utf-8")


def _read_year_cache(cache_dir: Path, year: int) -> pd.DataFrame:
    csv_path = cache_dir / f"{year}.csv"
    if not csv_path.exists():
        return _empty_income_frame()
    try:
        df = pd.read_csv(csv_path)
    except pd.errors.EmptyDataError:
        return _empty_income_frame()
    return _normalize_income_frame(df)


def _year_cache_covers(
    cache_dir: Path,
    year: int,
    *,
    now_utc: datetime,
    recent_cutoff: datetime,
) -> bool:
    csv_path = cache_dir / f"{year}.csv"
    if not csv_path.exists():
        return False

    required_through = _required_coverage_ms(year, now_utc=now_utc, recent_cutoff=recent_cutoff)
    if required_through is None:
        return False

    fetched_through = _read_fetched_through(cache_dir / f"{year}.meta.json")
    if fetched_through is None:
        df = _read_year_cache(cache_dir, year)
        if df.empty:
            return False
        fetched_through = _millis(df["time"].max().to_pydatetime())
    return fetched_through >= required_through


def _required_coverage_ms(year: int, *, now_utc: datetime, recent_cutoff: datetime) -> int | None:
    if year > recent_cutoff.year:
        return None
    if year == recent_cutoff.year:
        return _millis(recent_cutoff)
    year_end = min(now_utc, datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(milliseconds=1))
    return _millis(year_end)


def _read_fetched_through(meta_path: Path) -> int | None:
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = payload.get("fetched_through_ms")
    try:
        return int(value)
    except Exception:
        return None


def _parse_export_payload(raw_payload: bytes) -> pd.DataFrame:
    payload = _extract_payload(raw_payload)
    text = _decode_payload(payload)
    if not text.strip():
        return _empty_income_frame()

    parser_attempts = (
        {"sep": None, "engine": "python"},
        {"sep": ","},
        {"sep": ";"},
        {"sep": "\t"},
    )
    last_error: Exception | None = None
    for kwargs in parser_attempts:
        try:
            df = pd.read_csv(io.StringIO(text), **kwargs)
            return _normalize_income_frame(df)
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return _empty_income_frame()


def _extract_payload(raw_payload: bytes) -> bytes:
    if zipfile.is_zipfile(io.BytesIO(raw_payload)):
        with zipfile.ZipFile(io.BytesIO(raw_payload)) as zipped:
            file_names = [name for name in zipped.namelist() if not name.endswith("/")]
            if not file_names:
                return b""
            preferred = next(
                (name for name in file_names if name.lower().endswith((".csv", ".txt"))),
                file_names[0],
            )
            return zipped.read(preferred)
    try:
        return gzip.decompress(raw_payload)
    except OSError:
        return raw_payload


def _decode_payload(raw_payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_payload.decode("utf-8", errors="ignore")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_income_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_income_frame()

    rename_map: dict[str, str] = {}
    aliases = {
        "time": "time",
        "timestamp": "time",
        "datetime": "time",
        "date": "time",
        "incometype": "incomeType",
        "income": "income",
        "pnl": "income",
        "amount": "income",
        "asset": "asset",
        "symbol": "symbol",
        "info": "info",
        "tranid": "tranId",
        "transactionid": "tranId",
        "tradeid": "tradeId",
    }
    for column in df.columns:
        normalized = "".join(ch for ch in str(column).lower() if ch.isalnum())
        if normalized in aliases and aliases[normalized] not in rename_map.values():
            rename_map[column] = aliases[normalized]

    normalized_df = df.rename(columns=rename_map).copy()
    for column in INCOME_COLUMNS:
        if column not in normalized_df.columns:
            normalized_df[column] = ""

    time_series = normalized_df["time"]
    numeric_time = pd.to_numeric(time_series, errors="coerce")
    if numeric_time.notna().sum() >= max(1, int(len(normalized_df) * 0.7)):
        normalized_df["time"] = pd.to_datetime(numeric_time, unit="ms", utc=True, errors="coerce")
    else:
        normalized_df["time"] = pd.to_datetime(time_series, utc=True, errors="coerce")

    normalized_df["income"] = pd.to_numeric(
        normalized_df["income"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    for column in ("incomeType", "asset", "symbol", "info", "tranId", "tradeId"):
        normalized_df[column] = normalized_df[column].fillna("").astype(str).str.strip()
    normalized_df["incomeType"] = normalized_df["incomeType"].str.upper()
    normalized_df["asset"] = normalized_df["asset"].str.upper()
    normalized_df["symbol"] = normalized_df["symbol"].str.upper()

    normalized_df = normalized_df.dropna(subset=["time", "income"]).copy()
    normalized_df = normalized_df[INCOME_COLUMNS].sort_values("time").reset_index(drop=True)
    return normalized_df


def _empty_income_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=INCOME_COLUMNS)


def _dedupe_income_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_income_frame()
    deduped = df.drop_duplicates(
        subset=["time", "incomeType", "income", "asset", "symbol", "info", "tranId", "tradeId"]
    )
    return deduped.sort_values("time").reset_index(drop=True)


def _headline_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None, str | None]:
    if df.empty:
        return df.copy(), "USD-like", None

    assets = sorted(asset for asset in df["asset"].dropna().unique() if asset)
    if not assets:
        return df.copy(), "USD-like", None
    if set(assets).issubset(USD_LIKE_ASSETS):
        return df.copy(), "USD-like", None
    if len(assets) == 1:
        return df.copy(), assets[0], None

    usd_like_df = df[df["asset"].isin(USD_LIKE_ASSETS)].copy()
    if not usd_like_df.empty:
        return (
            usd_like_df,
            "USD-like",
            "Headline totals exclude non-USD income assets. See the asset table below for the full by-asset breakdown.",
        )
    return _empty_income_frame(), None, None


def _aggregate_totals(df: pd.DataFrame, group_col: str, label_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[label_col, "net_pnl", "events"])
    grouped = (
        df.groupby(group_col, dropna=False)
        .agg(net_pnl=("income", "sum"), events=("income", "size"))
        .reset_index()
        .rename(columns={group_col: label_col})
    )
    grouped[label_col] = grouped[label_col].replace("", "(blank)")
    grouped["abs_net_pnl"] = grouped["net_pnl"].abs()
    grouped = grouped.sort_values(["abs_net_pnl", "net_pnl"], ascending=False).drop(columns=["abs_net_pnl"])
    return grouped.reset_index(drop=True)


def _weekly_totals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["week_start", "net_pnl"])
    weekly = df.copy()
    weekly["week_start"] = weekly["time"].dt.normalize() - pd.to_timedelta(weekly["time"].dt.weekday, unit="D")
    grouped = weekly.groupby("week_start", as_index=False).agg(net_pnl=("income", "sum"))
    return grouped.sort_values("week_start").reset_index(drop=True)


def _daily_totals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "net_pnl", "cumulative_pnl", "positive_pnl", "negative_pnl", "events"])

    daily = df.copy()
    daily["date"] = daily["time"].dt.normalize()
    grouped = (
        daily.groupby("date", as_index=False)
        .agg(
            net_pnl=("income", "sum"),
            positive_pnl=("income", lambda s: float(s[s > 0].sum())),
            negative_pnl=("income", lambda s: float(s[s < 0].sum())),
            events=("income", "size"),
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    grouped["cumulative_pnl"] = grouped["net_pnl"].cumsum()
    return grouped


def _current_balances(account: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in account.get("assets", []):
        wallet_balance = _to_float(item.get("walletBalance"))
        margin_balance = _to_float(item.get("marginBalance"))
        available_balance = _to_float(item.get("availableBalance"))
        unrealized_profit = _to_float(item.get("unrealizedProfit"))
        if all(abs(value) < 1e-12 for value in (wallet_balance, margin_balance, available_balance, unrealized_profit)):
            continue
        rows.append(
            {
                "asset": str(item.get("asset", "")).upper(),
                "wallet_balance": wallet_balance,
                "margin_balance": margin_balance,
                "available_balance": available_balance,
                "unrealized_profit": unrealized_profit,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["asset", "wallet_balance", "margin_balance", "available_balance", "unrealized_profit"]
        )
    df = pd.DataFrame(rows)
    df["abs_wallet_balance"] = df["wallet_balance"].abs()
    return df.sort_values(["abs_wallet_balance", "wallet_balance"], ascending=False).drop(columns=["abs_wallet_balance"])


def _sum_since(df: pd.DataFrame, start_dt: datetime) -> float:
    if df.empty:
        return 0.0
    return float(df[df["time"] >= pd.Timestamp(start_dt)]["income"].sum())


def _period_is_complete(start_dt: datetime, recent_cutoff: datetime, available_years: set[int]) -> bool:
    if start_dt >= recent_cutoff:
        return True
    needed_years = set(range(start_dt.year, recent_cutoff.year + 1))
    return needed_years.issubset(available_years)


def _week_start(now_utc: datetime) -> datetime:
    start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    return start - timedelta(days=start.weekday())


def _api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]


def _millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
