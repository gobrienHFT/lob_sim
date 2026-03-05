from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt

from .black_scholes import OptionContract


@dataclass(frozen=True)
class SurfaceParams:
    atm_vol: float = 0.24
    skew: float = -0.18
    convexity: float = 0.65
    term_slope: float = 0.04
    min_vol: float = 0.05


class SimpleVolSurface:
    def __init__(self, params: SurfaceParams | None = None) -> None:
        self.params = params or SurfaceParams()

    def implied_vol(self, spot: float, contract: OptionContract, time_to_expiry: float | None = None) -> float:
        remaining = max(contract.expiry_years if time_to_expiry is None else time_to_expiry, 1e-6)
        log_moneyness = log(max(contract.strike, 1e-9) / max(spot, 1e-9))
        vol = (
            self.params.atm_vol
            + self.params.skew * log_moneyness
            + self.params.convexity * (log_moneyness ** 2)
            + self.params.term_slope * sqrt(remaining)
        )
        return max(self.params.min_vol, vol)
