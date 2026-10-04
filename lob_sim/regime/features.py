"""Bounded fixed-grid features from already validated book/trade observations.

This module does not parse raw exchange messages or reconstruct a second book.
The engine integration supplies immutable views of its authoritative book.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from ..book.local_book import LocalOrderBook
from ..book.types import AggTradeEvent, LevelChange
from .validation import identity, integer, require_keys


FEATURE_NAMES = (
    "spread_bps",
    "log_mid_return_bps",
    "realized_volatility_bps",
    "imbalance_l1",
    "imbalance_depth",
    "signed_trade_imbalance",
    "log_visible_depth_lots",
    "visible_depth_change_fraction",
    "log_market_event_rate",
    "log_trade_rate",
    "microprice_displacement_bps",
    "imbalance_change",
)
FEATURE_FORMULAS = (
    "10000*(ask-bid)/mid",
    "10000*log(mid/previous_sample_mid)",
    "sqrt(mean(trailing_sample_log_return_bps**2))",
    "(bid_lots_1-ask_lots_1)/(bid_lots_1+ask_lots_1)",
    "(sum_bid_lots_N-sum_ask_lots_N)/(sum_bid_lots_N+sum_ask_lots_N)",
    "(trailing_aggressive_buy_lots-trailing_aggressive_sell_lots)/trailing_total_trade_lots;0_if_no_trades",
    "log1p(sum_bid_lots_N+sum_ask_lots_N)",
    "trailing_visible_net_level_change_lots/mean(trailing_sample_top_N_depth_lots)",
    "log1p(trailing_depth_and_trade_record_count/window_seconds)",
    "log1p(trailing_trade_record_count/window_seconds)",
    "10000*((ask*bid_lots_1+bid*ask_lots_1)/(bid_lots_1+ask_lots_1)-mid)/mid",
    "imbalance_l1-previous_sample_imbalance_l1",
)
FeatureStatus = Literal[
    "VALID",
    "WARMING_UP",
    "INVALID_BOOK",
    "INVALID_TRADE_STREAM",
    "INVALID_CLOCK",
    "INVALID_CAPTURE",
    "STALE",
    "NONFINITE_FEATURE",
]


@dataclass(frozen=True)
class FeatureSpec:
    interval_ns: int = 1_000_000_000
    window_steps: int = 10
    depth_levels: int = 5
    stale_after_ns: int = 5_000_000_000

    def __post_init__(self) -> None:
        for name in ("interval_ns", "window_steps", "depth_levels", "stale_after_ns"):
            integer(getattr(self, name), name, minimum=1)
        if self.stale_after_ns < self.interval_ns:
            raise ValueError("stale_after_ns must be >= interval_ns")

    @property
    def window_ns(self) -> int:
        return self.interval_ns * self.window_steps

    @property
    def digest(self) -> str:
        return identity(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lob_sim.hmm_features.v1",
            "interval_ns": self.interval_ns,
            "window_steps": self.window_steps,
            "depth_levels": self.depth_levels,
            "stale_after_ns": self.stale_after_ns,
            "feature_names": list(FEATURE_NAMES),
            "feature_formulas": list(FEATURE_FORMULAS),
            "sampling": "integer_clock_grid;trailing_right_closed;receive_sequence_ties",
            "visible_change": "validated_changes_at_union_of_previous_and_current_top_N_prices;not_cancel_rate",
            "depth_units": "integer_instrument_lots;symbol_specific_training",
        }

    @classmethod
    def from_dict(cls, value: object) -> FeatureSpec:
        expected = cls().as_dict()
        data = require_keys(value, set(expected), "feature specification")
        result = cls(data["interval_ns"], data["window_steps"], data["depth_levels"], data["stale_after_ns"])
        if result.as_dict() != dict(data):
            raise ValueError("unsupported feature order/formula/sampling specification")
        return result


@dataclass(frozen=True)
class BookView:
    bids: tuple[tuple[int, int], ...]
    asks: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        for name, levels in (("bids", self.bids), ("asks", self.asks)):
            if not isinstance(levels, tuple) or not levels:
                raise ValueError("book view requires two nonempty immutable sides")
            for level in levels:
                if not isinstance(level, tuple) or len(level) != 2:
                    raise ValueError("book view levels must be tick/lot pairs")
                integer(level[0], "price_tick", minimum=1)
                integer(level[1], "qty_lots", minimum=1)
            prices = [level[0] for level in levels]
            if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=name == "bids"):
                raise ValueError("book view levels must be distinct and sorted")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("book view is crossed or locked")

    @classmethod
    def from_book(cls, book: LocalOrderBook, depth_levels: int) -> BookView:
        integer(depth_levels, "depth_levels", minimum=1)
        return cls(
            tuple(sorted(book.bids.items(), reverse=True)[:depth_levels]),
            tuple(sorted(book.asks.items())[:depth_levels]),
        )

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def depth(self) -> int:
        return sum(qty for _, qty in (*self.bids, *self.asks))

    @property
    def imbalance(self) -> float:
        bid, ask = self.bids[0][1], self.asks[0][1]
        return (bid - ask) / (bid + ask)


@dataclass(frozen=True)
class FeatureValidity:
    book: bool = True
    trade: bool = True
    clock: bool = True
    capture: bool = True
    # Local book epoch, public stream epoch, market stream epoch.
    epochs: tuple[int, int, int] = (0, 0, 0)

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (self.book, self.trade, self.clock, self.capture)):
            raise ValueError("validity flags must be bool")
        if not isinstance(self.epochs, tuple) or len(self.epochs) != 3:
            raise ValueError("validity epochs must be an immutable triple")
        for value in self.epochs:
            integer(value, "epoch")

    @property
    def status(self) -> FeatureStatus:
        if not self.capture:
            return "INVALID_CAPTURE"
        if not self.clock:
            return "INVALID_CLOCK"
        if not self.book:
            return "INVALID_BOOK"
        if not self.trade:
            return "INVALID_TRADE_STREAM"
        return "VALID"


@dataclass(frozen=True)
class FeatureSample:
    symbol: str
    sample_ns: int
    receive_seq: int
    epochs: tuple[int, int, int]
    feature_identity: str
    status: FeatureStatus
    values: tuple[float, ...] | None
    reset_reason: str | None

    def __post_init__(self) -> None:
        from .validation import vector

        integer(self.sample_ns, "sample_ns")
        integer(self.receive_seq, "receive_seq")
        FeatureValidity(epochs=self.epochs)
        if self.status not in {
            "VALID",
            "WARMING_UP",
            "INVALID_BOOK",
            "INVALID_TRADE_STREAM",
            "INVALID_CLOCK",
            "INVALID_CAPTURE",
            "STALE",
            "NONFINITE_FEATURE",
        }:
            raise ValueError("unknown feature status")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("sample symbol must be nonempty")
        if (self.status == "VALID") != (self.values is not None):
            raise ValueError("feature status/value mismatch")
        if self.values is not None:
            object.__setattr__(self, "values", vector(self.values, "sample features", len(FEATURE_NAMES)))


@dataclass
class _Bin:
    market_count: int = 0
    trade_count: int = 0
    signed_trade_lots: int = 0
    trade_lots: int = 0
    visible_change_lots: int = 0
    depth: int = 0
    return_bps: float = 0.0


class CausalFeatureSampler:
    """One symbol; O(window_steps + depth_levels) memory independent of tape length.

    Call ``advance_before(t)`` and consume it fully before applying an event at t.
    Then call ``observe`` with the validated causal state. Equal-time receipts
    all enter the same right-closed bin in receive-sequence order. ``finish(t)``
    closes the final grid point <= the last observation, never an invented tail.
    Sampling itself never reads a future book or interpolates a future price.
    """

    def __init__(self, symbol: str, spec: FeatureSpec = FeatureSpec()) -> None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be nonempty")
        self.symbol, self._spec = symbol, spec
        self._next_ns: int | None = None
        self._last_observation_ns: int | None = None
        self._last_receive_seq = -1
        self._advance_target_ns: int | None = None
        self._closed_through_ns: int | None = None
        self._last_book_ns: int | None = None
        self._valid_since_ns: int | None = None
        self._book: BookView | None = None
        self._validity = FeatureValidity(book=False, trade=False)
        self._status: FeatureStatus = "INVALID_BOOK"
        self._bins: deque[_Bin] = deque(maxlen=spec.window_steps)
        self._current = _Bin()
        self._previous_mid: float | None = None
        self._previous_imbalance: float | None = None
        self._reset_reason: str | None = "initialization"
        self._feature_identity = spec.digest

    @property
    def spec(self) -> FeatureSpec:
        return self._spec

    @property
    def status(self) -> FeatureStatus:
        return self._status

    @property
    def retained_bins(self) -> int:
        return len(self._bins)

    def _reset(self, reason: str) -> None:
        self._bins.clear()
        self._current = _Bin()
        self._valid_since_ns = None
        self._previous_mid = self._previous_imbalance = None
        self._reset_reason = reason

    def advance_before(self, time_ns: int) -> Iterator[FeatureSample]:
        integer(time_ns, "time_ns")
        if self._last_observation_ns is not None and time_ns < self._last_observation_ns:
            raise ValueError("regressing feature clock")
        if self._advance_target_ns is not None and time_ns < self._advance_target_ns:
            raise ValueError("regressing feature watermark")
        self._advance_target_ns = time_ns
        while self._next_ns is not None and self._next_ns < time_ns:
            sample_ns = self._next_ns
            self._next_ns += self.spec.interval_ns
            yield self._sample(sample_ns)

    def finish(self, last_observation_ns: int) -> Iterator[FeatureSample]:
        if last_observation_ns != self._last_observation_ns:
            raise ValueError("finish cannot invent an unobserved clock tail")
        while self._next_ns is not None and self._next_ns <= last_observation_ns:
            sample_ns = self._next_ns
            self._next_ns += self.spec.interval_ns
            yield self._sample(sample_ns)
        self._closed_through_ns = last_observation_ns

    def observe(
        self,
        time_ns: int,
        receive_seq: int,
        *,
        book: BookView | None,
        validity: FeatureValidity,
        depth_event: bool = False,
        trade: AggTradeEvent | None = None,
        changes: tuple[LevelChange, ...] = (),
    ) -> None:
        integer(time_ns, "time_ns")
        integer(receive_seq, "receive_seq")
        if type(depth_event) is not bool:
            raise ValueError("depth_event must be bool")
        if self._last_observation_ns is not None and time_ns < self._last_observation_ns:
            raise ValueError("regressing feature clock")
        if self._advance_target_ns is not None and time_ns < self._advance_target_ns:
            raise ValueError("event predates feature watermark")
        if self._closed_through_ns is not None and time_ns <= self._closed_through_ns:
            raise ValueError("event reopens a closed sampling point")
        if receive_seq <= self._last_receive_seq:
            raise ValueError("feature receive sequence must strictly increase")
        if self._next_ns is not None and self._next_ns < time_ns:
            raise RuntimeError("consume advance_before(time_ns) before observing a later event")
        if book is not None and (len(book.bids) > self.spec.depth_levels or len(book.asks) > self.spec.depth_levels):
            raise ValueError("book view exceeds configured depth_levels")
        if trade is not None:
            if trade.symbol != self.symbol or type(trade.buyer_is_maker) is not bool:
                raise ValueError("trade symbol/sign contract mismatch")
            integer(trade.qty_lots, "trade qty_lots", minimum=1)
        for change in changes:
            if change.side not in {"bid", "ask", "bids", "asks"}:
                raise ValueError("unknown visible-change side")
            integer(change.price_tick, "change price_tick", minimum=1)
            integer(change.previous_lots, "previous_lots")
            integer(change.new_lots, "new_lots")
        if self._next_ns is None:
            self._next_ns = (time_ns // self.spec.interval_ns + 1) * self.spec.interval_ns
        if validity.epochs != self._validity.epochs:
            self._reset("epoch_changed")
            self._book = None
            self._last_book_ns = None
        status = validity.status if book is not None else "INVALID_BOOK"
        if status == "VALID":
            assert book is not None
            try:
                if not math.isfinite(book.mid):
                    raise ValueError("non-finite book midpoint")
            except (ValueError, OverflowError):
                status = "NONFINITE_FEATURE"
            if not depth_event and (
                self._last_book_ns is None or time_ns - self._last_book_ns > self.spec.stale_after_ns
            ):
                status = "STALE"
        if status != "VALID":
            if self._status != status or self._valid_since_ns is not None:
                self._reset(status)
            self._status = status
        else:
            assert book is not None
            newly_valid = self._valid_since_ns is None
            if self._valid_since_ns is None:
                self._valid_since_ns = time_ns
                self._status = "WARMING_UP"
                self._previous_mid = book.mid
                self._previous_imbalance = book.imbalance
            if depth_event:
                self._last_book_ns = time_ns
            previous_prices = (
                {
                    (side, tick)
                    for side, levels in (("bid", self._book.bids), ("ask", self._book.asks))
                    for tick, _ in levels
                }
                if self._book is not None
                else set()
            )
            current_prices = {
                (side, tick) for side, levels in (("bid", book.bids), ("ask", book.asks)) for tick, _ in levels
            }
            self._current.visible_change_lots += (
                sum(
                    change.new_lots - change.previous_lots
                    for change in changes
                    if ("bid" if change.side in {"bid", "bids"} else "ask", change.price_tick)
                    in previous_prices | current_prices
                )
                if not newly_valid
                else 0
            )
            self._current.market_count += (int(depth_event) + int(trade is not None)) if not newly_valid else 0
            if trade is not None and not newly_valid:
                self._current.trade_count += 1
                self._current.trade_lots += trade.qty_lots
                self._current.signed_trade_lots += (-1 if trade.buyer_is_maker else 1) * trade.qty_lots
        self._book, self._validity = book, validity
        self._last_observation_ns, self._last_receive_seq = time_ns, receive_seq

    def _sample(self, sample_ns: int) -> FeatureSample:
        status: FeatureStatus = self._validity.status
        values: tuple[float, ...] | None = None
        if self._book is None:
            status = "INVALID_BOOK"
        elif status == "VALID" and (
            self._last_book_ns is None or sample_ns - self._last_book_ns > self.spec.stale_after_ns
        ):
            status = "STALE"
        if status != "VALID":
            self._reset(status)
        else:
            assert self._book is not None
            book = self._book
            try:
                mid, imbalance = book.mid, book.imbalance
                change = math.log(mid / self._previous_mid) * 10000 if self._previous_mid is not None else 0.0
                self._current.return_bps, self._current.depth = change, book.depth
                self._bins.append(self._current)
                if (
                    len(self._bins) < self.spec.window_steps
                    or self._previous_mid is None
                    or (self._valid_since_ns is None or sample_ns - self._valid_since_ns < self.spec.window_ns)
                ):
                    status = "WARMING_UP"
                else:
                    bid, bid_qty = book.bids[0]
                    ask, ask_qty = book.asks[0]
                    bid_depth = sum(qty for _, qty in book.bids)
                    ask_depth = sum(qty for _, qty in book.asks)
                    volume = sum(bin.trade_lots for bin in self._bins)
                    seconds = self.spec.window_ns / 1e9
                    values = (
                        10000 * (ask - bid) / mid,
                        change,
                        math.sqrt(math.fsum(bin.return_bps**2 for bin in self._bins) / len(self._bins)),
                        imbalance,
                        (bid_depth - ask_depth) / book.depth,
                        sum(bin.signed_trade_lots for bin in self._bins) / volume if volume else 0.0,
                        math.log1p(book.depth),
                        sum(bin.visible_change_lots for bin in self._bins)
                        / (sum(bin.depth for bin in self._bins) / len(self._bins)),
                        math.log1p(sum(bin.market_count for bin in self._bins) / seconds),
                        math.log1p(sum(bin.trade_count for bin in self._bins) / seconds),
                        10000 * ((ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty) - mid) / mid,
                        imbalance - (self._previous_imbalance if self._previous_imbalance is not None else imbalance),
                    )
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("non-finite feature")
            except (OverflowError, ValueError):
                status, values = "NONFINITE_FEATURE", None
                self._reset(status)
            else:
                self._previous_mid, self._previous_imbalance = mid, imbalance
        self._current = _Bin()
        self._status = status
        result = FeatureSample(
            self.symbol,
            sample_ns,
            self._last_receive_seq,
            self._validity.epochs,
            self._feature_identity,
            status,
            values,
            self._reset_reason,
        )
        self._reset_reason = None
        return result
