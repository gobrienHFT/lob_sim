from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

RECORD_SCHEMA_VERSION = "lob_sim.record.v1"
CAPTURE_SCHEMA_VERSION = "lob_sim.record.v3"
KNOWN_RECORD_TYPES = frozenset({"captureMeta", "captureEvent", "exchangeInfo", "snapshot", "depthUpdate", "aggTrade"})


class RecordValidationError(ValueError):
    """Raised when a recorded market-data row does not match the replay contract."""

    def __init__(
        self,
        message: str,
        *,
        path: str | Path | None = None,
        line_number: int | None = None,
    ) -> None:
        location = _format_location(path, line_number)
        super().__init__(f"{location}: {message}" if location else message)
        self.path = str(path) if path is not None else None
        self.line_number = line_number


def _format_location(path: str | Path | None, line_number: int | None) -> str:
    if path is None and line_number is None:
        return ""
    if path is None:
        return f"line {line_number}"
    if line_number is None:
        return str(path)
    return f"{path}:{line_number}"


def _fail(message: str, *, path: str | Path | None, line_number: int | None) -> None:
    raise RecordValidationError(message, path=path, line_number=line_number)


def _require_mapping(
    value: Any,
    name: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be a JSON object", path=path, line_number=line_number)
    return value


def _require_keys(
    obj: Mapping[str, Any],
    keys: Sequence[str],
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        _fail(
            f"{context} is missing required field(s): {', '.join(missing)}",
            path=path,
            line_number=line_number,
        )


def _require_numberish(
    value: Any,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RecordValidationError(
            f"{context} must be numeric, got {value!r}",
            path=path,
            line_number=line_number,
        ) from exc
    if not parsed.is_finite():
        raise RecordValidationError(
            f"{context} must be finite, got {value!r}",
            path=path,
            line_number=line_number,
        )


def _require_intish(
    value: Any,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    if isinstance(value, bool):
        _fail(f"{context} must be an integer, got {value!r}", path=path, line_number=line_number)
    try:
        int(str(value))
    except (TypeError, ValueError) as exc:
        raise RecordValidationError(
            f"{context} must be an integer, got {value!r}",
            path=path,
            line_number=line_number,
        ) from exc


def _require_exact_nonnegative_int(
    value: Any,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    """Require the JSON integer representation used by schema-v3 metadata.

    Payload sequence ids remain permissive for legacy NDJSON compatibility,
    but receipt identity is part of the causal contract.  Accepting a string
    or float here would make ``validate`` disagree with the replay engine,
    which intentionally fails closed on coercible metadata.
    """

    if type(value) is not int or value < 0:
        _fail(
            f"{context} must be an exact non-negative integer, got {value!r}",
            path=path,
            line_number=line_number,
        )


def _require_optional_nonempty_string(
    obj: Mapping[str, Any],
    key: str,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    if key in obj:
        value = obj[key]
        if not isinstance(value, str) or not value:
            _fail(f"{context}.{key} must be a non-empty string", path=path, line_number=line_number)


def _require_bool(
    value: Any,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    if not isinstance(value, bool):
        _fail(f"{context} must be boolean", path=path, line_number=line_number)


def _require_optional_string(
    obj: Mapping[str, Any],
    key: str,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    if key in obj and not isinstance(obj[key], str):
        _fail(f"{context}.{key} must be a string", path=path, line_number=line_number)


def _require_level_list(
    value: Any,
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    if not isinstance(value, list):
        _fail(f"{context} must be a list of [price, quantity] levels", path=path, line_number=line_number)
    for index, level in enumerate(value):
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            _fail(
                f"{context}[{index}] must contain price and quantity",
                path=path,
                line_number=line_number,
            )
        _require_numberish(level[0], f"{context}[{index}].price", path=path, line_number=line_number)
        _require_numberish(level[1], f"{context}[{index}].quantity", path=path, line_number=line_number)


def _validate_capture_metadata(
    data: Mapping[str, Any],
    context: str,
    *,
    path: str | Path | None,
    line_number: int | None,
) -> None:
    capture = data.get("_capture")
    if capture is None:
        return
    capture_obj = _require_mapping(capture, f"{context}._capture", path=path, line_number=line_number)
    for key in ("recvSeq", "recvWallNs", "recvMonotonicNs", "streamEpoch", "syncEpoch"):
        if key in capture_obj:
            _require_exact_nonnegative_int(
                capture_obj[key],
                f"{context}._capture.{key}",
                path=path,
                line_number=line_number,
            )
    _require_optional_nonempty_string(
        capture_obj,
        "route",
        f"{context}._capture",
        path=path,
        line_number=line_number,
    )
    for key in ("reason", "validationError"):
        _require_optional_string(capture_obj, key, f"{context}._capture", path=path, line_number=line_number)
    if "snapshotAccepted" in capture_obj:
        _require_bool(
            capture_obj["snapshotAccepted"],
            f"{context}._capture.snapshotAccepted",
            path=path,
            line_number=line_number,
        )


def validate_record_object(
    obj: Any,
    *,
    path: str | Path | None = None,
    line_number: int | None = None,
) -> None:
    """Validate one NDJSON row against the stable replay record contract."""

    record = _require_mapping(obj, "record", path=path, line_number=line_number)
    _require_keys(record, ("ts_local", "symbol", "type", "data"), "record", path=path, line_number=line_number)
    _require_numberish(record["ts_local"], "record.ts_local", path=path, line_number=line_number)

    symbol = record["symbol"]
    if not isinstance(symbol, str) or not symbol.strip():
        _fail("record.symbol must be a non-empty string", path=path, line_number=line_number)

    record_type = record["type"]
    if not isinstance(record_type, str) or record_type not in KNOWN_RECORD_TYPES:
        _fail(
            f"record.type must be one of {sorted(KNOWN_RECORD_TYPES)}, got {record_type!r}",
            path=path,
            line_number=line_number,
        )

    data = _require_mapping(record["data"], "record.data", path=path, line_number=line_number)
    _validate_capture_metadata(data, f"{record_type} payload", path=path, line_number=line_number)
    if record_type in {"captureMeta", "captureEvent"}:
        if record_type == "captureEvent":
            _require_keys(data, ("event", "route"), "captureEvent payload", path=path, line_number=line_number)
            _require_optional_nonempty_string(data, "event", "captureEvent", path=path, line_number=line_number)
            _require_optional_nonempty_string(data, "route", "captureEvent", path=path, line_number=line_number)
            _require_optional_string(data, "reason", "captureEvent", path=path, line_number=line_number)
            return
        if "schemaVersion" in data:
            _require_exact_nonnegative_int(
                data["schemaVersion"],
                "captureMeta.schemaVersion",
                path=path,
                line_number=line_number,
            )
        _require_optional_string(data, "clock", "captureMeta", path=path, line_number=line_number)
        return
    if record_type == "exchangeInfo":
        _require_keys(data, ("tickSize", "stepSize"), "exchangeInfo payload", path=path, line_number=line_number)
        _require_numberish(data["tickSize"], "exchangeInfo.tickSize", path=path, line_number=line_number)
        _require_numberish(data["stepSize"], "exchangeInfo.stepSize", path=path, line_number=line_number)
        for key in ("baseAsset", "quoteAsset", "venue"):
            _require_optional_string(data, key, "exchangeInfo", path=path, line_number=line_number)
        if "contractMultiplier" in data:
            _require_numberish(
                data["contractMultiplier"],
                "exchangeInfo.contractMultiplier",
                path=path,
                line_number=line_number,
            )
        return

    if record_type == "snapshot":
        _require_keys(data, ("lastUpdateId", "bids", "asks"), "snapshot payload", path=path, line_number=line_number)
        _require_intish(data["lastUpdateId"], "snapshot.lastUpdateId", path=path, line_number=line_number)
        _require_level_list(data["bids"], "snapshot.bids", path=path, line_number=line_number)
        _require_level_list(data["asks"], "snapshot.asks", path=path, line_number=line_number)
        return

    if record_type == "depthUpdate":
        _require_keys(data, ("U", "u", "b", "a"), "depthUpdate payload", path=path, line_number=line_number)
        _require_intish(data["U"], "depthUpdate.U", path=path, line_number=line_number)
        _require_intish(data["u"], "depthUpdate.u", path=path, line_number=line_number)
        if "pu" in data:
            _require_intish(data["pu"], "depthUpdate.pu", path=path, line_number=line_number)
        _require_level_list(data["b"], "depthUpdate.b", path=path, line_number=line_number)
        _require_level_list(data["a"], "depthUpdate.a", path=path, line_number=line_number)
        return

    _require_keys(data, ("p", "q", "m"), "aggTrade payload", path=path, line_number=line_number)
    _require_numberish(data["p"], "aggTrade.p", path=path, line_number=line_number)
    _require_numberish(data["q"], "aggTrade.q", path=path, line_number=line_number)
    _require_bool(data["m"], "aggTrade.m", path=path, line_number=line_number)
