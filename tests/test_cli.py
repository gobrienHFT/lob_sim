from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.cli import cmd_collect
from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord
from lob_sim.replay.reader import iter_records


REPO_ROOT = Path(__file__).resolve().parents[1]


class _CaptureRESTClient:
    def __init__(self, _config: object) -> None:
        pass

    async def __aenter__(self) -> "_CaptureRESTClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }


def test_cli_doctor_reports_redacted_config() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "--env", ".env.example", "doctor"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["record_schema_version"] == "lob_sim.record.v1"
    assert payload["symbols"]
    assert "binance_api_key" not in payload["config"]
    assert "binance_api_secret" not in payload["config"]
    assert payload["config"]["fill_assumption_profile"] == "base"


def test_cli_simulate_exposes_fill_profile_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "simulate", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--fill-profile" in result.stdout
    assert "--in-memory-export" in result.stdout
    assert "memory grows" in " ".join(result.stdout.split())
    assert "conservative" in result.stdout
    assert "aggressive" in result.stdout


def test_cli_replay_and_default_bounded_simulate_dispatch_end_to_end(tmp_path: Path) -> None:
    fixture = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "input_fixture.ndjson"
    env = {**os.environ, "RECORD_DIR": str(tmp_path)}
    replay_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lob_sim.cli",
            "--env",
            ".env.example",
            "replay",
            "--file",
            str(fixture),
            "--progress-every",
            "0",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert replay_result.returncode == 0, replay_result.stderr

    simulation_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lob_sim.cli",
            "--env",
            ".env.example",
            "simulate",
            "--file",
            str(fixture),
            "--progress-every",
            "0",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert simulation_result.returncode == 0, simulation_result.stderr
    summary = json.loads(simulation_result.stdout)
    assert summary["simulation_export"]["mode"] == "bounded_streaming"
    manifest_path = Path(summary["output_files"]["manifest"])
    assert manifest_path.is_file()
    assert not (manifest_path.parent / "_INCOMPLETE.json").exists()


def test_cli_exposes_reviewer_grade_command_surface() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lob_sim.cli", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for command in ("capture", "validate", "normalize", "replay", "simulate", "compare", "audit", "bench", "demo"):
        assert command in result.stdout


def test_capture_command_finalizes_trailer_and_writer_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        load_config(".env.example"),
        symbols=("BTCUSDT",),
        collect_seconds=1,
        record_dir=tmp_path,
        record_gzip=False,
        capture_schema_version=3,
        capture_writer_queue_max=16,
    )

    async def fake_collect_symbol(
        symbol: str,
        _spec: object,
        _config: object,
        _rest: object,
        writer: object,
        stop_event: asyncio.Event,
        _verbose: bool,
        next_receive_seq: object,
    ) -> None:
        sequence = next_receive_seq()  # type: ignore[operator]
        writer.write(  # type: ignore[attr-defined]
            NDJSONRecord(
                ts_local=1.0,
                symbol=symbol,
                type="captureEvent",
                data={
                    "event": "connect",
                    "route": "public",
                    "_capture": {
                        "recvSeq": sequence,
                        "recvMonotonicNs": sequence,
                        "streamEpoch": 1,
                        "syncEpoch": 1,
                        "route": "public",
                    },
                },
            )
        )
        await stop_event.wait()

    monkeypatch.setattr("lob_sim.cli.BinanceRESTClient", _CaptureRESTClient)
    monkeypatch.setattr("lob_sim.cli._collect_symbol", fake_collect_symbol)

    asyncio.run(cmd_collect(config))

    manifest_path = next(tmp_path.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = list(iter_records(manifest_path))
    assert [record.type for record in records] == [
        "captureMeta",
        "exchangeInfo",
        "captureEvent",
        "captureEvent",
    ]
    assert records[-1].data["event"] == "capture_trailer"
    assert records[0].data["writerQueueCapacity"] == 16
    assert [record.data["_capture"]["recvSeq"] for record in records] == [1, 2, 3, 4]
    assert manifest["capture_runtime"]["writer"]["complete"] is True
    assert manifest["capture_runtime"]["writer"]["overflow_count"] == 0
    assert manifest["capture_runtime"]["writer"]["records_written"] == 4
    assert not list(tmp_path.glob("*.partial"))
    assert not list(tmp_path.glob("*.failure.json"))


def test_capture_command_failure_keeps_partial_and_writes_sanitized_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        load_config(".env.example"),
        symbols=("BTCUSDT",),
        collect_seconds=60,
        record_dir=tmp_path,
        record_gzip=False,
        capture_schema_version=3,
        capture_writer_queue_max=16,
    )

    async def fail_collect_symbol(*_args: object, **_kwargs: object) -> None:
        raise OSError("sensitive disk path")

    monkeypatch.setattr("lob_sim.cli.BinanceRESTClient", _CaptureRESTClient)
    monkeypatch.setattr("lob_sim.cli._collect_symbol", fail_collect_symbol)

    with pytest.raises(ExceptionGroup):
        asyncio.run(cmd_collect(config))

    assert list(tmp_path.glob("*.ndjson.partial"))
    assert not list(tmp_path.glob("*.manifest.json"))
    report_path = next(tmp_path.glob("*.failure.json"))
    raw = report_path.read_text(encoding="utf-8")
    assert "sensitive disk path" not in raw
    report = json.loads(raw)
    assert report["complete"] is False
    assert "OSError" in report["failure_types"]
    assert report["writer"]["complete"] is False
