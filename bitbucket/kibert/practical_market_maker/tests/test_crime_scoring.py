import unittest

import pandas as pd

from crime_scoring import apply_lifecycle_model, filter_lifecycle_frame


class CrimeLifecycleScoringTests(unittest.TestCase):
    def test_rave_style_row_scores_and_explains(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "RAVEUSDT",
                    "crime_excluded_major": False,
                    "crime_supply_control_score": 75,
                    "crime_owner_circle_score": 78,
                    "top10_holder_pct": 90,
                    "locked_supply_pct": 70,
                    "fdv_to_market_cap": 8,
                    "holder_count": 25_000,
                    "inventory_sponsor_mismatch_score": 72,
                    "inventory_transfer_risk_score": 76,
                    "crime_mechanics_score": 82,
                    "crime_microstructure_score": 70,
                    "crime_spot_impulse_score": 76,
                    "broke_high_5d": True,
                    "broke_high_20d": True,
                    "broke_high_90d": True,
                    "hour_return_z": 4.5,
                    "day_return_pct": 140,
                    "hour_volume_multiple": 6,
                    "hour_trade_count_multiple": 5,
                    "taker_buy_sell_ratio": 1.8,
                    "taker_buy_share_pct": 66,
                    "oi_delta_pct": 12,
                    "oi_to_24h_volume_pct": 75,
                    "crime_coinbase_lane_score": 85,
                    "coinbase_spot_listed": True,
                    "coinbase_volume_share_pct": 18,
                    "mm_presence_score": 72,
                    "mm_bid_support_score": 86,
                    "mm_proximity_score": 80,
                    "mm_proximity_maker": "Wintermute",
                    "trade_bucket_note": "OI expanding | trade count spike | MM present on CB spot",
                    "oi_to_market_cap_pct": 42,
                    "carry_funding_pct": 0.04,
                    "predicted_funding_pct": 0.04,
                    "premium_index_pct": 0.25,
                    "basis_rate_pct": 0.18,
                    "crime_carry_stress_score": 70,
                    "long_short_account_ratio": 1.8,
                    "long_account_pct": 67,
                    "top_trader_long_position_pct": 66,
                    "top_trader_account_ratio": 1.6,
                    "top_trader_long_account_pct": 64,
                    "crowd_top_position_divergence_pct": 12,
                    "crowd_top_account_divergence_pct": 7,
                    "spot_to_perp_volume_pct": 4,
                    "coinbase_to_perp_volume_pct": 3,
                    "perp_volume_to_mcap_pct": 620,
                    "spot_volume_to_mcap_pct": 180,
                    "coinbase_bid_ask_spread_pct": 0.02,
                    "coinbase_bid_depth_2pct_usd": 1_200_000,
                    "coinbase_ask_depth_2pct_usd": 950_000,
                    "coinbase_total_depth_2pct_usd": 2_150_000,
                    "coinbase_book_imbalance_pct": 53,
                    "coinbase_depth_to_volume_pct": 1.20,
                    "coinbase_depth_to_perp_volume_pct": 0.60,
                    "venue_count": 8,
                    "top_venue_volume_share_pct": 32,
                    "top3_venue_volume_share_pct": 78,
                    "dex_volume_share_pct": 12,
                    "krw_volume_share_pct": 14,
                    "kraken_volume_share_pct": 4,
                    "mm_withdrawal_risk_score": 35,
                    "hour_upper_wick_pct": 10,
                    "hour_close_location_pct": 88,
                    "ask_depth_1pct_usdt": 60_000,
                    "ask_depth_to_24h_volume_pct": 0.08,
                    "crime_exhaustion_score": 35,
                    "blowoff_risk_flag": False,
                    "crime_largecap_penalty_score": 5,
                }
            ]
        )
        scored = apply_lifecycle_model(frame)
        self.assertGreater(scored.loc[0, "float_trap_score"], 60)
        self.assertGreater(scored.loc[0, "crime_pump_score_v2"], 65)
        self.assertTrue(bool(scored.loc[0, "coinbase_lane_flag"]))
        self.assertTrue(bool(scored.loc[0, "perp_heavy_flag"]))
        self.assertIn("Coinbase Lane", scored.loc[0, "why_flagged_summary"])
        self.assertIn("controlled float", scored.loc[0, "why_flagged_top_factors"])

    def test_major_is_stabilized_and_filtered(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "crime_excluded_major": True,
                    "crime_supply_control_score": 100,
                    "crime_owner_circle_score": 100,
                    "crime_mechanics_score": 100,
                    "crime_microstructure_score": 100,
                    "crime_spot_impulse_score": 100,
                    "crime_coinbase_lane_score": 100,
                    "crime_largecap_penalty_score": 100,
                    "broke_high_5d": True,
                    "broke_high_20d": True,
                    "broke_high_90d": True,
                }
            ]
        )
        scored = apply_lifecycle_model(frame)
        self.assertEqual(scored.loc[0, "crime_pump_score_v2"], 0)
        self.assertFalse(bool(scored.loc[0, "active_squeeze_flag"]))

    def test_filter_lifecycle_frame(self) -> None:
        frame = pd.DataFrame(
            [
                {"symbol": "A", "perp_heavy_flag": True, "setup_ready_flag": True},
                {"symbol": "B", "perp_heavy_flag": False, "setup_ready_flag": True},
            ]
        )
        filtered = filter_lifecycle_frame(frame, ["Perp Heavy"])
        self.assertEqual(filtered["symbol"].tolist(), ["A"])

    def test_cex_dex_skew_lifts_ignition_and_explains(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "THINUSDT",
                    "crime_excluded_major": False,
                    "crime_mechanics_score": 45,
                    "crime_microstructure_score": 60,
                    "crime_spot_impulse_score": 45,
                    "day_return_pct": 20,
                    "cex_to_dex_volume_ratio": 40,
                    "cex_dex_volume_ratio_score": 82,
                    "cex_volume_share_pct": 97,
                },
                {
                    "symbol": "FLATUSDT",
                    "crime_excluded_major": False,
                    "crime_mechanics_score": 45,
                    "crime_microstructure_score": 60,
                    "crime_spot_impulse_score": 45,
                    "day_return_pct": 20,
                    "cex_to_dex_volume_ratio": 1,
                    "cex_dex_volume_ratio_score": 0,
                    "cex_volume_share_pct": 50,
                },
            ]
        )

        scored = apply_lifecycle_model(frame)
        self.assertGreater(scored.loc[0, "ignition_score_v2"], scored.loc[1, "ignition_score_v2"])
        self.assertGreater(scored.loc[0, "venue_support_score"], scored.loc[1, "venue_support_score"])
        self.assertIn("CEX/DEX skew", scored.loc[0, "why_flagged_top_factors"])

    def test_venue_cluster_and_emfx_lane_lift_scores(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "CLUSTERUSDT",
                    "crime_excluded_major": False,
                    "crime_mechanics_score": 55,
                    "crime_microstructure_score": 58,
                    "crime_spot_impulse_score": 52,
                    "day_return_pct": 18,
                    "hour_return_z": 2.8,
                    "cex_dex_volume_ratio_score": 76,
                    "cex_to_dex_volume_ratio": 24,
                    "binance_bitget_gate_share_pct": 72,
                    "emfx_volume_share_pct": 22,
                    "try_volume_share_pct": 8,
                    "krw_volume_share_pct": 14,
                    "venue_hhi": 3600,
                },
                {
                    "symbol": "BROADUSDT",
                    "crime_excluded_major": False,
                    "crime_mechanics_score": 55,
                    "crime_microstructure_score": 58,
                    "crime_spot_impulse_score": 52,
                    "day_return_pct": 18,
                    "hour_return_z": 2.8,
                    "cex_dex_volume_ratio_score": 76,
                    "cex_to_dex_volume_ratio": 24,
                    "binance_bitget_gate_share_pct": 12,
                    "emfx_volume_share_pct": 0.5,
                    "try_volume_share_pct": 0.0,
                    "krw_volume_share_pct": 0.0,
                    "venue_hhi": 1100,
                },
            ]
        )

        scored = apply_lifecycle_model(frame)
        self.assertGreater(scored.loc[0, "ignition_score_v2"], scored.loc[1, "ignition_score_v2"])
        self.assertGreater(scored.loc[0, "venue_support_score"], scored.loc[1, "venue_support_score"])
        self.assertIn("Binance/Bitget/Gate lane", scored.loc[0, "why_flagged_top_factors"])


if __name__ == "__main__":
    unittest.main()
