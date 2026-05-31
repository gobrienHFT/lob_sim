from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ..book.types import AggTradeEvent, DepthUpdateEvent, InstrumentSpec, SnapshotEvent, SymbolSpec
from .reader import RecordedEvent
from . import normalization


class ReplayFeedAdapter(Protocol):
    """Boundary between venue-specific record payloads and normalized replay events."""

    name: str
    venue_label: str
    supported_record_types: frozenset[str]

    def instrument_spec_from_record(self, record: RecordedEvent) -> InstrumentSpec | None:
        """Return instrument metadata from a venue metadata row, if present."""

    def snapshot_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> SnapshotEvent:
        """Normalize a full-depth snapshot row."""

    def depth_update_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> DepthUpdateEvent:
        """Normalize an incremental depth-update row."""

    def agg_trade_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> AggTradeEvent:
        """Normalize an aggregate public trade row."""


class BinanceUsdMReplayAdapter:
    """Adapter for the current Binance USD-M normalized NDJSON contract."""

    name = "binance_usdm"
    venue_label = "BINANCE_USDM"
    supported_record_types = frozenset({"exchangeInfo", "snapshot", "depthUpdate", "aggTrade"})

    def instrument_spec_from_record(self, record: RecordedEvent) -> InstrumentSpec | None:
        spec = normalization.instrument_spec_from_record(record)
        if spec is None:
            return None
        if spec.venue:
            return spec
        return replace(spec, venue=self.venue_label)

    def snapshot_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> SnapshotEvent:
        return normalization.snapshot_from_record(record, spec)

    def depth_update_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> DepthUpdateEvent:
        return normalization.depth_update_from_record(record, spec)

    def agg_trade_from_record(self, record: RecordedEvent, spec: SymbolSpec) -> AggTradeEvent:
        return normalization.agg_trade_from_record(record, spec)


DEFAULT_REPLAY_ADAPTER: ReplayFeedAdapter = BinanceUsdMReplayAdapter()
