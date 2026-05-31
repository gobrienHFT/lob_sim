from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Config
from ..replay.inspection import file_sha256

RUN_MANIFEST_SCHEMA_VERSION = "lob_sim.simulation_run.v1"


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
    status = _git_output(["status", "--short"])
    return {
        "git_commit": _git_output(["rev-parse", "HEAD"]),
        "git_branch": _git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(status),
    }


def config_digest(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


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


def _run_id(input_sha: str, cfg: Config) -> str:
    payload = json.dumps(
        {
            "input_sha256": input_sha,
            "config": config_snapshot(cfg),
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
    runtime: dict[str, Any]
    source: dict[str, Any]
    outputs: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "lob_sim_version": self.lob_sim_version,
            "input": self.input,
            "config": self.config,
            "runtime": self.runtime,
            "source": self.source,
            "outputs": self.outputs,
        }


def build_run_manifest(
    input_path: str | Path,
    cfg: Config,
    output_files: dict[str, Path],
) -> RunManifest:
    path = Path(input_path)
    input_sha = file_sha256(path)
    return RunManifest(
        run_id=_run_id(input_sha, cfg),
        schema_version=RUN_MANIFEST_SCHEMA_VERSION,
        created_at_utc=_utc_now(),
        lob_sim_version=__version__,
        input={
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": input_sha,
            "modified_at_utc": _mtime_utc(path),
        },
        config=config_snapshot(cfg),
        runtime={
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        source=source_state(),
        outputs={name: str(path) for name, path in sorted(output_files.items())},
    )
