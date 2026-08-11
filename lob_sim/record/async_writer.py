"""Bounded, ordered capture I/O outside the websocket event loop."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


RecordT = TypeVar("RecordT")
RecordT_contra = TypeVar("RecordT_contra", contravariant=True)


class SynchronousWriter(Protocol[RecordT_contra]):
    def write(self, record: RecordT_contra) -> None: ...


class CaptureWriterError(RuntimeError):
    """Base error for fail-closed asynchronous capture writing."""


class CaptureWriterQueueFull(CaptureWriterError):
    """The bounded capture queue filled and the tape is incomplete."""


class CaptureWriterIOError(CaptureWriterError):
    """The underlying writer failed and the tape is incomplete."""


class CaptureWriterStateError(CaptureWriterError):
    """The writer was used outside its valid lifecycle."""


@dataclass(frozen=True)
class _QueuedRecord(Generic[RecordT]):
    value: RecordT
    enqueued_monotonic_ns: int


_STOP = object()


class BoundedCaptureWriter(Generic[RecordT]):
    """Serialize capture records on one worker thread behind a hard bound.

    ``write`` never waits for disk or compression. A full queue is an
    integrity failure, not permission to drop or block receipt processing.
    """

    def __init__(self, writer: SynchronousWriter[RecordT], *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capture writer queue capacity must be positive")
        self._writer = writer
        self._capacity = capacity
        self._queue: queue.Queue[_QueuedRecord[RecordT] | object] = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._failure_future: asyncio.Future[None] | None = None
        self._failure: CaptureWriterError | None = None
        self._accepting = False
        self._closed = False
        self._aborted = False
        self._enqueued_count = 0
        self._written_count = 0
        self._queue_high_water = 0
        self._outstanding_high_water = 0
        self._overflow_count = 0
        self._max_writer_lag_ns = 0

    async def start(self) -> None:
        if self._thread is not None or self._closed:
            raise CaptureWriterStateError("capture writer cannot be started twice")
        self._loop = asyncio.get_running_loop()
        self._failure_future = self._loop.create_future()
        self._accepting = True
        self._thread = threading.Thread(
            target=self._worker,
            name="lob-sim-capture-writer",
            daemon=False,
        )
        self._thread.start()

    def _notify_failure(self, failure: CaptureWriterError) -> None:
        with self._lock:
            if self._failure is not None:
                return
            self._failure = failure
        self._signal_failure(failure)

    def _signal_failure(self, failure: CaptureWriterError) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._complete_failure_future, failure)

    def _complete_failure_future(self, failure: CaptureWriterError) -> None:
        future = self._failure_future
        if future is not None and not future.done():
            future.set_exception(failure)

    def _worker(self) -> None:
        while not self._abort.is_set():
            queued = self._queue.get()
            if queued is _STOP:
                self._queue.task_done()
                break
            if not isinstance(queued, _QueuedRecord):
                self._queue.task_done()
                self._notify_failure(CaptureWriterIOError("capture queue contained an invalid record"))
                self._discard_pending()
                break
            if self._abort.is_set():
                self._queue.task_done()
                break
            try:
                self._writer.write(queued.value)
            except Exception as exc:
                self._queue.task_done()
                # Preserve the original exception for local debugging without
                # serializing potentially sensitive exception text.
                failure = CaptureWriterIOError("underlying capture writer failed")
                failure.__cause__ = exc
                self._notify_failure(failure)
                self._discard_pending()
                break
            lag_ns = max(0, time.monotonic_ns() - queued.enqueued_monotonic_ns)
            with self._lock:
                self._written_count += 1
                self._max_writer_lag_ns = max(self._max_writer_lag_ns, lag_ns)
            self._queue.task_done()

    def _discard_pending(self) -> None:
        """Release queue waiters after an unrecoverable sink failure."""

        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            self._queue.task_done()

    def _raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def _settle_failure_future(self) -> None:
        future = self._failure_future
        if future is None:
            return
        if future.done() and not future.cancelled():
            future.exception()
        elif not future.done():
            future.cancel()

    def write(self, record: RecordT) -> None:
        if not self._accepting or self._closed:
            raise CaptureWriterStateError("capture writer is not accepting records")
        queued = _QueuedRecord(record, time.monotonic_ns())
        queue_error: queue.Full | None = None
        failure_to_signal: CaptureWriterQueueFull | None = None
        with self._lock:
            if self._failure is not None:
                raise self._failure
            try:
                self._queue.put_nowait(queued)
            except queue.Full as exc:
                queue_error = exc
                self._overflow_count += 1
                failure_to_signal = CaptureWriterQueueFull(
                    f"capture writer queue reached its hard limit of {self._capacity} records"
                )
                self._failure = failure_to_signal
            else:
                self._enqueued_count += 1
                outstanding = self._enqueued_count - self._written_count
                self._queue_high_water = max(self._queue_high_water, 1, self._queue.qsize())
                self._outstanding_high_water = max(self._outstanding_high_water, outstanding)
        if failure_to_signal is not None:
            self._signal_failure(failure_to_signal)
            raise failure_to_signal from queue_error

    async def wait_for_failure_or_stop(self, stop_event: asyncio.Event) -> None:
        future = self._failure_future
        if future is None:
            raise CaptureWriterStateError("capture writer has not started")

        async def wait_for_stop() -> None:
            await stop_event.wait()

        stop_task = asyncio.create_task(wait_for_stop())
        try:
            waiters: set[asyncio.Future[None]] = {future, stop_task}
            done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if future in done:
                await future
        finally:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)

    async def drain(self) -> None:
        self._raise_if_failed()
        await asyncio.to_thread(self._queue.join)
        self._raise_if_failed()

    async def close(self) -> None:
        if self._closed:
            return
        if self._thread is None:
            raise CaptureWriterStateError("capture writer has not started")
        self._accepting = False
        while self._thread.is_alive():
            self._raise_if_failed()
            try:
                self._queue.put_nowait(_STOP)
                break
            except queue.Full:
                await asyncio.sleep(0)
        await asyncio.to_thread(self._thread.join)
        self._closed = True
        try:
            self._raise_if_failed()
        finally:
            self._settle_failure_future()

    async def abort(self) -> None:
        if self._closed:
            return
        self._accepting = False
        self._aborted = True
        self._abort.set()
        thread = self._thread
        if thread is not None:
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
            await asyncio.to_thread(thread.join)
        self._closed = True
        self._settle_failure_future()

    @property
    def stats(self) -> dict[str, object]:
        with self._lock:
            failure = self._failure
            enqueued_count = self._enqueued_count
            written_count = self._written_count
            return {
                "queue_capacity": self._capacity,
                "records_enqueued": enqueued_count,
                "records_written": written_count,
                "records_pending": max(0, enqueued_count - written_count),
                "queue_high_water": self._queue_high_water,
                "outstanding_high_water": self._outstanding_high_water,
                "overflow_count": self._overflow_count,
                "max_writer_lag_ms": self._max_writer_lag_ns / 1_000_000,
                "failure_type": type(failure).__name__ if failure is not None else None,
                "aborted": self._aborted,
                "complete": self._closed and not self._aborted and failure is None and written_count == enqueued_count,
            }

    async def __aenter__(self) -> "BoundedCaptureWriter[RecordT]":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            try:
                await self.close()
            except BaseException:
                await self.abort()
                raise
        else:
            await self.abort()
