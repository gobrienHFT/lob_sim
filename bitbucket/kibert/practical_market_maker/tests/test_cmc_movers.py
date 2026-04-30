import unittest
from unittest.mock import patch

from cmc_movers import fetch_cmc_movers


class CmcMoverTests(unittest.TestCase):
    def test_no_key_returns_empty_without_requesting(self) -> None:
        with patch("cmc_movers.requests.get") as mocked_get:
            self.assertEqual(fetch_cmc_movers(""), [])
            mocked_get.assert_not_called()

    def test_merges_one_hour_and_day_movers_by_symbol(self) -> None:
        rave_row = {
            "symbol": "RAVE",
            "name": "RaveDAO",
            "quote": {
                "USD": {
                    "percent_change_1h": 18.5,
                    "percent_change_24h": 111.6,
                    "market_cap": 389_000_000,
                    "volume_24h": 681_000_000,
                }
            },
        }
        bas_row = {
            "symbol": "BAS",
            "name": "BNB Attestation Service",
            "quote": {
                "USD": {
                    "percent_change_1h": 11.0,
                    "percent_change_24h": 63.9,
                    "market_cap": 40_000_000,
                    "volume_24h": 12_000_000,
                }
            },
        }

        with patch("cmc_movers._listing_rows", side_effect=[[rave_row, bas_row], [bas_row, rave_row]]):
            movers = fetch_cmc_movers("key", limit=2)

        by_symbol = {mover.base_asset: mover for mover in movers}
        self.assertEqual(set(by_symbol), {"RAVE", "BAS"})
        self.assertEqual(by_symbol["RAVE"].cmc_rank_1h, 1.0)
        self.assertEqual(by_symbol["RAVE"].cmc_rank_24h, 2.0)
        self.assertGreater(by_symbol["RAVE"].cmc_mover_score, 70.0)
        self.assertIn("CMC 1H #1", by_symbol["RAVE"].cmc_mover_label)


if __name__ == "__main__":
    unittest.main()
