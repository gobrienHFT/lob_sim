"""Bounded, sampled host/writer telemetry, with fail-closed resource guards."""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lob_sim.record.async_writer import BoundedCaptureWriter
from lob_sim.regime.validation import canonical_json, integer
from lob_sim.replay.inspection import file_sha256


class CaptureResourceError(RuntimeError):
    """Resource admission failed; retained partials are not successful evidence."""


@dataclass(frozen=True)
class SoakSettings:
    duration_seconds: int = 90_000
    sample_seconds: int = 5
    minimum_disk_free_bytes: int = 2 * 1024**3
    maximum_rss_bytes: int = 1024**3

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            integer(value, name, minimum=1)
        if self.duration_seconds > 172_800 or self.sample_seconds > 60:
            raise ValueError("soak is capped at 48h per run and 60s between resource samples")


def process_rss_bytes() -> tuple[int | None, str]:
    """Current RSS, not Python-only allocations or an unmeasured peak."""
    try:
        if os.name == "nt":
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak_ws",
                        "working_set",
                        "peak_paged",
                        "paged",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)  # type: ignore[attr-defined]
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            data = Counters()
            data.cb = ctypes.sizeof(data)
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(data), data.cb):
                raise OSError("GetProcessMemoryInfo failed")
            return int(data.working_set), "windows_working_set"
        if Path("/proc/self/status").exists():
            with Path("/proc/self/status").open("r", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        fields = line.split()
                        if len(fields) == 3 and fields[2] == "kB":
                            return int(fields[1]) * 1024, "linux_proc_vmrss"
        return None, "unsupported_host"
    except (OSError, AttributeError, ValueError):
        return None, "measurement_unavailable"


def resource_sample(root: Path, settings: SoakSettings) -> dict[str, Any]:
    free = shutil.disk_usage(root).free
    rss, basis = process_rss_bytes()
    if free < settings.minimum_disk_free_bytes:
        raise CaptureResourceError("disk guard reached; do not erase or append prior evidence")
    if rss is not None and rss > settings.maximum_rss_bytes:
        raise CaptureResourceError("RSS guard reached")
    return {"disk_free_bytes": free, "rss_bytes": rss, "rss_basis": basis}


class TelemetryFile:
    """One bounded row is written on the capture worker, never on the loop."""

    def __init__(self, root: Path):
        self.partial = root / "telemetry.jsonl.partial"
        self.final = root / "telemetry.jsonl"
        self.handle = self.partial.open("xb")

    def write(self, row: dict[str, Any]) -> None:
        raw = (canonical_json(row) + "\n").encode("utf-8")
        if len(raw) > 16 * 1024:
            raise ValueError("host telemetry row exceeds bounded size")
        self.handle.write(raw)

    def finalize(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        os.link(self.partial, self.final)
        self.partial.unlink()

    def abandon(self) -> None:
        if not self.handle.closed:
            self.handle.close()


class SoakMonitor:
    def __init__(
        self,
        root: Path,
        settings: SoakSettings,
        telemetry: BoundedCaptureWriter[dict[str, Any]],
        writer_stats: Callable[[], Mapping[str, object]],
        clock_anchor: Callable[[], None],
    ):
        self.root, self.settings, self.writer = root, settings, telemetry
        self.writer_stats, self.clock_anchor = writer_stats, clock_anchor
        self.samples = self.maximum_loop_lag_ns = 0
        self.maximum_rss_bytes: int | None = None
        self.minimum_disk_free_bytes: int | None = None
        self.rss_unavailable_samples = 0

    def sample(self, due_ns: int) -> None:
        observed = time.monotonic_ns()
        resources = resource_sample(self.root, self.settings)
        lag = max(0, observed - due_ns)
        self.maximum_loop_lag_ns = max(self.maximum_loop_lag_ns, lag)
        self.samples += 1
        rss = resources["rss_bytes"]
        if rss is None:
            self.rss_unavailable_samples += 1
        else:
            self.maximum_rss_bytes = max(self.maximum_rss_bytes or 0, rss)
        free = resources["disk_free_bytes"]
        self.minimum_disk_free_bytes = min(
            self.minimum_disk_free_bytes if self.minimum_disk_free_bytes is not None else free, free
        )
        self.writer.write(
            {
                "schema_version": "lob_sim.capture_host_sample.v1",
                "sample_index": self.samples,
                "recv_monotonic_ns": observed,
                "recv_wall_ns": time.time_ns(),
                "loop_lag_ns": lag,
                "resources": resources,
                "writer": dict(self.writer_stats()),
            }
        )
        self.clock_anchor()

    async def run(self, stop: asyncio.Event) -> None:
        due_ns = time.monotonic_ns()
        while not stop.is_set():
            self.sample(due_ns)
            due_ns += self.settings.sample_seconds * 1_000_000_000
            try:
                await asyncio.wait_for(stop.wait(), max(0, (due_ns - time.monotonic_ns()) / 1e9))
            except asyncio.TimeoutError:
                pass

    def report(self, path: Path, writer: Mapping[str, object]) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.capture_host_telemetry.v1",
            "path": path.name,
            "sha256": file_sha256(path),
            "sample_count": self.samples,
            "sample_seconds": self.settings.sample_seconds,
            "maximum_observed_rss_bytes": self.maximum_rss_bytes,
            "rss_unavailable_samples": self.rss_unavailable_samples,
            "minimum_observed_disk_free_bytes": self.minimum_disk_free_bytes,
            "maximum_observed_loop_lag_ns": self.maximum_loop_lag_ns,
            "writer": dict(writer),
            "limits": "sampled host telemetry;not continuous RSS peak or exchange/gateway latency",
        }
