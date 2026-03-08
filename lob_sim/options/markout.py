from __future__ import annotations


def markout_horizon_label(horizon_steps: int) -> str:
    if horizon_steps == 1:
        return "1-step"
    return f"{horizon_steps}-step"


def signed_markout(
    mm_side: str,
    fill_price: float,
    reference_fair_value: float,
    qty_contracts: int,
    contract_size: int,
) -> float:
    """Return signed markout for a market-maker fill.

    The repo defines markout against the model fair value at a fixed future
    horizon taken from the realized simulation path. Positive values are good
    for the market maker; negative values indicate adverse selection.

    Formula:
        direction = +1 for a market-maker buy fill
        direction = -1 for a market-maker sell fill
        signed_markout =
            direction * (reference_fair_value - fill_price) * qty_contracts * contract_size

    Examples:
    - If the market maker buys at 4.00 and the horizon fair value is 4.20,
      markout is positive.
    - If the market maker sells at 4.20 and the horizon fair value is 4.40,
      markout is negative because the option was sold too cheaply.
    """

    direction = 1.0 if mm_side == "buy" else -1.0
    return direction * (reference_fair_value - fill_price) * qty_contracts * contract_size
