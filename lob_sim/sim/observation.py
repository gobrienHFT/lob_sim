"""Read-only market observations from the authoritative simulation state.

Observers have no reference to mutable books, actions, strategies or RNGs.
They run independently of the event trace and cannot consume its IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..book.types import AggTradeEvent, LevelChange, SymbolSpec
from ..record.envelope import ValidityState


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    spec: SymbolSpec
    logical_ns: int
    receive_seq: int  # Validated capture sequence, or legacy input-row index.
    input_row: int
    wall_ns: int
    receive_clock: bool
    validity: ValidityState
    epochs: tuple[int, int, int]
    bids: tuple[tuple[int, int], ...]
    asks: tuple[tuple[int, int], ...]
    depth_observed: bool = False
    trade: AggTradeEvent | None = None
    changes: tuple[LevelChange, ...] = ()


class MarketObserver(Protocol):
    """Consume all before-record samples before observing the new state.

    Same-time observations close only on the next strictly later receipt, or
    at EOF. Thus a sample at t is not available for earlier decisions at t.
    ``finish`` must not extend past the last actual market/control observation.
    All callback failures propagate; audit failure is not silently ignored.
    """

    depth_levels: int

    def before_record(self, logical_ns: int) -> None: ...

    def observe(self, observation: MarketObservation) -> None: ...

    def finish(self, logical_ns: int) -> None: ...
