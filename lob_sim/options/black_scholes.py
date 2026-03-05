from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, pi, sqrt
from typing import Literal

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    option_type: OptionType
    strike: float
    expiry_years: float


@dataclass(frozen=True)
class OptionGreeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * pi)


def option_metrics(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
    option_type: OptionType,
) -> OptionGreeks:
    spot = max(spot, 1e-9)
    strike = max(strike, 1e-9)
    time_to_expiry = max(time_to_expiry, 0.0)
    vol = max(vol, 1e-9)

    if time_to_expiry <= 0.0:
        intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
        if option_type == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return OptionGreeks(price=intrinsic, delta=delta, gamma=0.0, vega=0.0, theta=0.0)

    sqrt_t = sqrt(time_to_expiry)
    d1 = (log(spot / strike) + (rate + 0.5 * vol * vol) * time_to_expiry) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    discount = exp(-rate * time_to_expiry)

    if option_type == "call":
        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (
            -(spot * _norm_pdf(d1) * vol) / (2.0 * sqrt_t)
            - rate * strike * discount * _norm_cdf(d2)
        )
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -(spot * _norm_pdf(d1) * vol) / (2.0 * sqrt_t)
            + rate * strike * discount * _norm_cdf(-d2)
        )

    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t
    return OptionGreeks(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta)
