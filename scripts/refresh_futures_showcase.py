from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.sim.engine import SimulationEngine
from lob_sim.util import write_summary_csv


REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough"
INPUT_FIXTURE_NAME = "input_fixture.ndjson"

SHOWCASE_ENV = {
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


def showcase_records() -> list[NDJSONRecord]:
    return [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"symbol": "BTCUSDT", "tickSize": "0.1", "stepSize": "0.001"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(
                100,
                [("100.0", "0.002")],
                [("100.1", "0.003")],
            ),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 95,
                "u": 105,
                "pu": 94,
                "b": [["100.0", "0.002"]],
                "a": [["100.1", "0.003"]],
            },
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 106,
                "u": 106,
                "pu": 105,
                "b": [["100.0", "0.001"]],
                "a": [["100.1", "0.003"]],
            },
        ),
        NDJSONRecord(
            ts_local=2.4,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
        NDJSONRecord(
            ts_local=3.6,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 107,
                "u": 107,
                "pu": 106,
                "b": [["100.0", "0.001"]],
                "a": [["100.1", "0.004"]],
            },
        ),
    ]


def _write_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in showcase_records():
            handle.write(record.to_json())
            handle.write("\n")
    return path


def _path_for_summary(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


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


def refresh_futures_showcase(output_dir: Path = SHOWCASE_DIR) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    fixture_path = _write_fixture(output_dir / INPUT_FIXTURE_NAME)

    with TemporaryDirectory(prefix="lob_sim_futures_showcase_") as temp_dir:
        env = dict(SHOWCASE_ENV)
        env["RECORD_DIR"] = temp_dir
        with _temporary_env(env):
            cfg = load_config(".env.example")
            engine = SimulationEngine(cfg)
            metrics = engine.run(fixture_path)
            generated_paths, summary = engine.write_outputs(str(fixture_path), metrics)

        if summary["fill_count"] <= 0:
            raise RuntimeError("Showcase fixture did not produce a passive fill.")
        if summary["quote_count"] <= 0:
            raise RuntimeError("Showcase fixture did not produce any strategy quotes.")

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
        "fixture": fixture_path,
        **committed_paths,
    }


def main() -> int:
    paths = refresh_futures_showcase()
    print(f"Refreshed futures showcase in {SHOWCASE_DIR}")
    for name, path in paths.items():
        print(f"- {name}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
