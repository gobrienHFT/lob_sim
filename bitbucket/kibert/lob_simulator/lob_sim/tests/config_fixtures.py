from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from lob_sim.config import Config


def make_config(**overrides) -> Config:
    values = {
        "binance_api_key": "",
        "binance_api_secret": "",
        "binance_fapi_base": "https://fapi.binance.com",
        "binance_fws_base": "wss://fstream.binance.com",
        "symbols": ("BTCUSDT",),
        "depth_stream_suffix": "@depth@100ms",
        "trade_stream_suffix": "@aggTrade",
        "snapshot_limit": 1000,
        "book_top_n": 50,
        "collect_seconds": 10,
        "record_dir": Path("data"),
        "record_format": "ndjson",
        "record_gzip": False,
        "record_flush_every": 100,
        "http_timeout": 10.0,
        "http_retries": 2,
        "rate_limit_req_per_sec": 8.0,
        "ws_ping_interval": 180.0,
        "ws_ping_timeout": 600.0,
        "ws_reconnect_max_sec": 30.0,
        "resync_on_gap": True,
        "sim_order_latency_ms": 10.0,
        "sim_cancel_latency_ms": 10.0,
        "mm_enabled": True,
        "mm_requote_ms": 250.0,
        "mm_order_qty": Decimal("0.001"),
        "mm_max_position": Decimal("0.01"),
        "mm_half_spread_bps": Decimal("2"),
        "mm_skew_bps_per_unit": Decimal("10"),
        "fees_maker_bps": Decimal("0"),
        "fees_taker_bps": Decimal("0"),
        "log_level": "CRITICAL",
    }
    values.update(overrides)
    return Config(**values)
