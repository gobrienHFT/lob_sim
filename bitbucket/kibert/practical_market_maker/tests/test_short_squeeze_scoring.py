import unittest

import pandas as pd

from short_squeeze_scoring import apply_short_squeeze_model


class ShortSqueezeScoringTests(unittest.TestCase):
    def test_funding_flip_breakout_candidate_scores_high(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "SPKUSDT",
                    "carry_funding_pct": 0.012,
                    "predicted_funding_pct": 0.014,
                    "last_settled_funding_pct": -0.016,
                    "prior_settled_funding_pct": -0.012,
                    "funding_flip_delta_pct": 0.030,
                    "premium_index_pct": 0.08,
                    "basis_rate_pct": 0.06,
                    "long_short_account_ratio": 0.82,
                    "long_account_pct": 45.0,
                    "short_account_pct": 55.0,
                    "top_trader_position_ratio": 0.88,
                    "top_trader_account_ratio": 0.91,
                    "oi_to_24h_volume_pct": 72.0,
                    "oi_delta_pct": 8.5,
                    "oi_to_market_cap_pct": 14.0,
                    "oi_value_usdt": 18_500_000.0,
                    "broke_high_5d": True,
                    "broke_high_20d": True,
                    "broke_high_90d": True,
                    "broke_high_180d": True,
                    "hour_return_z": 4.1,
                    "day_return_pct": 21.0,
                    "hour_volume_multiple": 4.5,
                    "hour_trade_count_multiple": 3.4,
                    "taker_buy_sell_ratio": 1.6,
                    "taker_buy_share_pct": 63.0,
                    "hour_close_location_pct": 87.0,
                    "hour_upper_wick_pct": 7.0,
                    "upside_to_ath_pct": 210.0,
                    "history_days": 260,
                    "crime_largecap_penalty_score": 12.0,
                    "crime_exhaustion_score": 22.0,
                }
            ]
        )

        scored = apply_short_squeeze_model(frame)
        self.assertTrue(bool(scored.loc[0, "funding_flip_up_flag"]))
        self.assertTrue(bool(scored.loc[0, "fresh_flip_flag"]))
        self.assertTrue(bool(scored.loc[0, "active_short_squeeze_flag"]))
        self.assertGreater(scored.loc[0, "short_squeeze_score"], 58.0)
        self.assertIn("Funding Flipped", scored.loc[0, "short_squeeze_summary"])

    def test_chase_flag_triggers_when_move_is_overextended(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "EXTUSDT",
                    "carry_funding_pct": 0.065,
                    "predicted_funding_pct": 0.070,
                    "last_settled_funding_pct": 0.020,
                    "prior_settled_funding_pct": 0.015,
                    "funding_flip_delta_pct": 0.050,
                    "day_return_pct": 95.0,
                    "hour_upper_wick_pct": 30.0,
                    "crime_exhaustion_score": 82.0,
                    "long_short_account_ratio": 1.8,
                }
            ]
        )

        scored = apply_short_squeeze_model(frame)
        self.assertTrue(bool(scored.loc[0, "squeeze_chase_flag"]))


if __name__ == "__main__":
    unittest.main()
