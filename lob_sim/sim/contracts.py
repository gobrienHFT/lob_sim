"""Small public interfaces separating replay, venue, risk and accounting.

These contracts are deliberately data-oriented.  The Python implementation is
the readable oracle; the Rust kernel implements selected primitives at the same
boundary without importing the strategy/reporting layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..book.local_book import LocalOrderBook
from ..book.types import SymbolSpec
from ..record.envelope import LogicalTime, ValidityState


@dataclass(frozen=True)
class MarketEvent:
    time: LogicalTime
    symbol: str
    kind: str
    payload: Mapping[str, Any]
    validity: ValidityState


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    symbol: str
    side: str
    price_tick: int | None
    qty_lots: int
    order_type: str = "limit"
    post_only: bool = True


@dataclass(frozen=True)
class VenueAction:
    action_id: str
    intent_id: str
    action: str
    due: LogicalTime


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    intent_id: str
    state: str
    time: LogicalTime
    reason: str | None = None


@dataclass(frozen=True)
class FillEvent:
    order_id: str
    symbol: str
    side: str
    price_tick: int
    qty_lots: int
    time: LogicalTime
    scenario_id: str
    validity: ValidityState
    evidence_ids: tuple[str, ...] = ()
    queue_trajectory: Mapping[str, int] = field(default_factory=dict)
    latency_draws_ms: Mapping[str, float | None] = field(default_factory=dict)
    latency_model: Mapping[str, Any] = field(default_factory=dict)
    order_state_at_fill: str = "live"
    fee_model_id: str = "static_config_bps"


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str
    reserved_lots: int
    position_lots: int


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    source_sha256: str
    config_sha256: str
    code_sha256: str
    seed: int
    assumptions: Mapping[str, Any]


class EventSource(Protocol):
    def events(self) -> Iterable[MarketEvent]: ...


class MarketBook(Protocol):
    symbol: str
    spec: SymbolSpec

    def apply(self, event: MarketEvent) -> None: ...

    def snapshot(self) -> Mapping[str, Any]: ...


class Strategy(Protocol):
    def on_market(self, event: MarketEvent, book: LocalOrderBook) -> Sequence[OrderIntent]: ...


class VenueModel(Protocol):
    def submit(self, intent: OrderIntent, book: LocalOrderBook) -> Sequence[FillEvent]: ...


class RiskEngine(Protocol):
    def check(self, intent: OrderIntent) -> RiskDecision: ...


class AccountingEngine(Protocol):
    def on_fill(self, fill: FillEvent, spec: SymbolSpec) -> None: ...

    def mark(self, symbol: str, mid: Decimal | None) -> None: ...
