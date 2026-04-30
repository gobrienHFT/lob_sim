from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


LIFECYCLE_SCORE_COLUMNS = [
    "float_trap_score",
    "ignition_score_v2",
    "perp_pressure_score",
    "venue_support_score",
    "exit_fragility_score",
    "large_cap_stabilizer",
    "crime_pump_score_v2",
    "setup_ready_flag",
    "active_squeeze_flag",
    "blowoff_watch_flag",
    "unwind_risk_flag",
    "coinbase_lane_flag",
    "owner_controlled_flag",
    "perp_heavy_flag",
    "why_flagged_summary",
    "why_flagged_top_factors",
    "why_flagged_offsets",
]

LIFECYCLE_FILTERS = {
    "Setup Ready": "setup_ready_flag",
    "Active Squeeze": "active_squeeze_flag",
    "Blowoff Watch": "blowoff_watch_flag",
    "Unwind Risk": "unwind_risk_flag",
    "Coinbase Lane": "coinbase_lane_flag",
    "Owner Controlled": "owner_controlled_flag",
    "Perp Heavy": "perp_heavy_flag",
}


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


def _text_score(df: pd.DataFrame, columns: Iterable[str], patterns: dict[str, float]) -> pd.Series:
    score = pd.Series(0.0, index=df.index, dtype="float64")
    text = pd.Series("", index=df.index, dtype="object")
    for column in columns:
        if column in df.columns:
            text = text.str.cat(df[column].fillna("").astype(str), sep=" | ")
    lower = text.str.lower()
    for pattern, points in patterns.items():
        score = score + lower.str.contains(pattern, regex=False).astype(float) * points
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


def _factor_notes(row: pd.Series) -> tuple[str, str, str]:
    positive: list[tuple[str, float]] = [
        ("controlled float", _safe_value(row, "float_trap_score")),
        ("breakout ignition", _safe_value(row, "ignition_score_v2")),
        ("perp crowding", _safe_value(row, "perp_pressure_score")),
        ("venue/MM support", _safe_value(row, "venue_support_score")),
        ("exit fragility", _safe_value(row, "exit_fragility_score")),
        ("MM proximity", _safe_value(row, "mm_proximity_score")),
        ("OTC inventory risk", _safe_value(row, "inventory_transfer_risk_score")),
        ("Coinbase lane", _safe_value(row, "crime_coinbase_lane_score")),
        ("CMC mover", _safe_value(row, "cmc_mover_score")),
        ("CMC volume/mcap", _safe_value(row, "cmc_volume_to_mcap_pct")),
        ("CEX/DEX skew", _safe_value(row, "cex_dex_volume_ratio_score")),
        ("KRW/Upbit lane", _safe_value(row, "krw_volume_share_pct")),
        ("Binance/Bitget/Gate lane", _safe_value(row, "binance_bitget_gate_share_score")),
        ("EMFX lane", _safe_value(row, "emfx_lane_score")),
        ("venue concentration", _safe_value(row, "venue_hhi_score")),
    ]
    offsets: list[tuple[str, float]] = [
        ("large-cap stabilizer", _safe_value(row, "large_cap_stabilizer")),
        ("MM pull risk", _safe_value(row, "mm_withdrawal_risk_score")),
        ("exhaustion", _safe_value(row, "crime_exhaustion_score")),
    ]
    positive = sorted(
        [(name, value) for name, value in positive if not math.isnan(value) and value >= 35.0],
        key=lambda item: item[1],
        reverse=True,
    )
    offsets = sorted(
        [(name, value) for name, value in offsets if not math.isnan(value) and value >= 45.0],
        key=lambda item: item[1],
        reverse=True,
    )
    if positive:
        summary = " + ".join(name.title() for name, _ in positive[:4])
    else:
        summary = "No strong lifecycle confluence yet"
    top = " | ".join(f"{name}: {value:.1f}" for name, value in positive[:5]) or "No top contributors above threshold."
    negative = " | ".join(f"{name}: {value:.1f}" for name, value in offsets[:4]) or "No major negative offsets."
    return summary, top, negative


def apply_lifecycle_model(df: pd.DataFrame) -> pd.DataFrame:
    """Apply pump-risk lifecycle scoring.

    Formula:
    crime_pump_score_v2 =
        0.27 * float_trap_score
      + 0.23 * ignition_score_v2
      + 0.22 * perp_pressure_score
      + 0.16 * venue_support_score
      + 0.12 * exit_fragility_score
      - 0.20 * large_cap_stabilizer

    This is suspicious-market-structure detection. It is not an assertion of illegal conduct.
    """
    out = df.copy()
    if out.empty:
        for column in LIFECYCLE_SCORE_COLUMNS:
            out[column] = pd.Series(dtype="object" if column.startswith("why_") else "float64")
        return out

    index = out.index
    major_excluded = out.get("crime_excluded_major", pd.Series(False, index=index)).fillna(False).astype(bool)
    mm_hint_score = _text_score(
        out,
        ("mm_proximity_maker", "mm_proximity_note", "trade_bucket_note"),
        {
            "wintermute": 35.0,
            "market maker": 25.0,
            "mm present": 25.0,
            "cb bid support": 20.0,
            "coinbase": 12.0,
            "oi expanding": 10.0,
            "trade count spike": 10.0,
            "controlled holder/float proxy": 20.0,
            "spot volume > market cap": 20.0,
            "cex >> dex": 18.0,
            "cex/dex": 14.0,
            "binance/bitget/gate": 18.0,
            "emfx": 16.0,
            "krw": 10.0,
            "try": 10.0,
            "cmc": 12.0,
            "top mover": 12.0,
        },
    )

    breakout_score = (
        _bool_score(out, "broke_high_5d") * 0.35
        + _bool_score(out, "broke_high_20d") * 0.35
        + _bool_score(out, "broke_high_90d") * 0.30
    )
    krw_lane_score = _score_linear(out, "krw_volume_share_pct", 5.0, 35.0).fillna(0.0)
    try_lane_score = _score_linear(out, "try_volume_share_pct", 1.0, 20.0).fillna(0.0)
    emfx_lane_score = _score_linear(out, "emfx_volume_share_pct", 4.0, 40.0).fillna(0.0)
    trio_lane_score = _score_linear(out, "binance_bitget_gate_share_pct", 20.0, 85.0).fillna(0.0)
    venue_hhi_score = _score_linear(out, "venue_hhi", 900.0, 4_500.0).fillna(0.0)
    kraken_lane_score = _score_linear(out, "kraken_volume_share_pct", 3.0, 20.0).fillna(0.0)
    out["binance_bitget_gate_share_score"] = trio_lane_score
    out["emfx_lane_score"] = emfx_lane_score
    out["venue_hhi_score"] = venue_hhi_score

    out["float_trap_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "crime_supply_control_score"), 0.17),
            (_numeric(out, "crime_owner_circle_score"), 0.17),
            (_score_linear(out, "top10_holder_pct", 25.0, 90.0), 0.12),
            (_score_linear(out, "owner_holder_pct", 3.0, 35.0), 0.08),
            (_score_linear(out, "creator_holder_pct", 3.0, 35.0), 0.07),
            (_score_linear(out, "locked_supply_pct", 15.0, 85.0), 0.10),
            (_score_linear(out, "fdv_to_market_cap", 1.5, 12.0), 0.08),
            (_score_log(out, "holder_count", 5_000.0, 200_000.0, invert=True), 0.08),
            (_numeric(out, "inventory_sponsor_mismatch_score"), 0.07),
            (_numeric(out, "inventory_transfer_risk_score"), 0.06),
        ],
    )

    out["ignition_score_v2"] = _weighted_average(
        index,
        [
            (_numeric(out, "crime_mechanics_score"), 0.10),
            (_numeric(out, "crime_microstructure_score"), 0.07),
            (_numeric(out, "crime_spot_impulse_score"), 0.07),
            (breakout_score, 0.10),
            (_score_linear(out, "hour_return_z", 0.5, 5.0), 0.09),
            (_score_linear(out, "day_return_pct", 5.0, 100.0), 0.07),
            (_numeric(out, "cmc_mover_score"), 0.04),
            (_score_linear(out, "cmc_pct_1h", 1.0, 20.0), 0.02),
            (_score_linear(out, "cmc_pct_24h", 8.0, 150.0), 0.02),
            (_score_linear(out, "cmc_volume_to_mcap_pct", 30.0, 300.0), 0.02),
            (_numeric(out, "cex_dex_volume_ratio_score"), 0.04),
            (_score_log(out, "cex_to_dex_volume_ratio", 2.0, 80.0), 0.03),
            (_score_linear(out, "hour_volume_multiple", 1.2, 8.0), 0.07),
            (_score_linear(out, "hour_trade_count_multiple", 1.2, 8.0), 0.07),
            (_score_linear(out, "taker_buy_sell_ratio", 1.0, 2.5), 0.06),
            (_score_linear(out, "taker_buy_share_pct", 50.0, 75.0), 0.04),
            (_score_linear(out, "oi_delta_pct", 1.0, 20.0), 0.06),
            (_score_linear(out, "oi_to_24h_volume_pct", 10.0, 120.0), 0.03),
            (_numeric(out, "crime_coinbase_lane_score"), 0.05),
            (_bool_score(out, "coinbase_spot_listed"), 0.02),
            (_score_linear(out, "coinbase_volume_share_pct", 3.0, 35.0), 0.03),
            (_numeric(out, "mm_presence_score"), 0.04),
            (_numeric(out, "mm_bid_support_score"), 0.04),
            (_numeric(out, "mm_proximity_score"), 0.03),
            (trio_lane_score, 0.04),
            (emfx_lane_score, 0.03),
            (try_lane_score, 0.02),
            (venue_hhi_score, 0.02),
            (mm_hint_score, 0.02),
        ],
    )

    out["perp_pressure_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "oi_to_market_cap_pct", 3.0, 40.0), 0.16),
            (_score_linear(out, "oi_to_24h_volume_pct", 10.0, 120.0), 0.11),
            (_score_linear(out, "carry_funding_pct", 0.005, 0.10), 0.08),
            (_score_linear(out, "predicted_funding_pct", 0.005, 0.10), 0.06),
            (_score_linear(out, "premium_index_pct", 0.02, 0.50), 0.07),
            (_score_linear(out, "basis_rate_pct", 0.03, 0.50), 0.06),
            (_numeric(out, "crime_carry_stress_score"), 0.10),
            (_score_linear(out, "long_short_account_ratio", 1.1, 3.0), 0.07),
            (_score_linear(out, "long_account_pct", 52.0, 80.0), 0.05),
            (_score_linear(out, "top_trader_long_position_pct", 52.0, 80.0), 0.05),
            (_score_linear(out, "top_trader_account_ratio", 1.1, 3.0), 0.04),
            (_score_linear(out, "top_trader_long_account_pct", 52.0, 80.0), 0.04),
            (_score_linear(out, "crowd_top_position_divergence_pct", 0.0, 25.0), 0.05),
            (_score_linear(out, "crowd_top_account_divergence_pct", 0.0, 25.0), 0.03),
            (_score_linear(out, "spot_to_perp_volume_pct", 0.0, 25.0, invert=True), 0.05),
            (_score_linear(out, "coinbase_to_perp_volume_pct", 0.0, 20.0, invert=True), 0.02),
            (_score_linear(out, "perp_volume_to_mcap_pct", 50.0, 500.0), 0.06),
        ],
    )

    spread_support = _score_linear(out, "coinbase_bid_ask_spread_pct", 0.0, 0.60, invert=True)
    out["venue_support_score"] = _weighted_average(
        index,
        [
            (_numeric(out, "crime_coinbase_lane_score"), 0.13),
            (_bool_score(out, "coinbase_spot_listed"), 0.05),
            (_score_linear(out, "coinbase_volume_share_pct", 3.0, 35.0), 0.07),
            (_score_log(out, "coinbase_bid_depth_2pct_usd", 10_000.0, 1_000_000.0), 0.06),
            (_score_log(out, "coinbase_ask_depth_2pct_usd", 10_000.0, 1_000_000.0), 0.04),
            (_score_log(out, "coinbase_total_depth_2pct_usd", 20_000.0, 2_000_000.0), 0.07),
            ((_score_linear(out, "coinbase_book_imbalance_pct", 42.0, 62.0).clip(upper=100.0)), 0.05),
            (_score_linear(out, "coinbase_depth_to_volume_pct", 0.05, 1.0), 0.07),
            (_score_linear(out, "coinbase_depth_to_perp_volume_pct", 0.02, 0.75), 0.05),
            (spread_support, 0.06),
            (_numeric(out, "mm_presence_score"), 0.11),
            (_numeric(out, "mm_bid_support_score"), 0.09),
            (100.0 - _numeric(out, "mm_withdrawal_risk_score"), 0.06),
            (_score_linear(out, "venue_count", 2.0, 12.0), 0.03),
            (_score_linear(out, "top_venue_volume_share_pct", 15.0, 55.0), 0.03),
            (_score_linear(out, "top3_venue_volume_share_pct", 35.0, 90.0), 0.02),
            (_score_linear(out, "dex_volume_share_pct", 0.0, 50.0, invert=True), 0.02),
            (_numeric(out, "cex_dex_volume_ratio_score"), 0.04),
            (_score_linear(out, "cex_volume_share_pct", 60.0, 98.0), 0.02),
            (krw_lane_score, 0.02),
            (try_lane_score, 0.02),
            (emfx_lane_score, 0.03),
            (trio_lane_score, 0.04),
            (venue_hhi_score, 0.03),
            (kraken_lane_score, 0.02),
        ],
    )

    close_fragility = _score_linear(out, "hour_close_location_pct", 0.0, 100.0, invert=True)
    out["exit_fragility_score"] = (
        _weighted_average(
            index,
            [
                (_numeric(out, "mm_withdrawal_risk_score"), 0.16),
                (_score_linear(out, "hour_upper_wick_pct", 15.0, 60.0), 0.11),
                (close_fragility, 0.09),
                (_score_log(out, "ask_depth_1pct_usdt", 10_000.0, 2_000_000.0, invert=True), 0.08),
                (_score_linear(out, "ask_depth_to_24h_volume_pct", 0.0, 0.80, invert=True), 0.10),
                (_numeric(out, "inventory_sponsor_mismatch_score"), 0.12),
                (_numeric(out, "inventory_transfer_risk_score"), 0.12),
                (_numeric(out, "crime_exhaustion_score"), 0.14),
                (_bool_score(out, "blowoff_risk_flag"), 0.08),
            ],
        )
        - _numeric(out, "crime_largecap_penalty_score").fillna(0.0) * 0.12
    ).clip(lower=0.0, upper=100.0)

    out["large_cap_stabilizer"] = _numeric(out, "crime_largecap_penalty_score").fillna(0.0).clip(lower=0.0, upper=100.0)
    out["crime_pump_score_v2"] = (
        out["float_trap_score"] * 0.27
        + out["ignition_score_v2"] * 0.23
        + out["perp_pressure_score"] * 0.22
        + out["venue_support_score"] * 0.16
        + out["exit_fragility_score"] * 0.12
        - out["large_cap_stabilizer"] * 0.20
    ).where(~major_excluded, other=0.0).clip(lower=0.0, upper=100.0)

    out["setup_ready_flag"] = (
        (out["float_trap_score"] >= 60.0)
        & (out["venue_support_score"] >= 40.0)
        & (out["ignition_score_v2"].between(45.0, 75.0, inclusive="both"))
        & (out["exit_fragility_score"] < 70.0)
        & (~major_excluded)
    )
    out["active_squeeze_flag"] = (
        (out["crime_pump_score_v2"] >= 70.0)
        & (out["ignition_score_v2"] >= 70.0)
        & (out["perp_pressure_score"] >= 60.0)
        & (out["venue_support_score"] >= 45.0)
        & (~major_excluded)
    )
    out["blowoff_watch_flag"] = (
        (out["exit_fragility_score"] >= 75.0)
        | ((_numeric(out, "hour_upper_wick_pct") >= 30.0) & (_numeric(out, "hour_close_location_pct") <= 35.0))
        | ((_numeric(out, "crime_exhaustion_score") >= 70.0) & (_numeric(out, "hour_return_z") >= 4.0))
    ) & (~major_excluded)
    out["unwind_risk_flag"] = (
        (_numeric(out, "mm_withdrawal_risk_score") >= 65.0)
        & ((out["venue_support_score"] < 45.0) | (_numeric(out, "mm_presence_score") < 40.0) | (_numeric(out, "mm_bid_support_score") < 35.0))
        & (out["exit_fragility_score"] >= 65.0)
        & (~major_excluded)
    )
    out["coinbase_lane_flag"] = (
        (_numeric(out, "crime_coinbase_lane_score") >= 55.0)
        | out.get("coinbase_spot_listed", pd.Series(False, index=index)).fillna(False).astype(bool)
        | (_numeric(out, "coinbase_volume_share_pct") >= 8.0)
    ) & (~major_excluded)
    out["owner_controlled_flag"] = (
        (out["float_trap_score"] >= 60.0)
        | (_numeric(out, "crime_owner_circle_score") >= 55.0)
        | (_numeric(out, "top10_holder_pct") >= 55.0)
    ) & (~major_excluded)
    out["perp_heavy_flag"] = (
        (out["perp_pressure_score"] >= 60.0)
        | ((_numeric(out, "spot_to_perp_volume_pct") <= 8.0) & (_numeric(out, "oi_to_market_cap_pct") >= 10.0))
        | (_numeric(out, "perp_volume_to_mcap_pct") >= 250.0)
    ) & (~major_excluded)

    notes = out.apply(_factor_notes, axis=1, result_type="expand")
    out["why_flagged_summary"] = notes[0]
    out["why_flagged_top_factors"] = notes[1]
    out["why_flagged_offsets"] = notes[2]
    return out


def filter_lifecycle_frame(df: pd.DataFrame, selected_filters: Iterable[str]) -> pd.DataFrame:
    filtered = df.copy()
    for label in selected_filters:
        column = LIFECYCLE_FILTERS.get(label)
        if column and column in filtered.columns:
            filtered = filtered[filtered[column].fillna(False).astype(bool)]
    return filtered
