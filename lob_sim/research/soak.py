"""Exclusive, restartable public paired-symbol capture with host telemetry.

This reuses the existing stream-first collector and synchronizer. It does not
add a parallel exchange reconstruction or change the ordinary capture path.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from itertools import count
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any
import uuid

from lob_sim.binance.rest import BinanceRESTClient
from lob_sim.binance.symbols import parse_exchange_info_for_symbol
from lob_sim.config import Config
from lob_sim.record.async_writer import BoundedCaptureWriter
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_telemetry import SoakMonitor, SoakSettings, TelemetryFile, resource_sample
from lob_sim.research.capture_instruments import validate_instrument_entry
from lob_sim.research.day_eligibility import REQUIRED_SYMBOLS
from lob_sim.research.evidence_io import read_json, retain_failure
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot, source_state


def instrument_entries(exchange: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Preserve all symbol fields/filters, without inventing absent contracts."""
    entries = exchange.get("symbols")
    if not isinstance(entries, list):
        raise ValueError("public exchangeInfo has no symbol array")
    result = {}
    for symbol in REQUIRED_SYMBOLS:
        selected = [e for e in entries if isinstance(e, dict) and e.get("symbol") == symbol]
        if len(selected) != 1:
            raise ValueError("required instrument metadata is missing or duplicated")
        result[symbol] = validate_instrument_entry(symbol, selected[0])
    return result


async def run_soak(
    config: Config, directory: Path, settings: SoakSettings = SoakSettings(), *, restart_from: Path | None = None
) -> dict[str, Any]:
    from lob_sim.cli import _EnvelopeRecordWriter, _collect_symbol

    if (
        config.hmm is not None
        or config.binance_fapi_base.rstrip("/") != "https://fapi.binance.com"
        or config.binance_fws_base.rstrip("/") != "wss://fstream.binance.com"
    ):
        raise ValueError("soak requires HMM off and the public production Binance USD-M endpoints")
    root = directory.resolve()
    restart = None
    if restart_from is not None:
        previous = restart_from.resolve()
        if previous == root or previous.is_relative_to(root):
            raise ValueError("restart must use a new independent directory")
        request = read_json(previous / "request.json")
        restart = {
            "previous_request_sha256": request.get("request_sha256"),
            "previous_request_file_sha256": file_sha256(previous / "request.json"),
            "continuity": "new capture identity;interruption gap unknown;never append or rewrite prior evidence",
        }
        if request.get("request_sha256") != identity({k: v for k, v in request.items() if k != "request_sha256"}):
            raise ValueError("restart parent request identity mismatch")
    root.mkdir(parents=True, exist_ok=False)
    telemetry_sink: TelemetryFile | None = None
    try:
        cfg = replace(
            config,
            symbols=REQUIRED_SYMBOLS,
            collect_seconds=settings.duration_seconds,
            record_dir=root,
            capture_schema_version=3,
            record_gzip=True,
            mm_enabled=False,
        )
        code = checkpoint_code_identity()
        runtime = {
            "pid": os.getpid(),
            "python_version": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "start_wall_ns": time.time_ns(),
            "start_monotonic_ns": time.monotonic_ns(),
            "source": source_state(),
            "code_identity": code,
        }
        request = {
            "schema_version": "lob_sim.public_soak_request.v1",
            "settings": asdict(settings),
            "configuration": config_snapshot(cfg),
            "runtime": runtime,
            "restart": restart,
            "source_kind": "public_binance_network",
        }
        request["request_sha256"] = identity(request)
        publish_json(root / "request.json", request)
        resource_sample(root, settings)
        async with BinanceRESTClient(cfg) as rest:
            exchange = await rest.get_exchange_info()
            entries = instrument_entries(exchange)
            specs = {s: parse_exchange_info_for_symbol(exchange, s) for s in REQUIRED_SYMBOLS}
            capture_id = "capture_" + str(time.time_ns()) + "_" + uuid.uuid4().hex
            stop = asyncio.Event()
            sequence = count(1)

            def next_sequence() -> int:
                return next(sequence)

            telemetry_sink = TelemetryFile(root)
            telemetry = BoundedCaptureWriter[dict[str, Any]](telemetry_sink, capacity=128)
            with SegmentedCaptureWriter(root, capture_id) as segments:
                capture = BoundedCaptureWriter[EventEnvelope](segments, capacity=cfg.capture_writer_queue_max)
                writer = _EnvelopeRecordWriter(capture, capture_id, next_sequence)

                def emit(kind: str, payload: dict[str, Any], symbol: str = "*") -> None:
                    # Control records use native integer receipt time. Network
                    # callbacks retain their original pre-parse identities.
                    capture.write(
                        EventEnvelope(
                            capture_id,
                            SCHEMA_V3,
                            "BINANCE_USDM",
                            symbol,
                            kind,
                            "control",
                            next_sequence(),
                            time.time_ns(),
                            time.monotonic_ns(),
                            payload,
                        )
                    )

                monitor = SoakMonitor(
                    root,
                    settings,
                    telemetry,
                    lambda: capture.stats,
                    lambda: emit("captureEvent", {"event": "clock_anchor", "route": "control"}),
                )
                async with capture, telemetry:
                    emit(
                        "captureMeta",
                        {
                            "schemaVersion": 3,
                            "clock": "receive_time",
                            "source_kind": "public_binance_network",
                            "request_sha256": request["request_sha256"],
                            "routes": {"depth": "public", "aggTrade": "market"},
                            "runtime": runtime,
                            "provenance": "public-network collector declaration;not author authentication",
                        },
                    )
                    for symbol, spec in specs.items():
                        emit(
                            "exchangeInfo",
                            {
                                "symbol": symbol,
                                "tickSize": str(spec.tick_size),
                                "stepSize": str(spec.step_size),
                                "baseAsset": spec.quantity_unit,
                                "quoteAsset": spec.price_currency,
                                "venue": spec.venue,
                                "exchangeInfo": entries[symbol],
                                "metadata_sha256": identity(entries[symbol]),
                            },
                            symbol,
                        )
                    async with asyncio.TaskGroup() as group:
                        group.create_task(capture.wait_for_failure_or_stop(stop))
                        group.create_task(telemetry.wait_for_failure_or_stop(stop))
                        group.create_task(monitor.run(stop))
                        for symbol, spec in specs.items():
                            group.create_task(
                                _collect_symbol(symbol, spec, cfg, rest, writer, stop, False, next_sequence)
                            )
                        await asyncio.sleep(settings.duration_seconds)
                        emit(
                            "captureEvent",
                            {"event": "capture_stopping", "route": "control", "reason": "configured_duration_complete"},
                        )
                        stop.set()
                    await capture.drain()
                    emit(
                        "captureEvent",
                        {"event": "capture_trailer", "route": "control", "reason": "configured_duration_complete"},
                    )
                    await capture.drain()
                await asyncio.to_thread(telemetry_sink.finalize)
                host = monitor.report(telemetry_sink.final, telemetry.stats)
                if checkpoint_code_identity() != code:
                    raise ValueError("capture source code changed during soak")
                segments.update_manifest_metadata(
                    {
                        "writer": capture.stats,
                        "request_sha256": request["request_sha256"],
                        "runtime": runtime,
                        "host_telemetry": host,
                    }
                )
            result = {
                "schema_version": "lob_sim.public_soak_completion.v1",
                "complete": True,
                "request_sha256": request["request_sha256"],
                "capture_manifest": segments.manifest_path.name,
                "capture_manifest_sha256": file_sha256(segments.manifest_path),
                "host_telemetry": host,
                "writer": capture.stats,
                "configured_seconds": settings.duration_seconds,
                "non_claims": [
                    "requires separate integrity and native validity audit",
                    "venue-side packet loss is unobservable",
                    "short runs are not 24h soak evidence",
                ],
            }
            result["completion_sha256"] = identity(result)
            publish_json(root / "completion.json", result)
            return result
    except BaseException as exc:
        if telemetry_sink is not None:
            try:
                telemetry_sink.abandon()
            except BaseException as secondary:
                exc.add_note("Telemetry close failed: " + type(secondary).__name__)
        retain_failure(root, exc, schema="lob_sim.public_soak_failure.v1")
        raise
