"""Bounded native-book observer; integrates only already-known causal state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lob_sim.regime.dataset import _decimal_grid_text
from lob_sim.regime.validation import identity
from lob_sim.sim.observation import MarketObservation
from lob_sim.sim.run_manifest import instrument_specs_snapshot
from lob_sim.research.capture_clock import CaptureClock

DEPTH_FRESH_NS = 5_000_000_000
TRADE_FRESH_NS = 60_000_000_000


@dataclass
class _Symbol:
    observation: MarketObservation | None = None
    last_depth_ns: int | None = None
    last_trade_ns: int | None = None
    instrument_sha256: str | None = None


class CaptureIntervalObserver:
    """One symbol state and one pending interval, not retained event history.

    Book/epoch/normalization validity comes from SimulationEngine. This layer
    only applies the independent, versioned freshness and clock admission rule.
    It never sends orders, mutates books or interpolates a future observation.
    """

    depth_levels = 1

    def __init__(self, receipts: Mapping[str, Any], symbols: tuple[str, ...], emit: Callable[[dict[str, Any]], None]):
        self.clock = CaptureClock.from_dict(receipts["clock"])
        self.clock_sha256 = self.clock.digest
        self.capture_id, self.input_sha256 = receipts["capture_id"], receipts["input_sha256"]
        self.stop_ns, self.clock_invalid_from_ns = receipts["stop_ns"], receipts["clock_invalid_from_ns"]
        self.current_ns = self.clock.origin_logical_ns
        self.states = {symbol: _Symbol() for symbol in symbols}
        self.emit = emit
        self.pending: dict[str, Any] | None = None
        self.rows = self.duration_ns = self.joint_valid_ns = self.joint_mark_ns = 0
        self.totals = {
            s: {"depth_valid_ns": 0, "trade_valid_ns": 0, "mark_available_ns": 0, "joint_valid_ns": 0} for s in symbols
        }
        self.instruments: dict[str, dict[str, Any]] = {}
        self.instrument_changes = 0
        self.epoch_changes = {s: 0 for s in symbols}

    def _status(self, symbol: str, now: int) -> dict[str, Any]:
        state = self.states[symbol]
        obs = state.observation
        reasons = []
        clock_valid = obs is not None and obs.receive_clock and obs.validity.clock_valid
        if self.clock_invalid_from_ns is not None and now >= self.clock_invalid_from_ns:
            clock_valid = False
            reasons.append("wall_or_monotonic_clock_inconsistent")
        capture_valid = obs is not None and obs.validity.capture_valid
        stopped = self.stop_ns is not None and now >= self.stop_ns
        fresh_depth = state.last_depth_ns is not None and now < state.last_depth_ns + DEPTH_FRESH_NS
        fresh_trade = state.last_trade_ns is not None and now < state.last_trade_ns + TRADE_FRESH_NS
        book = obs is not None and obs.validity.book_valid
        trade_stream = obs is not None and obs.validity.trade_stream_valid
        depth_valid = book and fresh_depth and not stopped
        trade_valid = trade_stream and fresh_trade and not stopped
        mark = bool(depth_valid and obs is not None and obs.bids and obs.asks and 0 < obs.bids[0][0] < obs.asks[0][0])
        for valid, reason in (
            (obs is not None, "instrument_unobserved"),
            (book, "book_or_depth_stream_invalid"),
            (fresh_depth, "depth_stale_or_unobserved"),
            (trade_stream, "trade_stream_invalid"),
            (fresh_trade, "trade_stale_or_unobserved"),
            (clock_valid, "clock_invalid"),
            (capture_valid, "capture_invalid"),
            (not stopped, "capture_stopped"),
            (mark, "mark_unavailable"),
        ):
            if not valid:
                reasons.append(reason)
        if obs is not None and obs.validity.reason is not None:
            reasons.append("native:" + obs.validity.reason[:500])
        return {
            "instrument_sha256": state.instrument_sha256,
            "epochs": list(obs.epochs) if obs else None,
            "depth_valid": depth_valid,
            "trade_valid": trade_valid,
            "mark_available": mark,
            "clock_valid": clock_valid,
            "capture_valid": capture_valid,
            "joint_valid": depth_valid and trade_valid and mark and clock_valid and capture_valid,
            "reasons": reasons,
        }

    def _flush(self) -> None:
        if self.pending is None:
            return
        row = self.pending
        self.emit(row)
        dt = row["end_ns"] - row["start_ns"]
        self.rows += 1
        self.duration_ns += dt
        self.joint_valid_ns += dt if row["joint_valid"] else 0
        self.joint_mark_ns += dt if row["joint_mark_available"] else 0
        for symbol, state in row["symbols"].items():
            for key, flag in (
                ("depth_valid_ns", "depth_valid"),
                ("trade_valid_ns", "trade_valid"),
                ("mark_available_ns", "mark_available"),
                ("joint_valid_ns", "joint_valid"),
            ):
                self.totals[symbol][key] += dt if state[flag] else 0
        self.pending = None

    def _queue(self, start: int, end: int) -> None:
        states = {symbol: self._status(symbol, start) for symbol in sorted(self.states)}
        row = {
            "schema_version": "lob_sim.capture_interval.v1",
            "input_sha256": self.input_sha256,
            "capture_id": self.capture_id,
            "clock_sha256": self.clock_sha256,
            "logical_start_ns": start,
            "logical_end_ns": end,
            "start_ns": self.clock.project(start),
            "end_ns": self.clock.project(end),
            "symbols": states,
            "joint_valid": all(s["joint_valid"] for s in states.values()),
            "joint_mark_available": all(
                s["mark_available"] and s["clock_valid"] and s["capture_valid"] for s in states.values()
            ),
        }
        previous = self.pending
        if (
            previous is not None
            and previous["logical_end_ns"] == start
            and all(previous[k] == row[k] for k in ("symbols", "joint_valid", "joint_mark_available"))
        ):
            previous["logical_end_ns"], previous["end_ns"] = end, row["end_ns"]
        else:
            self._flush()
            self.pending = row

    def before_record(self, logical_ns: int) -> None:
        if logical_ns < self.current_ns:
            raise ValueError("native observer watermark regressed")
        cutoffs = {self.current_ns, logical_ns}
        for state in self.states.values():
            for value, ttl in ((state.last_depth_ns, DEPTH_FRESH_NS), (state.last_trade_ns, TRADE_FRESH_NS)):
                if value is not None and self.current_ns < value + ttl < logical_ns:
                    cutoffs.add(value + ttl)
        for value in (self.stop_ns, self.clock_invalid_from_ns):
            if value is not None and self.current_ns < value < logical_ns:
                cutoffs.add(value)
        edges = sorted(cutoffs)
        for start, end in zip(edges, edges[1:]):
            if start < end:
                self._queue(start, end)
        self.current_ns = logical_ns

    def observe(self, observation: MarketObservation) -> None:
        state = self.states.get(observation.symbol)
        if state is None:
            return
        if observation.logical_ns != self.current_ns:
            raise ValueError("native observation differs from the global watermark")
        previous = state.observation
        if previous is not None:
            if previous.epochs != observation.epochs:
                self.epoch_changes[observation.symbol] += 1
            if previous.epochs[:2] != observation.epochs[:2]:
                state.last_depth_ns = None
            if previous.epochs[2] != observation.epochs[2]:
                state.last_trade_ns = None
        if previous is None or previous.spec != observation.spec:
            spec = instrument_specs_snapshot({observation.symbol: observation.spec})[observation.symbol]
            for field in ("tick_size", "step_size", "contract_multiplier"):
                spec[field] = _decimal_grid_text(getattr(observation.spec, field))
            instrument = identity(spec)
            known = self.instruments.get(observation.symbol)
            if known is None:
                self.instruments[observation.symbol] = {"sha256": instrument, "spec": spec}
            elif known["sha256"] != instrument:
                self.instrument_changes += 1
                state.last_depth_ns = state.last_trade_ns = None
            state.instrument_sha256 = instrument
        if observation.depth_observed:
            state.last_depth_ns = observation.logical_ns
        if observation.trade is not None:
            state.last_trade_ns = observation.logical_ns
        state.observation = observation

    def finish(self, logical_ns: int) -> None:
        self.before_record(logical_ns)
        self._flush()
