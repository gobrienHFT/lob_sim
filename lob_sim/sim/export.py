"""Transactional, bounded simulation artifact export.

The manifest is the commit record for a run bundle.  Until it and every
declared artifact exist, ``_INCOMPLETE.json`` remains in the unique run
directory.  Audit rows are streamed to same-directory ``.partial`` files and
are durably closed before they are promoted to their final names.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import Config
from ..replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter
from ..util import write_summary_csv
from .run_manifest import RunManifest, build_run_manifest
from .sinks import StreamingCsvSink


EVENT_TRACE_FIELDS = (
    "ts_local",
    "seq",
    "symbol",
    "event_type",
    "source",
    "side",
    "quote_slot",
    "price_tick",
    "qty_lots",
    "order_id",
    "fill_source",
    "details",
)

TRADE_AUDIT_FIELDS = (
    "provenance_schema_version",
    "ts_local",
    "symbol",
    "side",
    "price",
    "qty",
    "notional",
    "contract_multiplier",
    "maker",
    "fill_source",
    "fee_bps",
    "fee",
    "fee_currency",
    "order_id",
    "created_ts",
    "mid_at_fill",
    "spread_capture",
    "spread_capture_value",
    "regime",
    "queue_ahead_lots",
    "time_in_book_ms",
    "markout_horizon",
    "book_bid_tick",
    "book_ask_tick",
    "scenario_id",
    "evidence_ids",
    "validity",
    "queue_trajectory",
    "latency_draws_ms",
    "latency_model",
    "order_state_at_fill",
    "fee_model_id",
)

MARKOUT_AUDIT_FIELDS = (
    "symbol",
    "side",
    "fill_source",
    "regime",
    "fill_price",
    "price_tick",
    "qty",
    "qty_lots",
    "order_id",
    "fill_mid",
    "mid_after",
    "markout",
    "contract_multiplier",
    "adverse",
    "horizon",
    "ts_local",
    "deadline_ts",
    "markout_ts_local",
    "resolution_lag_seconds",
    "status",
    "invalid_reason",
)

FILL_JSON_FIELDS = frozenset({"evidence_ids", "validity", "queue_trajectory", "latency_draws_ms", "latency_model"})
FILL_FLOAT_FIELDS = frozenset({"ts_local", "created_ts", "time_in_book_ms", "markout_horizon"})
FILL_INT_FIELDS = frozenset({"queue_ahead_lots", "book_bid_tick", "book_ask_tick"})
FILL_OPTIONAL_FIELDS = frozenset(
    {"created_ts", "mid_at_fill", "spread_capture", "spread_capture_value", "book_bid_tick", "book_ask_tick"}
)
MARKOUT_FLOAT_FIELDS = frozenset({"horizon", "ts_local", "deadline_ts", "markout_ts_local", "resolution_lag_seconds"})
MARKOUT_INT_FIELDS = frozenset({"price_tick", "qty_lots"})
MARKOUT_OPTIONAL_FIELDS = frozenset(
    {
        "price_tick",
        "qty_lots",
        "order_id",
        "fill_mid",
        "mid_after",
        "markout",
        "adverse",
        "resolution_lag_seconds",
        "invalid_reason",
    }
)


def _partial_path(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON through an fsynced partial and publish it atomically."""

    partial = _partial_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(path)


def atomic_write_summary_csv(path: Path, summary: Mapping[str, Any]) -> None:
    """Write the one-row summary CSV through an fsynced partial."""

    partial = _partial_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_summary_csv(partial, dict(summary), exclude_keys={"fills", "markout_events"})
    with partial.open("r+b") as handle:
        os.fsync(handle.fileno())
    partial.replace(path)


def _parse_bool(path: Path, field: str, value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"{path} has invalid boolean {field}={value!r}")


def _iter_csv(path: Path, fields: tuple[str, ...]) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError(f"{path} has unexpected CSV schema {reader.fieldnames!r}")
        yield from reader


def iter_fill_audit_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Reconstruct canonical fill objects from a streamed fixed-schema CSV."""

    for row in _iter_csv(path, TRADE_AUDIT_FIELDS):
        event: dict[str, Any] = {}
        for field in TRADE_AUDIT_FIELDS:
            value = row[field]
            if value == "" and field in FILL_OPTIONAL_FIELDS:
                event[field] = None
            elif field in FILL_JSON_FIELDS:
                event[field] = json.loads(value)
            elif field in FILL_FLOAT_FIELDS:
                event[field] = float(value)
            elif field in FILL_INT_FIELDS:
                event[field] = int(value)
            elif field == "maker":
                event[field] = _parse_bool(path, field, value)
            else:
                event[field] = value
        yield event


def iter_markout_audit_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Reconstruct canonical markout objects from a streamed fixed-schema CSV."""

    for row in _iter_csv(path, MARKOUT_AUDIT_FIELDS):
        event: dict[str, Any] = {}
        for field in MARKOUT_AUDIT_FIELDS:
            value = row[field]
            if field == "invalid_reason" and value == "":
                continue
            if value == "" and field in MARKOUT_OPTIONAL_FIELDS:
                event[field] = None
            elif field in MARKOUT_FLOAT_FIELDS:
                event[field] = float(value)
            elif field in MARKOUT_INT_FIELDS:
                event[field] = int(value)
            elif field == "adverse":
                event[field] = _parse_bool(path, field, value)
            else:
                event[field] = value
        yield event


def _audit_chain(domain: str, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    from .metrics import advance_audit_digest

    count = 0
    digest = hashlib.sha256(domain.encode("utf-8")).digest()
    for row in rows:
        digest = advance_audit_digest(digest, row)
        count += 1
    return count, digest.hex()


def verify_streaming_audit_files(
    output_files: Mapping[str, Path],
    *,
    event_trace_count: int,
    fill_count: int,
    fill_sha256: str,
    markout_count: int,
    markout_sha256: str,
) -> None:
    """Fail closed unless serialized audits reproduce in-kernel counts/hashes."""

    from .metrics import FILL_AUDIT_CHAIN_DOMAIN, MARKOUT_AUDIT_CHAIN_DOMAIN

    trace_rows = sum(1 for _ in _iter_csv(output_files["event_trace"], EVENT_TRACE_FIELDS))
    observed_fill_count, observed_fill_sha = _audit_chain(
        FILL_AUDIT_CHAIN_DOMAIN,
        iter_fill_audit_rows(output_files["trades"]),
    )
    observed_markout_count, observed_markout_sha = _audit_chain(
        MARKOUT_AUDIT_CHAIN_DOMAIN,
        iter_markout_audit_rows(output_files["markouts"]),
    )
    observed = {
        "event_trace_count": trace_rows,
        "fill_count": observed_fill_count,
        "fill_sha256": observed_fill_sha,
        "markout_count": observed_markout_count,
        "markout_sha256": observed_markout_sha,
    }
    expected = {
        "event_trace_count": event_trace_count,
        "fill_count": fill_count,
        "fill_sha256": fill_sha256,
        "markout_count": markout_count,
        "markout_sha256": markout_sha256,
    }
    if observed != expected:
        raise RuntimeError(f"serialized simulation audit mismatch: observed={observed}, expected={expected}")


def _run_directory_name(input_path: str | Path, manifest_seed: RunManifest) -> str:
    stem = Path(input_path).stem.replace(".ndjson", "")
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "simulation"
    timestamp = re.sub(r"[^0-9A-Za-z]+", "", manifest_seed.created_at_utc)
    return f"run_{safe_stem}_{manifest_seed.run_id}_{timestamp}"


def streaming_output_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "event_trace": run_dir / "event_trace.csv",
        "markouts": run_dir / "markouts.csv",
        "summary": run_dir / "summary.json",
        "summary_csv": run_dir / "summary.csv",
        "trades": run_dir / "trades.csv",
        "manifest": run_dir / "manifest.json",
    }


@dataclass
class StreamingSimulationExport:
    """Own the three bounded audit sinks and the run completion sentinel."""

    input_path: Path
    output_files: dict[str, Path]
    manifest_seed: RunManifest
    event_sink: StreamingCsvSink
    fill_sink: StreamingCsvSink
    markout_sink: StreamingCsvSink
    incomplete_path: Path
    _audit_finalized: bool = False

    @classmethod
    def create(
        cls,
        input_path: str | Path,
        cfg: Config,
        *,
        adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
    ) -> "StreamingSimulationExport":
        source_path = Path(input_path)
        seed = build_run_manifest(source_path, cfg, {}, adapter=adapter)
        base_name = _run_directory_name(source_path, seed)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(1000):
            suffix = "" if attempt == 0 else f"_{attempt:03d}"
            run_dir = cfg.output_dir / f"{base_name}{suffix}"
            try:
                run_dir.mkdir(exist_ok=False)
            except FileExistsError:
                continue
            break
        else:
            raise FileExistsError(f"could not allocate a unique simulation run directory for {base_name}")
        output_files = streaming_output_paths(run_dir)
        incomplete_path = run_dir / "_INCOMPLETE.json"
        atomic_write_json(
            incomplete_path,
            {
                "schema_version": "lob_sim.incomplete_simulation_run.v1",
                "run_id": seed.run_id,
                "created_at_utc": seed.created_at_utc,
                "input": str(source_path),
                "reason": "run artifacts have not been fully finalized",
            },
        )

        sinks: list[StreamingCsvSink] = []
        try:
            event_sink = StreamingCsvSink(output_files["event_trace"], EVENT_TRACE_FIELDS)
            sinks.append(event_sink)
            fill_sink = StreamingCsvSink(output_files["trades"], TRADE_AUDIT_FIELDS)
            sinks.append(fill_sink)
            markout_sink = StreamingCsvSink(output_files["markouts"], MARKOUT_AUDIT_FIELDS)
            sinks.append(markout_sink)
        except Exception:
            for sink in sinks:
                sink.abort()
            raise

        return cls(
            input_path=source_path,
            output_files=output_files,
            manifest_seed=seed,
            event_sink=event_sink,
            fill_sink=fill_sink,
            markout_sink=markout_sink,
            incomplete_path=incomplete_path,
        )

    @property
    def run_dir(self) -> Path:
        return self.incomplete_path.parent

    @property
    def audit_sinks(self) -> tuple[StreamingCsvSink, StreamingCsvSink, StreamingCsvSink]:
        return (self.event_sink, self.fill_sink, self.markout_sink)

    def __enter__(self) -> "StreamingSimulationExport":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            for sink in self.audit_sinks:
                sink.abort()
            return

        try:
            for sink in self.audit_sinks:
                sink.prepare()
            for sink in self.audit_sinks:
                sink.commit()
        except Exception:
            for sink in self.audit_sinks:
                sink.abort()
            raise
        self._audit_finalized = True

    def assert_row_counts(self, *, event_trace: int, fills: int, markouts: int) -> None:
        expected = {
            "event_trace": event_trace,
            "fills": fills,
            "markouts": markouts,
        }
        observed = {
            "event_trace": self.event_sink.count,
            "fills": self.fill_sink.count,
            "markouts": self.markout_sink.count,
        }
        if observed != expected:
            raise RuntimeError(f"streaming audit row-count mismatch: observed={observed}, expected={expected}")

    def mark_complete(self) -> None:
        """Remove the sentinel only after every declared artifact is final."""

        if not self._audit_finalized:
            raise RuntimeError("cannot complete a run before audit sinks are finalized")
        missing = [name for name, path in self.output_files.items() if not path.is_file()]
        partials = [str(_partial_path(path)) for path in self.output_files.values() if _partial_path(path).exists()]
        if missing or partials:
            raise RuntimeError(f"cannot complete run: missing={missing}, partials={partials}")
        self.incomplete_path.unlink()
