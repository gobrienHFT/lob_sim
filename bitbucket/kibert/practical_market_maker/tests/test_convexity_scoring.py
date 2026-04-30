import unittest

import pandas as pd

from convexity_scoring import apply_convexity_model


class ConvexityScoringTests(unittest.TestCase):
    def test_early_convexity_beats_late_heat(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "EARLYUSDT",
                    "crime_excluded_major": False,
                    "float_trap_score": 72,
                    "crime_owner_circle_score": 70,
                    "crime_supply_control_score": 66,
                    "inventory_transfer_risk_score": 74,
                    "locked_supply_pct": 62,
                    "fdv_to_market_cap": 6.5,
                    "top10_holder_pct": 84,
                    "owner_holder_pct": 8,
                    "creator_holder_pct": 4,
                    "venue_support_score": 64,
                    "crime_spot_impulse_score": 72,
                    "inventory_sponsor_mismatch_score": 69,
                    "dwf_labs_portfolio_score": 90,
                    "cex_dex_volume_ratio_score": 82,
                    "binance_bitget_gate_share_score": 78,
                    "emfx_lane_score": 58,
                    "venue_hhi_score": 70,
                    "spot_to_perp_volume_pct": 42,
                    "spot_volume_to_mcap_pct": 160,
                    "ignition_score_v2": 58,
                    "crime_mechanics_score": 66,
                    "hour_volume_multiple": 3.6,
                    "hour_trade_count_multiple": 3.1,
                    "oi_delta_pct": 8.5,
                    "taker_buy_sell_ratio": 1.45,
                    "hour_close_location_pct": 82,
                    "broke_high_5d": True,
                    "broke_high_20d": True,
                    "broke_high_90d": False,
                    "broke_high_180d": False,
                    "day_return_pct": 22,
                    "daily_quote_volume_multiple": 2.8,
                    "distance_to_high_5d_pct": 1.5,
                    "distance_to_high_20d_pct": 4.0,
                    "distance_to_high_90d_pct": 18.0,
                    "perp_pressure_score": 54,
                    "funding_flip_score": 44,
                    "short_crowding_score": 52,
                    "short_squeeze_score": 46,
                    "oi_to_market_cap_pct": 14,
                    "oi_to_24h_volume_pct": 58,
                    "carry_funding_pct": 0.006,
                    "upside_to_ath_pct": 240,
                    "history_days": 220,
                    "crime_largecap_penalty_score": 18,
                    "market_cap_usd": 28_000_000,
                    "crime_exhaustion_score": 22,
                    "exit_fragility_score": 28,
                    "hour_upper_wick_pct": 4,
                    "basis_rate_pct": 0.03,
                    "blowoff_watch_flag": False,
                    "unwind_risk_flag": False,
                    "squeeze_chase_flag": False,
                },
                {
                    "symbol": "LATEUSDT",
                    "crime_excluded_major": False,
                    "float_trap_score": 72,
                    "crime_owner_circle_score": 70,
                    "crime_supply_control_score": 66,
                    "inventory_transfer_risk_score": 74,
                    "locked_supply_pct": 62,
                    "fdv_to_market_cap": 6.5,
                    "top10_holder_pct": 84,
                    "owner_holder_pct": 8,
                    "creator_holder_pct": 4,
                    "venue_support_score": 64,
                    "crime_spot_impulse_score": 72,
                    "inventory_sponsor_mismatch_score": 69,
                    "dwf_labs_portfolio_score": 90,
                    "cex_dex_volume_ratio_score": 82,
                    "binance_bitget_gate_share_score": 78,
                    "emfx_lane_score": 58,
                    "venue_hhi_score": 70,
                    "spot_to_perp_volume_pct": 42,
                    "spot_volume_to_mcap_pct": 160,
                    "ignition_score_v2": 82,
                    "crime_mechanics_score": 82,
                    "hour_volume_multiple": 9.0,
                    "hour_trade_count_multiple": 8.0,
                    "oi_delta_pct": 16.0,
                    "taker_buy_sell_ratio": 2.1,
                    "hour_close_location_pct": 61,
                    "broke_high_5d": True,
                    "broke_high_20d": True,
                    "broke_high_90d": True,
                    "broke_high_180d": True,
                    "day_return_pct": 128,
                    "daily_quote_volume_multiple": 12.0,
                    "distance_to_high_5d_pct": 0.0,
                    "distance_to_high_20d_pct": 0.0,
                    "distance_to_high_90d_pct": 0.0,
                    "perp_pressure_score": 78,
                    "funding_flip_score": 76,
                    "short_crowding_score": 70,
                    "short_squeeze_score": 82,
                    "oi_to_market_cap_pct": 24,
                    "oi_to_24h_volume_pct": 110,
                    "carry_funding_pct": 0.048,
                    "upside_to_ath_pct": 40,
                    "history_days": 220,
                    "crime_largecap_penalty_score": 18,
                    "market_cap_usd": 28_000_000,
                    "crime_exhaustion_score": 82,
                    "exit_fragility_score": 74,
                    "hour_upper_wick_pct": 24,
                    "basis_rate_pct": 0.16,
                    "blowoff_watch_flag": True,
                    "unwind_risk_flag": True,
                    "squeeze_chase_flag": True,
                },
            ]
        )

        scored = apply_convexity_model(frame)
        self.assertGreater(scored.loc[0, "convexity_score"], scored.loc[1, "convexity_score"])
        self.assertGreater(scored.loc[0, "convexity_preignition_score"], 45)
        self.assertTrue(bool(scored.loc[0, "pre_pump_candidate_flag"]))
        self.assertTrue(bool(scored.loc[0, "early_convexity_flag"]))
        self.assertTrue(bool(scored.loc[0, "convexity_prime_flag"]))
        self.assertGreater(scored.loc[0, "mm_sponsor_confluence_score"], 50)
        self.assertIn("DWF Labs portfolio", scored.loc[0, "convexity_top_factors"])
        self.assertFalse(bool(scored.loc[0, "convexity_chase_risk_flag"]))
        self.assertFalse(bool(scored.loc[0, "convexity_too_late_flag"]))
        self.assertFalse(bool(scored.loc[1, "pre_pump_candidate_flag"]))
        self.assertTrue(bool(scored.loc[1, "convexity_chase_risk_flag"]))
        self.assertTrue(bool(scored.loc[1, "convexity_too_late_flag"]))
        self.assertIn("DWF Labs Portfolio", scored.loc[0, "convexity_summary"])


if __name__ == "__main__":
    unittest.main()
