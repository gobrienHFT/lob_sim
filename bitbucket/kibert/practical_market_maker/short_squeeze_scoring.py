from __future__ import annotations

import math

import pandas as pd


SHORT_SQUEEZE_SCORE_COLUMNS = [
    "breakout_stack_count",
    "effective_funding_pct",
    "funding_flip_up_flag",
    "funding_flip_score",
    "short_crowding_score",
    "breakout_pressure_score",
    "runway_score",
    "short_squeeze_score",
    "fresh_flip_flag",
    "active_short_squeeze_flag",
    "squeeze_chase_flag",
    "short_squeeze_summary",
    "short_squeeze_top_factors",
    "short_squeeze_offsets",
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
        ("funding flipped", _safe_value(row, "funding_flip_score")),
        ("short crowding", _safe_value(row, "short_crowding_score")),
        ("breakout pressure", _safe_value(row, "breakout_pressure_score")),
        ("ATH runway", _safe_value(row, "runway_score")),
        ("breakout stack", _safe_value(row, "breakout_stack_count") * 20.0),
        ("flip delta", _safe_value(row, "funding_flip_delta_pct") * 1000.0),
    ]
    offsets: list[tuple[str, float]] = [
        ("chase risk", _safe_value(row, "squeeze_chase_penalty")),
        ("exhaustion", _safe_value(row, "crime_exhaustion_score")),
        ("upper wick", _safe_value(row, "hour_upper_wick_pct")),
        ("crowd already long", _safe_value(row, "long_short_account_ratio") * 25.0),
    ]
    positive = sorted(
        [(name, value) for name, value in positive if not math.isnan(value) and value >= 30.0],
        key=lambda item: item[1],
        reverse=True,
    )
    offsets = sorted(
        [(name, value) for name, value in offsets if not math.isnan(value) and value >= 35.0],
        key=lambda item: item[1],
        reverse=True,
    )
    summary = " + ".join(name.title() for name, _ in positive[:4]) if positive else "No strong short-squeeze confluence yet"
    top = " | ".join(f"{name}: {value:.1f}" for name, value in positive[:5]) or "No top contributors above threshold."
    negative = " | ".join(f"{name}: {value:.1f}" for name, value in offsets[:4]) or "No major negative offsets."
    return summary, top, negative


def apply_short_squeeze_model(df: pd.DataFrame) -> pd.DataFrame:
    """Apply funding-flip / short-squeeze detection.

    short_squeeze_score =
        0.32 * funding_flip_score
      + 0.24 * short_crowding_score
      + 0.29 * breakout_pressure_score
      + 0.15 * runway_score
      - 0.20 * squeeze_chase_penalty
    """
    out = df.copy()
    if out.empty:
        for column in SHORT_SQUEEZE_SCORE_COLUMNS:
            out[column] = pd.Series(dtype="object" if column.startswith("short_squeeze_") else "float64")
        return out

    index = out.index
    effective_funding = _numeric(out, "predicted_funding_pct").where(
        _numeric(out, "predicted_funding_pct").notna(),
        _numeric(out, "carry_funding_pct"),
    )
    out["effective_funding_pct"] = effective_funding
    out["breakout_stack_count"] = (
        out.get("broke_high_5d", pd.Series(False, index=index)).fillna(False).astype(bool).astype(int)
        + out.get("broke_high_20d", pd.Series(False, index=index)).fillna(False).astype(bool).astype(int)
        + out.get("broke_high_90d", pd.Series(False, index=index)).fillna(False).astype(bool).astype(int)
        + out.get("broke_high_180d", pd.Series(False, index=index)).fillna(False).astype(bool).astype(int)
    )
    breakout_stack_score = ((out["breakout_stack_count"] - 1.0) / 3.0 * 100.0).clip(lower=0.0, upper=100.0)

    out["funding_flip_up_flag"] = (
        (_numeric(out, "last_settled_funding_pct") <= -0.002)
        & (effective_funding >= 0.002)
        & (_numeric(out, "funding_flip_delta_pct") >= 0.004)
    )

    out["funding_flip_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "effective_funding_pct", 0.0015, 0.02), 0.25),
            (_score_linear(out.assign(last_negative=-_numeric(out, "last_settled_funding_pct")), "last_negative", 0.0015, 0.02), 0.27),
            (_score_linear(out.assign(prior_negative=-_numeric(out, "prior_settled_funding_pct")), "prior_negative", 0.0015, 0.02), 0.08),
            (_score_linear(out, "funding_flip_delta_pct", 0.004, 0.04), 0.20),
            (_score_linear(out, "premium_index_pct", 0.005, 0.30), 0.10),
            (_score_linear(out, "basis_rate_pct", 0.005, 0.25), 0.10),
        ],
    )

    out["short_crowding_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "long_short_account_ratio", 0.55, 1.05, invert=True), 0.24),
            (_score_linear(out, "short_account_pct", 50.0, 68.0), 0.18),
            (_score_linear(out, "top_trader_position_ratio", 0.55, 1.05, invert=True), 0.10),
            (_score_linear(out, "top_trader_account_ratio", 0.55, 1.05, invert=True), 0.08),
            (_score_linear(out, "oi_to_24h_volume_pct", 10.0, 120.0), 0.12),
            (_score_linear(out, "oi_delta_pct", 1.0, 20.0), 0.18),
            (_score_linear(out, "oi_to_market_cap_pct", 3.0, 35.0), 0.06),
            (_score_log(out, "oi_value_usdt", 250_000.0, 100_000_000.0), 0.04),
        ],
    )

    breakout_confirmation = (
        _bool_score(out, "broke_high_5d") * 0.15
        + _bool_score(out, "broke_high_20d") * 0.20
        + _bool_score(out, "broke_high_90d") * 0.30
        + _bool_score(out, "broke_high_180d") * 0.35
    ).clip(lower=0.0, upper=100.0)

    out["breakout_pressure_score"] = _weighted_average(
        index,
        [
            (breakout_confirmation, 0.15),
            (breakout_stack_score, 0.11),
            (_score_linear(out, "hour_return_z", 0.5, 5.0), 0.12),
            (_score_linear(out, "day_return_pct", 3.0, 60.0), 0.12),
            (_score_linear(out, "hour_volume_multiple", 1.2, 8.0), 0.12),
            (_score_linear(out, "hour_trade_count_multiple", 1.2, 8.0), 0.10),
            (_score_linear(out, "taker_buy_sell_ratio", 1.0, 2.5), 0.10),
            (_score_linear(out, "taker_buy_share_pct", 50.0, 75.0), 0.06),
            (_score_linear(out, "hour_close_location_pct", 55.0, 95.0), 0.07),
            (_score_linear(out, "hour_upper_wick_pct", 0.0, 25.0, invert=True), 0.05),
        ],
    )

    out["runway_score"] = _weighted_average(
        index,
        [
            (_score_linear(out, "upside_to_ath_pct", 15.0, 250.0), 0.70),
            (_score_linear(out, "history_days", 90.0, 365.0), 0.20),
            (_score_linear(out, "crime_largecap_penalty_score", 0.0, 100.0, invert=True), 0.10),
        ],
    )

    out["squeeze_chase_penalty"] = _weighted_average(
        index,
        [
            (_score_linear(out, "crime_exhaustion_score", 50.0, 90.0), 0.40),
            (_score_linear(out, "hour_upper_wick_pct", 18.0, 50.0), 0.20),
            (_score_linear(out, "day_return_pct", 40.0, 150.0), 0.15),
            (_score_linear(out, "effective_funding_pct", 0.025, 0.12), 0.10),
            (_score_linear(out, "long_short_account_ratio", 1.20, 2.50), 0.15),
        ],
    )

    out["short_squeeze_score"] = (
        out["funding_flip_score"] * 0.32
        + out["short_crowding_score"] * 0.24
        + out["breakout_pressure_score"] * 0.29
        + out["runway_score"] * 0.15
        - out["squeeze_chase_penalty"] * 0.20
    ).clip(lower=0.0, upper=100.0)

    out["fresh_flip_flag"] = (
        out["funding_flip_up_flag"]
        & (out["funding_flip_score"] >= 50.0)
        & (out["short_crowding_score"] >= 40.0)
        & (out["breakout_pressure_score"] >= 50.0)
        & (_numeric(out, "breakout_stack_count") >= 2.0)
        & (out["squeeze_chase_penalty"] < 45.0)
    )
    out["active_short_squeeze_flag"] = (
        out["funding_flip_up_flag"]
        & (out["short_squeeze_score"] >= 60.0)
        & (out["breakout_pressure_score"] >= 60.0)
        & (
            out.get("broke_high_20d", pd.Series(False, index=index)).fillna(False).astype(bool)
            | out.get("broke_high_90d", pd.Series(False, index=index)).fillna(False).astype(bool)
            | out.get("broke_high_180d", pd.Series(False, index=index)).fillna(False).astype(bool)
        )
    )
    out["squeeze_chase_flag"] = (
        (out["squeeze_chase_penalty"] >= 60.0)
        | ((_numeric(out, "day_return_pct") >= 60.0) & (_numeric(out, "hour_upper_wick_pct") >= 18.0))
        | (_numeric(out, "crime_exhaustion_score") >= 70.0)
    )

    notes = out.apply(_explain, axis=1, result_type="expand")
    out["short_squeeze_summary"] = notes[0]
    out["short_squeeze_top_factors"] = notes[1]
    out["short_squeeze_offsets"] = notes[2]
    return out
