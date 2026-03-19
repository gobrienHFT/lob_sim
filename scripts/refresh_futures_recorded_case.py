from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from lob_sim.config import load_config
from lob_sim.sim.engine import SimulationEngine
from lob_sim.util import write_summary_csv


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDED_CASE_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case"
INPUT_CANDIDATES = ("input_clip.ndjson", "input_clip.ndjson.gz")

RECORDED_CASE_ENV = {
    "BINANCE_FAPI_BASE": "https://fapi.binance.com",
    "BINANCE_FWS_BASE": "wss://fstream.binance.com",
    "SYMBOLS": "BTCUSDT",
    "DEPTH_STREAM_SUFFIX": "@depth@100ms",
    "TRADE_STREAM_SUFFIX": "@aggTrade",
    "SNAPSHOT_LIMIT": "1000",
    "BOOK_TOP_N": "20",
    "COLLECT_SECONDS": "10",
    "RECORD_FORMAT": "ndjson",
    "RECORD_GZIP": "0",
    "RECORD_FLUSH_EVERY": "300",
    "HTTP_TIMEOUT": "10",
    "HTTP_RETRIES": "2",
    "RATE_LIMIT_REQ_PER_SEC": "8",
    "WS_PING_INTERVAL": "180",
    "WS_PING_TIMEOUT": "600",
    "WS_RECONNECT_MAX_SEC": "30",
    "RESYNC_ON_GAP": "1",
    "SIM_SEED": "1",
    "SIM_ORDER_LATENCY_MS": "0",
    "SIM_CANCEL_LATENCY_MS": "0",
    "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
    "SIM_KILL_SWITCH_ENABLED": "0",
    "SIM_KILL_MAX_DRAWDOWN": "0",
    "SIM_KILL_MAX_CONSECUTIVE_LOSSES": "0",
    "MM_ENABLED": "1",
    "MM_REQUOTE_MS": "1000",
    "MM_ORDER_QTY": "0.001",
    "MM_MAX_POSITION": "0.01",
    "MM_HALF_SPREAD_BPS": "0.05",
    "MM_VOLATILITY_WINDOW": "30",
    "MM_VOLATILITY_SPREAD_FACTOR": "0",
    "MM_SKEW_BPS_PER_UNIT": "0",
    "MM_QUEUE_REPOST_LOTS": "99",
    "FEES_MAKER_BPS": "0",
    "FEES_TAKER_BPS": "0",
    "LOG_LEVEL": "ERROR",
}


def _path_for_summary(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _find_input_clip(case_dir: Path) -> Path:
    for name in INPUT_CANDIDATES:
        candidate = case_dir / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Recorded case input missing in {case_dir}. Expected one of: {', '.join(INPUT_CANDIDATES)}"
    )


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def refresh_futures_recorded_case(output_dir: Path = RECORDED_CASE_DIR) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    input_path = _find_input_clip(output_dir)

    with TemporaryDirectory(prefix="lob_sim_futures_recorded_case_") as temp_dir:
        env = dict(RECORDED_CASE_ENV)
        env["RECORD_DIR"] = temp_dir
        with _temporary_env(env):
            cfg = load_config(".env.example")
            engine = SimulationEngine(cfg)
            metrics = engine.run(input_path)
            generated_paths, summary = engine.write_outputs(str(input_path), metrics)

        if summary["quote_count"] <= 0:
            raise RuntimeError("Recorded case clip did not produce any strategy quotes.")
        if summary["fill_count"] <= 0:
            raise RuntimeError("Recorded case clip did not produce a passive fill.")

        committed_paths = {
            "summary": output_dir / "summary.json",
            "summary_csv": output_dir / "summary.csv",
            "trades": output_dir / "trades.csv",
        }
        summary["output_files"] = {
            name: _path_for_summary(path)
            for name, path in committed_paths.items()
        }

        committed_paths["summary"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        write_summary_csv(committed_paths["summary_csv"], summary, exclude_keys={"fills", "markout_events"})
        shutil.copyfile(generated_paths["trades"], committed_paths["trades"])

    return {
        "input": input_path,
        **committed_paths,
    }


def main() -> int:
    paths = refresh_futures_recorded_case()
    print(f"Refreshed recorded futures case in {RECORDED_CASE_DIR}")
    for name, path in paths.items():
        print(f"- {name}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
