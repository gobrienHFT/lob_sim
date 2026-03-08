from __future__ import annotations

import math

import pytest

from lob_sim.options.black_scholes import option_metrics
from lob_sim.options.markout import signed_markout


def test_call_price_is_monotonic_in_spot() -> None:
    low_spot = option_metrics(spot=95.0, strike=100.0, time_to_expiry=0.5, rate=0.01, vol=0.25, option_type="call")
    mid_spot = option_metrics(spot=100.0, strike=100.0, time_to_expiry=0.5, rate=0.01, vol=0.25, option_type="call")
    high_spot = option_metrics(spot=105.0, strike=100.0, time_to_expiry=0.5, rate=0.01, vol=0.25, option_type="call")

    assert low_spot.price < mid_spot.price < high_spot.price


def test_put_call_parity_holds_within_tolerance() -> None:
    spot = 102.0
    strike = 100.0
    time_to_expiry = 0.75
    rate = 0.015
    vol = 0.22

    call = option_metrics(
        spot=spot,
        strike=strike,
        time_to_expiry=time_to_expiry,
        rate=rate,
        vol=vol,
        option_type="call",
    )
    put = option_metrics(
        spot=spot,
        strike=strike,
        time_to_expiry=time_to_expiry,
        rate=rate,
        vol=vol,
        option_type="put",
    )
    forward_parity = spot - strike * math.exp(-rate * time_to_expiry)

    assert call.price - put.price == pytest.approx(forward_parity, abs=1e-6)


def test_call_delta_and_gamma_have_expected_signs() -> None:
    call = option_metrics(spot=100.0, strike=100.0, time_to_expiry=0.25, rate=0.0, vol=0.2, option_type="call")

    assert 0.0 < call.delta < 1.0
    assert call.gamma > 0.0


def test_signed_markout_sign_convention_matches_market_maker_side() -> None:
    buy_markout = signed_markout(
        mm_side="buy",
        fill_price=4.00,
        reference_fair_value=4.20,
        qty_contracts=2,
        contract_size=100,
    )
    sell_markout = signed_markout(
        mm_side="sell",
        fill_price=4.20,
        reference_fair_value=4.40,
        qty_contracts=2,
        contract_size=100,
    )

    assert buy_markout > 0.0
    assert sell_markout < 0.0
    assert buy_markout == pytest.approx(40.0)
    assert sell_markout == pytest.approx(-40.0)
