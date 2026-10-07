"""Opt-in study gates, not changes to the ordinary replay engine.

Derived control observations expire execution/marks without discarding a
still-sequence-valid reconstructed book. Prefix observations warm only the
book; regime features start at the registered day boundary, quotes later.
The study runner records this conditional experiment, never venue behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import Any

from lob_sim.book.types import InstrumentSpec, LevelChange, AggTradeEvent
from lob_sim.book.local_book import LocalOrderBook
from lob_sim.config import Config
from lob_sim.record.envelope import ValidityState
from lob_sim.regime.validation import integer
from lob_sim.replay.reader import RecordedEvent
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.metrics import SimulationMetrics

SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class ResearchConfig(Config):
    """Two books, but exactly one permitted quoting symbol per experiment.

    Ordinary policy configurations intentionally remain single-symbol. This
    opt-in subclass validates that ordinary single-symbol configuration before
    adding the other *observation-only* book. ResearchReplayEngine enforces the
    quoting boundary; ordinary SimulationEngine must not consume this class.
    """

    def __post_init__(self) -> None:
        if self.symbols != SYMBOLS:
            raise ValueError("research configuration needs the exact joint symbol universe")
        values = {field.name: getattr(self, field.name) for field in fields(Config)}
        if self.hmm is not None and self.hmm.mode == "policy":
            values["symbols"] = (self.hmm.symbol,)
        Config(**values)  # All ordinary configuration/risk checks, unchanged.


def research_config(cfg: Config, **changes: Any) -> ResearchConfig:
    values = {field.name: getattr(cfg, field.name) for field in fields(Config)}
    values.update(changes)
    values["symbols"] = SYMBOLS
    return ResearchConfig(**values)


@dataclass(frozen=True)
class ReplayWindow:
    symbol: str
    feature_start_ns: int
    score_start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        integer(self.feature_start_ns, "research feature start")
        integer(self.score_start_ns, "research score start", minimum=self.feature_start_ns)
        integer(self.end_ns, "research end", minimum=self.score_start_ns + 1)
        if self.symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("unsupported study symbol")


class ResearchMetrics(SimulationMetrics):
    def __init__(
        self,
        cfg: Config,
        mark_allowed: Callable[[str], bool],
        *,
        markout_allowed: Callable[[float], bool] = lambda now: True,
        **kwargs: Any,
    ):
        super().__init__(cfg, **kwargs)
        self.mark_allowed = mark_allowed
        self.markout_allowed = markout_allowed

    def update_unrealized(
        self,
        books: dict[str, LocalOrderBook],
        now_ts: float | None = None,
        mid_override: dict[str, Decimal] | None = None,
        specs: Mapping[str, InstrumentSpec] | None = None,
    ) -> None:
        # Keep immutable unit metadata even when the price is unavailable.
        # Omitted marks stay null; an old book cannot resolve a new markout.
        usable = {symbol: book for symbol, book in books.items() if self.mark_allowed(symbol)}
        marks = {symbol: mid for symbol, mid in (mid_override or {}).items() if self.mark_allowed(symbol)}
        # Positions may use an already-known fresh mark, but a control/EOF
        # timestamp cannot invent a new post-fill price observation.
        sample_ts = now_ts if now_ts is not None and self.markout_allowed(now_ts) else None
        super().update_unrealized(usable, sample_ts, marks, specs)


class ResearchReplayEngine(SimulationEngine):
    """Native matching/risk/accounting with an explicit extra study validity gate."""

    def __init__(self, cfg: ResearchConfig, window: ReplayWindow, **kwargs: Any):
        if not isinstance(cfg, ResearchConfig) or (cfg.hmm is not None and cfg.hmm.symbol != window.symbol):
            raise ValueError("research replay needs its joint-observation/single-quote configuration")
        self.window = window
        self._research_active = False
        self._research_stopped = False
        self._research_depth_fresh = {symbol: False for symbol in SYMBOLS}
        self._research_trade_fresh = {symbol: False for symbol in SYMBOLS}
        self._research_joint_was_valid = False
        self._research_last_depth_ns: dict[str, int | None] = {symbol: None for symbol in SYMBOLS}
        super().__init__(cfg, **kwargs)
        original = self.metrics  # Pristine at construction, before any observation.
        self.metrics = ResearchMetrics(
            cfg,
            lambda symbol: self._validity_state(symbol, require_trade=False).book_valid,
            markout_allowed=lambda now: self._schedule_time_key(now)[0] == self._research_last_depth_ns[window.symbol],
            fill_sink=original._fill_sink,
            markout_sink=original._markout_sink,
            retain_audit_rows=original.retain_audit_rows,
            buffer_markout_trace_events=original._buffer_markout_trace_events,
            regime_execution=self.hmm_execution,
        )

    def _validity_state(self, symbol: str, *, require_trade: bool) -> ValidityState:
        native = super()._validity_state(symbol, require_trade=require_trade)
        reasons = [native.reason] if native.reason else []
        if not self._research_active:
            reasons.append("research_book_only_prefix")
        if not self._research_depth_fresh[symbol]:
            reasons.append("research_depth_stale")
        if not self._research_trade_fresh[symbol]:
            reasons.append("research_trade_stale")
        joint = self._joint_research_valid()
        if not joint:
            reasons.append("research_joint_invalid")
        return replace(
            native,
            book_valid=native.book_valid and joint,
            trade_stream_valid=native.trade_stream_valid and self._research_trade_fresh[symbol],
            reason=";".join(reasons) or None,
        )

    def _joint_research_valid(self) -> bool:
        if not self._research_active or self._research_stopped:
            return False
        for symbol in SYMBOLS:
            native = super()._validity_state(symbol, require_trade=True)
            book = self._books.get(symbol)
            if (
                not native.execution_valid
                or not self._research_depth_fresh[symbol]
                or not self._research_trade_fresh[symbol]
                or book is None
                or book.mid_price() is None
            ):
                return False
        return True

    def _joint_transition(self, now: float) -> None:
        valid = self._joint_research_valid()
        if self._research_joint_was_valid and not valid:
            symbol = self.window.symbol
            self._call_strategy_epoch_hook("invalidate_book_epoch", symbol)
            details = self._clear_symbol_execution_state(symbol)
            self.metrics.invalidate_markouts(symbol, "research_joint_invalid", ts_local=now)
            self._trace(now, symbol, "epoch_invalidated", "research_joint_validity", details=details)
        self._research_joint_was_valid = valid

    def _handle_decision(self, symbol: str, ts: float) -> None:
        logical_ns = self._schedule_time_key(ts)[0]
        if (
            symbol != self.window.symbol
            or not self.window.score_start_ns <= logical_ns < self.window.end_ns
            or not self._validity_state(symbol, require_trade=True).execution_valid
        ):
            return
        super()._handle_decision(symbol, ts)

    def _schedule_observation_decisions(self, symbol: str, now: float, *, include_now: bool) -> None:
        # Both instruments advance the global clock, but the counterpart is
        # observation-only. Do not create its no-op strategy timers. Schedule
        # the quoted symbol before any study-only control drains older actions.
        self._schedule_decisions_up_to(self.window.symbol, now, include_now=include_now)

    def _schedule_decisions_up_to(self, symbol: str, now: float, *, include_now: bool) -> None:
        logical_ns = self._schedule_time_key(now)[0]
        if (
            symbol == self.window.symbol
            and self._research_active
            and not self._research_stopped
            and self.window.score_start_ns <= logical_ns <= self.window.end_ns
        ):
            # The native post-market phase also calls this lower-level hook.
            # Guard it too: an observation-only counterpart must not insert
            # overdue timers after the current risk boundary has been emitted.
            super()._schedule_decisions_up_to(symbol, now, include_now=include_now)

    def _drain_before_research_control(self, now: float, logical_ns: int) -> None:
        self._schedule_observation_decisions(self.window.symbol, now, include_now=False)
        self._drain_events(now, inclusive=False, logical_ns=logical_ns, legacy_subns=0)

    def _prevalidate_capture_boundary(self, rec: RecordedEvent, now: float) -> bool:
        checked = super()._prevalidate_capture_boundary(rec, now)
        if (
            rec.type == "captureEvent"
            and rec.data.get("event") == "capture_trailer"
            and rec.data.get("source_kind") == "derived_research_view"
        ):
            if self._active_logical_ns != self.window.end_ns:
                raise ValueError("study trailer differs from its frozen endpoint")
            self._drain_before_research_control(now, self.window.end_ns)
            details = self._clear_symbol_execution_state(self.window.symbol)
            self._trace(now, self.window.symbol, "epoch_invalidated", "research_window_end", details=details)
        if rec.type == "captureEvent" and rec.data.get("event") == "stop":
            assert self._active_logical_ns is not None
            self._drain_before_research_control(now, self._active_logical_ns)
            self._research_stopped = True
            self._joint_transition(now)
        if rec.type != "captureEvent" or rec.data.get("event") not in {"research_day_start", "research_timeout"}:
            return checked
        logical_ns = self._active_logical_ns
        if logical_ns is None or rec.symbol not in SYMBOLS:
            raise ValueError("research control lacks a matching logical/symbol identity")
        # Older internal actions still occur in their valid causal interval.
        # Timeout controls precede same-time data/actions, not earlier actions.
        self._drain_before_research_control(now, logical_ns)
        if rec.data["event"] == "research_day_start":
            if rec.symbol != self.window.symbol or logical_ns != self.window.feature_start_ns or self._research_active:
                raise ValueError("research day starts exactly once at its frozen boundary")
            self._research_active = True
        else:
            dimension = rec.data.get("dimension")
            if dimension == "depth":
                self._research_depth_fresh[rec.symbol] = False
            elif dimension == "trade":
                self._research_trade_fresh[rec.symbol] = False
            else:
                raise ValueError("unsupported research freshness dimension")
        self._joint_transition(now)
        return checked

    def _notify_market_observer(
        self,
        rec: RecordedEvent,
        input_row: int,
        logical_ns: int,
        *,
        depth_observed: bool = False,
        trade: AggTradeEvent | None = None,
        changes: tuple[LevelChange, ...] = (),
    ) -> None:
        if rec.symbol in SYMBOLS:
            if depth_observed:
                self._research_depth_fresh[rec.symbol] = True
                self._research_last_depth_ns[rec.symbol] = logical_ns
            if trade is not None:
                self._research_trade_fresh[rec.symbol] = True
        self._joint_transition(self._active_event_ts if self._active_event_ts is not None else logical_ns / 1e9)
        super()._notify_market_observer(
            rec, input_row, logical_ns, depth_observed=depth_observed, trade=trade, changes=changes
        )

    def run(
        self, file_path: str | Any, verbose: bool = False, progress_every: int = 5000, **kwargs: Any
    ) -> SimulationMetrics:
        if kwargs:
            raise ValueError("study replay does not support ordinary checkpoint/resume; restart into an exclusive run")
        result = super().run(file_path, verbose, progress_every)
        if not self._research_active:
            raise ValueError("study replay never observed its registered day-start control")
        return result
