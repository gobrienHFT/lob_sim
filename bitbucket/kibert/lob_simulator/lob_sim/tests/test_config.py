from __future__ import annotations

import math

import pytest
from config_fixtures import make_config

from lob_sim.config import ConfigError, _parse_positive_int_tuple


def test_new_simulation_controls_have_conservative_defaults():
    cfg = make_config()

    assert cfg.sim_fill_model == "trade"
    assert cfg.sim_markout_horizons_ms == (100, 1000, 5000)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sim_order_latency_ms", -0.1, "SIM_ORDER_LATENCY_MS"),
        ("sim_cancel_latency_ms", -0.1, "SIM_CANCEL_LATENCY_MS"),
        ("sim_order_latency_ms", math.inf, "SIM_ORDER_LATENCY_MS"),
        ("sim_cancel_latency_ms", math.nan, "SIM_CANCEL_LATENCY_MS"),
    ],
)
def test_latencies_must_be_finite_and_nonnegative(field, value, message):
    with pytest.raises(ConfigError, match=message):
        make_config(**{field: value})


@pytest.mark.parametrize("model", ["optimistic", "both", "", "TRADE"])
def test_fill_model_is_explicit_and_validated(model):
    with pytest.raises(ConfigError, match="SIM_FILL_MODEL"):
        make_config(sim_fill_model=model)


@pytest.mark.parametrize("horizons", [(), (0,), (-1, 100), (100, 100), (1000, 100)])
def test_markout_horizons_are_positive_unique_and_ascending(horizons):
    with pytest.raises(ConfigError, match="SIM_MARKOUT_HORIZONS_MS"):
        make_config(sim_markout_horizons_ms=horizons)


def test_markout_horizon_parser_normalizes_whitespace_and_order():
    assert _parse_positive_int_tuple("SIM_MARKOUT_HORIZONS_MS", " 5000, 100,1000 ") == (
        100,
        1000,
        5000,
    )


@pytest.mark.parametrize("value", ["", "100,,1000", "100,0", "abc,100"])
def test_markout_horizon_parser_rejects_invalid_values(value):
    with pytest.raises(ConfigError, match="SIM_MARKOUT_HORIZONS_MS"):
        _parse_positive_int_tuple("SIM_MARKOUT_HORIZONS_MS", value)
