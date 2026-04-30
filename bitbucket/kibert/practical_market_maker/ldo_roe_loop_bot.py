from __future__ import annotations

import argparse
import builtins
import os
import time
from collections import deque
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, getcontext
from pathlib import Path
from typing import Any

from binance_futures import BinanceFuturesPublic, BinanceHTTPError

getcontext().prec = 28

APP_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOL = "AUTO"
DEFAULT_LEVERAGE = 5
DEFAULT_MIN_PROFIT_LEVERAGE = 2
DEFAULT_TAKE_PROFIT_ROE = Decimal("0.05")
DEFAULT_STOP_LOSS_ROE = Decimal("0.90")
DEFAULT_EQUITY_STOP = Decimal("10000000")
DEFAULT_MAKER_FEE_RATE = Decimal("0.0002")
DEFAULT_MIN_NET_PROFIT_USDT = Decimal("0.03")
DEFAULT_MIN_REALIZED_EQUITY_PROFIT_USDT = Decimal("0")
DEFAULT_MIN_MARGIN_UTILIZATION_PCT = Decimal("95")
DEFAULT_GREEN_EXIT_AFTER_SECONDS = 600.0
DEFAULT_GREEN_EXIT_MIN_ROE = Decimal("0")
DEFAULT_MAX_HOLD_SECONDS = 3600.0
DEFAULT_FLAT_EXIT_ROE = Decimal("0.03")
DEFAULT_MIN_PROFIT_DISABLE_TIME_EXITS = True
DEFAULT_ENTRY_MAX_CHASE_PCT = Decimal("0.50")
DEFAULT_ENTRY_ABANDON_COOLDOWN_SECONDS = 900.0
DEFAULT_NO_CANDIDATE_RETRY_SECONDS = 10.0
DEFAULT_REALIZED_FLAT_COOLDOWN_SECONDS = 60.0
DEFAULT_SAFETY_RECENT_VOL_MINUTES = 10
DEFAULT_SAFETY_MIN_RECENT_QUOTE_VOLUME = Decimal("50000")
DEFAULT_SAFETY_MIN_RECENT_VOLATILITY_PCT = Decimal("0.50")
DEFAULT_SAFETY_MAX_ADVERSE_VOL_RATIO = Decimal("1.20")
DEFAULT_SAFETY_LIQUIDITY_WINDOW_SECONDS = 30.0
DEFAULT_SAFETY_LIQUIDITY_SAMPLES = 4
DEFAULT_SAFETY_MAX_DEPTH_DROP_PCT = Decimal("35")
DEFAULT_SAFETY_MAX_SPREAD_WIDEN_MULTIPLE = Decimal("2")
DEFAULT_SAFETY_FUNDING_BUFFER_SECONDS = 300.0
DEFAULT_MAX_SAME_SYMBOL_STREAK = 2
DEFAULT_SAME_SYMBOL_COOLDOWN_SECONDS = 3600.0
DEFAULT_MIN_PROFIT_SCAN_MIN_QUOTE_VOLUME = Decimal("250000000")
DEFAULT_MIN_PROFIT_SCAN_MAX_SPREAD_PCT = Decimal("0.03")
DEFAULT_MIN_PROFIT_SCAN_MIN_DEPTH_1PCT = Decimal("500000")
DEFAULT_MIN_PROFIT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "DOGEUSDT")
DEFAULT_MIN_PROFIT_MAX_MIN_NOTIONAL_LEVERAGE = 20
DEFAULT_FLOW_REFERENCE_SYMBOL = "BTCUSDT"
DEFAULT_FLOW_LOOKBACK_HOURS = 1
DEFAULT_SCAN_MIN_FUNDING_RATE = Decimal("0")
DEFAULT_SCAN_MIN_VOLUME_MULTIPLE = Decimal("2")
DEFAULT_SCAN_MIN_RECENT_VOLUME_RATIO = Decimal("1")
DEFAULT_SCAN_RECENT_HOURS = 3
DEFAULT_SCAN_MIN_QUOTE_VOLUME = Decimal("1000000")
DEFAULT_SCAN_MAX_SPREAD_PCT = Decimal("0.20")
DEFAULT_SCAN_MIN_DEPTH_1PCT = Decimal("2500")
DEFAULT_SCAN_MIN_DEPTH_TO_VOLUME_PCT = Decimal("0.002")
DEFAULT_SCAN_MAX_SYMBOLS = 80
UNTRADABLE_ERROR_CODES = {-2027, -4003, -4046, -4141, -4164, -5021}
TRADFI_UNDERLYING_TYPES = {"TRADFI", "COMMODITY", "EQUITY", "INDEX"}


class EntryAbandoned(RuntimeError):
    pass


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    tick_size: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True)
class PlannedTrade:
    symbol: str
    direction: str
    leverage: int
    equity: Decimal
    available_balance: Decimal
    mark_price: Decimal
    quantity: Decimal
    notional: Decimal
    take_profit_price: Decimal
    stop_loss_price: Decimal
    take_profit_roe: Decimal
    stop_loss_roe: Decimal
    take_profit_mode: str
    entry_fee_rate: Decimal
    exit_fee_rate: Decimal
    min_net_profit: Decimal


@dataclass(frozen=True)
class IncomeTotals:
    realized_pnl: Decimal
    commission: Decimal
    funding_fee: Decimal
    other: Decimal

    @property
    def net(self) -> Decimal:
        return self.realized_pnl + self.commission + self.funding_fee + self.other

    @property
    def has_activity(self) -> bool:
        return any(
            value != 0
            for value in (self.realized_pnl, self.commission, self.funding_fee, self.other)
        )


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    direction: str
    outcome: str
    started_at: float
    ended_at: float
    duration_seconds: float
    equity_before: Decimal
    equity_after: Decimal
    equity_delta: Decimal
    pnl: Decimal
    pnl_source: str
    realized_pnl: Decimal
    commission: Decimal
    funding_fee: Decimal
    income_net: Decimal


_RAW_PRINT = builtins.print


class RuntimeStats:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.start_equity: Decimal | None = None
        self.current_equity: Decimal | None = None
        self.trades: list[TradeRecord] = []

    def observe_equity(self, equity: Decimal) -> None:
        if self.start_equity is None:
            self.start_equity = equity
        self.current_equity = equity

    def record_trade(self, record: TradeRecord) -> None:
        self.observe_equity(record.equity_after)
        self.trades.append(record)

    def _window(self, seconds: float) -> list[TradeRecord]:
        cutoff = time.time() - seconds
        return [record for record in self.trades if record.ended_at >= cutoff]

    @staticmethod
    def _sum_delta(records: list[TradeRecord]) -> Decimal:
        total = Decimal("0")
        for record in records:
            total += record.pnl
        return total

    @staticmethod
    def _average_delta(records: list[TradeRecord]) -> Decimal:
        if not records:
            return Decimal("0")
        return RuntimeStats._sum_delta(records) / Decimal(len(records))

    @staticmethod
    def _win_count(records: list[TradeRecord]) -> int:
        return sum(1 for record in records if record.pnl > 0)

    def lines(self) -> list[str]:
        now = time.time()
        uptime_seconds = max(1.0, now - self.started_at)
        last_hour = self._window(3600)
        last_day = self._window(86400)
        total_pnl = self._sum_delta(self.trades)
        hourly_pnl = self._sum_delta(last_hour)
        daily_pnl = self._sum_delta(last_day)
        projected_daily = (total_pnl / Decimal(str(uptime_seconds))) * Decimal("86400")
        session_trades_per_hour = (Decimal(len(self.trades)) / Decimal(str(uptime_seconds))) * Decimal("3600")
        wins = self._win_count(self.trades)
        losses = sum(1 for record in self.trades if record.pnl < 0)
        flat = len(self.trades) - wins - losses
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at))
        uptime = _format_duration(uptime_seconds)
        start_equity = self.start_equity if self.start_equity is not None else Decimal("0")
        current_equity = self.current_equity if self.current_equity is not None else start_equity

        lines = [
            "PNL ANALYSIS - press D for main log",
            "",
            f"Started: {started}",
            f"Uptime: {uptime}",
            f"Start equity: {_format_decimal(start_equity)} USDT",
            f"Current equity: {_format_decimal(current_equity)} USDT",
            f"Session equity delta: {_format_decimal(current_equity - start_equity)} USDT",
            "",
            f"Closed trades: {len(self.trades)} total, {wins} win, {losses} loss, {flat} flat",
            f"Session PnL: {_format_decimal(total_pnl)} USDT",
            f"Last 1h PnL: {_format_decimal(hourly_pnl)} USDT",
            f"Last 24h PnL: {_format_decimal(daily_pnl)} USDT",
            f"Projected daily PnL at current session pace: {_format_decimal(projected_daily)} USDT",
            f"Avg profit/trade: {_format_decimal(self._average_delta(self.trades))} USDT",
            f"Trades last 1h: {len(last_hour)}",
            f"Trades/hour, session avg: {_format_decimal(session_trades_per_hour)}",
        ]

        if self.trades:
            latest = self.trades[-1]
            lines.extend(
                [
                    "",
                    "Latest trade:",
                    (
                        f"{latest.symbol} {latest.direction} {latest.outcome}, "
                        f"PnL {_format_decimal(latest.pnl)} USDT ({latest.pnl_source}), "
                        f"duration {_format_duration(latest.duration_seconds)}"
                    ),
                    (
                        f"Income ledger: realized {_format_decimal(latest.realized_pnl)}, "
                        f"commission {_format_decimal(latest.commission)}, "
                        f"funding {_format_decimal(latest.funding_fee)}, "
                        f"net {_format_decimal(latest.income_net)}"
                    ),
                ]
            )

        by_symbol: dict[str, list[TradeRecord]] = {}
        for record in self.trades:
            by_symbol.setdefault(record.symbol, []).append(record)
        if by_symbol:
            lines.extend(["", "By symbol:"])
            for symbol in sorted(by_symbol):
                records = by_symbol[symbol]
                pnl = self._sum_delta(records)
                avg = self._average_delta(records)
                lines.append(
                    f"{symbol}: {len(records)} trades, PnL {_format_decimal(pnl)} USDT, "
                    f"avg {_format_decimal(avg)} USDT/trade"
                )

        recent = self.trades[-8:]
        if recent:
            lines.extend(["", "Recent trades:"])
            for record in reversed(recent):
                ended = time.strftime("%H:%M:%S", time.localtime(record.ended_at))
                lines.append(
                    f"{ended} {record.symbol} {record.outcome} "
                    f"{_format_decimal(record.pnl)} USDT "
                    f"({_format_duration(record.duration_seconds)})"
                )
        return lines


class ConsoleView:
    def __init__(self, stats: RuntimeStats) -> None:
        self.stats = stats
        self.mode = "log"
        self.logs: deque[str] = deque(maxlen=240)
        self.symbol_input_active = False
        self.symbol_buffer: list[str] = []
        self.requested_symbol: str | None = None
        self._last_render_at = 0.0
        try:
            import msvcrt  # type: ignore[import-not-found]
        except ImportError:
            self._msvcrt = None
        else:
            self._msvcrt = msvcrt

    def print(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("file") not in (None,):
            _RAW_PRINT(*args, **kwargs)
            return
        sep = str(kwargs.get("sep", " "))
        end = str(kwargs.get("end", "\n"))
        message = sep.join(str(arg) for arg in args)
        for line in (message + end).splitlines():
            if line:
                self.logs.append(line)
        if self.mode == "log":
            _RAW_PRINT(*args, **kwargs)
        else:
            self.render_stats(throttle=True)

    def poll_hotkeys(self) -> None:
        if self._msvcrt is None:
            return
        while self._msvcrt.kbhit():
            key = self._msvcrt.getwch()
            if self.symbol_input_active:
                self.handle_symbol_key(key)
                continue
            lowered = key.lower()
            if lowered == "f":
                self.mode = "stats"
                self.render_stats(force=True)
            elif lowered == "d":
                self.mode = "log"
                self.render_log()
            elif lowered == "s":
                self.symbol_input_active = True
                self.symbol_buffer = []
                self.render_symbol_prompt()

    def handle_symbol_key(self, key: str) -> None:
        if key in ("\r", "\n"):
            symbol = "".join(self.symbol_buffer).strip().upper()
            if symbol:
                self.requested_symbol = symbol
            self.symbol_input_active = False
            self.mode = "log"
            self.render_log()
            if symbol:
                print(f"Symbol switch requested: {symbol}. Will apply before the next entry.")
            return
        if key == "\x1b":
            self.symbol_input_active = False
            self.mode = "log"
            self.render_log()
            print("Symbol switch cancelled.")
            return
        if key == "\b":
            if self.symbol_buffer:
                self.symbol_buffer.pop()
            self.render_symbol_prompt()
            return
        if key.isprintable() and len(key) == 1:
            candidate = key.upper()
            if candidate.isalnum():
                self.symbol_buffer.append(candidate)
                self.render_symbol_prompt()

    def pop_symbol_request(self) -> str | None:
        symbol = self.requested_symbol
        self.requested_symbol = None
        return symbol

    def push_symbol_request(self, symbol: str) -> None:
        self.requested_symbol = symbol.upper()

    def render_symbol_prompt(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")
        _RAW_PRINT("SYMBOL SWITCH - type ticker then press Enter")
        _RAW_PRINT("Esc cancels. Example: LDOUSDT")
        _RAW_PRINT("")
        _RAW_PRINT("Ticker: " + "".join(self.symbol_buffer))

    def render_stats(self, *, force: bool = False, throttle: bool = False) -> None:
        now = time.monotonic()
        if throttle and not force and now - self._last_render_at < 1.0:
            return
        self._last_render_at = now
        os.system("cls" if os.name == "nt" else "clear")
        for line in self.stats.lines():
            _RAW_PRINT(line)

    def render_log(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")
        _RAW_PRINT("MAIN LOG - press F for PnL analysis, S to switch symbol")
        _RAW_PRINT("")
        for line in list(self.logs)[-80:]:
            _RAW_PRINT(line)

    def refresh(self) -> None:
        self.poll_hotkeys()
        if self.mode == "stats":
            self.render_stats(throttle=True)


STATS = RuntimeStats()
CONSOLE = ConsoleView(STATS)


def print(*args: Any, **kwargs: Any) -> None:  # type: ignore[override]
    CONSOLE.print(*args, **kwargs)


def poll_console() -> None:
    CONSOLE.refresh()


def pop_requested_symbol() -> str | None:
    return CONSOLE.pop_symbol_request()


def requeue_requested_symbol(symbol: str) -> None:
    CONSOLE.push_symbol_request(symbol)


def bot_sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        poll_console()
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    poll_console()


@dataclass(frozen=True)
class BookTop:
    bid: Decimal
    ask: Decimal


@dataclass(frozen=True)
class VolumeWindows:
    current_24h_quote_volume: Decimal
    previous_24h_quote_volume: Decimal
    volume_multiple: Decimal
    recent_quote_volume: Decimal
    previous_recent_quote_volume: Decimal
    recent_volume_ratio: Decimal


@dataclass(frozen=True)
class LiquidityMetrics:
    spread_pct: Decimal
    bid_depth_1pct: Decimal
    ask_depth_1pct: Decimal
    total_depth_1pct: Decimal
    depth_to_24h_volume_pct: Decimal
    mm_pulled: bool
    note: str


@dataclass(frozen=True)
class BookLiquiditySnapshot:
    spread_pct: Decimal
    bid_depth_1pct: Decimal
    ask_depth_1pct: Decimal


@dataclass(frozen=True)
class SymbolCandidate:
    symbol: str
    price_change_pct: Decimal
    funding_rate: Decimal
    next_funding_time: int
    current_24h_quote_volume: Decimal
    previous_24h_quote_volume: Decimal
    volume_multiple: Decimal
    recent_volume_ratio: Decimal
    spread_pct: Decimal
    bid_depth_1pct: Decimal
    ask_depth_1pct: Decimal
    depth_to_24h_volume_pct: Decimal
    score: Decimal


@dataclass(frozen=True)
class SymbolMeta:
    symbol: str
    underlying_type: str
    contract_type: str


@dataclass(frozen=True)
class FundingSnapshot:
    funding_rate: Decimal
    next_funding_time: int


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


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def parse_symbol_list(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text or text.upper() == "ALL":
        return set()
    return {part.strip().upper() for part in text.split(",") if part.strip()}


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator > 0:
        return numerator / denominator
    if numerator > 0:
        return Decimal("999999")
    return Decimal("0")


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def symbol_rules(client: BinanceFuturesPublic, symbol: str) -> SymbolRules:
    info = client.exchange_info()
    for item in info.get("symbols", []):
        if str(item.get("symbol", "")).upper() != symbol.upper():
            continue
        filters = {str(f.get("filterType")): f for f in item.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE", {})
        min_notional_filter = filters.get("MIN_NOTIONAL", {})
        return SymbolRules(
            symbol=symbol.upper(),
            tick_size=_decimal(price_filter.get("tickSize"), "0.0001"),
            qty_step=_decimal(lot_filter.get("stepSize"), "1"),
            min_qty=_decimal(lot_filter.get("minQty"), "0"),
            min_notional=_decimal(min_notional_filter.get("notional"), "5"),
        )
    raise RuntimeError(f"{symbol.upper()} was not found in Binance USD-M futures exchangeInfo.")


def usdt_perp_symbol_meta(client: BinanceFuturesPublic) -> dict[str, SymbolMeta]:
    info = client.exchange_info()
    rows: dict[str, SymbolMeta] = {}
    for item in info.get("symbols", []):
        contract_type = str(item.get("contractType", "")).upper()
        if contract_type not in {"PERPETUAL", "TRADIFI_PERPETUAL"}:
            continue
        if str(item.get("status", "")).upper() != "TRADING":
            continue
        if str(item.get("quoteAsset", "")).upper() != "USDT":
            continue
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        rows[symbol] = SymbolMeta(
            symbol=symbol,
            underlying_type=str(item.get("underlyingType", "")).upper(),
            contract_type=contract_type,
        )
    return rows


def account_equity(account: dict[str, Any]) -> Decimal:
    for key in ("totalMarginBalance", "totalWalletBalance", "totalCrossWalletBalance"):
        value = account.get(key)
        if value not in (None, ""):
            return _decimal(value)
    for asset in account.get("assets", []):
        if str(asset.get("asset", "")).upper() == "USDT":
            for key in ("marginBalance", "walletBalance", "availableBalance"):
                value = asset.get(key)
                if value not in (None, ""):
                    return _decimal(value)
    return Decimal("0")


def account_available_balance(account: dict[str, Any]) -> Decimal:
    value = account.get("availableBalance")
    if value not in (None, ""):
        return _decimal(value)
    for asset in account.get("assets", []):
        if str(asset.get("asset", "")).upper() == "USDT":
            value = asset.get("availableBalance")
            if value not in (None, ""):
                return _decimal(value)
    return account_equity(account)


def maker_fee_rate(client: BinanceFuturesPublic, symbol: str, *, fallback: Decimal) -> Decimal:
    try:
        rates = client.user_commission_rate(symbol)
    except BinanceHTTPError as exc:
        print(f"Commission-rate warning, using fallback maker fee {fallback}: {exc.payload}")
        return fallback
    value = rates.get("makerCommissionRate")
    rate = _decimal(value, str(fallback))
    if rate < 0:
        return fallback
    print(f"Maker fee rate for {symbol}: {rate}")
    return rate


def latest_mark_price(client: BinanceFuturesPublic, symbol: str) -> Decimal:
    rows = client.mark_price(symbol)
    if not rows:
        raise RuntimeError(f"No mark price returned for {symbol}.")
    value = rows[0].get("markPrice") or rows[0].get("indexPrice")
    price = _decimal(value)
    if price <= 0:
        raise RuntimeError(f"Invalid mark price for {symbol}: {value!r}")
    return price


def order_book_top(client: BinanceFuturesPublic, symbol: str) -> BookTop:
    depth = client.depth(symbol, limit=5)
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"No order book returned for {symbol}.")
    bid = _decimal(bids[0][0])
    ask = _decimal(asks[0][0])
    if bid <= 0 or ask <= 0:
        raise RuntimeError(f"Invalid order book top for {symbol}: bid={bid}, ask={ask}")
    return BookTop(bid=bid, ask=ask)


def book_liquidity_snapshot(depth_snapshot: dict[str, Any]) -> BookLiquiditySnapshot | None:
    bids = depth_snapshot.get("bids") or []
    asks = depth_snapshot.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = _decimal(bids[0][0])
    best_ask = _decimal(asks[0][0])
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None

    mid = (best_bid + best_ask) / Decimal("2")
    spread_pct = ((best_ask - best_bid) / mid) * Decimal("100")
    bid_floor = mid * Decimal("0.99")
    ask_ceiling = mid * Decimal("1.01")
    bid_depth = sum(
        _decimal(price) * _decimal(qty)
        for price, qty, *_ in bids
        if _decimal(price) >= bid_floor
    )
    ask_depth = sum(
        _decimal(price) * _decimal(qty)
        for price, qty, *_ in asks
        if _decimal(price) <= ask_ceiling
    )
    return BookLiquiditySnapshot(
        spread_pct=spread_pct,
        bid_depth_1pct=bid_depth,
        ask_depth_1pct=ask_depth,
    )


def quote_volume_windows(
    klines: list[list[Any]],
    *,
    recent_hours: int,
) -> VolumeWindows | None:
    closed_klines = klines[:-1] if len(klines) >= 49 else klines
    quote_volumes = [_decimal(row[7]) for row in closed_klines if len(row) > 7]
    if len(quote_volumes) < 48:
        return None
    current_24h = sum(quote_volumes[-24:], Decimal("0"))
    previous_24h = sum(quote_volumes[-48:-24], Decimal("0"))
    hours = max(1, min(int(recent_hours), 12))
    if len(quote_volumes) < hours * 2:
        return None
    recent = sum(quote_volumes[-hours:], Decimal("0"))
    previous_recent = sum(quote_volumes[-hours * 2 : -hours], Decimal("0"))
    return VolumeWindows(
        current_24h_quote_volume=current_24h,
        previous_24h_quote_volume=previous_24h,
        volume_multiple=_ratio(current_24h, previous_24h),
        recent_quote_volume=recent,
        previous_recent_quote_volume=previous_recent,
        recent_volume_ratio=_ratio(recent, previous_recent),
    )


def liquidity_metrics(
    depth_snapshot: dict[str, Any],
    *,
    quote_volume_24h: Decimal,
    max_spread_pct: Decimal,
    min_depth_1pct: Decimal,
    min_depth_to_volume_pct: Decimal,
) -> LiquidityMetrics | None:
    snapshot = book_liquidity_snapshot(depth_snapshot)
    if snapshot is None:
        return None
    spread_pct = snapshot.spread_pct
    bid_depth = snapshot.bid_depth_1pct
    ask_depth = snapshot.ask_depth_1pct
    total_depth = bid_depth + ask_depth
    depth_to_volume_pct = _ratio(total_depth, quote_volume_24h) * Decimal("100")

    reasons: list[str] = []
    if spread_pct > max_spread_pct:
        reasons.append(f"wide spread {_format_decimal(spread_pct)}%")
    if bid_depth < min_depth_1pct:
        reasons.append(f"thin bid depth {_format_decimal(bid_depth)}")
    if ask_depth < min_depth_1pct:
        reasons.append(f"thin ask depth {_format_decimal(ask_depth)}")
    if depth_to_volume_pct < min_depth_to_volume_pct:
        reasons.append(f"low depth/volume {_format_decimal(depth_to_volume_pct)}%")

    return LiquidityMetrics(
        spread_pct=spread_pct,
        bid_depth_1pct=bid_depth,
        ask_depth_1pct=ask_depth,
        total_depth_1pct=total_depth,
        depth_to_24h_volume_pct=depth_to_volume_pct,
        mm_pulled=bool(reasons),
        note=", ".join(reasons) if reasons else "spread and near-book depth look present",
    )


def funding_snapshots(client: BinanceFuturesPublic) -> dict[str, FundingSnapshot]:
    rows: dict[str, FundingSnapshot] = {}
    for item in client.mark_price():
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        rows[symbol] = FundingSnapshot(
            funding_rate=_decimal(item.get("lastFundingRate")),
            next_funding_time=int(_decimal(item.get("nextFundingTime"))),
        )
    return rows


def funding_seconds_until(snapshot: FundingSnapshot, *, now_ms: int | None = None) -> float | None:
    if snapshot.next_funding_time <= 0:
        return None
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return (snapshot.next_funding_time - current_ms) / 1000.0


def funding_is_too_close(
    snapshot: FundingSnapshot,
    *,
    buffer_seconds: float,
    now_ms: int | None = None,
) -> bool:
    if buffer_seconds <= 0:
        return False
    seconds_until = funding_seconds_until(snapshot, now_ms=now_ms)
    if seconds_until is None:
        return False
    return abs(seconds_until) <= float(buffer_seconds)


def directional_volatility_from_klines(klines: list[list[Any]]) -> tuple[Decimal, Decimal]:
    closed_klines = klines[:-1] if len(klines) > 2 else klines
    closes = [_decimal(row[4]) for row in closed_klines if len(row) > 4 and _decimal(row[4]) > 0]
    upside = Decimal("0")
    downside = Decimal("0")
    for previous, current in zip(closes, closes[1:]):
        if previous <= 0:
            continue
        move = (current - previous) / previous
        if move > 0:
            upside += move
        elif move < 0:
            downside += abs(move)
    return upside, downside


def recent_quote_volume_from_klines(klines: list[list[Any]]) -> Decimal:
    closed_klines = klines[:-1] if len(klines) > 1 else klines
    return sum((_decimal(row[7]) for row in closed_klines if len(row) > 7), Decimal("0"))


def closed_hour_change_pct(klines: list[list[Any]], *, lookback_hours: int) -> Decimal:
    closed_klines = klines[:-1] if len(klines) > 1 else klines
    hours = max(1, int(lookback_hours))
    if len(closed_klines) < hours:
        return Decimal("0")
    rows = closed_klines[-hours:]
    open_price = _decimal(rows[0][1]) if len(rows[0]) > 1 else Decimal("0")
    close_price = _decimal(rows[-1][4]) if len(rows[-1]) > 4 else Decimal("0")
    if open_price <= 0:
        return Decimal("0")
    return ((close_price - open_price) / open_price) * Decimal("100")


def ticker_24h_change_pct(client: BinanceFuturesPublic, symbol: str) -> Decimal:
    symbol = symbol.upper()
    for row in client.ticker_24hr():
        if str(row.get("symbol", "")).upper() == symbol:
            return _decimal(row.get("priceChangePercent"))
    return Decimal("0")


def market_flow_direction(
    client: BinanceFuturesPublic,
    *,
    reference_symbol: str,
    lookback_hours: int,
) -> tuple[str, Decimal, Decimal]:
    symbol = reference_symbol.upper()
    rows = client.klines(symbol, interval="1h", limit=max(3, int(lookback_hours) + 2))
    hourly_change = closed_hour_change_pct(rows, lookback_hours=lookback_hours)
    daily_change = ticker_24h_change_pct(client, symbol)
    direction = "SHORT" if hourly_change < 0 and daily_change < 0 else "LONG"
    return direction, hourly_change, daily_change


def adverse_volatility_reason(
    *,
    direction: str,
    upside: Decimal,
    downside: Decimal,
    max_adverse_ratio: Decimal,
) -> str | None:
    if max_adverse_ratio <= 0:
        return None
    direction = direction.upper()
    if direction == "LONG":
        favorable = upside
        adverse = downside
        label = "downside"
    elif direction == "SHORT":
        favorable = downside
        adverse = upside
        label = "upside"
    else:
        raise ValueError("direction must be LONG or SHORT")

    if adverse <= 0:
        return None
    if favorable <= 0 or adverse > favorable * max_adverse_ratio:
        return (
            f"{label} 1m move pressure dominates "
            f"(up {_format_decimal(upside * Decimal('100'))}%, "
            f"down {_format_decimal(downside * Decimal('100'))}%)"
        )
    return None


def liquidity_decay_reason(
    snapshots: list[BookLiquiditySnapshot],
    *,
    direction: str,
    min_depth_1pct: Decimal,
    max_depth_drop_pct: Decimal,
    max_spread_widen_multiple: Decimal,
) -> str | None:
    if len(snapshots) < 2:
        return None
    first = snapshots[0]
    last = snapshots[-1]
    if last.bid_depth_1pct < min_depth_1pct:
        return f"bid depth fell below minimum ({_format_decimal(last.bid_depth_1pct)} USDT)"
    if last.ask_depth_1pct < min_depth_1pct:
        return f"ask depth fell below minimum ({_format_decimal(last.ask_depth_1pct)} USDT)"

    direction = direction.upper()
    if direction == "LONG":
        first_support = first.bid_depth_1pct
        last_support = last.bid_depth_1pct
        side = "bid"
    elif direction == "SHORT":
        first_support = first.ask_depth_1pct
        last_support = last.ask_depth_1pct
        side = "ask"
    else:
        raise ValueError("direction must be LONG or SHORT")

    if first_support <= 0:
        return f"initial {side} depth was empty"
    depth_drop_pct = ((first_support - last_support) / first_support) * Decimal("100")
    if depth_drop_pct > max_depth_drop_pct:
        return (
            f"{side} depth dropped {_format_decimal(depth_drop_pct)}% "
            f"over the pre-entry window"
        )
    if first.spread_pct > 0 and max_spread_widen_multiple > 0:
        spread_multiple = last.spread_pct / first.spread_pct
        if spread_multiple > max_spread_widen_multiple:
            return (
                f"spread widened {_format_decimal(spread_multiple)}x "
                f"over the pre-entry window"
            )
    return None


def candidate_score(candidate: SymbolCandidate) -> Decimal:
    return (
        candidate.volume_multiple * Decimal("100")
        + candidate.recent_volume_ratio * Decimal("15")
        + _ratio(candidate.bid_depth_1pct + candidate.ask_depth_1pct, Decimal("1000"))
        - candidate.spread_pct * Decimal("20")
    )


def liquidity_rank_key(candidate: SymbolCandidate) -> tuple[Decimal, Decimal, Decimal]:
    return (
        candidate.current_24h_quote_volume,
        candidate.bid_depth_1pct + candidate.ask_depth_1pct,
        -candidate.spread_pct,
    )


def scan_auto_symbol(
    client: BinanceFuturesPublic,
    *,
    direction: str,
    min_volume_multiple: Decimal,
    min_recent_volume_ratio: Decimal,
    recent_hours: int,
    min_quote_volume: Decimal,
    max_spread_pct: Decimal,
    min_depth_1pct: Decimal,
    min_depth_to_volume_pct: Decimal,
    max_symbols: int,
    allow_against_momentum: bool,
    include_tradfi: bool,
    min_funding_rate: Decimal,
    funding_buffer_seconds: float,
    require_funding: bool = True,
    rank_by: str = "funding",
    skip_symbols: set[str] | None = None,
) -> list[SymbolCandidate]:
    skipped = {symbol.upper() for symbol in (skip_symbols or set())}
    meta = usdt_perp_symbol_meta(client)
    tradable = {
        symbol
        for symbol, item in meta.items()
        if symbol not in skipped
        and (
            include_tradfi
            or (item.underlying_type not in TRADFI_UNDERLYING_TYPES and "TRADFI" not in item.contract_type)
        )
    }
    tickers = [
        row
        for row in client.ticker_24hr()
        if str(row.get("symbol", "")).upper() in tradable
        and _decimal(row.get("quoteVolume")) >= min_quote_volume
    ]
    direction = direction.upper()
    if not allow_against_momentum:
        if direction == "LONG":
            tickers = [row for row in tickers if _decimal(row.get("priceChangePercent")) >= 0]
        elif direction == "SHORT":
            tickers = [row for row in tickers if _decimal(row.get("priceChangePercent")) <= 0]

    funding = funding_snapshots(client)
    now_ms = int(time.time() * 1000)
    tickers.sort(key=lambda row: _decimal(row.get("quoteVolume")), reverse=True)
    candidates: list[SymbolCandidate] = []
    for row in tickers[: max(1, int(max_symbols))]:
        symbol = str(row.get("symbol", "")).upper()
        funding_snapshot = funding.get(symbol)
        if funding_snapshot is None:
            if require_funding:
                continue
            funding_snapshot = FundingSnapshot(funding_rate=Decimal("0"), next_funding_time=0)
        if require_funding and funding_snapshot.funding_rate <= min_funding_rate:
            continue
        if require_funding and funding_is_too_close(funding_snapshot, buffer_seconds=funding_buffer_seconds, now_ms=now_ms):
            continue
        windows = quote_volume_windows(
            client.klines(symbol, interval="1h", limit=49),
            recent_hours=recent_hours,
        )
        if windows is None:
            continue
        if windows.volume_multiple < min_volume_multiple:
            continue
        if windows.recent_volume_ratio < min_recent_volume_ratio:
            continue

        liquidity = liquidity_metrics(
            client.depth(symbol, limit=100),
            quote_volume_24h=windows.current_24h_quote_volume,
            max_spread_pct=max_spread_pct,
            min_depth_1pct=min_depth_1pct,
            min_depth_to_volume_pct=min_depth_to_volume_pct,
        )
        if liquidity is None or liquidity.mm_pulled:
            continue

        candidate = SymbolCandidate(
            symbol=symbol,
            price_change_pct=_decimal(row.get("priceChangePercent")),
            funding_rate=funding_snapshot.funding_rate,
            next_funding_time=funding_snapshot.next_funding_time,
            current_24h_quote_volume=windows.current_24h_quote_volume,
            previous_24h_quote_volume=windows.previous_24h_quote_volume,
            volume_multiple=windows.volume_multiple,
            recent_volume_ratio=windows.recent_volume_ratio,
            spread_pct=liquidity.spread_pct,
            bid_depth_1pct=liquidity.bid_depth_1pct,
            ask_depth_1pct=liquidity.ask_depth_1pct,
            depth_to_24h_volume_pct=liquidity.depth_to_24h_volume_pct,
            score=Decimal("0"),
        )
        candidates.append(replace(candidate, score=candidate_score(candidate)))

    if rank_by == "liquidity":
        candidates.sort(key=liquidity_rank_key, reverse=True)
    else:
        candidates.sort(key=lambda candidate: (candidate.funding_rate, candidate.score), reverse=True)
    return candidates


def print_candidates(candidates: list[SymbolCandidate], *, limit: int = 8) -> None:
    if not candidates:
        print("Auto-scan found no symbols passing the volume/liquidity filters.")
        return
    print("Auto-scan candidates:")
    for candidate in candidates[: max(1, int(limit))]:
        print(
            f"- {candidate.symbol}: "
            f"funding {_format_decimal(candidate.funding_rate * Decimal('100'))}%, "
            f"vol x{_format_decimal(candidate.volume_multiple)}, "
            f"recent x{_format_decimal(candidate.recent_volume_ratio)}, "
            f"24h {_format_decimal(candidate.current_24h_quote_volume)} USDT, "
            f"spread {_format_decimal(candidate.spread_pct)}%, "
            f"1pct depth bid/ask {_format_decimal(candidate.bid_depth_1pct)}/{_format_decimal(candidate.ask_depth_1pct)}"
        )


def post_only_entry_price(book: BookTop, *, direction: str, rules: SymbolRules) -> Decimal:
    if direction.upper() == "LONG":
        return floor_to_step(book.bid, rules.tick_size)
    if direction.upper() == "SHORT":
        return ceil_to_step(book.ask, rules.tick_size)
    raise ValueError("direction must be LONG or SHORT")


def entry_moved_away(
    reference_price: Decimal,
    current_price: Decimal,
    *,
    direction: str,
    max_chase_pct: Decimal,
) -> bool:
    if max_chase_pct <= 0 or reference_price <= 0:
        return False
    max_move = max_chase_pct / Decimal("100")
    if direction.upper() == "LONG":
        return current_price > reference_price * (Decimal("1") + max_move)
    if direction.upper() == "SHORT":
        return current_price < reference_price * (Decimal("1") - max_move)
    raise ValueError("direction must be LONG or SHORT")


def post_only_close_price(book: BookTop, *, direction: str, rules: SymbolRules) -> Decimal:
    if direction.upper() == "LONG":
        return ceil_to_step(book.ask, rules.tick_size)
    if direction.upper() == "SHORT":
        return floor_to_step(book.bid, rules.tick_size)
    raise ValueError("direction must be LONG or SHORT")


def unrealized_roe(entry_price: Decimal, mark_price: Decimal, *, direction: str, leverage: int) -> Decimal:
    if entry_price <= 0 or leverage <= 0:
        return Decimal("0")
    move = (mark_price - entry_price) / entry_price
    if direction.upper() == "SHORT":
        move = -move
    elif direction.upper() != "LONG":
        raise ValueError("direction must be LONG or SHORT")
    return move * Decimal(leverage)


def exit_prices(
    entry_price: Decimal,
    *,
    direction: str,
    leverage: int,
    take_profit_roe: Decimal,
    stop_loss_roe: Decimal,
    tick_size: Decimal,
) -> tuple[Decimal, Decimal]:
    if leverage <= 0:
        raise ValueError("leverage must be positive")

    tp_move = take_profit_roe / Decimal(leverage)
    sl_move = stop_loss_roe / Decimal(leverage)
    direction = direction.upper()
    if direction == "LONG":
        take_profit = floor_to_step(entry_price * (Decimal("1") + tp_move), tick_size)
        stop_loss = ceil_to_step(entry_price * (Decimal("1") - sl_move), tick_size)
    elif direction == "SHORT":
        take_profit = ceil_to_step(entry_price * (Decimal("1") - tp_move), tick_size)
        stop_loss = floor_to_step(entry_price * (Decimal("1") + sl_move), tick_size)
    else:
        raise ValueError("direction must be LONG or SHORT")
    return take_profit, stop_loss


def minimum_viable_take_profit_price(
    entry_price: Decimal,
    *,
    quantity: Decimal,
    direction: str,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    min_net_profit: Decimal,
    tick_size: Decimal,
) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    net_profit_per_unit = max(Decimal("0"), min_net_profit) / quantity
    direction = direction.upper()
    if direction == "LONG":
        raw_price = ((entry_price * (Decimal("1") + entry_fee_rate)) + net_profit_per_unit) / (
            Decimal("1") - exit_fee_rate
        )
        take_profit = ceil_to_step(raw_price, tick_size)
        return max(take_profit, entry_price + tick_size)
    if direction == "SHORT":
        raw_price = ((entry_price * (Decimal("1") - entry_fee_rate)) - net_profit_per_unit) / (
            Decimal("1") + exit_fee_rate
        )
        take_profit = floor_to_step(raw_price, tick_size)
        return min(take_profit, entry_price - tick_size)
    raise ValueError("direction must be LONG or SHORT")


def planned_exit_prices(
    entry_price: Decimal,
    *,
    quantity: Decimal,
    direction: str,
    leverage: int,
    take_profit_roe: Decimal,
    stop_loss_roe: Decimal,
    tick_size: Decimal,
    take_profit_mode: str,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    min_net_profit: Decimal,
) -> tuple[Decimal, Decimal]:
    fixed_take_profit, stop_loss = exit_prices(
        entry_price,
        direction=direction,
        leverage=leverage,
        take_profit_roe=take_profit_roe,
        stop_loss_roe=stop_loss_roe,
        tick_size=tick_size,
    )
    if take_profit_mode == "min-viable":
        return (
            minimum_viable_take_profit_price(
                entry_price,
                quantity=quantity,
                direction=direction,
                entry_fee_rate=entry_fee_rate,
                exit_fee_rate=exit_fee_rate,
                min_net_profit=min_net_profit,
                tick_size=tick_size,
            ),
            stop_loss,
        )
    return fixed_take_profit, stop_loss


def estimated_net_profit(
    plan: PlannedTrade,
    *,
    exit_price: Decimal | None = None,
) -> Decimal:
    close_price = exit_price if exit_price is not None else plan.take_profit_price
    if plan.direction == "LONG":
        gross = plan.quantity * (close_price - plan.mark_price)
    else:
        gross = plan.quantity * (plan.mark_price - close_price)
    entry_fee = plan.quantity * plan.mark_price * plan.entry_fee_rate
    exit_fee = plan.quantity * close_price * plan.exit_fee_rate
    return gross - entry_fee - exit_fee


def equity_delta(equity_before: Decimal, equity_after: Decimal) -> Decimal:
    return equity_after - equity_before


def realized_equity_profit_ok(
    equity_before: Decimal,
    equity_after: Decimal,
    *,
    min_profit: Decimal,
) -> bool:
    delta = equity_delta(equity_before, equity_after)
    if min_profit <= 0:
        return delta > 0
    return delta >= min_profit


def realized_trade_profit(
    *,
    totals: IncomeTotals | None,
    equity_before: Decimal,
    equity_after: Decimal,
) -> tuple[Decimal, str]:
    if totals is not None and totals.has_activity:
        return totals.net, "income ledger"
    return equity_delta(equity_before, equity_after), "account equity sample"


def realized_trade_profit_ok(
    *,
    totals: IncomeTotals | None,
    equity_before: Decimal,
    equity_after: Decimal,
    min_profit: Decimal,
) -> bool:
    pnl, _ = realized_trade_profit(
        totals=totals,
        equity_before=equity_before,
        equity_after=equity_after,
    )
    if min_profit <= 0:
        return pnl > 0
    return pnl >= min_profit


def income_totals(rows: list[dict[str, Any]], *, symbol: str | None = None) -> IncomeTotals:
    wanted_symbol = symbol.upper() if symbol else None
    realized_pnl = Decimal("0")
    commission = Decimal("0")
    funding_fee = Decimal("0")
    other = Decimal("0")
    for row in rows:
        row_symbol = str(row.get("symbol", "")).upper()
        if wanted_symbol and row_symbol and row_symbol != wanted_symbol:
            continue
        amount = _decimal(row.get("income"), "0")
        income_type = str(row.get("incomeType", "")).upper()
        if income_type == "REALIZED_PNL":
            realized_pnl += amount
        elif income_type == "COMMISSION":
            commission += amount
        elif income_type == "FUNDING_FEE":
            funding_fee += amount
        else:
            other += amount
    return IncomeTotals(
        realized_pnl=realized_pnl,
        commission=commission,
        funding_fee=funding_fee,
        other=other,
    )


def cycle_income_totals(
    client: BinanceFuturesPublic,
    *,
    symbol: str,
    started_ms: int,
    ended_ms: int,
) -> IncomeTotals | None:
    try:
        rows = client.income_history(
            start_time=max(0, int(started_ms)),
            end_time=max(int(started_ms), int(ended_ms)),
            limit=1000,
        )
    except BinanceHTTPError as exc:
        print(f"Income ledger warning for {symbol}: {exc.payload}")
        return None
    return income_totals(rows, symbol=symbol)


def print_cycle_accounting(
    client: BinanceFuturesPublic,
    *,
    symbol: str,
    equity_before: Decimal,
    equity_after: Decimal,
    started_ms: int,
    ended_ms: int,
    elapsed_seconds: float,
) -> IncomeTotals | None:
    delta = equity_delta(equity_before, equity_after)
    print(
        "Cycle accounting: "
        f"account equity sample before {_format_decimal(equity_before)} USDT, "
        f"after {_format_decimal(equity_after)} USDT, "
        f"sample delta {_format_decimal(delta)} USDT, "
        f"duration {elapsed_seconds:.1f}s."
    )
    totals = cycle_income_totals(
        client,
        symbol=symbol,
        started_ms=started_ms,
        ended_ms=ended_ms,
    )
    if totals is not None:
        if totals.has_activity:
            print(
                "Binance income ledger: "
                f"realized PnL {_format_decimal(totals.realized_pnl)} USDT, "
                f"commission {_format_decimal(totals.commission)} USDT, "
                f"funding {_format_decimal(totals.funding_fee)} USDT, "
                f"other {_format_decimal(totals.other)} USDT, "
                f"net {_format_decimal(totals.net)} USDT."
            )
        else:
            print("Binance income ledger: no matching income rows yet for this cycle.")
    pnl, source = realized_trade_profit(
        totals=totals,
        equity_before=equity_before,
        equity_after=equity_after,
    )
    print(f"Trade PnL used for stats: {_format_decimal(pnl)} USDT ({source}).")
    return totals


def record_cycle_stats(
    *,
    plan: PlannedTrade,
    outcome: str,
    equity_before: Decimal,
    equity_after: Decimal,
    started_at: float,
    ended_at: float,
    elapsed_seconds: float,
    totals: IncomeTotals | None,
) -> None:
    empty_totals = IncomeTotals(
        realized_pnl=Decimal("0"),
        commission=Decimal("0"),
        funding_fee=Decimal("0"),
        other=Decimal("0"),
    )
    income = totals if totals is not None else empty_totals
    pnl, source = realized_trade_profit(
        totals=totals,
        equity_before=equity_before,
        equity_after=equity_after,
    )
    STATS.record_trade(
        TradeRecord(
            symbol=plan.symbol,
            direction=plan.direction,
            outcome=outcome,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=elapsed_seconds,
            equity_before=equity_before,
            equity_after=equity_after,
            equity_delta=equity_delta(equity_before, equity_after),
            pnl=pnl,
            pnl_source=source,
            realized_pnl=income.realized_pnl,
            commission=income.commission,
            funding_fee=income.funding_fee,
            income_net=income.net,
        )
    )


def margin_utilization_pct(plan: PlannedTrade) -> Decimal:
    max_notional = plan.available_balance * Decimal(plan.leverage)
    return _ratio(plan.notional, max_notional) * Decimal("100")


def planned_quantity_and_notional(
    *,
    rules: SymbolRules,
    mark_price: Decimal,
    available_balance: Decimal,
    allocation_pct: Decimal,
    fee_buffer_pct: Decimal,
    leverage: int,
) -> tuple[Decimal, Decimal]:
    usable_balance = available_balance * allocation_pct * (Decimal("1") - fee_buffer_pct)
    notional_target = usable_balance * Decimal(leverage)
    quantity = floor_to_step(notional_target / mark_price, rules.qty_step)
    return quantity, quantity * mark_price


def leverage_for_exchange_minimums(
    *,
    rules: SymbolRules,
    mark_price: Decimal,
    available_balance: Decimal,
    allocation_pct: Decimal,
    fee_buffer_pct: Decimal,
    base_leverage: int,
    max_leverage: int,
) -> int:
    for leverage in range(max(1, int(base_leverage)), max(1, int(max_leverage)) + 1):
        quantity, notional = planned_quantity_and_notional(
            rules=rules,
            mark_price=mark_price,
            available_balance=available_balance,
            allocation_pct=allocation_pct,
            fee_buffer_pct=fee_buffer_pct,
            leverage=leverage,
        )
        if quantity >= rules.min_qty and notional >= rules.min_notional:
            return leverage
    return int(base_leverage)


def effective_entry_leverage(
    *,
    symbol: str,
    rules: SymbolRules,
    mark_price: Decimal,
    available_balance: Decimal,
    args: argparse.Namespace,
    auto_symbol: bool,
) -> int:
    base_leverage = int(args.leverage)
    if not auto_symbol and not getattr(args, "adaptive_min_notional_leverage", False):
        return base_leverage

    effective_leverage = leverage_for_exchange_minimums(
        rules=rules,
        mark_price=mark_price,
        available_balance=available_balance,
        allocation_pct=args.allocation_pct,
        fee_buffer_pct=args.fee_buffer_pct,
        base_leverage=base_leverage,
        max_leverage=getattr(args, "max_min_notional_leverage", base_leverage),
    )
    if effective_leverage > base_leverage:
        print(
            f"Bumping {symbol.upper()} leverage from {base_leverage}x to {effective_leverage}x "
            "for this entry so available balance can satisfy exchange minimum size."
        )
    return effective_leverage


def plan_trade(
    *,
    rules: SymbolRules,
    symbol: str,
    direction: str,
    leverage: int,
    equity: Decimal,
    available_balance: Decimal,
    mark_price: Decimal,
    allocation_pct: Decimal,
    fee_buffer_pct: Decimal,
    take_profit_roe: Decimal,
    stop_loss_roe: Decimal,
    take_profit_mode: str,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    min_net_profit: Decimal,
) -> PlannedTrade:
    quantity, notional = planned_quantity_and_notional(
        rules=rules,
        mark_price=mark_price,
        available_balance=available_balance,
        allocation_pct=allocation_pct,
        fee_buffer_pct=fee_buffer_pct,
        leverage=leverage,
    )
    if quantity < rules.min_qty:
        raise RuntimeError(
            f"Calculated quantity {_format_decimal(quantity)} is below minimum {_format_decimal(rules.min_qty)}."
        )
    if notional < rules.min_notional:
        raise RuntimeError(
            f"Calculated notional {_format_decimal(notional)} is below minimum {_format_decimal(rules.min_notional)}."
        )

    take_profit, stop_loss = planned_exit_prices(
        mark_price,
        quantity=quantity,
        direction=direction,
        leverage=leverage,
        take_profit_roe=take_profit_roe,
        stop_loss_roe=stop_loss_roe,
        tick_size=rules.tick_size,
        take_profit_mode=take_profit_mode,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=exit_fee_rate,
        min_net_profit=min_net_profit,
    )
    return PlannedTrade(
        symbol=symbol.upper(),
        direction=direction.upper(),
        leverage=leverage,
        equity=equity,
        available_balance=available_balance,
        mark_price=mark_price,
        quantity=quantity,
        notional=notional,
        take_profit_price=take_profit,
        stop_loss_price=stop_loss,
        take_profit_roe=take_profit_roe,
        stop_loss_roe=stop_loss_roe,
        take_profit_mode=take_profit_mode,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=exit_fee_rate,
        min_net_profit=min_net_profit,
    )


def current_symbol_position(
    client: BinanceFuturesPublic,
    symbol: str,
    *,
    direction: str,
    position_side: str,
) -> dict[str, Any] | None:
    positions = client.position_information_v3(symbol)
    direction = direction.upper()
    position_side = position_side.upper()
    for position in positions:
        if str(position.get("symbol", "")).upper() != symbol.upper():
            continue
        amount = _decimal(position.get("positionAmt"))
        side = str(position.get("positionSide", "BOTH")).upper()
        if position_side != "BOTH" and side != position_side:
            continue
        if direction == "LONG" and amount > 0:
            return position
        if direction == "SHORT" and amount < 0:
            return position
        if position_side != "BOTH" and abs(amount) > 0:
            return position
    return None


def current_any_symbol_position(
    client: BinanceFuturesPublic,
    *,
    direction: str,
    position_side: str,
) -> dict[str, Any] | None:
    direction = direction.upper()
    position_side = position_side.upper()
    for position in client.position_information_v3():
        amount = _decimal(position.get("positionAmt"))
        side = str(position.get("positionSide", "BOTH")).upper()
        if position_side != "BOTH" and side != position_side:
            continue
        if direction == "LONG" and amount > 0:
            return position
        if direction == "SHORT" and amount < 0:
            return position
        if position_side != "BOTH" and abs(amount) > 0:
            return position
    return None


def cancel_symbol_orders(client: BinanceFuturesPublic, symbol: str) -> None:
    for cancel in (client.cancel_all_futures_orders, client.cancel_all_futures_algo_orders):
        try:
            cancel(symbol)
        except BinanceHTTPError as exc:
            print(f"Cancel warning for {symbol}: {exc.payload}")


def wait_for_position(
    client: BinanceFuturesPublic,
    symbol: str,
    *,
    direction: str,
    position_side: str,
    attempts: int = 12,
) -> dict[str, Any]:
    for _ in range(max(1, attempts)):
        position = current_symbol_position(
            client,
            symbol,
            direction=direction,
            position_side=position_side,
        )
        if position is not None:
            return position
        bot_sleep(0.75)
    raise RuntimeError(f"No open {direction} position found for {symbol} after entry order.")


def place_maker_entry_once(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    rules: SymbolRules,
    position_side: str,
    price: Decimal | None = None,
) -> dict[str, Any]:
    side = "BUY" if plan.direction == "LONG" else "SELL"
    order_price = price
    if order_price is None:
        book = order_book_top(client, plan.symbol)
        order_price = post_only_entry_price(book, direction=plan.direction, rules=rules)
    params: dict[str, Any] = {
        "symbol": plan.symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "quantity": _format_decimal(plan.quantity),
        "price": _format_decimal(order_price),
        "newOrderRespType": "ACK",
    }
    if position_side.upper() != "BOTH":
        params["positionSide"] = position_side.upper()
    return client.new_futures_order(**params)


def place_market_entry_and_wait(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    rules: SymbolRules,
    position_side: str,
) -> dict[str, Any]:
    side = "BUY" if plan.direction == "LONG" else "SELL"
    quantity = plan.quantity
    while quantity >= rules.min_qty and quantity * plan.mark_price >= rules.min_notional:
        params: dict[str, Any] = {
            "symbol": plan.symbol,
            "side": side,
            "type": "MARKET",
            "quantity": _format_decimal(quantity),
            "newOrderRespType": "RESULT",
        }
        if position_side.upper() != "BOTH":
            params["positionSide"] = position_side.upper()
        try:
            order = client.new_futures_order(**params)
        except BinanceHTTPError as exc:
            if not is_insufficient_margin_error(exc):
                raise
            next_quantity = floor_to_step(quantity - rules.qty_step, rules.qty_step)
            if next_quantity < rules.min_qty or next_quantity * plan.mark_price < rules.min_notional:
                raise
            print(
                "Market entry margin rejected at "
                f"{_format_decimal(quantity)}; retrying {_format_decimal(next_quantity)}."
            )
            quantity = next_quantity
            continue
        print(f"Market entry order placed: {order.get('orderId')} quantity {_format_decimal(quantity)}")
        return wait_for_position(
            client,
            plan.symbol,
            direction=plan.direction,
            position_side=position_side,
            attempts=16,
        )
    raise RuntimeError(f"Cannot size market entry for {plan.symbol} above exchange minimums.")


def close_position_market_and_wait(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    position_side: str,
) -> None:
    position = current_symbol_position(
        client,
        plan.symbol,
        direction=plan.direction,
        position_side=position_side,
    )
    if position is None:
        return
    quantity = abs(_decimal(position.get("positionAmt")))
    if quantity <= 0:
        return
    side = "SELL" if plan.direction == "LONG" else "BUY"
    params: dict[str, Any] = {
        "symbol": plan.symbol,
        "side": side,
        "type": "MARKET",
        "quantity": _format_decimal(quantity),
        "reduceOnly": "true",
        "newOrderRespType": "RESULT",
    }
    if position_side.upper() != "BOTH":
        params.pop("reduceOnly", None)
        params["positionSide"] = position_side.upper()
    order = client.new_futures_order(**params)
    print(f"Emergency market close order placed: {order.get('orderId')} quantity {_format_decimal(quantity)}")
    for _ in range(16):
        if current_symbol_position(
            client,
            plan.symbol,
            direction=plan.direction,
            position_side=position_side,
        ) is None:
            return
        bot_sleep(0.5)


def place_maker_close_once(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    rules: SymbolRules,
    position_side: str,
    quantity: Decimal,
) -> dict[str, Any]:
    side = "SELL" if plan.direction == "LONG" else "BUY"
    book = order_book_top(client, plan.symbol)
    price = post_only_close_price(book, direction=plan.direction, rules=rules)
    params: dict[str, Any] = {
        "symbol": plan.symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "quantity": _format_decimal(quantity),
        "price": _format_decimal(price),
        "newOrderRespType": "ACK",
    }
    if position_side.upper() != "BOTH":
        params["positionSide"] = position_side.upper()
    else:
        params["reduceOnly"] = "true"
    return client.new_futures_order(**params)


def place_maker_entry_and_wait(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    rules: SymbolRules,
    position_side: str,
    requote_seconds: float,
    timeout_seconds: float,
    max_chase_pct: Decimal,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(5.0, float(timeout_seconds))
    last_order: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if last_order:
            try:
                client.cancel_all_futures_orders(plan.symbol)
            except BinanceHTTPError:
                pass
        try:
            book = order_book_top(client, plan.symbol)
            entry_price = post_only_entry_price(book, direction=plan.direction, rules=rules)
            if entry_moved_away(
                plan.mark_price,
                entry_price,
                direction=plan.direction,
                max_chase_pct=max_chase_pct,
            ):
                raise EntryAbandoned(
                    f"{plan.symbol} maker entry moved away from "
                    f"{_format_decimal(plan.mark_price)} to {_format_decimal(entry_price)}"
                )
            last_order = place_maker_entry_once(
                client,
                plan,
                rules=rules,
                position_side=position_side,
                price=entry_price,
            )
            print(f"Maker entry order placed: {last_order.get('orderId')}")
        except BinanceHTTPError as exc:
            if is_untradable_error(exc):
                raise
            print(f"Maker entry rejected/retryable: {exc.payload}")
            bot_sleep(max(0.5, float(requote_seconds)))
            continue

        wait_until = min(deadline, time.monotonic() + max(0.5, float(requote_seconds)))
        while time.monotonic() < wait_until:
            position = current_symbol_position(
                client,
                plan.symbol,
                direction=plan.direction,
                position_side=position_side,
            )
            if position is not None:
                try:
                    client.cancel_all_futures_orders(plan.symbol)
                except BinanceHTTPError:
                    pass
                return position
            bot_sleep(0.5)

    try:
        client.cancel_all_futures_orders(plan.symbol)
    except BinanceHTTPError:
        pass
    raise RuntimeError("Timed out waiting for maker-only entry fill.")


def pre_entry_safety_check(
    client: BinanceFuturesPublic,
    *,
    symbol: str,
    direction: str,
    min_depth_1pct: Decimal,
    recent_vol_minutes: int,
    max_adverse_vol_ratio: Decimal,
    liquidity_window_seconds: float,
    liquidity_samples: int,
    max_depth_drop_pct: Decimal,
    max_spread_widen_multiple: Decimal,
    funding_buffer_seconds: float,
    min_recent_quote_volume: Decimal,
    min_recent_volatility_pct: Decimal,
) -> tuple[bool, str]:
    funding_rows = funding_snapshots(client)
    funding = funding_rows.get(symbol.upper())
    if funding and funding_is_too_close(funding, buffer_seconds=funding_buffer_seconds):
        seconds_until = funding_seconds_until(funding)
        if seconds_until is None:
            seconds_text = "unknown"
        else:
            seconds_text = _format_decimal(Decimal(str(seconds_until)))
        return False, f"funding timestamp too close ({seconds_text}s away)"

    minutes = max(0, int(recent_vol_minutes))
    if minutes >= 2:
        rows = client.klines(symbol, interval="1m", limit=minutes + 2)
        recent_quote_volume = recent_quote_volume_from_klines(rows)
        if min_recent_quote_volume > 0 and recent_quote_volume < min_recent_quote_volume:
            return (
                False,
                f"recent {minutes}m quote volume too low "
                f"({_format_decimal(recent_quote_volume)} USDT)",
            )
        upside, downside = directional_volatility_from_klines(rows)
        recent_volatility_pct = (upside + downside) * Decimal("100")
        if min_recent_volatility_pct > 0 and recent_volatility_pct < min_recent_volatility_pct:
            return (
                False,
                f"recent {minutes}m volatility too low "
                f"({_format_decimal(recent_volatility_pct)}%)",
            )
        if max_adverse_vol_ratio > 0:
            reason = adverse_volatility_reason(
                direction=direction,
                upside=upside,
                downside=downside,
                max_adverse_ratio=max_adverse_vol_ratio,
            )
            if reason:
                return False, reason

    sample_count = max(1, int(liquidity_samples))
    if sample_count >= 2 and liquidity_window_seconds > 0:
        snapshots: list[BookLiquiditySnapshot] = []
        sleep_seconds = max(0.0, float(liquidity_window_seconds)) / float(sample_count - 1)
        for index in range(sample_count):
            snapshot = book_liquidity_snapshot(client.depth(symbol, limit=100))
            if snapshot is None:
                return False, "order book snapshot was empty during pre-entry check"
            snapshots.append(snapshot)
            if index < sample_count - 1:
                bot_sleep(sleep_seconds)
        reason = liquidity_decay_reason(
            snapshots,
            direction=direction,
            min_depth_1pct=min_depth_1pct,
            max_depth_drop_pct=max_depth_drop_pct,
            max_spread_widen_multiple=max_spread_widen_multiple,
        )
        if reason:
            return False, reason

    return True, "pre-entry safety checks passed"


def close_position_maker_and_wait(
    client: BinanceFuturesPublic,
    plan: PlannedTrade,
    *,
    rules: SymbolRules,
    position_side: str,
    requote_seconds: float,
) -> None:
    while True:
        position = current_symbol_position(
            client,
            plan.symbol,
            direction=plan.direction,
            position_side=position_side,
        )
        if position is None:
            cancel_symbol_orders(client, plan.symbol)
            return

        quantity = abs(_decimal(position.get("positionAmt"), str(plan.quantity)))
        if quantity <= 0:
            cancel_symbol_orders(client, plan.symbol)
            return

        try:
            client.cancel_all_futures_orders(plan.symbol)
        except BinanceHTTPError as exc:
            print(f"Cancel warning for {plan.symbol}: {exc.payload}")

        try:
            close_order = place_maker_close_once(
                client,
                plan,
                rules=rules,
                position_side=position_side,
                quantity=quantity,
            )
            print(f"Maker time-exit close order placed: {close_order.get('orderId')}")
        except BinanceHTTPError as exc:
            if is_untradable_error(exc):
                raise
            print(f"Maker time-exit close rejected/retryable: {exc.payload}")
            bot_sleep(max(0.5, float(requote_seconds)))
            continue

        wait_until = time.monotonic() + max(0.5, float(requote_seconds))
        while time.monotonic() < wait_until:
            position = current_symbol_position(
                client,
                plan.symbol,
                direction=plan.direction,
                position_side=position_side,
            )
            if position is None:
                cancel_symbol_orders(client, plan.symbol)
                return
            bot_sleep(0.5)


def place_exits(
    client: BinanceFuturesPublic,
    *,
    plan: PlannedTrade,
    entry_price: Decimal,
    rules: SymbolRules,
    position_side: str,
    working_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    take_profit, stop_loss = planned_exit_prices(
        entry_price,
        quantity=plan.quantity,
        direction=plan.direction,
        leverage=plan.leverage,
        take_profit_roe=plan.take_profit_roe,
        stop_loss_roe=plan.stop_loss_roe,
        tick_size=rules.tick_size,
        take_profit_mode=plan.take_profit_mode,
        entry_fee_rate=plan.entry_fee_rate,
        exit_fee_rate=plan.exit_fee_rate,
        min_net_profit=plan.min_net_profit,
    )
    exit_side = "SELL" if plan.direction == "LONG" else "BUY"
    stop_loss_params: dict[str, Any] = {
        "algoType": "CONDITIONAL",
        "symbol": plan.symbol,
        "side": exit_side,
        "type": "STOP_MARKET",
        "closePosition": "true",
        "triggerPrice": _format_decimal(stop_loss),
        "workingType": working_type,
        "newOrderRespType": "ACK",
    }
    if position_side.upper() != "BOTH":
        stop_loss_params["positionSide"] = position_side.upper()
    stop_loss_order = client.new_futures_algo_order(**stop_loss_params)

    take_profit_params: dict[str, Any] = {
        "symbol": plan.symbol,
        "side": exit_side,
        "type": "LIMIT",
        "timeInForce": "GTX",
        "quantity": _format_decimal(plan.quantity),
        "price": _format_decimal(take_profit),
        "newOrderRespType": "ACK",
    }
    if position_side.upper() != "BOTH":
        take_profit_params["positionSide"] = position_side.upper()
    else:
        take_profit_params["reduceOnly"] = "true"
    take_profit_order = client.new_futures_order(**take_profit_params)
    return take_profit_order, stop_loss_order


def plan_at_entry_price(
    plan: PlannedTrade,
    rules: SymbolRules,
    entry_price: Decimal,
    *,
    quantity: Decimal | None = None,
) -> PlannedTrade:
    actual_quantity = quantity if quantity is not None else plan.quantity
    take_profit, stop_loss = planned_exit_prices(
        entry_price,
        quantity=actual_quantity,
        direction=plan.direction,
        leverage=plan.leverage,
        take_profit_roe=plan.take_profit_roe,
        stop_loss_roe=plan.stop_loss_roe,
        tick_size=rules.tick_size,
        take_profit_mode=plan.take_profit_mode,
        entry_fee_rate=plan.entry_fee_rate,
        exit_fee_rate=plan.exit_fee_rate,
        min_net_profit=plan.min_net_profit,
    )
    return replace(
        plan,
        mark_price=entry_price,
        quantity=actual_quantity,
        notional=actual_quantity * entry_price,
        take_profit_price=take_profit,
        stop_loss_price=stop_loss,
    )


def plan_from_position(
    position: dict[str, Any],
    *,
    rules: SymbolRules,
    direction: str,
    leverage: int,
    equity: Decimal,
    available_balance: Decimal,
    take_profit_roe: Decimal,
    stop_loss_roe: Decimal,
    take_profit_mode: str,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    min_net_profit: Decimal,
) -> PlannedTrade:
    symbol = str(position.get("symbol", rules.symbol)).upper()
    entry_price = _decimal(position.get("entryPrice"))
    quantity = abs(_decimal(position.get("positionAmt")))
    if entry_price <= 0 or quantity <= 0:
        raise RuntimeError(f"Cannot build exit plan from position: {position}")
    take_profit, stop_loss = planned_exit_prices(
        entry_price,
        quantity=quantity,
        direction=direction,
        leverage=leverage,
        take_profit_roe=take_profit_roe,
        stop_loss_roe=stop_loss_roe,
        tick_size=rules.tick_size,
        take_profit_mode=take_profit_mode,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=exit_fee_rate,
        min_net_profit=min_net_profit,
    )
    return PlannedTrade(
        symbol=symbol,
        direction=direction.upper(),
        leverage=leverage,
        equity=equity,
        available_balance=available_balance,
        mark_price=entry_price,
        quantity=quantity,
        notional=quantity * entry_price,
        take_profit_price=take_profit,
        stop_loss_price=stop_loss,
        take_profit_roe=take_profit_roe,
        stop_loss_roe=stop_loss_roe,
        take_profit_mode=take_profit_mode,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=exit_fee_rate,
        min_net_profit=min_net_profit,
    )


def position_leverage(position: dict[str, Any], fallback: int) -> int:
    leverage = _decimal(position.get("leverage"), str(fallback))
    if leverage < 1:
        return int(fallback)
    return int(leverage)


def order_was_filled(client: BinanceFuturesPublic, symbol: str, order: dict[str, Any]) -> bool:
    order_id = order.get("orderId")
    if order_id in (None, ""):
        return False
    try:
        current = client.query_futures_order(symbol, order_id=order_id)
    except BinanceHTTPError:
        return False
    return str(current.get("status", "")).upper() == "FILLED"


def algo_order_was_triggered(client: BinanceFuturesPublic, order: dict[str, Any]) -> bool:
    algo_id = order.get("algoId")
    client_algo_id = order.get("clientAlgoId")
    if algo_id in (None, "") and client_algo_id in (None, ""):
        return False
    try:
        current = client.query_futures_algo_order(algo_id=algo_id, client_algo_id=client_algo_id)
    except BinanceHTTPError:
        return False
    status = str(current.get("algoStatus", "")).upper()
    return status in {"TRIGGERED", "FINISHED", "FILLED"}


def monitor_until_exit(
    client: BinanceFuturesPublic,
    *,
    plan: PlannedTrade,
    rules: SymbolRules,
    position_side: str,
    take_profit_order: dict[str, Any],
    stop_loss_order: dict[str, Any],
    poll_seconds: float,
    green_exit_after_seconds: float,
    green_exit_min_roe: Decimal,
    max_hold_seconds: float,
    flat_exit_roe: Decimal,
    exit_requote_seconds: float,
    emergency_stop_roe: Decimal,
) -> str:
    started_at = time.monotonic()
    while True:
        position = current_symbol_position(
            client,
            plan.symbol,
            direction=plan.direction,
            position_side=position_side,
        )
        if position is None:
            if order_was_filled(client, plan.symbol, take_profit_order):
                return "take_profit"
            if algo_order_was_triggered(client, stop_loss_order):
                return "stop_loss"

            mark = latest_mark_price(client, plan.symbol)
            if plan.direction == "LONG":
                return "take_profit" if mark >= plan.take_profit_price else "stop_loss"
            return "take_profit" if mark <= plan.take_profit_price else "stop_loss"

        elapsed = time.monotonic() - started_at
        mark = None
        current_roe = None
        if emergency_stop_roe > 0:
            mark = latest_mark_price(client, plan.symbol)
            current_roe = unrealized_roe(
                plan.mark_price,
                mark,
                direction=plan.direction,
                leverage=plan.leverage,
            )
            if current_roe <= -emergency_stop_roe:
                print(
                    "Emergency ROE stop triggered: "
                    f"unrealized ROE {_format_decimal(current_roe * Decimal('100'))}% "
                    f"<= -{_format_decimal(emergency_stop_roe * Decimal('100'))}%."
                )
                close_position_market_and_wait(
                    client,
                    plan,
                    position_side=position_side,
                )
                return "emergency_stop"

        if green_exit_after_seconds > 0 and elapsed >= green_exit_after_seconds:
            if mark is None:
                mark = latest_mark_price(client, plan.symbol)
            if current_roe is None:
                current_roe = unrealized_roe(
                    plan.mark_price,
                    mark,
                    direction=plan.direction,
                    leverage=plan.leverage,
                )
            estimated_net = estimated_net_profit(plan, exit_price=mark)
            if current_roe > green_exit_min_roe and estimated_net > 0:
                print(
                    "Green time exit triggered: "
                    f"held at least {int(green_exit_after_seconds)}s, "
                    f"unrealized ROE {_format_decimal(current_roe * Decimal('100'))}%, "
                    f"estimated net {_format_decimal(estimated_net)} USDT."
                )
                close_position_maker_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                    requote_seconds=exit_requote_seconds,
                )
                return "time_green_close"

        if max_hold_seconds > 0 and elapsed >= max_hold_seconds:
            if mark is None:
                mark = latest_mark_price(client, plan.symbol)
            if current_roe is None:
                current_roe = unrealized_roe(
                    plan.mark_price,
                    mark,
                    direction=plan.direction,
                    leverage=plan.leverage,
                )
            if abs(current_roe) <= flat_exit_roe:
                print(
                    "Time exit triggered: "
                    f"held at least {int(max_hold_seconds)}s, "
                    f"unrealized ROE {_format_decimal(current_roe * Decimal('100'))}% "
                    f"is inside +/-{_format_decimal(flat_exit_roe * Decimal('100'))}%."
                )
                close_position_maker_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                    requote_seconds=exit_requote_seconds,
                )
                return "time_neutral_close"

        bot_sleep(max(0.5, float(poll_seconds)))


def print_plan(plan: PlannedTrade) -> None:
    tp_price_move = plan.take_profit_roe / Decimal(plan.leverage)
    sl_price_move = plan.stop_loss_roe / Decimal(plan.leverage)
    tp_move_pct = ((plan.take_profit_price - plan.mark_price) / plan.mark_price) * Decimal("100")
    if plan.direction == "SHORT":
        tp_move_pct = ((plan.mark_price - plan.take_profit_price) / plan.mark_price) * Decimal("100")
    estimated_net = estimated_net_profit(plan)
    print(f"Symbol: {plan.symbol} {plan.direction}")
    print(f"Equity: {_format_decimal(plan.equity)} USDT")
    print(f"Available balance: {_format_decimal(plan.available_balance)} USDT")
    print(f"Leverage: {plan.leverage}x")
    print(f"Maker entry estimate: {_format_decimal(plan.mark_price)}")
    print(f"Quantity: {_format_decimal(plan.quantity)}")
    print(f"Notional: {_format_decimal(plan.notional)} USDT")
    print(f"Buying power used: {_format_decimal(margin_utilization_pct(plan))}%")
    if plan.take_profit_mode == "min-viable":
        print(
            "Maker take profit: "
            f"{_format_decimal(plan.take_profit_price)} "
            f"(min-viable, about {_format_decimal(tp_move_pct)}% price move, "
            f"est. net {_format_decimal(estimated_net)} USDT)"
        )
    else:
        print(f"Maker take profit: {_format_decimal(plan.take_profit_price)} ({plan.take_profit_roe * 100}% ROE, about {tp_price_move * 100}% price move)")
    print(f"Protective stop loss: {_format_decimal(plan.stop_loss_price)} ({plan.stop_loss_roe * 100}% ROE, about {sl_price_move * 100}% price move)")


def run_position_cycle(
    client: BinanceFuturesPublic,
    *,
    plan: PlannedTrade,
    rules: SymbolRules,
    position_side: str,
    poll_seconds: float,
    green_exit_after_seconds: float,
    green_exit_min_roe: Decimal,
    max_hold_seconds: float,
    flat_exit_roe: Decimal,
    exit_requote_seconds: float,
    emergency_stop_roe: Decimal,
) -> str:
    cancel_symbol_orders(client, plan.symbol)
    take_profit_order, stop_loss_order = place_exits(
        client,
        plan=plan,
        entry_price=plan.mark_price,
        rules=rules,
        position_side=position_side,
        working_type="MARK_PRICE",
    )
    print(
        "Exit orders placed: "
        f"maker TP order {take_profit_order.get('orderId')} / protective algo SL {stop_loss_order.get('algoId')}"
    )
    outcome = monitor_until_exit(
        client,
        plan=plan,
        rules=rules,
        position_side=position_side,
        take_profit_order=take_profit_order,
        stop_loss_order=stop_loss_order,
        poll_seconds=poll_seconds,
        green_exit_after_seconds=green_exit_after_seconds,
        green_exit_min_roe=green_exit_min_roe,
        max_hold_seconds=max_hold_seconds,
        flat_exit_roe=flat_exit_roe,
        exit_requote_seconds=exit_requote_seconds,
        emergency_stop_roe=emergency_stop_roe,
    )
    cancel_symbol_orders(client, plan.symbol)
    return outcome


def auto_symbol_candidates(
    client: BinanceFuturesPublic,
    *,
    args: argparse.Namespace,
    direction: str,
) -> list[SymbolCandidate]:
    profile = str(getattr(args, "_effective_scan_profile", "signal"))
    candidates = scan_auto_symbol(
        client,
        direction=direction,
        min_volume_multiple=args.scan_min_volume_multiple,
        min_recent_volume_ratio=args.scan_min_recent_volume_ratio,
        recent_hours=args.scan_recent_hours,
        min_quote_volume=args.scan_min_quote_volume_usdt,
        max_spread_pct=args.scan_max_spread_pct,
        min_depth_1pct=args.scan_min_depth_1pct_usdt,
        min_depth_to_volume_pct=args.scan_min_depth_to_volume_pct,
        max_symbols=args.scan_max_symbols,
        allow_against_momentum=args.scan_allow_against_momentum,
        include_tradfi=args.scan_include_tradfi,
        min_funding_rate=args.scan_min_funding_rate,
        funding_buffer_seconds=args.safety_funding_buffer_seconds,
        require_funding=profile != "liquidity",
        rank_by="liquidity" if profile == "liquidity" else "funding",
        skip_symbols=active_skip_symbols(args),
    )
    allowlist = set(getattr(args, "_scan_symbol_allowlist", set()))
    if allowlist:
        candidates = [candidate for candidate in candidates if candidate.symbol in allowlist]
        if not candidates:
            print(f"No candidates remained after symbol allowlist: {', '.join(sorted(allowlist))}")
        else:
            print(f"Symbol allowlist active: {', '.join(sorted(allowlist))}")
    print_candidates(candidates)
    return candidates


def select_auto_symbol(
    client: BinanceFuturesPublic,
    *,
    args: argparse.Namespace,
    direction: str,
) -> str | None:
    candidates = auto_symbol_candidates(client, args=args, direction=direction)
    if not candidates:
        return None
    print(f"Selected auto symbol: {candidates[0].symbol}")
    return candidates[0].symbol


def maker_fee_for_symbol(
    client: BinanceFuturesPublic,
    *,
    symbol: str,
    args: argparse.Namespace,
    fallback: Decimal,
) -> Decimal:
    if args.live and args.take_profit_mode == "min-viable":
        return maker_fee_rate(client, symbol, fallback=fallback)
    return fallback


def apply_margin_type_if_requested(
    client: BinanceFuturesPublic,
    *,
    symbol: str,
    args: argparse.Namespace,
) -> None:
    margin_type = str(getattr(args, "margin_type", "UNCHANGED")).upper()
    if margin_type == "UNCHANGED":
        return
    try:
        client.change_margin_type(symbol, margin_type)
        print(f"Margin type set for {symbol}: {margin_type}")
    except BinanceHTTPError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        code = payload.get("code")
        msg = str(payload.get("msg", exc.payload))
        if code == -4046 or "no need to change margin type" in msg.lower():
            print(f"Margin type already {margin_type} for {symbol}.")
            return
        raise


def margin_underutilization_reason(
    args: argparse.Namespace,
    *,
    plan: PlannedTrade,
    rules: SymbolRules,
) -> str | None:
    min_utilization = args.min_margin_utilization_pct
    if min_utilization <= 0:
        return None
    utilization = margin_utilization_pct(plan)
    if utilization >= min_utilization:
        return None
    return (
        f"only uses {_format_decimal(utilization)}% of available {plan.leverage}x buying power "
        f"after {plan.symbol} quantity step {_format_decimal(rules.qty_step)}"
    )


def select_auto_trade_plan(
    client: BinanceFuturesPublic,
    *,
    args: argparse.Namespace,
    direction: str,
    account: dict[str, Any],
    fee_fallback: Decimal,
) -> tuple[str, SymbolRules, Decimal, PlannedTrade] | None:
    equity = account_equity(account)
    available = account_available_balance(account)
    candidates = auto_symbol_candidates(client, args=args, direction=direction)
    for candidate in candidates:
        symbol = candidate.symbol
        try:
            rules = symbol_rules(client, symbol)
            live_maker_fee_rate = maker_fee_for_symbol(
                client,
                symbol=symbol,
                args=args,
                fallback=fee_fallback,
            )
            entry_estimate = post_only_entry_price(
                order_book_top(client, symbol),
                direction=direction,
                rules=rules,
            )
            effective_leverage = effective_entry_leverage(
                symbol=symbol,
                rules=rules,
                mark_price=entry_estimate,
                available_balance=available,
                args=args,
                auto_symbol=True,
            )
            apply_margin_type_if_requested(client, symbol=symbol, args=args)
            client.change_initial_leverage(symbol, effective_leverage)
            plan = plan_trade(
                rules=rules,
                symbol=symbol,
                direction=direction,
                leverage=effective_leverage,
                equity=equity,
                available_balance=available,
                mark_price=entry_estimate,
                allocation_pct=args.allocation_pct,
                fee_buffer_pct=args.fee_buffer_pct,
                take_profit_roe=args.take_profit_roe,
                stop_loss_roe=args.stop_loss_roe,
                take_profit_mode=args.take_profit_mode,
                entry_fee_rate=live_maker_fee_rate,
                exit_fee_rate=live_maker_fee_rate,
                min_net_profit=args.min_net_profit_usdt,
            )
        except BinanceHTTPError as exc:
            if is_untradable_error(exc):
                mark_auto_symbol_untradable(args, symbol, exc, "auto plan")
                continue
            raise
        except RuntimeError as exc:
            print(f"Skipping {symbol} this scan: {exc}")
            continue

        underutilized = margin_underutilization_reason(args, plan=plan, rules=rules)
        if underutilized is not None:
            print(f"Skipping {symbol} this scan: {underutilized}")
            continue

        print(f"Selected auto symbol: {symbol}")
        return symbol, rules, live_maker_fee_rate, plan

    return None


def effective_scan_profile(args: argparse.Namespace) -> str:
    profile = str(args.scan_profile).lower()
    if profile == "auto":
        return "liquidity" if args.take_profit_mode == "min-viable" else "signal"
    return profile


def apply_scan_profile(args: argparse.Namespace) -> None:
    profile = effective_scan_profile(args)
    setattr(args, "_effective_scan_profile", profile)
    allowlist = parse_symbol_list(getattr(args, "scan_symbols", ""))
    if not allowlist and profile == "liquidity" and args.take_profit_mode == "min-viable":
        allowlist = set(DEFAULT_MIN_PROFIT_SYMBOLS)
    setattr(args, "_scan_symbol_allowlist", allowlist)
    if profile != "liquidity":
        return

    if args.scan_min_volume_multiple == DEFAULT_SCAN_MIN_VOLUME_MULTIPLE:
        args.scan_min_volume_multiple = Decimal("0")
    if args.scan_min_recent_volume_ratio == DEFAULT_SCAN_MIN_RECENT_VOLUME_RATIO:
        args.scan_min_recent_volume_ratio = Decimal("0")
    if args.scan_min_quote_volume_usdt == DEFAULT_SCAN_MIN_QUOTE_VOLUME:
        args.scan_min_quote_volume_usdt = DEFAULT_MIN_PROFIT_SCAN_MIN_QUOTE_VOLUME
    if args.scan_max_spread_pct == DEFAULT_SCAN_MAX_SPREAD_PCT:
        args.scan_max_spread_pct = DEFAULT_MIN_PROFIT_SCAN_MAX_SPREAD_PCT
    if args.scan_min_depth_1pct_usdt == DEFAULT_SCAN_MIN_DEPTH_1PCT:
        args.scan_min_depth_1pct_usdt = DEFAULT_MIN_PROFIT_SCAN_MIN_DEPTH_1PCT
    if args.scan_min_funding_rate == DEFAULT_SCAN_MIN_FUNDING_RATE:
        args.scan_min_funding_rate = Decimal("-1")

    args.scan_allow_against_momentum = True
    if args.safety_funding_buffer_seconds == DEFAULT_SAFETY_FUNDING_BUFFER_SECONDS:
        args.safety_funding_buffer_seconds = 0.0
    if args.safety_recent_vol_minutes == DEFAULT_SAFETY_RECENT_VOL_MINUTES:
        args.safety_recent_vol_minutes = 0
    if args.safety_min_recent_quote_volume_usdt == DEFAULT_SAFETY_MIN_RECENT_QUOTE_VOLUME:
        args.safety_min_recent_quote_volume_usdt = Decimal("0")
    if args.safety_min_recent_volatility_pct == DEFAULT_SAFETY_MIN_RECENT_VOLATILITY_PCT:
        args.safety_min_recent_volatility_pct = Decimal("0")
    if args.safety_max_adverse_vol_ratio == DEFAULT_SAFETY_MAX_ADVERSE_VOL_RATIO:
        args.safety_max_adverse_vol_ratio = Decimal("0")
    if args.safety_liquidity_window_seconds == DEFAULT_SAFETY_LIQUIDITY_WINDOW_SECONDS:
        args.safety_liquidity_window_seconds = 0.0
    if args.safety_liquidity_samples == DEFAULT_SAFETY_LIQUIDITY_SAMPLES:
        args.safety_liquidity_samples = 1
    if args.max_same_symbol_streak == DEFAULT_MAX_SAME_SYMBOL_STREAK:
        args.max_same_symbol_streak = 0
    if DEFAULT_MIN_PROFIT_DISABLE_TIME_EXITS and args.take_profit_mode == "min-viable":
        args.green_exit_after_seconds = 0.0
        args.max_hold_seconds = 0.0


def is_untradable_error(exc: BinanceHTTPError) -> bool:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    code = payload.get("code")
    msg = str(payload.get("msg", exc.payload)).lower()
    if code in UNTRADABLE_ERROR_CODES:
        return True
    markers = (
        "not supported",
        "not available",
        "cannot trade",
        "symbol is not permitted",
        "restricted",
        "permission",
        "tradfi",
    )
    return any(marker in msg for marker in markers)


def is_insufficient_margin_error(exc: BinanceHTTPError) -> bool:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    code = payload.get("code")
    msg = str(payload.get("msg", exc.payload)).lower()
    return code == -2019 or "margin is insufficient" in msg


def mark_auto_symbol_untradable(args: argparse.Namespace, symbol: str, exc: BinanceHTTPError, stage: str) -> None:
    skipped = set(getattr(args, "_skip_symbols", set()))
    skipped.add(symbol.upper())
    setattr(args, "_skip_symbols", skipped)
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    code = payload.get("code")
    msg = payload.get("msg", exc.payload)
    print(f"Skipping {symbol.upper()} after {stage} rejected it for this account: code={code} {msg}")


def active_skip_symbols(args: argparse.Namespace) -> set[str]:
    skipped = {symbol.upper() for symbol in getattr(args, "_skip_symbols", set())}
    cooldown_until = dict(getattr(args, "_cooldown_until", {}))
    now = time.monotonic()
    active_cooldowns = {
        symbol.upper()
        for symbol, until in cooldown_until.items()
        if float(until) > now
    }
    expired = {
        symbol
        for symbol, until in cooldown_until.items()
        if float(until) <= now
    }
    for symbol in expired:
        cooldown_until.pop(symbol, None)
    setattr(args, "_cooldown_until", cooldown_until)
    return skipped | active_cooldowns


def set_symbol_cooldown(args: argparse.Namespace, symbol: str, seconds: float, reason: str) -> None:
    if seconds <= 0:
        return
    cooldown_until = dict(getattr(args, "_cooldown_until", {}))
    cooldown_until[symbol.upper()] = time.monotonic() + float(seconds)
    setattr(args, "_cooldown_until", cooldown_until)
    print(f"Cooling down {symbol.upper()} for {int(seconds)}s: {reason}")


def record_take_profit_symbol(
    args: argparse.Namespace,
    *,
    symbol: str,
    last_symbol: str | None,
    streak: int,
) -> tuple[str | None, int]:
    if args.max_same_symbol_streak <= 0:
        return symbol.upper(), 0
    symbol = symbol.upper()
    next_streak = streak + 1 if symbol == last_symbol else 1
    if next_streak >= args.max_same_symbol_streak:
        set_symbol_cooldown(
            args,
            symbol,
            args.same_symbol_cooldown_seconds,
            f"{next_streak} consecutive take-profit exits on the same symbol",
        )
        return None, 0
    return symbol, next_streak


def maybe_skip_underutilized_symbol(
    args: argparse.Namespace,
    *,
    plan: PlannedTrade,
    rules: SymbolRules,
    auto_symbol: bool,
) -> bool:
    reason = margin_underutilization_reason(args, plan=plan, rules=rules)
    if reason is None:
        return False
    if auto_symbol:
        print(f"Skipping {plan.symbol}: {reason}")
        set_symbol_cooldown(args, plan.symbol, args.entry_abandon_cooldown_seconds, reason)
        return True

    print(f"Position sizing warning for {plan.symbol}: {reason}")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "LDOUSDT futures loop bot: maker entry, exchange-side TP/SL, "
            "re-enter after take-profit, stop after stop-loss or equity cap."
        )
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--mode", choices=("long", "short", "flow"), default=None, help="Alias for --side, or flow for BTC-led direction.")
    parser.add_argument("--side", choices=("LONG", "SHORT"), default="LONG")
    parser.add_argument("--flow-reference-symbol", default=DEFAULT_FLOW_REFERENCE_SYMBOL)
    parser.add_argument("--flow-lookback-hours", type=int, default=DEFAULT_FLOW_LOOKBACK_HOURS)
    parser.add_argument("--position-side", choices=("BOTH", "LONG", "SHORT"), default="BOTH")
    parser.add_argument(
        "--leverage",
        type=int,
        default=None,
        help=(
            f"Defaults to {DEFAULT_MIN_PROFIT_LEVERAGE}x for min-viable mode "
            f"and {DEFAULT_LEVERAGE}x otherwise."
        ),
    )
    parser.add_argument(
        "--max-min-notional-leverage",
        type=int,
        default=DEFAULT_MIN_PROFIT_MAX_MIN_NOTIONAL_LEVERAGE,
        help="Auto/adaptive mode may raise leverage up to this value when balance is too small for exchange minimum notional.",
    )
    parser.add_argument(
        "--adaptive-min-notional-leverage",
        action="store_true",
        help="For fixed-symbol runs, use the lowest leverage from --leverage up to --max-min-notional-leverage that satisfies exchange minimum size.",
    )
    parser.add_argument("--take-profit-mode", choices=("fixed-roe", "min-viable"), default="fixed-roe")
    parser.add_argument("--take-profit-roe", type=Decimal, default=DEFAULT_TAKE_PROFIT_ROE)
    parser.add_argument("--stop-loss-roe", type=Decimal, default=DEFAULT_STOP_LOSS_ROE)
    parser.add_argument("--emergency-stop-roe", type=Decimal, default=Decimal("0"))
    parser.add_argument("--maker-fee-rate", type=Decimal, default=None)
    parser.add_argument("--min-net-profit-usdt", type=Decimal, default=DEFAULT_MIN_NET_PROFIT_USDT)
    parser.add_argument(
        "--min-realized-equity-profit-usdt",
        type=Decimal,
        default=DEFAULT_MIN_REALIZED_EQUITY_PROFIT_USDT,
        help="After a TP fill, require account equity to rise by at least this much before re-entering.",
    )
    parser.add_argument("--min-margin-utilization-pct", type=Decimal, default=DEFAULT_MIN_MARGIN_UTILIZATION_PCT)
    parser.add_argument("--equity-stop", type=Decimal, default=DEFAULT_EQUITY_STOP)
    parser.add_argument("--allocation-pct", type=Decimal, default=Decimal("1"))
    parser.add_argument("--fee-buffer-pct", type=Decimal, default=Decimal("0.002"))
    parser.add_argument("--margin-type", choices=("UNCHANGED", "CROSSED", "ISOLATED"), default="UNCHANGED")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--entry-requote-seconds", type=float, default=4.0)
    parser.add_argument("--entry-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--green-exit-after-seconds", type=float, default=DEFAULT_GREEN_EXIT_AFTER_SECONDS)
    parser.add_argument("--green-exit-min-roe", type=Decimal, default=DEFAULT_GREEN_EXIT_MIN_ROE)
    parser.add_argument("--max-hold-seconds", type=float, default=DEFAULT_MAX_HOLD_SECONDS)
    parser.add_argument("--flat-exit-roe", type=Decimal, default=DEFAULT_FLAT_EXIT_ROE)
    parser.add_argument("--entry-max-chase-pct", type=Decimal, default=DEFAULT_ENTRY_MAX_CHASE_PCT)
    parser.add_argument("--entry-abandon-cooldown-seconds", type=float, default=DEFAULT_ENTRY_ABANDON_COOLDOWN_SECONDS)
    parser.add_argument(
        "--entry-mode",
        choices=("maker", "market", "maker-then-market"),
        default="maker",
        help="maker uses post-only entry; market uses taker entry; maker-then-market falls back to taker entry.",
    )
    parser.add_argument(
        "--entry-fallback-market",
        action="store_true",
        help="Deprecated alias for --entry-mode maker-then-market.",
    )
    parser.add_argument("--skip-pre-entry-safety", action="store_true")
    parser.add_argument("--no-candidate-retry-seconds", type=float, default=DEFAULT_NO_CANDIDATE_RETRY_SECONDS)
    parser.add_argument("--realized-flat-cooldown-seconds", type=float, default=DEFAULT_REALIZED_FLAT_COOLDOWN_SECONDS)
    parser.add_argument("--safety-recent-vol-minutes", type=int, default=DEFAULT_SAFETY_RECENT_VOL_MINUTES)
    parser.add_argument("--safety-min-recent-quote-volume-usdt", type=Decimal, default=DEFAULT_SAFETY_MIN_RECENT_QUOTE_VOLUME)
    parser.add_argument("--safety-min-recent-volatility-pct", type=Decimal, default=DEFAULT_SAFETY_MIN_RECENT_VOLATILITY_PCT)
    parser.add_argument("--safety-max-adverse-vol-ratio", type=Decimal, default=DEFAULT_SAFETY_MAX_ADVERSE_VOL_RATIO)
    parser.add_argument("--safety-liquidity-window-seconds", type=float, default=DEFAULT_SAFETY_LIQUIDITY_WINDOW_SECONDS)
    parser.add_argument("--safety-liquidity-samples", type=int, default=DEFAULT_SAFETY_LIQUIDITY_SAMPLES)
    parser.add_argument("--safety-max-depth-drop-pct", type=Decimal, default=DEFAULT_SAFETY_MAX_DEPTH_DROP_PCT)
    parser.add_argument("--safety-max-spread-widen-multiple", type=Decimal, default=DEFAULT_SAFETY_MAX_SPREAD_WIDEN_MULTIPLE)
    parser.add_argument("--safety-funding-buffer-seconds", type=float, default=DEFAULT_SAFETY_FUNDING_BUFFER_SECONDS)
    parser.add_argument("--max-same-symbol-streak", type=int, default=DEFAULT_MAX_SAME_SYMBOL_STREAK)
    parser.add_argument("--same-symbol-cooldown-seconds", type=float, default=DEFAULT_SAME_SYMBOL_COOLDOWN_SECONDS)
    parser.add_argument("--dry-equity", type=Decimal, default=Decimal("16"))
    parser.add_argument("--scan-only", action="store_true", help="Run the default volume/liquidity scan and exit.")
    parser.add_argument(
        "--scan-profile",
        choices=("auto", "signal", "liquidity"),
        default="auto",
        help="auto uses liquidity-first scanning for min-viable mode and signal scanning otherwise.",
    )
    parser.add_argument("--scan-min-volume-multiple", type=Decimal, default=DEFAULT_SCAN_MIN_VOLUME_MULTIPLE)
    parser.add_argument("--scan-min-recent-volume-ratio", type=Decimal, default=DEFAULT_SCAN_MIN_RECENT_VOLUME_RATIO)
    parser.add_argument("--scan-recent-hours", type=int, default=DEFAULT_SCAN_RECENT_HOURS)
    parser.add_argument("--scan-min-quote-volume-usdt", type=Decimal, default=DEFAULT_SCAN_MIN_QUOTE_VOLUME)
    parser.add_argument("--scan-max-spread-pct", type=Decimal, default=DEFAULT_SCAN_MAX_SPREAD_PCT)
    parser.add_argument("--scan-min-depth-1pct-usdt", type=Decimal, default=DEFAULT_SCAN_MIN_DEPTH_1PCT)
    parser.add_argument("--scan-min-depth-to-volume-pct", type=Decimal, default=DEFAULT_SCAN_MIN_DEPTH_TO_VOLUME_PCT)
    parser.add_argument("--scan-max-symbols", type=int, default=DEFAULT_SCAN_MAX_SYMBOLS)
    parser.add_argument("--scan-min-funding-rate", type=Decimal, default=DEFAULT_SCAN_MIN_FUNDING_RATE)
    parser.add_argument("--scan-allow-against-momentum", action="store_true")
    parser.add_argument("--scan-include-tradfi", action="store_true")
    parser.add_argument(
        "--scan-symbols",
        default="",
        help="Comma-separated symbol allowlist for auto mode. Use ALL to allow every scanned symbol.",
    )
    parser.add_argument("--base-url", default=os.environ.get("BINANCE_FAPI_BASE", "https://fapi.binance.com"))
    parser.add_argument("--check-auth", action="store_true", help="Check signed Binance futures auth without trading.")
    parser.add_argument("--live", action="store_true", help="Place real futures orders.")
    parser.add_argument(
        "--confirm-live",
        default="",
        help='Required with --live. Must be exactly: I_UNDERSTAND_THIS_CAN_LIQUIDATE',
    )
    return parser.parse_args()


def main() -> int:
    _load_local_env()
    args = parse_args()
    setattr(args, "_skip_symbols", set())
    setattr(args, "_cooldown_until", {})
    symbol = str(args.symbol).upper()
    auto_symbol = symbol == "AUTO"
    flow_mode = str(args.mode or "").lower() == "flow"
    direction = str(args.side if flow_mode else args.mode or args.side).upper()
    args.side = direction
    position_side = str(args.position_side).upper()
    if not flow_mode and position_side != "BOTH" and position_side != direction:
        raise SystemExit("--position-side must be BOTH or match --side.")
    if not Decimal("0") < args.allocation_pct <= Decimal("1"):
        raise SystemExit("--allocation-pct must be greater than 0 and no more than 1.")
    if not Decimal("0") <= args.fee_buffer_pct < Decimal("0.05"):
        raise SystemExit("--fee-buffer-pct must be from 0 to less than 0.05.")
    if args.leverage is None:
        args.leverage = (
            DEFAULT_MIN_PROFIT_LEVERAGE
            if args.take_profit_mode == "min-viable"
            else DEFAULT_LEVERAGE
        )
    if args.leverage < 1:
        raise SystemExit("--leverage must be at least 1.")
    if args.max_min_notional_leverage < args.leverage:
        args.max_min_notional_leverage = args.leverage
    if args.flow_lookback_hours < 1:
        raise SystemExit("--flow-lookback-hours must be at least 1.")
    if args.min_net_profit_usdt < 0:
        raise SystemExit("--min-net-profit-usdt must be zero or greater.")
    if args.min_realized_equity_profit_usdt < 0:
        raise SystemExit("--min-realized-equity-profit-usdt must be zero or greater.")
    if not Decimal("0") <= args.min_margin_utilization_pct <= Decimal("100"):
        raise SystemExit("--min-margin-utilization-pct must be from 0 to 100.")
    if args.emergency_stop_roe < 0:
        raise SystemExit("--emergency-stop-roe must be zero or greater.")
    if args.green_exit_after_seconds < 0:
        raise SystemExit("--green-exit-after-seconds must be zero or greater.")
    if args.green_exit_min_roe < 0:
        raise SystemExit("--green-exit-min-roe must be zero or greater.")
    if args.max_hold_seconds < 0:
        raise SystemExit("--max-hold-seconds must be zero or greater.")
    if args.flat_exit_roe < 0:
        raise SystemExit("--flat-exit-roe must be zero or greater.")
    if args.entry_max_chase_pct < 0:
        raise SystemExit("--entry-max-chase-pct must be zero or greater.")
    if args.entry_abandon_cooldown_seconds < 0:
        raise SystemExit("--entry-abandon-cooldown-seconds must be zero or greater.")
    if args.no_candidate_retry_seconds < 0:
        raise SystemExit("--no-candidate-retry-seconds must be zero or greater.")
    if args.realized_flat_cooldown_seconds < 0:
        raise SystemExit("--realized-flat-cooldown-seconds must be zero or greater.")
    if args.safety_recent_vol_minutes < 0:
        raise SystemExit("--safety-recent-vol-minutes must be zero or greater.")
    if args.safety_min_recent_quote_volume_usdt < 0:
        raise SystemExit("--safety-min-recent-quote-volume-usdt must be zero or greater.")
    if args.safety_min_recent_volatility_pct < 0:
        raise SystemExit("--safety-min-recent-volatility-pct must be zero or greater.")
    if args.safety_max_adverse_vol_ratio < 0:
        raise SystemExit("--safety-max-adverse-vol-ratio must be zero or greater.")
    if args.safety_liquidity_window_seconds < 0:
        raise SystemExit("--safety-liquidity-window-seconds must be zero or greater.")
    if args.safety_liquidity_samples < 0:
        raise SystemExit("--safety-liquidity-samples must be zero or greater.")
    if args.safety_max_depth_drop_pct < 0:
        raise SystemExit("--safety-max-depth-drop-pct must be zero or greater.")
    if args.safety_max_spread_widen_multiple < 0:
        raise SystemExit("--safety-max-spread-widen-multiple must be zero or greater.")
    if args.safety_funding_buffer_seconds < 0:
        raise SystemExit("--safety-funding-buffer-seconds must be zero or greater.")
    if args.max_same_symbol_streak < 0:
        raise SystemExit("--max-same-symbol-streak must be zero or greater.")
    if args.same_symbol_cooldown_seconds < 0:
        raise SystemExit("--same-symbol-cooldown-seconds must be zero or greater.")
    if args.scan_min_volume_multiple < 0:
        raise SystemExit("--scan-min-volume-multiple must be zero or greater.")
    if args.scan_min_recent_volume_ratio < 0:
        raise SystemExit("--scan-min-recent-volume-ratio must be zero or greater.")
    if args.scan_min_quote_volume_usdt < 0:
        raise SystemExit("--scan-min-quote-volume-usdt must be zero or greater.")
    if args.live and args.confirm_live != "I_UNDERSTAND_THIS_CAN_LIQUIDATE":
        raise SystemExit("Live mode requires --confirm-live I_UNDERSTAND_THIS_CAN_LIQUIDATE")
    if args.entry_fallback_market and args.entry_mode == "maker":
        args.entry_mode = "maker-then-market"
    apply_scan_profile(args)

    client = BinanceFuturesPublic(
        base_url=args.base_url,
        api_key=os.environ.get("BINANCE_API_KEY", ""),
        api_secret=os.environ.get("BINANCE_API_SECRET", ""),
        requests_per_second=float(os.environ.get("RATE_LIMIT_REQ_PER_SEC", "4")),
        timeout=int(os.environ.get("HTTP_TIMEOUT", "12")),
        retries=int(os.environ.get("RETRIES", "3")),
    )
    if flow_mode:
        direction, flow_hourly_change, flow_daily_change = market_flow_direction(
            client,
            reference_symbol=args.flow_reference_symbol,
            lookback_hours=args.flow_lookback_hours,
        )
        args.side = direction
        print(
            "Market flow mode: "
            f"{args.flow_reference_symbol.upper()} "
            f"{args.flow_lookback_hours}h {_format_decimal(flow_hourly_change)}%, "
            f"24h {_format_decimal(flow_daily_change)}% -> {direction}"
        )
        if position_side != "BOTH" and position_side != direction:
            raise SystemExit("--position-side must be BOTH or match resolved flow direction.")
    if args.check_auth:
        account = client.account_information_v3()
        positions = [] if symbol == "AUTO" else client.position_information_v3(symbol)
        print(
            "AUTH OK "
            f"canTrade={account.get('canTrade')} "
            f"assets={len(account.get('assets', []))} "
            f"{symbol}_position_rows={len(positions)}"
        )
        return 0

    print(f"Scan profile: {getattr(args, '_effective_scan_profile', 'signal')}")

    live_auto = bool(args.live and auto_symbol and not args.scan_only)
    fee_fallback = args.maker_fee_rate if args.maker_fee_rate is not None else DEFAULT_MAKER_FEE_RATE

    if auto_symbol and not live_auto:
        selected_symbol = select_auto_symbol(client, args=args, direction=direction)
        if args.scan_only:
            return 0
        if selected_symbol is None:
            print("No trade opened because no symbol passed the default auto filter.")
            return 0
        symbol = selected_symbol

    if not live_auto:
        rules = symbol_rules(client, symbol)
        entry_estimate = post_only_entry_price(order_book_top(client, symbol), direction=direction, rules=rules)
        live_maker_fee_rate = maker_fee_for_symbol(client, symbol=symbol, args=args, fallback=fee_fallback)

        if args.live:
            account = client.account_information_v3()
            equity = account_equity(account)
            STATS.observe_equity(equity)
            available = account_available_balance(account)
        else:
            equity = args.dry_equity
            available = args.dry_equity

        effective_leverage = effective_entry_leverage(
            symbol=symbol,
            rules=rules,
            mark_price=entry_estimate,
            available_balance=available,
            args=args,
            auto_symbol=False,
        )
        plan = plan_trade(
            rules=rules,
            symbol=symbol,
            direction=direction,
            leverage=effective_leverage,
            equity=equity,
            available_balance=available,
            mark_price=entry_estimate,
            allocation_pct=args.allocation_pct,
            fee_buffer_pct=args.fee_buffer_pct,
            take_profit_roe=args.take_profit_roe,
            stop_loss_roe=args.stop_loss_roe,
            take_profit_mode=args.take_profit_mode,
            entry_fee_rate=live_maker_fee_rate,
            exit_fee_rate=live_maker_fee_rate,
            min_net_profit=args.min_net_profit_usdt,
        )
        print_plan(plan)

        if not args.live:
            print("\nDRY RUN ONLY. Add --live --confirm-live I_UNDERSTAND_THIS_CAN_LIQUIDATE to place orders.")
            return 0
    else:
        rules = None
        live_maker_fee_rate = fee_fallback

    print("\nLIVE MODE ENABLED.")
    cycle = 0
    last_take_profit_symbol: str | None = None
    same_symbol_streak = 0
    while True:
        cycle += 1
        account = client.account_information_v3()
        equity = account_equity(account)
        STATS.observe_equity(equity)
        if auto_symbol:
            existing_position = current_any_symbol_position(
                client,
                direction=direction,
                position_side=position_side,
            )
            if existing_position is not None:
                symbol = str(existing_position.get("symbol", symbol)).upper()
                rules = symbol_rules(client, symbol)
                live_maker_fee_rate = maker_fee_for_symbol(client, symbol=symbol, args=args, fallback=fee_fallback)
        else:
            existing_position = current_symbol_position(
                client,
                symbol,
                direction=direction,
                position_side=position_side,
            )
        if existing_position is not None:
            live_plan = plan_from_position(
                existing_position,
                rules=rules,
                direction=direction,
                leverage=position_leverage(existing_position, args.leverage),
                equity=equity,
                available_balance=account_available_balance(account),
                take_profit_roe=args.take_profit_roe,
                stop_loss_roe=args.stop_loss_roe,
                take_profit_mode=args.take_profit_mode,
                entry_fee_rate=live_maker_fee_rate,
                exit_fee_rate=live_maker_fee_rate,
                min_net_profit=args.min_net_profit_usdt,
            )
            print(f"\nCycle {cycle}: managing existing {live_plan.symbol} {live_plan.direction} position")
            print_plan(live_plan)
            cycle_equity_before = equity
            cycle_started_wall = time.time()
            cycle_started_ms = int(time.time() * 1000) - 1000
            cycle_started_at = time.monotonic()
            outcome = run_position_cycle(
                client,
                plan=live_plan,
                rules=rules,
                position_side=position_side,
                poll_seconds=args.poll_seconds,
                green_exit_after_seconds=args.green_exit_after_seconds,
                green_exit_min_roe=args.green_exit_min_roe,
                max_hold_seconds=args.max_hold_seconds,
                flat_exit_roe=args.flat_exit_roe,
                exit_requote_seconds=args.entry_requote_seconds,
                emergency_stop_roe=args.emergency_stop_roe,
            )
            cycle_ended_wall = time.time()
            cycle_ended_ms = int(time.time() * 1000) + 1000
            cycle_elapsed_seconds = time.monotonic() - cycle_started_at
            account = client.account_information_v3()
            equity = account_equity(account)
            print(f"Cycle {cycle} exit: {outcome}; equity now {_format_decimal(equity)} USDT")
            totals = print_cycle_accounting(
                client,
                symbol=live_plan.symbol,
                equity_before=cycle_equity_before,
                equity_after=equity,
                started_ms=cycle_started_ms,
                ended_ms=cycle_ended_ms,
                elapsed_seconds=cycle_elapsed_seconds,
            )
            record_cycle_stats(
                plan=live_plan,
                outcome=outcome,
                equity_before=cycle_equity_before,
                equity_after=equity,
                started_at=cycle_started_wall,
                ended_at=cycle_ended_wall,
                elapsed_seconds=cycle_elapsed_seconds,
                totals=totals,
            )
            if outcome == "take_profit" and not realized_trade_profit_ok(
                totals=totals,
                equity_before=cycle_equity_before,
                equity_after=equity,
                min_profit=args.min_realized_equity_profit_usdt,
            ):
                required = (
                    "any positive increase"
                    if args.min_realized_equity_profit_usdt <= 0
                    else f"at least {_format_decimal(args.min_realized_equity_profit_usdt)} USDT"
                )
                print(
                    "Take-profit filled, but realized trade PnL did not show "
                    f"{required}."
                )
                if auto_symbol:
                    set_symbol_cooldown(
                        args,
                        live_plan.symbol,
                        args.realized_flat_cooldown_seconds,
                        "TP fill did not show positive realized trade PnL",
                    )
                    continue
                print("Stopping instead of re-entering.")
                return 0
            if outcome == "take_profit" and equity < args.equity_stop:
                if auto_symbol:
                    last_take_profit_symbol, same_symbol_streak = record_take_profit_symbol(
                        args,
                        symbol=live_plan.symbol,
                        last_symbol=last_take_profit_symbol,
                        streak=same_symbol_streak,
                    )
                continue
            if outcome == "take_profit":
                print(f"Equity stop reached: {_format_decimal(equity)} >= {_format_decimal(args.equity_stop)}.")
            if auto_symbol:
                set_symbol_cooldown(
                    args,
                    live_plan.symbol,
                    args.entry_abandon_cooldown_seconds,
                    f"{outcome} exit",
                )
                continue
            return 0

        requested_symbol = pop_requested_symbol()
        if requested_symbol:
            requested_symbol = requested_symbol.upper()
            if auto_symbol:
                print(f"Symbol switch request {requested_symbol} ignored while --symbol AUTO is active.")
            elif requested_symbol != symbol:
                print(f"Switching next entry from {symbol} to {requested_symbol}.")
                symbol = requested_symbol
                rules = symbol_rules(client, symbol)
                live_maker_fee_rate = maker_fee_for_symbol(
                    client,
                    symbol=symbol,
                    args=args,
                    fallback=fee_fallback,
                )
            else:
                print(f"Already trading {symbol}.")

        if equity >= args.equity_stop:
            print(f"Equity stop reached before entry: {_format_decimal(equity)} >= {_format_decimal(args.equity_stop)}.")
            if symbol != "AUTO":
                cancel_symbol_orders(client, symbol)
            return 0

        if auto_symbol:
            selected_plan = select_auto_trade_plan(
                client,
                args=args,
                direction=direction,
                account=account,
                fee_fallback=fee_fallback,
            )
            if selected_plan is None:
                print(
                    "No viable auto symbol could be sized safely right now. "
                    f"Retrying in {int(args.no_candidate_retry_seconds)}s."
                )
                bot_sleep(max(1.0, float(args.no_candidate_retry_seconds)))
                continue
            symbol, rules, live_maker_fee_rate, plan = selected_plan
        else:
            apply_margin_type_if_requested(client, symbol=symbol, args=args)
            entry_estimate = post_only_entry_price(order_book_top(client, symbol), direction=direction, rules=rules)
            effective_leverage = effective_entry_leverage(
                symbol=symbol,
                rules=rules,
                mark_price=entry_estimate,
                available_balance=account_available_balance(account),
                args=args,
                auto_symbol=False,
            )
            client.change_initial_leverage(symbol, effective_leverage)
            plan = plan_trade(
                rules=rules,
                symbol=symbol,
                direction=direction,
                leverage=effective_leverage,
                equity=equity,
                available_balance=account_available_balance(account),
                mark_price=entry_estimate,
                allocation_pct=args.allocation_pct,
                fee_buffer_pct=args.fee_buffer_pct,
                take_profit_roe=args.take_profit_roe,
                stop_loss_roe=args.stop_loss_roe,
                take_profit_mode=args.take_profit_mode,
                entry_fee_rate=live_maker_fee_rate,
                exit_fee_rate=live_maker_fee_rate,
                min_net_profit=args.min_net_profit_usdt,
            )
        print(f"\nCycle {cycle}: entering {plan.symbol} {plan.direction}")
        print_plan(plan)
        if not auto_symbol and maybe_skip_underutilized_symbol(
            args,
            plan=plan,
            rules=rules,
            auto_symbol=auto_symbol,
        ):
            continue

        if args.skip_pre_entry_safety:
            print(f"Pre-entry safety checks skipped for {symbol}.")
        else:
            safe_to_enter, safety_reason = pre_entry_safety_check(
                client,
                symbol=symbol,
                direction=direction,
                min_depth_1pct=args.scan_min_depth_1pct_usdt,
                recent_vol_minutes=args.safety_recent_vol_minutes,
                max_adverse_vol_ratio=args.safety_max_adverse_vol_ratio,
                liquidity_window_seconds=args.safety_liquidity_window_seconds,
                liquidity_samples=args.safety_liquidity_samples,
                max_depth_drop_pct=args.safety_max_depth_drop_pct,
                max_spread_widen_multiple=args.safety_max_spread_widen_multiple,
                funding_buffer_seconds=args.safety_funding_buffer_seconds,
                min_recent_quote_volume=args.safety_min_recent_quote_volume_usdt,
                min_recent_volatility_pct=args.safety_min_recent_volatility_pct,
            )
            if not safe_to_enter:
                print(f"Pre-entry safety filter blocked {symbol}: {safety_reason}")
                if auto_symbol:
                    set_symbol_cooldown(args, symbol, args.entry_abandon_cooldown_seconds, safety_reason)
                    continue
                bot_sleep(max(1.0, float(args.no_candidate_retry_seconds)))
                continue
            print(f"Pre-entry safety checks passed for {symbol}.")

        cycle_equity_before = equity
        cycle_started_wall = time.time()
        cycle_started_ms = int(time.time() * 1000) - 1000
        cycle_started_at = time.monotonic()
        cancel_symbol_orders(client, symbol)
        try:
            if args.entry_mode == "market":
                print("Entry mode market: placing taker entry now.")
                position = place_market_entry_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                )
            else:
                position = place_maker_entry_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                    requote_seconds=args.entry_requote_seconds,
                    timeout_seconds=args.entry_timeout_seconds,
                    max_chase_pct=args.entry_max_chase_pct,
                )
        except EntryAbandoned as exc:
            print(f"Maker entry abandoned: {exc}")
            if args.entry_mode == "maker-then-market":
                print("Entry fallback enabled: placing market entry now.")
                position = place_market_entry_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                )
            elif auto_symbol:
                set_symbol_cooldown(args, symbol, args.entry_abandon_cooldown_seconds, str(exc))
                continue
            else:
                bot_sleep(max(1.0, float(args.no_candidate_retry_seconds)))
                continue
        except RuntimeError as exc:
            print(f"Maker entry failed: {exc}")
            if args.entry_mode == "maker-then-market":
                print("Entry fallback enabled: placing market entry now.")
                position = place_market_entry_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                )
            elif auto_symbol:
                set_symbol_cooldown(args, symbol, args.entry_abandon_cooldown_seconds, str(exc))
                continue
            else:
                bot_sleep(max(1.0, float(args.no_candidate_retry_seconds)))
                continue
        except BinanceHTTPError as exc:
            if auto_symbol and is_untradable_error(exc):
                mark_auto_symbol_untradable(args, symbol, exc, "maker entry")
                continue
            if args.entry_mode == "maker-then-market" and not is_untradable_error(exc):
                print(f"Maker entry API error, trying market fallback: {exc.payload}")
                position = place_market_entry_and_wait(
                    client,
                    plan,
                    rules=rules,
                    position_side=position_side,
                )
            else:
                raise
        entry_price = _decimal(position.get("entryPrice"), str(plan.mark_price))
        actual_quantity = abs(_decimal(position.get("positionAmt"), str(plan.quantity)))
        live_plan = plan_at_entry_price(plan, rules, entry_price, quantity=actual_quantity)
        print(f"Live entry price: {_format_decimal(entry_price)}")

        outcome = run_position_cycle(
            client,
            plan=live_plan,
            rules=rules,
            position_side=position_side,
            poll_seconds=args.poll_seconds,
            green_exit_after_seconds=args.green_exit_after_seconds,
            green_exit_min_roe=args.green_exit_min_roe,
            max_hold_seconds=args.max_hold_seconds,
            flat_exit_roe=args.flat_exit_roe,
            exit_requote_seconds=args.entry_requote_seconds,
            emergency_stop_roe=args.emergency_stop_roe,
        )
        cycle_ended_wall = time.time()
        cycle_ended_ms = int(time.time() * 1000) + 1000
        cycle_elapsed_seconds = time.monotonic() - cycle_started_at
        account = client.account_information_v3()
        equity = account_equity(account)
        print(f"Cycle {cycle} exit: {outcome}; equity now {_format_decimal(equity)} USDT")
        totals = print_cycle_accounting(
            client,
            symbol=live_plan.symbol,
            equity_before=cycle_equity_before,
            equity_after=equity,
            started_ms=cycle_started_ms,
            ended_ms=cycle_ended_ms,
            elapsed_seconds=cycle_elapsed_seconds,
        )
        record_cycle_stats(
            plan=live_plan,
            outcome=outcome,
            equity_before=cycle_equity_before,
            equity_after=equity,
            started_at=cycle_started_wall,
            ended_at=cycle_ended_wall,
            elapsed_seconds=cycle_elapsed_seconds,
            totals=totals,
        )

        if outcome == "take_profit":
            if not realized_trade_profit_ok(
                totals=totals,
                equity_before=cycle_equity_before,
                equity_after=equity,
                min_profit=args.min_realized_equity_profit_usdt,
            ):
                required = (
                    "any positive increase"
                    if args.min_realized_equity_profit_usdt <= 0
                    else f"at least {_format_decimal(args.min_realized_equity_profit_usdt)} USDT"
                )
                print(
                    "Take-profit filled, but realized trade PnL did not show "
                    f"{required}."
                )
                if auto_symbol:
                    set_symbol_cooldown(
                        args,
                        live_plan.symbol,
                        args.realized_flat_cooldown_seconds,
                        "TP fill did not show positive realized trade PnL",
                    )
                    continue
                print("Stopping instead of re-entering.")
                return 0
            if auto_symbol:
                last_take_profit_symbol, same_symbol_streak = record_take_profit_symbol(
                    args,
                    symbol=live_plan.symbol,
                    last_symbol=last_take_profit_symbol,
                    streak=same_symbol_streak,
                )
            if equity >= args.equity_stop:
                print(f"Equity stop reached: {_format_decimal(equity)} >= {_format_decimal(args.equity_stop)}.")
                return 0
            continue
        if auto_symbol:
            set_symbol_cooldown(
                args,
                live_plan.symbol,
                args.entry_abandon_cooldown_seconds,
                f"{outcome} exit",
            )
            continue
        return 0


def print_binance_error(exc: BinanceHTTPError) -> Any:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    code = payload.get("code")
    msg = payload.get("msg", exc.payload)
    print(f"\nBinance API error {exc.status_code} code={code}: {msg}")
    if code == -1022:
        print(
            "This means Binance rejected the request signature. Check that BINANCE_API_KEY "
            "and BINANCE_API_SECRET are a matching pair, that the secret was copied exactly, "
            "and that you are not using stale keys."
        )
    elif code == -2015:
        print(
            "This usually means the key is invalid for this endpoint, IP-restricted, "
            "or missing futures trading/API permissions."
        )
    return code


if __name__ == "__main__":
    while True:
        try:
            raise SystemExit(main())
        except BinanceHTTPError as exc:
            code = print_binance_error(exc)
            if code in {-1022, -2015}:
                raise SystemExit(1)
            print(f"Recovering in {int(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)}s and checking positions again.")
            bot_sleep(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)
        except RuntimeError as exc:
            print(f"\nRecoverable runtime error: {exc}")
            print(f"Recovering in {int(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)}s and checking positions again.")
            bot_sleep(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            raise SystemExit(130)
        except Exception as exc:
            print(f"\nUnexpected error: {type(exc).__name__}: {exc}")
            print(f"Recovering in {int(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)}s and checking positions again.")
            bot_sleep(DEFAULT_NO_CANDIDATE_RETRY_SECONDS)
