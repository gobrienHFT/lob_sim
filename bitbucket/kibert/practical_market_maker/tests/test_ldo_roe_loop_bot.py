from decimal import Decimal
from types import SimpleNamespace

from binance_futures import BinanceHTTPError
from ldo_roe_loop_bot import (
    BookLiquiditySnapshot,
    BookTop,
    DEFAULT_MAKER_FEE_RATE,
    FundingSnapshot,
    PlannedTrade,
    SymbolRules,
    adverse_volatility_reason,
    closed_hour_change_pct,
    directional_volatility_from_klines,
    entry_moved_away,
    effective_entry_leverage,
    equity_delta,
    estimated_net_profit,
    funding_is_too_close,
    income_totals,
    is_insufficient_margin_error,
    is_untradable_error,
    leverage_for_exchange_minimums,
    liquidity_decay_reason,
    liquidity_metrics,
    market_flow_direction,
    margin_utilization_pct,
    minimum_viable_take_profit_price,
    plan_trade,
    position_leverage,
    post_only_close_price,
    quote_volume_windows,
    recent_quote_volume_from_klines,
    realized_equity_profit_ok,
    realized_trade_profit,
    realized_trade_profit_ok,
    scan_auto_symbol,
    select_auto_trade_plan,
    unrealized_roe,
)


def test_minimum_viable_long_take_profit_covers_maker_fees() -> None:
    entry_price = Decimal("0.4397")
    quantity = Decimal("181")
    take_profit = minimum_viable_take_profit_price(
        entry_price,
        quantity=quantity,
        direction="LONG",
        entry_fee_rate=DEFAULT_MAKER_FEE_RATE,
        exit_fee_rate=DEFAULT_MAKER_FEE_RATE,
        min_net_profit=Decimal("0.001"),
        tick_size=Decimal("0.0001"),
    )
    plan = PlannedTrade(
        symbol="LDOUSDT",
        direction="LONG",
        leverage=5,
        equity=Decimal("16"),
        available_balance=Decimal("16"),
        mark_price=entry_price,
        quantity=quantity,
        notional=entry_price * quantity,
        take_profit_price=take_profit,
        stop_loss_price=Decimal("0.3606"),
        take_profit_roe=Decimal("0.05"),
        stop_loss_roe=Decimal("0.90"),
        take_profit_mode="min-viable",
        entry_fee_rate=DEFAULT_MAKER_FEE_RATE,
        exit_fee_rate=DEFAULT_MAKER_FEE_RATE,
        min_net_profit=Decimal("0.001"),
    )

    assert take_profit > entry_price
    assert estimated_net_profit(plan) >= Decimal("0.001")


def test_minimum_viable_short_take_profit_covers_maker_fees() -> None:
    entry_price = Decimal("0.4397")
    quantity = Decimal("181")
    take_profit = minimum_viable_take_profit_price(
        entry_price,
        quantity=quantity,
        direction="SHORT",
        entry_fee_rate=DEFAULT_MAKER_FEE_RATE,
        exit_fee_rate=DEFAULT_MAKER_FEE_RATE,
        min_net_profit=Decimal("0.001"),
        tick_size=Decimal("0.0001"),
    )
    plan = PlannedTrade(
        symbol="LDOUSDT",
        direction="SHORT",
        leverage=5,
        equity=Decimal("16"),
        available_balance=Decimal("16"),
        mark_price=entry_price,
        quantity=quantity,
        notional=entry_price * quantity,
        take_profit_price=take_profit,
        stop_loss_price=Decimal("0.5188"),
        take_profit_roe=Decimal("0.05"),
        stop_loss_roe=Decimal("0.90"),
        take_profit_mode="min-viable",
        entry_fee_rate=DEFAULT_MAKER_FEE_RATE,
        exit_fee_rate=DEFAULT_MAKER_FEE_RATE,
        min_net_profit=Decimal("0.001"),
    )

    assert take_profit < entry_price
    assert estimated_net_profit(plan) >= Decimal("0.001")


def test_realized_equity_profit_requires_account_equity_to_increase() -> None:
    assert equity_delta(Decimal("245.82260606"), Decimal("245.81414919")) == Decimal("-0.00845687")
    assert not realized_equity_profit_ok(
        Decimal("245.82260606"),
        Decimal("245.81414919"),
        min_profit=Decimal("0"),
    )
    assert realized_equity_profit_ok(
        Decimal("245.81414919"),
        Decimal("245.82047103"),
        min_profit=Decimal("0"),
    )
    assert not realized_equity_profit_ok(
        Decimal("245.81414919"),
        Decimal("245.82047103"),
        min_profit=Decimal("0.01"),
    )


def test_income_totals_subtracts_notional_commissions() -> None:
    totals = income_totals(
        [
            {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "0.50"},
            {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-0.24"},
            {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-0.24"},
            {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "-0.01"},
            {"symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": "99"},
        ],
        symbol="BTCUSDT",
    )

    assert totals.realized_pnl == Decimal("0.50")
    assert totals.commission == Decimal("-0.48")
    assert totals.funding_fee == Decimal("-0.01")
    assert totals.net == Decimal("0.01")


def test_realized_trade_profit_prefers_income_ledger_over_equity_sample() -> None:
    totals = income_totals(
        [
            {"symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": "0.03654"},
            {"symbol": "ETHUSDT", "incomeType": "COMMISSION", "income": "-0.0263915"},
        ],
        symbol="ETHUSDT",
    )

    pnl, source = realized_trade_profit(
        totals=totals,
        equity_before=Decimal("34.20017358"),
        equity_after=Decimal("34.19680825"),
    )

    assert pnl == Decimal("0.0101485")
    assert source == "income ledger"
    assert realized_trade_profit_ok(
        totals=totals,
        equity_before=Decimal("34.20017358"),
        equity_after=Decimal("34.19680825"),
        min_profit=Decimal("0"),
    )


def test_unrealized_roe_uses_direction_and_leverage() -> None:
    long_roe = unrealized_roe(
        Decimal("100"),
        Decimal("100.6"),
        direction="LONG",
        leverage=5,
    )
    short_roe = unrealized_roe(
        Decimal("100"),
        Decimal("99.4"),
        direction="SHORT",
        leverage=5,
    )

    assert long_roe == Decimal("0.030")
    assert short_roe == Decimal("0.030")


def test_post_only_close_price_posts_on_exit_side() -> None:
    rules = SymbolRules(
        symbol="LDOUSDT",
        tick_size=Decimal("0.0001"),
        qty_step=Decimal("1"),
        min_qty=Decimal("1"),
        min_notional=Decimal("5"),
    )
    book = BookTop(bid=Decimal("0.45123"), ask=Decimal("0.45129"))

    assert post_only_close_price(book, direction="LONG", rules=rules) == Decimal("0.4513")
    assert post_only_close_price(book, direction="SHORT", rules=rules) == Decimal("0.4512")


def test_margin_utilization_reveals_btc_step_underuse() -> None:
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    plan = plan_trade(
        rules=rules,
        symbol="BTCUSDT",
        direction="LONG",
        leverage=5,
        equity=Decimal("20.42"),
        available_balance=Decimal("20.42"),
        mark_price=Decimal("75986.7"),
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0"),
        take_profit_roe=Decimal("0.05"),
        stop_loss_roe=Decimal("0.90"),
        take_profit_mode="fixed-roe",
        entry_fee_rate=DEFAULT_MAKER_FEE_RATE,
        exit_fee_rate=DEFAULT_MAKER_FEE_RATE,
        min_net_profit=Decimal("0.001"),
    )

    assert plan.quantity == Decimal("0.001")
    assert margin_utilization_pct(plan) < Decimal("95")


def test_entry_moved_away_blocks_chasing() -> None:
    assert entry_moved_away(
        Decimal("100"),
        Decimal("100.6"),
        direction="LONG",
        max_chase_pct=Decimal("0.50"),
    )
    assert entry_moved_away(
        Decimal("100"),
        Decimal("99.4"),
        direction="SHORT",
        max_chase_pct=Decimal("0.50"),
    )
    assert not entry_moved_away(
        Decimal("100"),
        Decimal("99.8"),
        direction="LONG",
        max_chase_pct=Decimal("0.50"),
    )


def test_funding_buffer_blocks_near_funding_time() -> None:
    near = FundingSnapshot(funding_rate=Decimal("0.0001"), next_funding_time=103_000)
    far = FundingSnapshot(funding_rate=Decimal("0.0001"), next_funding_time=1_000_000)

    assert funding_is_too_close(near, buffer_seconds=5, now_ms=100_000)
    assert not funding_is_too_close(far, buffer_seconds=5, now_ms=100_000)


def test_adverse_volatility_reason_flags_downside_pressure_for_longs() -> None:
    rows = [
        [0, 0, 0, 0, "100"],
        [0, 0, 0, 0, "101"],
        [0, 0, 0, 0, "100"],
        [0, 0, 0, 0, "98"],
        [0, 0, 0, 0, "98"],
    ]
    upside, downside = directional_volatility_from_klines(rows)

    reason = adverse_volatility_reason(
        direction="LONG",
        upside=upside,
        downside=downside,
        max_adverse_ratio=Decimal("1.20"),
    )

    assert reason is not None
    assert "downside" in reason


def test_recent_quote_volume_from_klines_sums_closed_rows() -> None:
    rows = [
        [0, 0, 0, 0, "100", 0, 0, "1000"],
        [0, 0, 0, 0, "101", 0, 0, "2000"],
        [0, 0, 0, 0, "102", 0, 0, "9999"],
    ]

    assert recent_quote_volume_from_klines(rows) == Decimal("3000")


def test_closed_hour_change_pct_uses_closed_candle_window() -> None:
    rows = [
        [0, "100", 0, 0, "99"],
        [0, "99", 0, 0, "98"],
        [0, "98", 0, 0, "120"],
    ]

    assert closed_hour_change_pct(rows, lookback_hours=1) == Decimal("-1.010101010101010101010101010")


def test_liquidity_decay_reason_flags_support_pull() -> None:
    snapshots = [
        BookLiquiditySnapshot(
            spread_pct=Decimal("0.01"),
            bid_depth_1pct=Decimal("10000"),
            ask_depth_1pct=Decimal("10000"),
        ),
        BookLiquiditySnapshot(
            spread_pct=Decimal("0.01"),
            bid_depth_1pct=Decimal("5000"),
            ask_depth_1pct=Decimal("10000"),
        ),
    ]

    reason = liquidity_decay_reason(
        snapshots,
        direction="LONG",
        min_depth_1pct=Decimal("2500"),
        max_depth_drop_pct=Decimal("35"),
        max_spread_widen_multiple=Decimal("2"),
    )

    assert reason is not None
    assert "bid depth dropped" in reason


def test_quote_volume_windows_requires_doubled_volume_and_recent_follow_through() -> None:
    rows = []
    for _ in range(24):
        rows.append([0, 0, 0, 0, 0, 0, 0, "100"])
    for index in range(24):
        volume = "250" if index >= 21 else "200"
        rows.append([0, 0, 0, 0, 0, 0, 0, volume])
    rows.append([0, 0, 0, 0, 0, 0, 0, "1"])

    windows = quote_volume_windows(rows, recent_hours=3)

    assert windows is not None
    assert windows.previous_24h_quote_volume == Decimal("2400")
    assert windows.current_24h_quote_volume == Decimal("4950")
    assert windows.volume_multiple > Decimal("2")
    assert windows.recent_volume_ratio == Decimal("1.25")


def test_liquidity_metrics_flags_market_maker_pull_proxy() -> None:
    thin_depth = {
        "bids": [["99.9", "1"], ["99.5", "1"]],
        "asks": [["100.1", "1"], ["100.5", "1"]],
    }

    metrics = liquidity_metrics(
        thin_depth,
        quote_volume_24h=Decimal("1000000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
    )

    assert metrics is not None
    assert metrics.mm_pulled
    assert "thin bid depth" in metrics.note


def test_liquidity_metrics_accepts_tight_deep_book() -> None:
    deep_book = {
        "bids": [["99.99", "100"], ["99.5", "100"], ["99.0", "100"]],
        "asks": [["100.01", "100"], ["100.5", "100"], ["101.0", "100"]],
    }

    metrics = liquidity_metrics(
        deep_book,
        quote_volume_24h=Decimal("1000000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
    )

    assert metrics is not None
    assert not metrics.mm_pulled


class FakeScanClient:
    def exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "CRYPTOUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
                {
                    "symbol": "TRADFIUSDT",
                    "contractType": "TRADIFI_PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "TRADFI",
                },
                {
                    "symbol": "XAGUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COMMODITY",
                },
                {
                    "symbol": "SKIPUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
            ]
        }

    def ticker_24hr(self) -> list[dict]:
        return [
            {"symbol": "TRADFIUSDT", "quoteVolume": "9000000", "priceChangePercent": "12"},
            {"symbol": "XAGUSDT", "quoteVolume": "8500000", "priceChangePercent": "13"},
            {"symbol": "SKIPUSDT", "quoteVolume": "8000000", "priceChangePercent": "11"},
            {"symbol": "CRYPTOUSDT", "quoteVolume": "7000000", "priceChangePercent": "10"},
        ]

    def mark_price(self, symbol: str | None = None) -> list[dict]:
        return [
            {"symbol": "TRADFIUSDT", "lastFundingRate": "0.0006", "nextFundingTime": "1"},
            {"symbol": "XAGUSDT", "lastFundingRate": "0.0008", "nextFundingTime": "1"},
            {"symbol": "SKIPUSDT", "lastFundingRate": "0.0005", "nextFundingTime": "1"},
            {"symbol": "CRYPTOUSDT", "lastFundingRate": "0.0004", "nextFundingTime": "1"},
        ]

    def klines(self, symbol: str, interval: str, limit: int) -> list[list[str]]:
        rows = []
        for _ in range(24):
            rows.append(["0", "0", "0", "0", "0", "0", "0", "100"])
        for _ in range(24):
            rows.append(["0", "0", "0", "0", "0", "0", "0", "250"])
        rows.append(["0", "0", "0", "0", "0", "0", "0", "1"])
        return rows

    def depth(self, symbol: str, limit: int) -> dict:
        return {
            "bids": [["99.99", "100"], ["99.5", "100"], ["99.0", "100"]],
            "asks": [["100.01", "100"], ["100.5", "100"], ["101.0", "100"]],
        }


def test_scan_auto_symbol_excludes_tradfi_and_skipped_symbols() -> None:
    candidates = scan_auto_symbol(
        FakeScanClient(),
        direction="LONG",
        min_volume_multiple=Decimal("2"),
        min_recent_volume_ratio=Decimal("1"),
        recent_hours=3,
        min_quote_volume=Decimal("1000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
        max_symbols=10,
        allow_against_momentum=False,
        include_tradfi=False,
        min_funding_rate=Decimal("0"),
        funding_buffer_seconds=0,
        skip_symbols={"SKIPUSDT"},
    )

    assert [candidate.symbol for candidate in candidates] == ["CRYPTOUSDT"]


class FundingRankClient(FakeScanClient):
    def exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "LOWFUNDUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
                {
                    "symbol": "HIGHFUNDUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
                {
                    "symbol": "NEGATIVEUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
            ]
        }

    def ticker_24hr(self) -> list[dict]:
        return [
            {"symbol": "LOWFUNDUSDT", "quoteVolume": "9000000", "priceChangePercent": "12"},
            {"symbol": "NEGATIVEUSDT", "quoteVolume": "8000000", "priceChangePercent": "11"},
            {"symbol": "HIGHFUNDUSDT", "quoteVolume": "7000000", "priceChangePercent": "10"},
        ]

    def mark_price(self, symbol: str | None = None) -> list[dict]:
        return [
            {"symbol": "LOWFUNDUSDT", "lastFundingRate": "0.0001", "nextFundingTime": "1"},
            {"symbol": "NEGATIVEUSDT", "lastFundingRate": "-0.0002", "nextFundingTime": "1"},
            {"symbol": "HIGHFUNDUSDT", "lastFundingRate": "0.0007", "nextFundingTime": "1"},
        ]


class ShortFundingRankClient(FundingRankClient):
    def ticker_24hr(self) -> list[dict]:
        return [
            {"symbol": "LOWFUNDUSDT", "quoteVolume": "9000000", "priceChangePercent": "-12"},
            {"symbol": "NEGATIVEUSDT", "quoteVolume": "8000000", "priceChangePercent": "-11"},
            {"symbol": "HIGHFUNDUSDT", "quoteVolume": "7000000", "priceChangePercent": "-10"},
        ]


class LiquidityRankClient(FakeScanClient):
    def exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "THINUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
                {
                    "symbol": "DEEPUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
            ]
        }

    def ticker_24hr(self) -> list[dict]:
        return [
            {"symbol": "THINUSDT", "quoteVolume": "9000000", "priceChangePercent": "-12"},
            {"symbol": "DEEPUSDT", "quoteVolume": "8000000", "priceChangePercent": "-10"},
        ]

    def mark_price(self, symbol: str | None = None) -> list[dict]:
        return []

    def depth(self, symbol: str, limit: int) -> dict:
        if symbol == "DEEPUSDT":
            return {
                "bids": [["99.99", "1000"], ["99.5", "1000"], ["99.0", "1000"]],
                "asks": [["100.01", "1000"], ["100.5", "1000"], ["101.0", "1000"]],
            }
        return {
            "bids": [["99.99", "100"], ["99.5", "100"], ["99.0", "100"]],
            "asks": [["100.01", "100"], ["100.5", "100"], ["101.0", "100"]],
        }


class FlowClient:
    def __init__(self, *, hourly_down: bool, daily_change: str) -> None:
        self.hourly_down = hourly_down
        self.daily_change = daily_change

    def klines(self, symbol: str, interval: str, limit: int) -> list[list[str]]:
        if self.hourly_down:
            return [
                [0, "100", "101", "98", "99"],
                [0, "99", "100", "97", "98"],
                [0, "98", "99", "97", "98.5"],
            ]
        return [
            [0, "100", "101", "99", "101"],
            [0, "101", "102", "100", "102"],
            [0, "102", "103", "101", "102.5"],
        ]

    def ticker_24hr(self) -> list[dict]:
        return [{"symbol": "BTCUSDT", "priceChangePercent": self.daily_change}]


class AutoPlanFallbackClient:
    def __init__(self) -> None:
        self.leverage_changes: list[str] = []

    def exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
                {
                    "symbol": "ETHUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
                {
                    "symbol": "DOGEUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
                        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
            ]
        }

    def ticker_24hr(self) -> list[dict]:
        return [
            {"symbol": "BTCUSDT", "quoteVolume": "1000000000", "priceChangePercent": "1"},
            {"symbol": "ETHUSDT", "quoteVolume": "900000000", "priceChangePercent": "1"},
            {"symbol": "DOGEUSDT", "quoteVolume": "800000000", "priceChangePercent": "1"},
        ]

    def mark_price(self, symbol: str | None = None) -> list[dict]:
        return [
            {"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "nextFundingTime": "1"},
            {"symbol": "ETHUSDT", "lastFundingRate": "0.0001", "nextFundingTime": "1"},
            {"symbol": "DOGEUSDT", "lastFundingRate": "0.0001", "nextFundingTime": "1"},
        ]

    def klines(self, symbol: str, interval: str, limit: int) -> list[list[str]]:
        current = "500" if symbol == "BTCUSDT" else "400"
        rows = []
        for _ in range(24):
            rows.append(["0", "0", "0", "0", "0", "0", "0", "100"])
        for _ in range(24):
            rows.append(["0", "0", "0", "0", "0", "0", "0", current])
        rows.append(["0", "0", "0", "0", "0", "0", "0", "1"])
        return rows

    def depth(self, symbol: str, limit: int) -> dict:
        if symbol == "BTCUSDT":
            return {
                "bids": [["76000", "5"], ["75500", "5"], ["75250", "5"]],
                "asks": [["76001", "5"], ["76500", "5"], ["76750", "5"]],
            }
        if symbol == "DOGEUSDT":
            return {
                "bids": [["0.10000", "500000"], ["0.09950", "500000"], ["0.09900", "500000"]],
                "asks": [["0.10001", "500000"], ["0.10050", "500000"], ["0.10100", "500000"]],
            }
        return {
            "bids": [["3500.00", "50"], ["3480.00", "50"], ["3470.00", "50"]],
            "asks": [["3500.10", "50"], ["3520.00", "50"], ["3530.00", "50"]],
        }

    def user_commission_rate(self, symbol: str) -> dict:
        return {"makerCommissionRate": "0.0002"}

    def change_initial_leverage(self, symbol: str, leverage: int) -> dict:
        self.leverage_changes.append(symbol)
        return {"symbol": symbol, "leverage": leverage}


def test_market_flow_direction_shorts_only_when_1h_and_24h_are_down() -> None:
    down_client = FlowClient(hourly_down=True, daily_change="-2.5")
    mixed_client = FlowClient(hourly_down=True, daily_change="1.0")

    direction, hourly, daily = market_flow_direction(
        down_client,
        reference_symbol="BTCUSDT",
        lookback_hours=1,
    )
    mixed_direction, _, _ = market_flow_direction(
        mixed_client,
        reference_symbol="BTCUSDT",
        lookback_hours=1,
    )

    assert direction == "SHORT"
    assert hourly < 0
    assert daily < 0
    assert mixed_direction == "LONG"


def test_auto_trade_plan_falls_back_when_btc_quantity_is_below_minimum() -> None:
    client = AutoPlanFallbackClient()
    args = SimpleNamespace(
        _effective_scan_profile="liquidity",
        _skip_symbols=set(),
        _cooldown_until={},
        scan_min_volume_multiple=Decimal("0"),
        scan_min_recent_volume_ratio=Decimal("0"),
        scan_recent_hours=3,
        scan_min_quote_volume_usdt=Decimal("1000"),
        scan_max_spread_pct=Decimal("0.20"),
        scan_min_depth_1pct_usdt=Decimal("1000"),
        scan_min_depth_to_volume_pct=Decimal("0.002"),
        scan_max_symbols=10,
        scan_allow_against_momentum=True,
        scan_include_tradfi=False,
        scan_min_funding_rate=Decimal("-1"),
        safety_funding_buffer_seconds=0,
        live=True,
        take_profit_mode="min-viable",
        leverage=2,
        max_min_notional_leverage=2,
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0.002"),
        take_profit_roe=Decimal("0.05"),
        stop_loss_roe=Decimal("0.90"),
        min_net_profit_usdt=Decimal("0.01"),
        min_margin_utilization_pct=Decimal("0"),
    )

    selected = select_auto_trade_plan(
        client,
        args=args,
        direction="LONG",
        account={"totalMarginBalance": "20.43", "availableBalance": "20.43"},
        fee_fallback=DEFAULT_MAKER_FEE_RATE,
    )

    assert selected is not None
    symbol, _, _, plan = selected
    assert symbol == "ETHUSDT"
    assert plan.symbol == "ETHUSDT"
    assert client.leverage_changes == ["BTCUSDT", "ETHUSDT"]


def test_auto_trade_plan_uses_doge_with_leverage_bump_for_tiny_balance() -> None:
    client = AutoPlanFallbackClient()
    args = SimpleNamespace(
        _effective_scan_profile="liquidity",
        _skip_symbols=set(),
        _cooldown_until={},
        scan_min_volume_multiple=Decimal("0"),
        scan_min_recent_volume_ratio=Decimal("0"),
        scan_recent_hours=3,
        scan_min_quote_volume_usdt=Decimal("1000"),
        scan_max_spread_pct=Decimal("0.20"),
        scan_min_depth_1pct_usdt=Decimal("1000"),
        scan_min_depth_to_volume_pct=Decimal("0.002"),
        scan_max_symbols=10,
        scan_allow_against_momentum=True,
        scan_include_tradfi=False,
        scan_min_funding_rate=Decimal("-1"),
        safety_funding_buffer_seconds=0,
        live=True,
        take_profit_mode="min-viable",
        leverage=2,
        max_min_notional_leverage=20,
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0.002"),
        take_profit_roe=Decimal("0.05"),
        stop_loss_roe=Decimal("0.90"),
        min_net_profit_usdt=Decimal("0.03"),
        min_margin_utilization_pct=Decimal("0"),
    )

    selected = select_auto_trade_plan(
        client,
        args=args,
        direction="LONG",
        account={"totalMarginBalance": "0.34", "availableBalance": "0.34"},
        fee_fallback=DEFAULT_MAKER_FEE_RATE,
    )

    assert selected is not None
    symbol, _, _, plan = selected
    assert symbol == "DOGEUSDT"
    assert plan.leverage > 2
    assert plan.notional >= Decimal("5")


def test_leverage_for_exchange_minimums_returns_base_when_already_sizable() -> None:
    rules = SymbolRules(
        symbol="DOGEUSDT",
        tick_size=Decimal("0.00001"),
        qty_step=Decimal("1"),
        min_qty=Decimal("1"),
        min_notional=Decimal("5"),
    )

    leverage = leverage_for_exchange_minimums(
        rules=rules,
        mark_price=Decimal("0.10"),
        available_balance=Decimal("10"),
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0"),
        base_leverage=2,
        max_leverage=20,
    )

    assert leverage == 2


def test_effective_entry_leverage_bumps_fixed_symbol_only_when_enabled() -> None:
    rules = SymbolRules(
        symbol="BSBUSDT",
        tick_size=Decimal("0.00001"),
        qty_step=Decimal("1"),
        min_qty=Decimal("1"),
        min_notional=Decimal("5"),
    )
    args = SimpleNamespace(
        leverage=3,
        max_min_notional_leverage=10,
        adaptive_min_notional_leverage=False,
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0"),
    )

    fixed_leverage = effective_entry_leverage(
        symbol="BSBUSDT",
        rules=rules,
        mark_price=Decimal("0.44"),
        available_balance=Decimal("1"),
        args=args,
        auto_symbol=False,
    )
    args.adaptive_min_notional_leverage = True
    adaptive_leverage = effective_entry_leverage(
        symbol="BSBUSDT",
        rules=rules,
        mark_price=Decimal("0.44"),
        available_balance=Decimal("1"),
        args=args,
        auto_symbol=False,
    )

    assert fixed_leverage == 3
    assert adaptive_leverage == 6


def test_effective_entry_leverage_returns_base_when_fixed_symbol_is_viable() -> None:
    rules = SymbolRules(
        symbol="BSBUSDT",
        tick_size=Decimal("0.00001"),
        qty_step=Decimal("1"),
        min_qty=Decimal("1"),
        min_notional=Decimal("5"),
    )
    args = SimpleNamespace(
        leverage=3,
        max_min_notional_leverage=10,
        adaptive_min_notional_leverage=True,
        allocation_pct=Decimal("1"),
        fee_buffer_pct=Decimal("0"),
    )

    leverage = effective_entry_leverage(
        symbol="BSBUSDT",
        rules=rules,
        mark_price=Decimal("0.44"),
        available_balance=Decimal("5"),
        args=args,
        auto_symbol=False,
    )

    assert leverage == 3


def test_position_leverage_prefers_exchange_value() -> None:
    assert position_leverage({"leverage": "7"}, 3) == 7
    assert position_leverage({"leverage": "0"}, 3) == 3


def test_scan_auto_symbol_prefers_highest_positive_funding_after_filters() -> None:
    candidates = scan_auto_symbol(
        FundingRankClient(),
        direction="LONG",
        min_volume_multiple=Decimal("2"),
        min_recent_volume_ratio=Decimal("1"),
        recent_hours=3,
        min_quote_volume=Decimal("1000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
        max_symbols=10,
        allow_against_momentum=False,
        include_tradfi=False,
        min_funding_rate=Decimal("0"),
        funding_buffer_seconds=0,
    )

    assert [candidate.symbol for candidate in candidates] == ["HIGHFUNDUSDT", "LOWFUNDUSDT"]


def test_scan_auto_symbol_liquidity_rank_allows_negative_momentum_without_funding() -> None:
    candidates = scan_auto_symbol(
        LiquidityRankClient(),
        direction="LONG",
        min_volume_multiple=Decimal("0"),
        min_recent_volume_ratio=Decimal("0"),
        recent_hours=3,
        min_quote_volume=Decimal("1000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
        max_symbols=10,
        allow_against_momentum=True,
        include_tradfi=False,
        min_funding_rate=Decimal("-1"),
        funding_buffer_seconds=0,
        require_funding=False,
        rank_by="liquidity",
    )

    assert [candidate.symbol for candidate in candidates] == ["DEEPUSDT", "THINUSDT"]


def test_scan_auto_symbol_short_mode_uses_negative_momentum_and_positive_funding() -> None:
    candidates = scan_auto_symbol(
        ShortFundingRankClient(),
        direction="SHORT",
        min_volume_multiple=Decimal("2"),
        min_recent_volume_ratio=Decimal("1"),
        recent_hours=3,
        min_quote_volume=Decimal("1000"),
        max_spread_pct=Decimal("0.20"),
        min_depth_1pct=Decimal("1000"),
        min_depth_to_volume_pct=Decimal("0.002"),
        max_symbols=10,
        allow_against_momentum=False,
        include_tradfi=False,
        min_funding_rate=Decimal("0"),
        funding_buffer_seconds=0,
    )

    assert [candidate.symbol for candidate in candidates] == ["HIGHFUNDUSDT", "LOWFUNDUSDT"]


def test_untradable_error_detects_tradfi_restrictions() -> None:
    error = BinanceHTTPError(400, {"code": -4120, "msg": "TRADFI symbol not supported"}, "/fapi/v1/order")

    assert is_untradable_error(error)


def test_insufficient_margin_is_resizable_not_untradable() -> None:
    error = BinanceHTTPError(400, {"code": -2019, "msg": "Margin is insufficient."}, "/fapi/v1/order")

    assert is_insufficient_margin_error(error)
    assert not is_untradable_error(error)
