from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv
import logging
import os


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ConfigError(f"Missing required env var: {name}")
    return value


def _get_optional(name: str, default: str) -> str:
    return os.getenv(name, default)


def _parse_bool(name: str, value: str | None) -> bool:
    if value is None:
        raise ConfigError(f"Missing boolean env var: {name}")
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Invalid boolean env var {name}: {value}")


def _parse_int(name: str, value: str | None) -> int:
    if value is None:
        raise ConfigError(f"Missing int env var: {name}")
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid int env var {name}: {value}") from exc


def _parse_float(name: str, value: str | None) -> float:
    if value is None:
        raise ConfigError(f"Missing float env var: {name}")
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid float env var {name}: {value}") from exc


def _parse_decimal(name: str, value: str | None) -> Decimal:
    if value is None:
        raise ConfigError(f"Missing decimal env var: {name}")
    try:
        return Decimal(value)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid decimal env var {name}: {value}") from exc


def _parse_symbols(value: str | None) -> Tuple[str, ...]:
    if value is None:
        raise ConfigError("Missing env var SYMBOLS")
    symbols = tuple(sorted({sym.strip().upper() for sym in value.split(",") if sym.strip()}))
    if not symbols:
        raise ConfigError("SYMBOLS must contain at least one symbol")
    return symbols


@dataclass(frozen=True)
class Config:
    binance_api_key: str
    binance_api_secret: str
    binance_fapi_base: str
    binance_fws_base: str
    symbols: Tuple[str, ...]
    depth_stream_suffix: str
    trade_stream_suffix: str
    snapshot_limit: int
    book_top_n: int
    collect_seconds: int
    record_dir: Path
    record_format: str
    record_gzip: bool
    record_flush_every: int
    http_timeout: float
    http_retries: int
    rate_limit_req_per_sec: float
    ws_ping_interval: float
    ws_ping_timeout: float
    ws_reconnect_max_sec: float
    resync_on_gap: bool
    sim_seed: int
    sim_order_latency_ms: float
    sim_cancel_latency_ms: float
    sim_adverse_markout_seconds: float
    sim_kill_switch_enabled: bool
    sim_kill_max_drawdown: Decimal
    sim_kill_max_consecutive_losses: int
    mm_enabled: bool
    mm_strategy_profile: str
    mm_requote_ms: float
    mm_order_qty: Decimal
    mm_max_position: Decimal
    mm_half_spread_bps: Decimal
    mm_layered_inner_spread_bps: Decimal
    mm_layered_outer_spread_bps: Decimal
    mm_volatility_window: int
    mm_volatility_spread_factor: Decimal
    mm_skew_bps_per_unit: Decimal
    mm_queue_repost_lots: int
    mm_trade_imbalance_window: int
    mm_microstructure_gate_threshold: Decimal
    mm_microstructure_gate_bps: Decimal
    fees_maker_bps: Decimal
    fees_taker_bps: Decimal
    log_level: str

    def __post_init__(self) -> None:
        errs = []

        if not self.binance_fapi_base.startswith("http"):
            errs.append("BINANCE_FAPI_BASE must be http/https URL")
        if not self.binance_fws_base.startswith("wss"):
            errs.append("BINANCE_FWS_BASE must be wss URL")
        if not self.symbols:
            errs.append("SYMBOLS must include at least one symbol")
        if not self.depth_stream_suffix:
            errs.append("DEPTH_STREAM_SUFFIX cannot be empty")
        if not self.trade_stream_suffix:
            errs.append("TRADE_STREAM_SUFFIX cannot be empty")
        if self.snapshot_limit <= 0:
            errs.append("SNAPSHOT_LIMIT must be > 0")
        if self.book_top_n <= 0:
            errs.append("BOOK_TOP_N must be > 0")
        if self.collect_seconds <= 0:
            errs.append("COLLECT_SECONDS must be > 0")
        if str(self.record_format).lower() != "ndjson":
            errs.append("RECORD_FORMAT must be ndjson")
        if self.record_flush_every <= 0:
            errs.append("RECORD_FLUSH_EVERY must be > 0")
        if self.http_timeout <= 0:
            errs.append("HTTP_TIMEOUT must be > 0")
        if self.http_retries < 0:
            errs.append("HTTP_RETRIES must be >= 0")
        if self.rate_limit_req_per_sec <= 0:
            errs.append("RATE_LIMIT_REQ_PER_SEC must be > 0")
        if self.ws_ping_interval <= 0 or self.ws_ping_timeout <= 0:
            errs.append("WS ping timings must be > 0")
        if self.ws_reconnect_max_sec <= 0:
            errs.append("WS_RECONNECT_MAX_SEC must be > 0")
        if self.mm_requote_ms <= 0:
            errs.append("MM_REQUOTE_MS must be > 0")
        if self.mm_order_qty <= 0:
            errs.append("MM_ORDER_QTY must be > 0")
        if self.mm_max_position <= 0:
            errs.append("MM_MAX_POSITION must be > 0")
        if self.mm_strategy_profile not in {"baseline", "layered_mm"}:
            errs.append("MM_STRATEGY_PROFILE must be baseline or layered_mm")
        if self.mm_half_spread_bps < 0:
            errs.append("MM_HALF_SPREAD_BPS must be >= 0")
        if self.mm_layered_inner_spread_bps < 0:
            errs.append("MM_LAYERED_INNER_SPREAD_BPS must be >= 0")
        if self.mm_layered_outer_spread_bps < self.mm_layered_inner_spread_bps:
            errs.append("MM_LAYERED_OUTER_SPREAD_BPS must be >= MM_LAYERED_INNER_SPREAD_BPS")
        if self.mm_volatility_window <= 0:
            errs.append("MM_VOLATILITY_WINDOW must be > 0")
        if self.mm_volatility_spread_factor < 0:
            errs.append("MM_VOLATILITY_SPREAD_FACTOR must be >= 0")
        if self.mm_queue_repost_lots < 0:
            errs.append("MM_QUEUE_REPOST_LOTS must be >= 0")
        if self.mm_trade_imbalance_window <= 0:
            errs.append("MM_TRADE_IMBALANCE_WINDOW must be > 0")
        if self.mm_microstructure_gate_threshold < 0 or self.mm_microstructure_gate_threshold > 1:
            errs.append("MM_MICROSTRUCTURE_GATE_THRESHOLD must be between 0 and 1")
        if self.mm_microstructure_gate_bps < 0:
            errs.append("MM_MICROSTRUCTURE_GATE_BPS must be >= 0")
        if self.sim_adverse_markout_seconds < 0:
            errs.append("SIM_ADVERSE_MARKOUT_SECONDS must be >= 0")
        if self.sim_kill_max_drawdown < 0:
            errs.append("SIM_KILL_MAX_DRAWDOWN must be >= 0")
        if self.sim_kill_max_consecutive_losses < 0:
            errs.append("SIM_KILL_MAX_CONSECUTIVE_LOSSES must be >= 0")

        if errs:
            raise ConfigError("; ".join(errs))

        if self.log_level not in logging._nameToLevel:
            raise ConfigError(f"Invalid LOG_LEVEL: {self.log_level}")
        logging.basicConfig(level=self.log_level)

    @property
    def output_dir(self) -> Path:
        return self.record_dir / "outputs"


def load_config(env_path: str = ".env") -> Config:
    resolved_env_path = Path(env_path)
    if not resolved_env_path.exists() and resolved_env_path.name == ".env":
        example_path = resolved_env_path.with_name(".env.example")
        if example_path.exists():
            resolved_env_path = example_path
    load_dotenv(resolved_env_path)

    cfg = Config(
        binance_api_key=_get_optional("BINANCE_API_KEY", "").strip(),
        binance_api_secret=_get_optional("BINANCE_API_SECRET", "").strip(),
        binance_fapi_base=_require("BINANCE_FAPI_BASE").rstrip("/"),
        binance_fws_base=_require("BINANCE_FWS_BASE").rstrip("/"),
        symbols=_parse_symbols(_get_optional("SYMBOLS", "")),
        depth_stream_suffix=_require("DEPTH_STREAM_SUFFIX"),
        trade_stream_suffix=_require("TRADE_STREAM_SUFFIX"),
        snapshot_limit=_parse_int("SNAPSHOT_LIMIT", _get_optional("SNAPSHOT_LIMIT", "1000")),
        book_top_n=_parse_int("BOOK_TOP_N", _get_optional("BOOK_TOP_N", "50")),
        collect_seconds=_parse_int("COLLECT_SECONDS", _get_optional("COLLECT_SECONDS", "3600")),
        record_dir=Path(_get_optional("RECORD_DIR", "./data")),
        record_format=_get_optional("RECORD_FORMAT", "ndjson"),
        record_gzip=_parse_bool("RECORD_GZIP", _get_optional("RECORD_GZIP", "1")),
        record_flush_every=_parse_int("RECORD_FLUSH_EVERY", _get_optional("RECORD_FLUSH_EVERY", "2000")),
        http_timeout=_parse_float("HTTP_TIMEOUT", _get_optional("HTTP_TIMEOUT", "10")),
        http_retries=_parse_int("HTTP_RETRIES", _get_optional("HTTP_RETRIES", "2")),
        rate_limit_req_per_sec=_parse_float("RATE_LIMIT_REQ_PER_SEC", _get_optional("RATE_LIMIT_REQ_PER_SEC", "8")),
        ws_ping_interval=_parse_float("WS_PING_INTERVAL", _get_optional("WS_PING_INTERVAL", "180")),
        ws_ping_timeout=_parse_float("WS_PING_TIMEOUT", _get_optional("WS_PING_TIMEOUT", "600")),
        ws_reconnect_max_sec=_parse_float("WS_RECONNECT_MAX_SEC", _get_optional("WS_RECONNECT_MAX_SEC", "30")),
        resync_on_gap=_parse_bool("RESYNC_ON_GAP", _get_optional("RESYNC_ON_GAP", "1")),
        sim_seed=_parse_int("SIM_SEED", _get_optional("SIM_SEED", "1")),
        sim_order_latency_ms=_parse_float("SIM_ORDER_LATENCY_MS", _get_optional("SIM_ORDER_LATENCY_MS", "25")),
        sim_cancel_latency_ms=_parse_float("SIM_CANCEL_LATENCY_MS", _get_optional("SIM_CANCEL_LATENCY_MS", "25")),
        sim_adverse_markout_seconds=_parse_float(
            "SIM_ADVERSE_MARKOUT_SECONDS",
            _get_optional("SIM_ADVERSE_MARKOUT_SECONDS", "1.0"),
        ),
        sim_kill_switch_enabled=_parse_bool("SIM_KILL_SWITCH_ENABLED", _get_optional("SIM_KILL_SWITCH_ENABLED", "0")),
        sim_kill_max_drawdown=_parse_decimal("SIM_KILL_MAX_DRAWDOWN", _get_optional("SIM_KILL_MAX_DRAWDOWN", "0")),
        sim_kill_max_consecutive_losses=_parse_int(
            "SIM_KILL_MAX_CONSECUTIVE_LOSSES",
            _get_optional("SIM_KILL_MAX_CONSECUTIVE_LOSSES", "0"),
        ),
        mm_enabled=_parse_bool("MM_ENABLED", _get_optional("MM_ENABLED", "1")),
        mm_strategy_profile=_get_optional("MM_STRATEGY_PROFILE", "baseline").strip().lower(),
        mm_requote_ms=_parse_float("MM_REQUOTE_MS", _get_optional("MM_REQUOTE_MS", "250")),
        mm_order_qty=_parse_decimal("MM_ORDER_QTY", _get_optional("MM_ORDER_QTY", "0.001")),
        mm_max_position=_parse_decimal("MM_MAX_POSITION", _get_optional("MM_MAX_POSITION", "0.01")),
        mm_half_spread_bps=_parse_decimal("MM_HALF_SPREAD_BPS", _get_optional("MM_HALF_SPREAD_BPS", "2.0")),
        mm_layered_inner_spread_bps=_parse_decimal(
            "MM_LAYERED_INNER_SPREAD_BPS",
            _get_optional("MM_LAYERED_INNER_SPREAD_BPS", "2.0"),
        ),
        mm_layered_outer_spread_bps=_parse_decimal(
            "MM_LAYERED_OUTER_SPREAD_BPS",
            _get_optional("MM_LAYERED_OUTER_SPREAD_BPS", "6.0"),
        ),
        mm_volatility_window=_parse_int("MM_VOLATILITY_WINDOW", _get_optional("MM_VOLATILITY_WINDOW", "30")),
        mm_volatility_spread_factor=_parse_decimal(
            "MM_VOLATILITY_SPREAD_FACTOR",
            _get_optional("MM_VOLATILITY_SPREAD_FACTOR", "8.0"),
        ),
        mm_skew_bps_per_unit=_parse_decimal("MM_SKEW_BPS_PER_UNIT", _get_optional("MM_SKEW_BPS_PER_UNIT", "10.0")),
        mm_queue_repost_lots=_parse_int("MM_QUEUE_REPOST_LOTS", _get_optional("MM_QUEUE_REPOST_LOTS", "0")),
        mm_trade_imbalance_window=_parse_int(
            "MM_TRADE_IMBALANCE_WINDOW",
            _get_optional("MM_TRADE_IMBALANCE_WINDOW", "12"),
        ),
        mm_microstructure_gate_threshold=_parse_decimal(
            "MM_MICROSTRUCTURE_GATE_THRESHOLD",
            _get_optional("MM_MICROSTRUCTURE_GATE_THRESHOLD", "0.20"),
        ),
        mm_microstructure_gate_bps=_parse_decimal(
            "MM_MICROSTRUCTURE_GATE_BPS",
            _get_optional("MM_MICROSTRUCTURE_GATE_BPS", "1.0"),
        ),
        fees_maker_bps=_parse_decimal("FEES_MAKER_BPS", _get_optional("FEES_MAKER_BPS", "-0.2")),
        fees_taker_bps=_parse_decimal("FEES_TAKER_BPS", _get_optional("FEES_TAKER_BPS", "4.0")),
        log_level=_get_optional("LOG_LEVEL", "INFO").upper(),
    )
    return cfg
