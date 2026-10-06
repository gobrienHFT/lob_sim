"""Labeled synthetic market-by-price tapes, not a Binance participant model.

The hidden process changes observable spread, depth, price increments and print
activity. Labels live ONLY in a separate truth file. Ordinary replay sees the
same instrument, snapshot, depth, trade and control contracts as other tapes.
"""

from __future__ import annotations

import os
import random
from contextlib import ExitStack
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..replay.inspection import file_sha256
from .dataset import DAY_NS, publish_json
from .validation import canonical_json, identity, integer

SECOND = 1_000_000_000
TRANSITION = ((0.96, 0.04), (0.08, 0.92))


@dataclass(frozen=True)
class SyntheticTapeConfig:
    seed: int = 7
    days: int = 5
    seconds_per_day: int = 480
    start_day: str = "2025-01-01"

    def __post_init__(self) -> None:
        integer(self.seed, "seed")
        integer(self.days, "days", minimum=3)
        integer(self.seconds_per_day, "seconds_per_day", minimum=60)
        if self.days > 30 or self.seconds_per_day > 3600:
            raise ValueError("synthetic diagnostic is capped at 30 one-hour snippets")
        if date.fromisoformat(self.start_day).isoformat() != self.start_day:
            raise ValueError("start_day must be a canonical UTC date")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(units: int, scale: int) -> str:
    return f"{units // scale}.{units % scale:0{len(str(scale)) - 1}d}"


def synthetic_rows(config: SyntheticTapeConfig) -> Iterator[tuple[str, dict[str, Any]]]:
    """Stream raw/truth rows. Compressed monotonic gaps are explicitly invalid.

    Each snippet is a separate public/trade epoch and UTC day. Its first state
    is day%2; subsequent transitions use TRANSITION once per second. Independent
    deterministic latent/emission RNGs prevent event rate from changing the
    hidden transition clock. No label or future outcome enters the raw tape.
    """
    latent = random.Random(int(identity({"seed": config.seed, "stream": "latent"})[:16], 16))
    market = random.Random(int(identity({"seed": config.seed, "stream": "market"})[:16], 16))
    wall_start = (
        int(datetime.combine(date.fromisoformat(config.start_day), datetime.min.time(), timezone.utc).timestamp())
        * SECOND
    )
    receive_seq = 0
    update_id = 100
    trade_id = 0

    def raw(
        ns: int, day: int, kind: str, data: dict[str, Any], route: str, symbol: str = "BTCUSDT"
    ) -> tuple[str, dict[str, Any]]:
        nonlocal receive_seq
        base = day * (config.seconds_per_day + 2) * SECOND
        wall = wall_start + day * DAY_NS + ns - base
        payload = dict(data)
        payload["_capture"] = {
            "recvSeq": receive_seq,
            "recvMonotonicNs": ns,
            "recvWallNs": wall,
            "route": route,
            "streamEpoch": day,
            "syncEpoch": day,
        }
        receive_seq += 1
        return "raw", {"ts_local": wall / SECOND, "symbol": symbol, "type": kind, "data": payload}

    yield raw(0, 0, "captureMeta", {"schemaVersion": 3, "clock": "receive_time", "synthetic": True}, "control", "*")
    yield raw(10_000_000, 0, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001"}, "control")
    for day in range(config.days):
        base = day * (config.seconds_per_day + 2) * SECOND
        for route, offset in (("public", 100_000_000), ("market", 200_000_000)):
            yield raw(base + offset, day, "captureEvent", {"event": "connect", "route": route}, route)
        midpoint = 100_000
        previous_bids, previous_asks = {midpoint - 1: 400}, {midpoint + 1: 400}
        yield raw(
            base + 300_000_000,
            day,
            "snapshot",
            {
                "lastUpdateId": update_id,
                "bids": [[_decimal(p, 10), _decimal(q, 1000)] for p, q in previous_bids.items()],
                "asks": [[_decimal(p, 10), _decimal(q, 1000)] for p, q in previous_asks.items()],
            },
            "public",
        )
        state = day % 2
        for step in range(1, config.seconds_per_day + 1):
            if step > 1 and latent.random() >= TRANSITION[state][state]:
                state = 1 - state
            sample_ns = base + step * SECOND
            yield (
                "truth",
                {
                    "schema_version": "lob_sim.synthetic_regime_truth.v1",
                    "symbol": "BTCUSDT",
                    "utc_day": (date.fromisoformat(config.start_day) + timedelta(days=day)).isoformat(),
                    "sample_ns": sample_ns,
                    "wall_ns": wall_start + day * DAY_NS + step * SECOND,
                    "state": state,
                    "sequence": day,
                },
            )
            midpoint += market.choice((-1, 0, 1)) * (1 if state == 0 else 8)
            half_spread = market.randint(1, 2) if state == 0 else market.randint(6, 9)
            depth = market.randint(320, 480) if state == 0 else market.randint(30, 70)
            pressure = market.randint(-30, 30) if state == 0 else market.randint(-20, 20)
            for event in range(2 if state == 0 else 4):
                ns = sample_ns - 500_000_000 + event * 90_000_000
                bids = {
                    midpoint - half_spread - 2 * level: max(1, depth + pressure + market.randint(-8, 8))
                    for level in range(5)
                }
                asks = {
                    midpoint + half_spread + 2 * level: max(1, depth - pressure + market.randint(-8, 8))
                    for level in range(5)
                }
                before = update_id
                update_id += 1
                yield raw(
                    ns,
                    day,
                    "depthUpdate",
                    {
                        "U": before if step == 1 and event == 0 else update_id,
                        "u": update_id,
                        "pu": before,
                        "b": [
                            [_decimal(p, 10), _decimal(bids.get(p, 0), 1000)]
                            for p in sorted(previous_bids.keys() | bids.keys(), reverse=True)
                        ],
                        "a": [
                            [_decimal(p, 10), _decimal(asks.get(p, 0), 1000)]
                            for p in sorted(previous_asks.keys() | asks.keys())
                        ],
                    },
                    "public",
                )
                previous_bids, previous_asks = bids, asks
                if event == 0 or state == 1:
                    trade_id += 1
                    maker = market.random() < (0.5 if state == 0 else 0.2)
                    yield raw(
                        ns + 30_000_000,
                        day,
                        "aggTrade",
                        {
                            "a": trade_id,
                            "p": _decimal(max(bids) if maker else min(asks), 10),
                            "q": _decimal(market.randint(2, 8) if state == 0 else market.randint(5, 15), 1000),
                            "m": maker,
                        },
                        "market",
                    )
        for route, offset in (("public", 100_000_000), ("market", 200_000_000)):
            yield raw(
                base + config.seconds_per_day * SECOND + offset,
                day,
                "captureEvent",
                {"event": "disconnect", "route": route},
                route,
            )
    end = (config.days - 1) * (config.seconds_per_day + 2) * SECOND + config.seconds_per_day * SECOND + 300_000_000
    yield raw(end, config.days - 1, "captureEvent", {"event": "capture_trailer", "route": "control"}, "control", "*")


def generate_synthetic_tape(
    directory: str | Path, config: SyntheticTapeConfig = SyntheticTapeConfig()
) -> dict[str, Any]:
    """Exclusive publication; incomplete raw/truth files are never replaced."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    counts = {"raw": 0, "truth": 0}
    raw_path = root / "market.ndjson"
    raw_partial = raw_path.with_name(raw_path.name + ".partial")
    truth_directory = root / "truth"
    truth_directory.mkdir()
    truth_paths: dict[str, Path] = {}
    day_counts: dict[str, int] = {}
    with ExitStack() as stack:
        raw_handle = stack.enter_context(raw_partial.open("xb"))
        truth_handles: dict[str, Any] = {}
        for kind, row in synthetic_rows(config):
            handle = raw_handle
            if kind == "truth":
                day = row["utc_day"]
                if day not in truth_handles:
                    truth_paths[day] = truth_directory / (day + ".jsonl")
                    truth_handles[day] = stack.enter_context(truth_paths[day].with_suffix(".jsonl.partial").open("xb"))
                    day_counts[day] = 0
                handle = truth_handles[day]
                day_counts[day] += 1
            handle.write((canonical_json(row) + "\n").encode("utf-8"))
            counts[kind] += 1
        for handle in (raw_handle, *truth_handles.values()):
            handle.flush()
            os.fsync(handle.fileno())
    os.link(raw_partial, raw_path)
    raw_partial.unlink()
    for path in truth_paths.values():
        partial = path.with_suffix(".jsonl.partial")
        os.link(partial, path)
        partial.unlink()
    report: dict[str, Any] = {
        "schema_version": "lob_sim.synthetic_regime_tape.v1",
        "config": config.as_dict(),
        "raw": {"path": raw_path.name, "sha256": file_sha256(raw_path), "records": counts["raw"]},
        "truth": {
            "records": counts["truth"],
            "day_files": [
                {"utc_day": day, "path": "truth/" + path.name, "sha256": file_sha256(path), "records": day_counts[day]}
                for day, path in sorted(truth_paths.items())
            ],
        },
        "latent_transition": [list(row) for row in TRANSITION],
        "latent_interval_ns": SECOND,
        "labels": ["synthetic_narrow_deep", "synthetic_wide_thin"],
        "claim_ready": False,
        "limitations": "UTC snippets, not complete days; MBP state-transition fixture, not synthetic MBO matching, Binance calibration, private FIFO, alpha or economics",
    }
    report["manifest_sha256"] = identity(report)
    publish_json(root / "manifest.json", report)
    return report


def generate_synthetic_sources(
    directory: str | Path, config: SyntheticTapeConfig = SyntheticTapeConfig(seconds_per_day=180)
) -> dict[str, Any]:
    """Publish independent diagnostic snippets, never transform real captures.

    Market payloads and UTC receipt times come unchanged from synthetic_rows.
    Only synthetic receipt identity is made local to each source. Hidden truth
    is discarded; the existing combined raw/truth generator stays unchanged.
    Writer state is bounded by the configured day cap, not the event count.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    incomplete = root / "_INCOMPLETE.json"
    publish_json(
        incomplete, {"schema_version": "lob_sim.synthetic_regime_sources.pending.v1", "config": config.as_dict()}
    )
    headers: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    counts: dict[str, int] = {}
    completed = False
    with ExitStack() as stack:
        handles: dict[str, Any] = {}

        def write(day: str, value: dict[str, Any], *, header: bool = False) -> None:
            # Copy just the objects being changed; nested market arrays are read
            # only by canonical_json and are neither mutated nor retained.
            index = (date.fromisoformat(day) - date.fromisoformat(config.start_day)).days
            item = dict(value)
            payload = dict(item["data"])
            capture = dict(payload["_capture"])
            if header:
                capture["recvWallNs"] += index * DAY_NS
                capture["streamEpoch"] = capture["syncEpoch"] = index
            else:
                capture["recvMonotonicNs"] -= index * (config.seconds_per_day + 2) * SECOND
            capture["recvSeq"] = counts[day]
            payload["_capture"] = capture
            item["data"] = payload
            item["ts_local"] = capture["recvWallNs"] / SECOND
            handles[day].write((canonical_json(item) + "\n").encode("utf-8"))
            counts[day] += 1

        for kind, row in synthetic_rows(config):
            if kind != "raw":
                continue
            if len(headers) < 2:
                headers.append(row)
                continue
            completed = row["type"] == "captureEvent" and row["data"].get("event") == "capture_trailer"
            wall_ns = row["data"]["_capture"]["recvWallNs"]
            day = datetime.fromtimestamp(wall_ns // SECOND, timezone.utc).date().isoformat()
            if day not in handles:
                paths[day] = root / (day + ".ndjson")
                partial = paths[day].with_suffix(".ndjson.partial")
                handles[day] = stack.enter_context(partial.open("xb"))
                counts[day] = 0
                for value in headers:
                    write(day, value, header=True)
            write(day, row)
        if not completed or len(paths) != config.days:
            raise ValueError("synthetic generation ended before declared source/trailer completion")
        for handle in handles.values():
            handle.flush()
            os.fsync(handle.fileno())
    for path in paths.values():
        partial = path.with_suffix(".ndjson.partial")
        os.link(partial, path)
        partial.unlink()
    report: dict[str, Any] = {
        "schema_version": "lob_sim.synthetic_regime_sources.v1",
        "synthetic": True,
        "config": config.as_dict(),
        "sources": [
            {"utc_day": day, "path": path.name, "sha256": file_sha256(path), "records": counts[day]}
            for day, path in sorted(paths.items())
        ],
        "receipt_transform": "synthetic only: replicate capture/instrument headers; local monotonic origin and sequence; preserve market payloads, UTC times, ordering and stream/sync epochs",
        "storage": "NDJSON importer with schema-v3 receipt metadata; not finalized segmented exchange captures",
        "claim_ready": False,
        "limitations": "short synthetic UTC snippets, not complete valid days, Binance calibration, private FIFO, real fills or policy benefit; hidden truth never enters inputs",
    }
    report["manifest_sha256"] = identity(report)
    publish_json(root / "manifest.json", report)
    incomplete.unlink()
    return report
