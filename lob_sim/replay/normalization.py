from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ..book.types import AggTradeEvent, DepthUpdateEvent, InstrumentSpec, SnapshotEvent, SymbolSpec
from ..record.envelope import require_nonnegative_int
from .reader import RecordedEvent


def _require_record_type(record: RecordedEvent, expected_type: str) -> None:
    if record.type != expected_type:
        raise ValueError(f"Expected {expected_type} record, got {record.type}")


def _capture_metadata(record: RecordedEvent) -> Mapping[str, object]:
    """Return receipt metadata without changing its wire-level types.

    ``iter_records`` validates schema-v3 rows before they reach these
    adapters, but the normalizers are also a public boundary used by direct
    callers and injected replay adapters.  Treating a malformed ``_capture``
    value as an empty legacy capture would silently erase causal identity.
    """

    value = record.data.get("_capture")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("_capture must be a mapping")
    return value


def _capture_int(capture: Mapping[str, object], field_name: str) -> int | None:
    value = capture.get(field_name)
    if value is None:
        return None
    return require_nonnegative_int(value, field_name)


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
    capture = _capture_metadata(record)
    return SnapshotEvent(
        symbol=record.symbol,
        last_update_id=int(record.data["lastUpdateId"]),
        bids=[
            (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty)) for price, qty in record.data.get("bids", [])
        ],
        asks=[
            (spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty)) for price, qty in record.data.get("asks", [])
        ],
        ts_local=float(record.ts_local),
        receive_seq=_capture_int(capture, "recvSeq"),
        stream_epoch=_capture_int(capture, "streamEpoch"),
        sync_epoch=_capture_int(capture, "syncEpoch"),
    )


def depth_update_from_record(record: RecordedEvent, spec: SymbolSpec) -> DepthUpdateEvent:
    _require_record_type(record, "depthUpdate")
    capture = _capture_metadata(record)
    event_ts = record.data.get("E")
    transaction_ts = record.data.get("T")
    return DepthUpdateEvent(
        symbol=record.symbol,
        first_update_id=int(record.data["U"]),
        final_update_id=int(record.data["u"]),
        prev_update_id=int(record.data.get("pu", record.data["U"])),
        bids=[(spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty)) for price, qty in record.data.get("b", [])],
        asks=[(spec.price_to_tick_exact(price), spec.qty_to_lot_exact(qty)) for price, qty in record.data.get("a", [])],
        ts_local=float(record.ts_local),
        event_ts=float(event_ts) / 1000.0 if event_ts is not None else None,
        transaction_ts=float(transaction_ts) / 1000.0 if transaction_ts is not None else None,
        receive_seq=_capture_int(capture, "recvSeq"),
        receive_monotonic_ns=_capture_int(capture, "recvMonotonicNs"),
        stream_epoch=_capture_int(capture, "streamEpoch"),
        sync_epoch=_capture_int(capture, "syncEpoch"),
    )


def agg_trade_from_record(record: RecordedEvent, spec: SymbolSpec) -> AggTradeEvent:
    _require_record_type(record, "aggTrade")
    capture = _capture_metadata(record)
    event_ts = record.data.get("E")
    transaction_ts = record.data.get("T")
    return AggTradeEvent(
        symbol=record.symbol,
        price_tick=spec.price_to_tick_exact(record.data["p"]),
        qty_lots=spec.qty_to_lot_exact(record.data["q"]),
        buyer_is_maker=bool(record.data["m"]),
        ts_local=float(record.ts_local),
        aggregate_trade_id=int(record.data["a"]) if record.data.get("a") is not None else None,
        event_ts=float(event_ts) / 1000.0 if event_ts is not None else None,
        transaction_ts=float(transaction_ts) / 1000.0 if transaction_ts is not None else None,
        receive_seq=_capture_int(capture, "recvSeq"),
        receive_monotonic_ns=_capture_int(capture, "recvMonotonicNs"),
        stream_epoch=_capture_int(capture, "streamEpoch"),
        sync_epoch=_capture_int(capture, "syncEpoch"),
    )
