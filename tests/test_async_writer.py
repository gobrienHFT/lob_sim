from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from lob_sim.record.async_writer import (
    BoundedCaptureWriter,
    CaptureWriterIOError,
    CaptureWriterQueueFull,
)
from lob_sim.config import ConfigError, load_config
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter, recover_valid_envelopes


class _ListWriter:
    def __init__(self) -> None:
        self.values: list[int] = []

    def write(self, value: int) -> None:
        self.values.append(value)


def test_bounded_writer_preserves_order_and_drains_before_success() -> None:
    sink = _ListWriter()
    writer = BoundedCaptureWriter(sink, capacity=8)

    async def scenario() -> None:
        async with writer:
            for value in range(6):
                writer.write(value)
            await writer.drain()
            assert writer.stats["records_pending"] == 0
            assert writer.stats["complete"] is False

    asyncio.run(scenario())

    assert sink.values == list(range(6))
    assert writer.stats == {
        "queue_capacity": 8,
        "records_enqueued": 6,
        "records_written": 6,
        "records_pending": 0,
        "queue_high_water": pytest.approx(writer.stats["queue_high_water"]),
        "outstanding_high_water": pytest.approx(writer.stats["outstanding_high_water"]),
        "overflow_count": 0,
        "max_writer_lag_ms": pytest.approx(writer.stats["max_writer_lag_ms"]),
        "failure_type": None,
        "aborted": False,
        "complete": True,
    }
    assert 1 <= int(writer.stats["queue_high_water"]) <= 8
    assert float(writer.stats["max_writer_lag_ms"]) >= 0


def test_queue_overflow_fails_closed_without_blocking_the_producer() -> None:
    entered = threading.Event()
    release = threading.Event()

    class _BlockingWriter:
        def write(self, _value: int) -> None:
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test writer was not released")

    writer = BoundedCaptureWriter(_BlockingWriter(), capacity=1)

    async def scenario() -> None:
        with pytest.raises(CaptureWriterQueueFull, match="hard limit"):
            async with writer:
                writer.write(1)
                assert await asyncio.to_thread(entered.wait, 1)
                writer.write(2)
                try:
                    writer.write(3)
                finally:
                    release.set()

    asyncio.run(scenario())

    assert writer.stats["overflow_count"] == 1
    assert writer.stats["failure_type"] == "CaptureWriterQueueFull"
    assert writer.stats["complete"] is False
    assert int(writer.stats["records_enqueued"]) == 2


def test_underlying_writer_failure_wakes_monitor_and_preserves_cause() -> None:
    class _FailingWriter:
        def write(self, _value: int) -> None:
            raise OSError("simulated disk failure")

    writer = BoundedCaptureWriter(_FailingWriter(), capacity=4)

    async def scenario() -> None:
        stop_event = asyncio.Event()
        with pytest.raises(CaptureWriterIOError) as exc_info:
            async with writer:
                writer.write(1)
                await writer.wait_for_failure_or_stop(stop_event)
        assert isinstance(exc_info.value.__cause__, OSError)

    asyncio.run(scenario())

    assert writer.stats["failure_type"] == "CaptureWriterIOError"
    assert writer.stats["complete"] is False


def test_drain_fails_promptly_when_underlying_writer_fails() -> None:
    class _FailingWriter:
        def write(self, _value: int) -> None:
            raise OSError("simulated disk failure")

    writer = BoundedCaptureWriter(_FailingWriter(), capacity=4)

    async def scenario() -> None:
        with pytest.raises(CaptureWriterIOError):
            async with writer:
                writer.write(1)
                await asyncio.wait_for(writer.drain(), timeout=1)

    asyncio.run(scenario())


def test_overflow_leaves_segment_partial_without_success_manifest(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def envelope(sequence: int) -> EventEnvelope:
        return EventEnvelope(
            capture_id="capture-1",
            schema_version=SCHEMA_V3,
            venue="BINANCE_USDM",
            instrument="BTCUSDT",
            event_kind="depthUpdate",
            route="public",
            recv_seq=sequence,
            recv_wall_ns=1_000_000_000 + sequence,
            recv_monotonic_ns=5_000 + sequence,
            stream_epoch=1,
            sync_epoch=1,
            payload={"U": sequence, "u": sequence, "b": [], "a": []},
        )

    class _BlockingSegmentWriter:
        def __init__(self, writer: SegmentedCaptureWriter) -> None:
            self.writer = writer

        def write(self, value: EventEnvelope) -> None:
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test segment writer was not released")
            self.writer.write(value)

    async def scenario() -> None:
        with pytest.raises(CaptureWriterQueueFull):
            with SegmentedCaptureWriter(tmp_path, "capture-1", compression="none") as segmented:
                writer = BoundedCaptureWriter(_BlockingSegmentWriter(segmented), capacity=1)
                async with writer:
                    writer.write(envelope(1))
                    assert await asyncio.to_thread(entered.wait, 1)
                    writer.write(envelope(2))
                    try:
                        writer.write(envelope(3))
                    finally:
                        release.set()

    asyncio.run(scenario())

    partial = next(tmp_path.glob("*.partial"))
    assert [event.recv_seq for event in recover_valid_envelopes(partial)] == [1]
    assert not (tmp_path / "capture-1.manifest.json").exists()


def test_failure_monitor_exits_cleanly_when_capture_stops() -> None:
    writer = BoundedCaptureWriter(_ListWriter(), capacity=2)

    async def scenario() -> None:
        stop_event = asyncio.Event()
        async with writer:
            stop_event.set()
            await writer.wait_for_failure_or_stop(stop_event)

    asyncio.run(scenario())

    assert writer.stats["complete"] is True


def test_capture_writer_queue_capacity_is_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPTURE_WRITER_QUEUE_MAX", "0")

    with pytest.raises(ConfigError, match="CAPTURE_WRITER_QUEUE_MAX must be > 0"):
        load_config(".env.example")
