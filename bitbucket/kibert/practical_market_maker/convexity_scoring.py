from __future__ import annotations

import math

import pandas as pd


CONVEXITY_SCORE_COLUMNS = [
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
    "valuation_trap_score",
    "short_liquidation_fuel_score",
    "spot_control_score",
    "squeeze_machine_score",
    "convexity_entry_score",
    "convexity_score",
    "trend_confluence_flag",
    "spot_flow_confluence_flag",
    "perp_squeeze_confluence_flag",
    "float_control_confluence_flag",
    "mm_sponsor_confluence_flag",
    "ath_runway_confluence_flag",
    "squeeze_machine_flag",
    "pre_pump_candidate_flag",
    "early_convexity_flag",
    "convexity_prime_flag",
    "convexity_chase_risk_flag",
    "convexity_too_late_flag",
    "convexity_confluence_note",
    "convexity_summary",
    "convexity_top_factors",
    "convexity_offsets",
]


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _bool_score(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index, dtype="float64")
    return df[column].fillna(False).astype(bool).astype(float) * 100.0


def _score_linear(df: pd.DataFrame, column: str, low: float, high: float, *, invert: bool = False) -> pd.Series:
    values = _numeric(df, column)
    if high <= low:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    score = ((values - low) / (high - low) * 100.0).clip(lower=0.0, upper=100.0)
    if invert:
        score = 100.0 - score
    return score


def _score_log(df: pd.DataFrame, column: str, low: float, high: float, *, invert: bool = False) -> pd.Series:
    values = _numeric(df, column).where(lambda series: series > 0)
    low_log = math.log10(max(low, 1e-12))
    high_log = math.log10(max(high, low + 1e-12))
    score = ((values.map(math.log10) - low_log) / (high_log - low_log) * 100.0).clip(lower=0.0, upper=100.0)
    if invert:
        score = 100.0 - score
    return score


def _score_band(df: pd.DataFrame, column: str, low: float, sweet_low: float, sweet_high: float, high: float) -> pd.Series:
    """Return 100 inside the preferred band and fade outside it.

    This is useful for early convexity because a 12-25% day can be constructive,
    while a 150% day is often no longer an entry-quality setup.
    """
    values = _numeric(df, column)
    score = pd.Series(float("nan"), index=df.index, dtype="float64")
    if sweet_low <= low or high <= sweet_high:
        return score

    left = ((values - low) / (sweet_low - low) * 100.0).clip(lower=0.0, upper=100.0)
    right = ((high - values) / (high - sweet_high) * 100.0).clip(lower=0.0, upper=100.0)
    score = pd.Series(100.0, index=df.index, dtype="float64")
    score = score.where(values >= sweet_low, other=left)
    score = score.where(values <= sweet_high, other=right)
    return score.clip(lower=0.0, upper=100.0)


def _weighted_average(index: pd.Index, parts: list[tuple[pd.Series, float]]) -> pd.Series:
    total = pd.Series(0.0, index=index, dtype="float64")
    weights = pd.Series(0.0, index=index, dtype="float64")
    for series, weight in parts:
        values = pd.to_numeric(series, errors="coerce")
        valid = values.notna()
        total = total + values.fillna(0.0).clip(lower=0.0, upper=100.0) * weight
        weights = weights + valid.astype(float) * weight
    return (total / weights.where(weights > 0)).fillna(0.0).clip(lower=0.0, upper=100.0)


def _safe_value(row: pd.Series, column: str) -> float:
    try:
        value = float(row.get(column, float("nan")))
    except Exception:
        return float("nan")
    return value


def _explain(row: pd.Series) -> tuple[str, str, str]:
    positive: list[tuple[str, float]] = [
        ("squeeze machine", _safe_value(row, "squeeze_machine_score")),
        ("mechanic confluence", _safe_value(row, "convexity_confluence_score")),
        ("short liquidation fuel", _safe_value(row, "short_liquidation_fuel_score")),
        ("spot control", _safe_value(row, "spot_control_score")),
        ("DWF Labs portfolio", _safe_value(row, "dwf_labs_portfolio_score")),
        ("valuation trap", _safe_value(row, "valuation_trap_score")),
        ("controlled float", _safe_value(row, "convexity_float_score")),
        ("sponsored spot", _safe_value(row, "convexity_sponsor_score")),
        ("pre-ignition pressure", _safe_value(row, "convexity_preignition_score")),
        ("expansion readiness", _safe_value(row, "convexity_expansion_score")),
        ("squeeze optionality", _safe_value(row, "convexity_squeeze_score")),
        ("ATH runway", _safe_value(row, "convexity_runway_score")),
        ("B/B/G lane", _safe_value(row, "binance_bitget_gate_share_score")),
        ("EMFX lane", _safe_value(row, "emfx_lane_score")),
        ("daily volume expansion", _safe_value(row, "daily_quote_volume_multiple") * 18.0),
        ("volume acceleration", _safe_value(row, "hour_volume_multiple") * 12.0),
        ("trade acceleration", _safe_value(row, "hour_trade_count_multiple") * 12.0),
        ("ATH multiple", _safe_value(row, "ath_multiple") * 4.0),
    ]
    offsets: list[tuple[str, float]] = [
        ("late penalty", _safe_value(row, "convexity_late_penalty")),
        ("chase risk", _safe_value(row, "convexity_chase_risk_flag") * 100.0),
        ("exhaustion", _safe_value(row, "crime_exhaustion_score")),
        ("hot funding", _safe_value(row, "carry_funding_pct") * 3000.0),
        ("upper wick", _safe_value(row, "hour_upper_wick_pct")),
    ]
    positive = sorted(
        [(name, value) for name, value in positive if not math.isnan(value) and value >= 35.0],
        key=lambda item: item[1],
        reverse=True,
    )
    offsets = sorted(
        [(name, value) for name, value in offsets if not math.isnan(value) and value >= 35.0],
        key=lambda item: item[1],
        reverse=True,
    )
    display_names = {
        "DWF Labs portfolio": "DWF Labs Portfolio",
        "ATH runway": "ATH Runway",
        "B/B/G lane": "B/B/G Lane",
        "EMFX lane": "EMFX Lane",
        "ATH multiple": "ATH Multiple",
    }
    summary = " + ".join(display_names.get(name, name.title()) for name, _ in positive[:4]) if positive else "No early convexity yet"
    top = " | ".join(f"{name}: {value:.1f}" for name, value in positive[:5]) or "No top contributors above threshold."
    negative = " | ".join(f"{name}: {value:.1f}" for name, value in offsets[:4]) or "No major negative offsets."
    return summary, top, negative


def _confluence_note(row: pd.Series) -> str:
    notes: list[str] = []
    if bool(row.get("trend_confluence_flag")):
        notes.append("trend/breakout pressure")
    if bool(row.get("spot_flow_confluence_flag")):
        notes.append("CEX/spot lane active")
    if bool(row.get("perp_squeeze_confluence_flag")):
        notes.append("short/perp fuel")
    if bool(row.get("float_control_confluence_flag")):
        notes.append("controlled float")
    if bool(row.get("mm_sponsor_confluence_flag")):
        notes.append("MM/sponsor hint")
    if bool(row.get("ath_runway_confluence_flag")):
        multiple = _safe_value(row, "ath_multiple")
        notes.append(f"{multiple:.1f}x ATH runway" if not math.isnan(multiple) else "large ATH runway")
    if bool(row.get("squeeze_machine_flag")):
        notes.append("float-control/perp-squeeze machine")
    return " | ".join(notes) if notes else "No multi-mechanic confluence yet."


def apply_convexity_model(df: pd.DataFrame) -> pd.DataFrame:
    """Score early-stage pump-risk / squeeze-risk convexity.

    convexity_confluence_score =
        0.18 * trend_confluence_score
      + 0.20 * spot_flow_confluence_score
      + 0.16 * perp_squeeze_confluence_score
      + 0.17 * float_control_confluence_score
      + 0.12 * mm_sponsor_confluence_score
      + 0.17 * ath_runway_confluence_score
      + 4.00 * convexity_confluence_count

    squeeze_machine_score =
        0.26 * float_control_confluence_score
      + 0.20 * spot_control_score
      + 0.22 * short_liquidation_fuel_score
      + 0.14 * trend_confluence_score
      + 0.10 * valuation_trap_score
      + 0.08 * ath_runway_confluence_score
      - 0.20 * convexity_late_penalty

    convexity_entry_score =
        0.22 * convexity_float_score
      + 0.23 * convexity_sponsor_score
      + 0.18 * convexity_preignition_score
      + 0.08 * convexity_expansion_score
      + 0.11 * convexity_squeeze_score
      + 0.12 * convexity_runway_score
      + 0.12 * convexity_confluence_score
      + 0.12 * squeeze_machine_score
      - 0.28 * convexity_late_penalty

    The intent is to rank names that still have asymmetric upside if sponsor flow persists,
    not names that are already obviously blown out.
    """
    out = df.copy()
    if out.empty:
        for column in CONVEXITY_SCORE_COLUMNS:
            out[column] = pd.Series(dtype="object" if column.startswith("convexity_") and column.endswith(("summary", "factors", "offsets")) else "float64")
        return out

    index = out.index
    major_excluded = out.get("crime_excluded_major", pd.Series(False, index=index)).fillna(False).astype(bool)
    breakout_stack_score = (
        _bool_score(out, "broke_high_5d") * 0.25
        + _bool_score(out, "broke_high_20d") * 0.35
        + _bool_score(out, "broke_high_90d") * 0.25
        + _bool_score(out, "broke_high_180d") * 0.15
    ).clip(lower=0.0, upper=100.0)

    out["convexity_float_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "float_trap_score"), 0.26),
            (_numeric(out, "crime_owner_circle_score"), 0.16),
            (_numeric(out, "crime_supply_control_score"), 0.12),
            (_numeric(out, "inventory_transfer_risk_score"), 0.10),
            (_numeric(out, "dwf_labs_portfolio_score"), 0.08),
            (_score_linear(out, "locked_supply_pct", 15.0, 85.0), 0.08),
            (_score_linear(out, "fdv_to_market_cap", 1.5, 12.0), 0.08),
            (_score_linear(out, "top10_holder_pct", 25.0, 90.0), 0.07),
            (_score_linear(out, "owner_holder_pct", 3.0, 35.0), 0.07),
            (_score_linear(out, "creator_holder_pct", 3.0, 35.0), 0.06),
        ],
    )

    out["convexity_sponsor_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "venue_support_score"), 0.16),
            (_numeric(out, "crime_spot_impulse_score"), 0.15),
            (_numeric(out, "inventory_sponsor_mismatch_score"), 0.10),
            (_numeric(out, "cex_dex_volume_ratio_score"), 0.13),
            (_numeric(out, "binance_bitget_gate_share_score"), 0.14),
            (_numeric(out, "emfx_lane_score"), 0.12),
            (_numeric(out, "venue_hhi_score"), 0.08),
            (_numeric(out, "dwf_labs_portfolio_score"), 0.11),
            (_score_linear(out, "coinbase_volume_share_pct", 2.0, 28.0), 0.05),
            (_score_linear(out, "spot_to_perp_volume_pct", 5.0, 130.0), 0.07),
            (_score_linear(out, "spot_volume_to_mcap_pct", 6.0, 180.0), 0.08),
            (_score_linear(out, "top3_venue_volume_share_pct", 45.0, 92.0), 0.04),
        ],
    )

    near_5d = _score_linear(out, "distance_to_high_5d_pct", 0.0, 18.0, invert=True)
    near_20d = _score_linear(out, "distance_to_high_20d_pct", 0.0, 25.0, invert=True)
    near_90d = _score_linear(out, "distance_to_high_90d_pct", 0.0, 40.0, invert=True)
    constructive_24h = _score_band(out, "day_return_pct", -8.0, 4.0, 38.0, 85.0)
    constructive_1h = _score_band(out, "hour_return_pct", -3.0, 0.0, 9.0, 24.0)
    volume_lift = _score_linear(out, "daily_quote_volume_multiple", 1.15, 6.0)
    prebreakout_stack = (
        near_5d.fillna(0.0) * 0.35
        + near_20d.fillna(0.0) * 0.40
        + near_90d.fillna(0.0) * 0.25
    ).clip(lower=0.0, upper=100.0)
    out["convexity_preignition_score"] = _weighted_average(
        index,
        [
            (volume_lift, 0.18),
            (_score_linear(out, "hour_volume_multiple", 1.05, 5.5), 0.13),
            (_score_linear(out, "hour_trade_count_multiple", 1.05, 5.5), 0.12),
            (prebreakout_stack, 0.15),
            (constructive_24h, 0.13),
            (constructive_1h, 0.08),
            (_score_linear(out, "cmc_volume_to_mcap_pct", 12.0, 180.0), 0.07),
            (_score_linear(out, "spot_volume_to_mcap_pct", 5.0, 120.0), 0.06),
            (_score_linear(out, "oi_delta_pct", -1.0, 10.0), 0.04),
            (_score_linear(out, "hour_close_location_pct", 52.0, 90.0), 0.04),
        ],
    )

    out["convexity_expansion_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "ignition_score_v2", 18.0, 78.0), 0.15),
            (_score_linear(out, "crime_mechanics_score", 25.0, 85.0), 0.10),
            (_score_linear(out, "daily_quote_volume_multiple", 1.2, 8.0), 0.12),
            (_score_linear(out, "hour_volume_multiple", 1.1, 8.0), 0.13),
            (_score_linear(out, "hour_trade_count_multiple", 1.1, 8.0), 0.13),
            (_score_linear(out, "oi_delta_pct", 0.5, 18.0), 0.13),
            (_score_linear(out, "taker_buy_sell_ratio", 1.0, 2.4), 0.10),
            (_score_linear(out, "hour_close_location_pct", 55.0, 95.0), 0.06),
            (breakout_stack_score, 0.06),
            (constructive_24h, 0.05),
            (prebreakout_stack, 0.05),
        ],
    )

    cool_funding = _score_linear(out, "carry_funding_pct", 0.0, 0.02, invert=True)
    out["convexity_squeeze_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "perp_pressure_score", 25.0, 80.0), 0.22),
            (_score_linear(out, "funding_flip_score", 20.0, 85.0), 0.23),
            (_score_linear(out, "short_crowding_score", 20.0, 80.0), 0.19),
            (_score_linear(out, "short_squeeze_score", 20.0, 80.0), 0.16),
            (_score_linear(out, "oi_to_market_cap_pct", 3.0, 28.0), 0.08),
            (_score_linear(out, "oi_to_24h_volume_pct", 8.0, 110.0), 0.06),
            (cool_funding, 0.06),
        ],
    )

    out["convexity_runway_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "upside_to_ath_pct", 20.0, 400.0), 0.36),
            (_score_log(out, "ath_multiple", 1.2, 50.0), 0.26),
            (_bool_score(out, "ath_runway_20x_flag"), 0.08),
            (_score_linear(out, "history_days", 60.0, 365.0), 0.12),
            (_score_linear(out, "crime_largecap_penalty_score", 0.0, 100.0, invert=True), 0.09),
            (_score_log(out, "market_cap_usd", 5_000_000.0, 2_000_000_000.0, invert=True), 0.09),
        ],
    )

    out["convexity_late_penalty"] = _weighted_average(
        index,
        [
            (_score_linear(out, "crime_exhaustion_score", 45.0, 90.0), 0.24),
            (_score_linear(out, "exit_fragility_score", 45.0, 90.0), 0.16),
            (_score_linear(out, "day_return_pct", 45.0, 180.0), 0.17),
            (_score_linear(out, "hour_return_pct", 12.0, 45.0), 0.08),
            (_score_linear(out, "hour_upper_wick_pct", 12.0, 45.0), 0.11),
            (_score_linear(out, "carry_funding_pct", 0.012, 0.08), 0.12),
            (_score_linear(out, "basis_rate_pct", 0.05, 0.40), 0.07),
            (_score_linear(out, "convexity_expansion_score", 80.0, 100.0), 0.04),
            (_bool_score(out, "blowoff_watch_flag"), 0.05),
            (_bool_score(out, "unwind_risk_flag"), 0.04),
            (_bool_score(out, "squeeze_chase_flag"), 0.04),
        ],
    )

    out["trend_confluence_score"] = _weighted_average(
        index,
        [
            (breakout_stack_score, 0.22),
            (prebreakout_stack, 0.22),
            (volume_lift, 0.18),
            (_score_linear(out, "daily_quote_volume_multiple", 1.3, 8.0), 0.12),
            (_score_linear(out, "hour_volume_multiple", 1.1, 6.0), 0.08),
            (_score_band(out, "day_return_pct", -8.0, 4.0, 40.0, 90.0), 0.10),
            (_score_linear(out, "hour_close_location_pct", 55.0, 90.0), 0.08),
        ],
    )
    out["spot_flow_confluence_score"] = _weighted_average(
        index,
        [
            (out["convexity_sponsor_score"], 0.22),
            (_numeric(out, "cex_dex_volume_ratio_score"), 0.18),
            (_numeric(out, "binance_bitget_gate_share_score"), 0.14),
            (_numeric(out, "emfx_lane_score"), 0.12),
            (_score_linear(out, "krw_volume_share_pct", 3.0, 35.0), 0.08),
            (_score_linear(out, "try_volume_share_pct", 1.0, 20.0), 0.07),
            (_score_linear(out, "spot_volume_to_mcap_pct", 10.0, 180.0), 0.11),
            (_score_linear(out, "top3_venue_volume_share_pct", 45.0, 92.0), 0.08),
        ],
    )
    out["perp_squeeze_confluence_score"] = _weighted_average(
        index,
        [
            (out["convexity_squeeze_score"], 0.24),
            (_numeric(out, "perp_pressure_score"), 0.18),
            (_numeric(out, "funding_flip_score"), 0.15),
            (_numeric(out, "short_crowding_score"), 0.14),
            (_score_linear(out, "oi_delta_pct", 0.5, 18.0), 0.10),
            (_score_linear(out, "oi_to_market_cap_pct", 3.0, 30.0), 0.09),
            (_score_linear(out, "taker_buy_sell_ratio", 1.0, 2.4), 0.06),
            (_score_linear(out, "carry_funding_pct", 0.0, 0.025, invert=True), 0.04),
        ],
    )
    out["float_control_confluence_score"] = _weighted_average(
        index,
        [
            (out["convexity_float_score"], 0.45),
            (_score_linear(out, "top10_holder_pct", 40.0, 90.0), 0.14),
            (_score_linear(out, "locked_supply_pct", 20.0, 85.0), 0.12),
            (_score_log(out, "holder_count", 4_000.0, 150_000.0, invert=True), 0.10),
            (_score_linear(out, "fdv_to_market_cap", 1.5, 12.0), 0.09),
            (_numeric(out, "inventory_transfer_risk_score"), 0.10),
        ],
    )
    out["mm_sponsor_confluence_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "mm_presence_score"), 0.25),
            (_numeric(out, "mm_bid_support_score"), 0.22),
            (_numeric(out, "mm_proximity_score"), 0.18),
            (_numeric(out, "dwf_labs_portfolio_score"), 0.15),
            (_score_linear(out, "coinbase_depth_to_volume_pct", 0.0, 1.0), 0.12),
            (_score_linear(out, "coinbase_depth_to_perp_volume_pct", 0.0, 0.75), 0.10),
            (_score_linear(out, "coinbase_bid_ask_spread_pct", 0.0, 0.60, invert=True), 0.08),
            (_score_linear(out, "coinbase_book_imbalance_pct", 45.0, 75.0), 0.05),
        ],
    )
    out["ath_runway_confluence_score"] = _weighted_average(
        index,
        [
            (out["convexity_runway_score"], 0.40),
            (_score_log(out, "ath_multiple", 2.0, 50.0), 0.35),
            (_score_linear(out, "ath_upside_pct", 100.0, 4900.0), 0.15),
            (_bool_score(out, "ath_runway_20x_flag"), 0.10),
        ],
    )
    short_accounts_direct = pd.concat(
        [
            _score_linear(out, "short_account_pct", 52.0, 75.0),
            _score_linear(out, "long_short_account_ratio", 0.55, 1.05, invert=True),
        ],
        axis=1,
    ).max(axis=1)
    out["valuation_trap_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "fdv_to_market_cap", 1.3, 15.0), 0.20),
            (_score_linear(out, "locked_supply_pct", 20.0, 90.0), 0.16),
            (_score_linear(out, "top10_holder_pct", 35.0, 92.0), 0.14),
            (_score_log(out, "holder_count", 3_000.0, 140_000.0, invert=True), 0.10),
            (_score_linear(out, "spot_volume_to_mcap_pct", 15.0, 250.0), 0.13),
            (_score_linear(out, "perp_volume_to_mcap_pct", 60.0, 700.0), 0.13),
            (_score_linear(out, "cmc_volume_to_mcap_pct", 20.0, 250.0), 0.08),
            (_score_band(out, "market_cap_usd", 2_000_000.0, 12_000_000.0, 500_000_000.0, 3_000_000_000.0), 0.06),
        ],
    )
    out["short_liquidation_fuel_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "short_crowding_score"), 0.22),
            (short_accounts_direct, 0.18),
            (_numeric(out, "funding_flip_score"), 0.18),
            (_score_linear(out, "oi_to_market_cap_pct", 3.0, 35.0), 0.12),
            (_score_linear(out, "oi_delta_pct", 0.25, 14.0), 0.11),
            (_score_linear(out, "perp_pressure_score", 28.0, 85.0), 0.09),
            (_score_linear(out, "oi_to_24h_volume_pct", 8.0, 120.0), 0.06),
            (_score_linear(out, "carry_funding_pct", -0.015, 0.025, invert=True), 0.04),
        ],
    )
    out["spot_control_score"] = _weighted_average(
        index,
        [
            (out["spot_flow_confluence_score"], 0.22),
            (out["mm_sponsor_confluence_score"], 0.18),
            (_numeric(out, "venue_support_score"), 0.14),
            (_numeric(out, "crime_spot_impulse_score"), 0.12),
            (_numeric(out, "cex_dex_volume_ratio_score"), 0.10),
            (_numeric(out, "binance_bitget_gate_share_score"), 0.09),
            (_numeric(out, "dwf_labs_portfolio_score"), 0.08),
            (_numeric(out, "emfx_lane_score"), 0.06),
            (_numeric(out, "venue_hhi_score"), 0.05),
            (_score_linear(out, "coinbase_book_imbalance_pct", 45.0, 75.0), 0.04),
        ],
    )

    out["trend_confluence_flag"] = (
        (out["trend_confluence_score"] >= 48.0)
        | (_bool_score(out, "broke_high_5d") >= 100.0)
        | (_bool_score(out, "broke_high_20d") >= 100.0)
    )
    out["spot_flow_confluence_flag"] = out["spot_flow_confluence_score"] >= 50.0
    out["perp_squeeze_confluence_flag"] = out["perp_squeeze_confluence_score"] >= 46.0
    out["float_control_confluence_flag"] = out["float_control_confluence_score"] >= 48.0
    out["mm_sponsor_confluence_flag"] = out["mm_sponsor_confluence_score"] >= 42.0
    out["ath_runway_confluence_flag"] = (
        (out["ath_runway_confluence_score"] >= 45.0)
        | (_numeric(out, "ath_multiple") >= 8.0)
    )
    confluence_flags = [
        "trend_confluence_flag",
        "spot_flow_confluence_flag",
        "perp_squeeze_confluence_flag",
        "float_control_confluence_flag",
        "mm_sponsor_confluence_flag",
        "ath_runway_confluence_flag",
    ]
    out["convexity_confluence_count"] = sum(out[column].astype(int) for column in confluence_flags)
    out["convexity_confluence_score"] = (
        out["trend_confluence_score"] * 0.18
        + out["spot_flow_confluence_score"] * 0.20
        + out["perp_squeeze_confluence_score"] * 0.16
        + out["float_control_confluence_score"] * 0.17
        + out["mm_sponsor_confluence_score"] * 0.12
        + out["ath_runway_confluence_score"] * 0.17
        + out["convexity_confluence_count"] * 4.0
    ).clip(lower=0.0, upper=100.0)
    out["squeeze_machine_score"] = (
        out["float_control_confluence_score"] * 0.26
        + out["spot_control_score"] * 0.20
        + out["short_liquidation_fuel_score"] * 0.22
        + out["trend_confluence_score"] * 0.14
        + out["valuation_trap_score"] * 0.10
        + out["ath_runway_confluence_score"] * 0.08
        - out["convexity_late_penalty"] * 0.20
    ).clip(lower=0.0, upper=100.0)
    out["squeeze_machine_flag"] = (
        (out["squeeze_machine_score"] >= 55.0)
        & (out["float_control_confluence_score"] >= 42.0)
        & (out["spot_control_score"] >= 38.0)
        & (out["short_liquidation_fuel_score"] >= 35.0)
        & (out["convexity_late_penalty"] < 58.0)
        & (~major_excluded)
    )

    out["convexity_entry_score"] = (
        out["convexity_float_score"] * 0.22
        + out["convexity_sponsor_score"] * 0.23
        + out["convexity_preignition_score"] * 0.18
        + out["convexity_expansion_score"] * 0.08
        + out["convexity_squeeze_score"] * 0.11
        + out["convexity_runway_score"] * 0.12
        + out["convexity_confluence_score"] * 0.12
        + out["squeeze_machine_score"] * 0.12
        - out["convexity_late_penalty"] * 0.28
    ).where(~major_excluded, other=0.0).clip(lower=0.0, upper=100.0)
    out["convexity_score"] = out["convexity_entry_score"]

    out["convexity_chase_risk_flag"] = (
        (_numeric(out, "day_return_pct") >= 65.0)
        | (_numeric(out, "hour_return_pct") >= 18.0)
        | (out["convexity_late_penalty"] >= 55.0)
        | ((_numeric(out, "crime_exhaustion_score") >= 65.0) & (_numeric(out, "hour_upper_wick_pct") >= 18.0))
    ) & (~major_excluded)

    out["pre_pump_candidate_flag"] = (
        (~major_excluded)
        & ((out["convexity_entry_score"] >= 48.0) | (out["squeeze_machine_score"] >= 55.0))
        & (out["convexity_float_score"] >= 36.0)
        & ((out["convexity_sponsor_score"] >= 42.0) | (out["spot_control_score"] >= 42.0))
        & (out["convexity_preignition_score"] >= 32.0)
        & ((out["convexity_confluence_count"] >= 3) | out["squeeze_machine_flag"])
        & (out["convexity_late_penalty"] < 52.0)
        & (~out["convexity_chase_risk_flag"])
    )
    out["early_convexity_flag"] = (
        (~major_excluded)
        & ((out["convexity_entry_score"] >= 52.0) | (out["squeeze_machine_score"] >= 60.0))
        & (out["convexity_float_score"] >= 40.0)
        & ((out["convexity_sponsor_score"] >= 45.0) | (out["spot_control_score"] >= 46.0))
        & (out["convexity_preignition_score"] >= 35.0)
        & ((out["convexity_confluence_count"] >= 3) | out["squeeze_machine_flag"])
        & (out["convexity_late_penalty"] < 55.0)
        & (~out["convexity_chase_risk_flag"])
    )
    out["convexity_prime_flag"] = (
        (~major_excluded)
        & ((out["convexity_entry_score"] >= 58.0) | (out["squeeze_machine_score"] >= 66.0))
        & (out["convexity_float_score"] >= 48.0)
        & ((out["convexity_sponsor_score"] >= 52.0) | (out["spot_control_score"] >= 55.0))
        & (out["convexity_preignition_score"] >= 42.0)
        & ((out["convexity_confluence_count"] >= 4) | (out["squeeze_machine_flag"] & (out["convexity_confluence_count"] >= 3)))
        & (out["convexity_runway_score"] >= 40.0)
        & (out["convexity_late_penalty"] < 45.0)
        & (_numeric(out, "day_return_pct") < 55.0)
        & (~out["convexity_chase_risk_flag"])
    )
    out["convexity_too_late_flag"] = (
        (_numeric(out, "day_return_pct") >= 95.0)
        | (out["convexity_late_penalty"] >= 70.0)
        | (_numeric(out, "crime_exhaustion_score") >= 72.0)
        | (_bool_score(out, "squeeze_chase_flag") >= 100.0)
    ) & (~major_excluded)

    out["convexity_confluence_note"] = out.apply(_confluence_note, axis=1)
    notes = out.apply(_explain, axis=1, result_type="expand")
    out["convexity_summary"] = notes[0]
    out["convexity_top_factors"] = notes[1]
    out["convexity_offsets"] = notes[2]
    return out
