from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OrderSide = Literal["bid", "ask"]
OrderType = Literal["limit", "market", "cancel"]
FillSource = Literal["depth_update", "agg_trade", "taker_order"]
OrderState = Literal[
    "intent",
    "outbound_new",
    "live",
    "pending_cancel",
    "filled",
    "cancelled",
    "expired",
    "epoch_invalidated",
    "rejected",
]


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    price_tick: int | None
    qty_lots: int
    quote_slot: str = "base"
    queue_ahead_lots: int = 0
    created_ts: float = 0.0
    remaining_lots: int = 0
    active: bool = True
    order_type: OrderType = "limit"
    is_strategy: bool = True
    refresh_key: str = ""
    state: OrderState = "live"
    decision_evidence_ids: tuple[str, ...] = ()
    arrival_evidence_ids: tuple[str, ...] = ()
    arrival_validity: dict[str, Any] | None = None
    new_order_latency_ms: float = 0.0
    cancel_latency_ms: float | None = None

    def mark_pending_cancel(self) -> None:
        if self.state == "live":
            self.state = "pending_cancel"

    def mark_cancelled(self) -> None:
        self.state = "cancelled"
        self.active = False

    def mark_filled(self) -> None:
        self.state = "filled"
        self.active = False

    def mark_epoch_invalidated(self) -> None:
        self.state = "epoch_invalidated"
        self.active = False


@dataclass
class Fill:
    ts_local: float
    symbol: str
    side: OrderSide
    price_tick: int
    qty_lots: int
    maker: bool = True
    order_id: str | None = None
    queue_ahead_lots: int = 0
    created_ts: float | None = None
    source: FillSource = "depth_update"
    scenario_id: str = "public_l2_legacy"
    evidence_ids: tuple[str, ...] = ()
    validity: dict[str, Any] | None = None
    queue_trajectory: dict[str, int] = field(default_factory=dict)
    latency_draws_ms: dict[str, float | None] = field(default_factory=dict)
    order_state_at_fill: OrderState = "live"
