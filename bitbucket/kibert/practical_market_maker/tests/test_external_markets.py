import math
import unittest

from external_markets import _category_lookup, _dwf_membership_score, _summarize_tickers


class ExternalMarketMetricsTests(unittest.TestCase):
    def test_summarize_tickers_tracks_venue_cluster_and_emfx(self) -> None:
        tickers = [
            {
                "market": {"name": "Binance", "identifier": "binance"},
                "target": "USDT",
                "converted_volume": {"usd": 500.0},
                "bid_ask_spread_percentage": 0.12,
            },
            {
                "market": {"name": "Bitget", "identifier": "bitget"},
                "target": "USDT",
                "converted_volume": {"usd": 250.0},
            },
            {
                "market": {"name": "Gate.io", "identifier": "gate"},
                "target": "USDT",
                "converted_volume": {"usd": 150.0},
            },
            {
                "market": {"name": "Upbit", "identifier": "upbit"},
                "target": "KRW",
                "converted_volume": {"usd": 80.0},
            },
            {
                "market": {"name": "BTCTurk", "identifier": "btcturk"},
                "target": "TRY",
                "converted_volume": {"usd": 20.0},
            },
            {
                "market": {"name": "Uniswap v3 (Ethereum)", "identifier": "uniswap_v3"},
                "target": "USDC",
                "converted_volume": {"usd": 40.0},
            },
        ]

        metrics = _summarize_tickers(tickers)
        self.assertAlmostEqual(metrics["binance_bitget_gate_share_pct"], 86.5384615385, places=4)
        self.assertAlmostEqual(metrics["krw_share_pct"], 7.6923076923, places=4)
        self.assertAlmostEqual(metrics["try_share_pct"], 1.9230769231, places=4)
        self.assertAlmostEqual(metrics["emfx_share_pct"], 9.6153846154, places=4)
        self.assertAlmostEqual(metrics["dex_share_pct"], 3.8461538462, places=4)
        self.assertEqual(metrics["top_venue"], "Binance")
        self.assertTrue(not math.isnan(metrics["venue_hhi"]))

    def test_dwf_category_lookup_dedupes_by_id_and_best_symbol_rank(self) -> None:
        rows = [
            {"coingecko_id": "api3", "normalized_base_asset": "API3", "rank": 26},
            {"coingecko_id": "api3-duplicate", "normalized_base_asset": "API3", "rank": 80},
            {"coingecko_id": "highstreet", "normalized_base_asset": "HIGH", "rank": 124},
        ]

        by_id, by_symbol = _category_lookup(rows)

        self.assertEqual(by_id["api3"]["normalized_base_asset"], "API3")
        self.assertEqual(by_symbol["API3"]["rank"], 26)
        self.assertGreater(_dwf_membership_score(26), 70)
        self.assertGreater(_dwf_membership_score(float("nan")), 70)


if __name__ == "__main__":
    unittest.main()
