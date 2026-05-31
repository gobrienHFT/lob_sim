from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import __version__
from ..book.types import InstrumentSpec
from ..config import Config
from ..replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter, adapter_metadata
from ..replay.inspection import file_sha256
from .fill_model import TRADE_DEPTH_OVERLAP_WINDOW_SECONDS

RUN_MANIFEST_SCHEMA_VERSION = "lob_sim.simulation_run.v2"
SIMULATION_ASSUMPTIONS_SCHEMA_VERSION = "lob_sim.simulation_assumptions.v1"
SOURCE_STATE_OVERRIDE_ENV = "LOB_SIM_SOURCE_STATE_JSON"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_output(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def source_state() -> dict[str, Any]:
    override = os.getenv(SOURCE_STATE_OVERRIDE_ENV)
    if override:
        decoded = json.loads(override)
        if not isinstance(decoded, dict):
            raise ValueError(f"{SOURCE_STATE_OVERRIDE_ENV} must decode to a JSON object")
        return {
            "git_commit": decoded.get("git_commit"),
            "git_branch": decoded.get("git_branch"),
            "git_dirty": bool(decoded.get("git_dirty")),
        }
    status = _git_output(["status", "--short"])
    return {
        "git_commit": _git_output(["rev-parse", "HEAD"]),
        "git_branch": _git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(status),
    }


def config_digest(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def output_artifact_snapshot(
    output_files: dict[str, Path],
    path_formatter: Callable[[Path], str] | None = None,
) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    format_path = path_formatter or str
    for name, path in sorted(output_files.items()):
        metadata: dict[str, Any] = {"path": format_path(path)}
        if name != "manifest" and path.exists():
            metadata.update(
                {
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                    "modified_at_utc": _mtime_utc(path),
                }
            )
        artifacts[name] = metadata
    return artifacts


def config_snapshot(cfg: Config) -> dict[str, Any]:
    """Return the non-secret configuration that affects replay/simulation behavior."""

    return {
        "symbols": list(cfg.symbols),
        "book_top_n": cfg.book_top_n,
        "snapshot_limit": cfg.snapshot_limit,
        "resync_on_gap": cfg.resync_on_gap,
        "sim_seed": cfg.sim_seed,
        "sim_order_latency_ms": cfg.sim_order_latency_ms,
        "sim_cancel_latency_ms": cfg.sim_cancel_latency_ms,
        "sim_adverse_markout_seconds": cfg.sim_adverse_markout_seconds,
        "sim_kill_switch_enabled": cfg.sim_kill_switch_enabled,
        "sim_kill_max_drawdown": str(cfg.sim_kill_max_drawdown),
        "sim_kill_max_consecutive_losses": cfg.sim_kill_max_consecutive_losses,
        "mm_enabled": cfg.mm_enabled,
        "mm_strategy_profile": cfg.mm_strategy_profile,
        "mm_requote_ms": cfg.mm_requote_ms,
        "mm_order_qty": str(cfg.mm_order_qty),
        "mm_max_position": str(cfg.mm_max_position),
        "mm_half_spread_bps": str(cfg.mm_half_spread_bps),
        "mm_layered_inner_spread_bps": str(cfg.mm_layered_inner_spread_bps),
        "mm_layered_outer_spread_bps": str(cfg.mm_layered_outer_spread_bps),
        "mm_volatility_window": cfg.mm_volatility_window,
        "mm_volatility_spread_factor": str(cfg.mm_volatility_spread_factor),
        "mm_skew_bps_per_unit": str(cfg.mm_skew_bps_per_unit),
        "mm_queue_repost_lots": cfg.mm_queue_repost_lots,
        "mm_trade_imbalance_window": cfg.mm_trade_imbalance_window,
        "mm_microstructure_gate_threshold": str(cfg.mm_microstructure_gate_threshold),
        "mm_microstructure_gate_bps": str(cfg.mm_microstructure_gate_bps),
        "mm_fee_floor_buffer_bps": str(cfg.mm_fee_floor_buffer_bps),
        "mm_toxicity_spread_factor": str(cfg.mm_toxicity_spread_factor),
        "fees_maker_bps": str(cfg.fees_maker_bps),
        "fees_taker_bps": str(cfg.fees_taker_bps),
    }


def instrument_specs_snapshot(specs: Mapping[str, InstrumentSpec]) -> dict[str, dict[str, str]]:
    """Return stable, JSON-friendly instrument metadata keyed by symbol."""

    snapshot: dict[str, dict[str, str]] = {}
    for symbol, spec in sorted(specs.items()):
        snapshot[str(symbol)] = {
            "symbol": spec.symbol,
            "venue": spec.venue,
            "price_currency": spec.price_currency,
            "quantity_unit": spec.quantity_unit,
            "tick_size": str(spec.tick_size),
            "step_size": str(spec.step_size),
            "contract_multiplier": str(spec.contract_multiplier),
        }
    return snapshot


def simulation_assumptions_snapshot() -> dict[str, Any]:
    """Return the public-data and queue-fill assumptions attached to run artifacts."""

    return {
        "schema_version": SIMULATION_ASSUMPTIONS_SCHEMA_VERSION,
        "data_scope": "public_l2_order_book_and_agg_trade_records",
        "private_exchange_execution_reports": False,
        "queue_priority_model": "visible_price_time_fifo",
        "snapshot_seed": "Snapshot levels seed visible venue liquidity ahead of strategy orders at each price level.",
        "depth_increase": "Depth increases append later venue liquidity behind existing visible queue at that price.",
        "depth_decrease": (
            "Depth reductions consume FIFO visible queue ahead of strategy orders; reductions may be trades, "
            "cancels, or both, so unmatched public consumption is reported instead of hidden."
        ),
        "agg_trade_consumption": (
            "Public aggTrade prints consume same-price visible queue on the resting side as an additional "
            "queue-consumption signal."
        ),
        "overlap_netting": {
            "enabled": True,
            "window_seconds": TRADE_DEPTH_OVERLAP_WINDOW_SECONDS,
            "purpose": "Net recent depth and aggTrade consumption at the same symbol, side, and price to reduce double counting.",
        },
        "cancel_model": "Cancel requests take configured latency; resting quotes remain fillable until acknowledgement.",
        "same_timestamp_ordering": (
            "Engine events due at a market-row timestamp are drained before that row; strategy reactions to "
            "that row run after the row and any fills it produced."
        ),
        "marketable_limits": (
            "Marketable strategy limits execute as taker orders against visible depth and post only any "
            "non-crossing remainder."
        ),
        "self_trade_prevention": (
            "Strategy taker orders stop before own resting liquidity; the crossed remainder expires instead "
            "of self-trading."
        ),
        "markout": "Post-fill adverse selection uses signed mid-price markout over the configured horizon.",
        "limitations": [
            "no_private_queue_ids",
            "no_hidden_liquidity",
            "not_private_exchange_fill_truth",
            "public_l2_cannot_distinguish_all_cancels_from_trades",
        ],
    }


def _run_id(input_sha: str, cfg: Config, feed_adapter: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "input_sha256": input_sha,
            "config": config_snapshot(cfg),
            "feed_adapter": feed_adapter,
            "lob_sim_version": __version__,
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    schema_version: str
    created_at_utc: str
    lob_sim_version: str
    input: dict[str, Any]
    config: dict[str, Any]
    feed_adapter: dict[str, Any]
    instrument_specs: dict[str, dict[str, str]]
    simulation_assumptions: dict[str, Any]
    runtime: dict[str, Any]
    source: dict[str, Any]
    outputs: dict[str, str]
    output_artifacts: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "lob_sim_version": self.lob_sim_version,
            "input": self.input,
            "config": self.config,
            "feed_adapter": self.feed_adapter,
            "instrument_specs": self.instrument_specs,
            "simulation_assumptions": self.simulation_assumptions,
            "runtime": self.runtime,
            "source": self.source,
            "outputs": self.outputs,
            "output_artifacts": self.output_artifacts,
        }


def build_run_manifest(
    input_path: str | Path,
    cfg: Config,
    output_files: dict[str, Path],
    *,
    created_at_utc: str | None = None,
    source: dict[str, Any] | None = None,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
    instrument_specs: Mapping[str, InstrumentSpec] | None = None,
) -> RunManifest:
    path = Path(input_path)
    input_sha = file_sha256(path)
    feed_adapter = adapter_metadata(adapter)
    return RunManifest(
        run_id=_run_id(input_sha, cfg, feed_adapter),
        schema_version=RUN_MANIFEST_SCHEMA_VERSION,
        created_at_utc=created_at_utc or _utc_now(),
        lob_sim_version=__version__,
        input={
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": input_sha,
            "modified_at_utc": _mtime_utc(path),
        },
        config=config_snapshot(cfg),
        feed_adapter=feed_adapter,
        instrument_specs=instrument_specs_snapshot(instrument_specs or {}),
        simulation_assumptions=simulation_assumptions_snapshot(),
        runtime={
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        source=source if source is not None else source_state(),
        outputs={name: str(path) for name, path in sorted(output_files.items())},
        output_artifacts=output_artifact_snapshot(output_files),
    )
