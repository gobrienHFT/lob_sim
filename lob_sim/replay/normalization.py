from __future__ import annotations

from decimal import Decimal

from ..book.types import AggTradeEvent, DepthUpdateEvent, InstrumentSpec, SnapshotEvent, SymbolSpec
from .reader import RecordedEvent


def _require_record_type(record: RecordedEvent, expected_type: str) -> None:
    if record.type != expected_type:
        raise ValueError(f"Expected {expected_type} record, got {record.type}")


def instrument_spec_from_record(record: RecordedEvent) -> InstrumentSpec | None:
    if record.type != "exchangeInfo":
        return None
    data = record.data
    tick_size = data.get("tickSize")
    step_size = data.get("stepSize")
    if tick_size is None or step_size is None:
        return None
    return InstrumentSpec(
        symbol=record.symbol,
        tick_size=Decimal(str(tick_size)),
        step_size=Decimal(str(step_size)),
        price_currency=str(data.get("quoteAsset", "")),
        quantity_unit=str(data.get("baseAsset", "")),
        contract_multiplier=Decimal(str(data.get("contractMultiplier", "1"))),
        venue=str(data.get("venue", "")),
    )


def snapshot_from_record(record: RecordedEvent, spec: SymbolSpec) -> SnapshotEvent:
    _require_record_type(record, "snapshot")
    return SnapshotEvent(
        symbol=record.symbol,
        last_update_id=int(record.data["lastUpdateId"]),
        bids=[(spec.price_to_tick(price), spec.qty_to_lot(qty)) for price, qty in record.data.get("bids", [])],
        asks=[(spec.price_to_tick(price), spec.qty_to_lot(qty)) for price, qty in record.data.get("asks", [])],
    )


def depth_update_from_record(record: RecordedEvent, spec: SymbolSpec) -> DepthUpdateEvent:
    _require_record_type(record, "depthUpdate")
    return DepthUpdateEvent(
        symbol=record.symbol,
        first_update_id=int(record.data["U"]),
        final_update_id=int(record.data["u"]),
        prev_update_id=int(record.data.get("pu", record.data["U"])),
        bids=[(spec.price_to_tick(price), spec.qty_to_lot(qty)) for price, qty in record.data.get("b", [])],
        asks=[(spec.price_to_tick(price), spec.qty_to_lot(qty)) for price, qty in record.data.get("a", [])],
        ts_local=float(record.ts_local),
    )


def agg_trade_from_record(record: RecordedEvent, spec: SymbolSpec) -> AggTradeEvent:
    _require_record_type(record, "aggTrade")
    return AggTradeEvent(
        symbol=record.symbol,
        price_tick=spec.price_to_tick(record.data["p"]),
        qty_lots=spec.qty_to_lot(record.data["q"]),
        buyer_is_maker=bool(record.data["m"]),
        ts_local=float(record.ts_local),
    )
