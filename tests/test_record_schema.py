from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.record.schema import RecordValidationError
from lob_sim.replay.inspection import inspect_stream
from lob_sim.replay.reader import iter_records


def test_iter_records_rejects_malformed_payload_with_location(tmp_path: Path) -> None:
    path = tmp_path / "bad_stream.ndjson"
    valid = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="exchangeInfo",
        data={"tickSize": "0.1", "stepSize": "0.001"},
    )
    path.write_text(
        valid.to_json()
        + "\n"
        + json.dumps({"ts_local": 2.0, "symbol": "BTCUSDT", "type": "snapshot", "data": {"bids": [], "asks": []}})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    message = str(exc.value)
    assert "bad_stream.ndjson:2" in message
    assert "lastUpdateId" in message


def test_iter_records_rejects_fractional_sequence_ids(tmp_path: Path) -> None:
    path = tmp_path / "bad_sequence.ndjson"
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="depthUpdate",
        data={"U": "100.5", "u": 101, "pu": 99, "b": [], "a": []},
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    assert "depthUpdate.U must be an integer" in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recvSeq", "100"),
        ("recvMonotonicNs", 100.0),
        ("streamEpoch", True),
        ("syncEpoch", -1),
    ],
)
def test_iter_records_rejects_coercible_schema_v3_capture_metadata(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / f"bad_capture_metadata_{field}.ndjson"
    capture = {
        "recvSeq": 100,
        "recvMonotonicNs": 1_000,
        "streamEpoch": 1,
        "syncEpoch": 1,
        "route": "public",
    }
    capture[field] = value
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="depthUpdate",
        data={"U": 100, "u": 100, "pu": 99, "b": [], "a": [], "_capture": capture},
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    assert f"depthUpdate payload._capture.{field} must be an exact non-negative integer" in str(exc.value)


def test_iter_records_rejects_coercible_capture_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "bad_capture_schema_version.ndjson"
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="*",
        type="captureMeta",
        data={"schemaVersion": "3", "clock": "receive_time"},
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError, match="captureMeta.schemaVersion must be an exact non-negative integer"):
        list(iter_records(path))


def test_iter_records_rejects_empty_schema_v3_route(tmp_path: Path) -> None:
    path = tmp_path / "bad_capture_route.ndjson"
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="captureEvent",
        data={
            "event": "connect",
            "route": "",
            "_capture": {
                "recvSeq": 1,
                "recvMonotonicNs": 1_000,
                "streamEpoch": 1,
                "syncEpoch": 1,
                "route": "public",
            },
        },
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError, match="captureEvent.route must be a non-empty string"):
        list(iter_records(path))


@pytest.mark.parametrize(
    ("record_type", "data", "field"),
    [
        ("exchangeInfo", {"tickSize": "NaN", "stepSize": "0.001"}, "exchangeInfo.tickSize"),
        ("snapshot", snapshot_payload(100, [("Infinity", "0.001")], [("100.1", "0.001")]), "snapshot.bids[0].price"),
        (
            "depthUpdate",
            {"U": 100, "u": 100, "pu": 99, "b": [["100.0", "-Infinity"]], "a": []},
            "depthUpdate.b[0].quantity",
        ),
        ("aggTrade", {"p": "100.0", "q": "NaN", "m": True}, "aggTrade.q"),
    ],
)
def test_iter_records_rejects_nonfinite_numeric_fields(
    tmp_path: Path,
    record_type: str,
    data: dict,
    field: str,
) -> None:
    path = tmp_path / f"nonfinite_{record_type}.ndjson"
    path.write_text(
        NDJSONRecord(ts_local=1.0, symbol="BTCUSDT", type=record_type, data=data).to_json() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    assert field in str(exc.value)


def test_iter_records_rejects_nonfinite_record_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "nonfinite_timestamp.ndjson"
    path.write_text(
        NDJSONRecord(
            ts_local=float("nan"),
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={"tickSize": "0.1", "stepSize": "0.001"},
        ).to_json()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordValidationError, match="record.ts_local must be finite"):
        list(iter_records(path))


def test_inspect_stream_reports_counts_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "stream.ndjson"
    records = [
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={
                "tickSize": "0.1",
                "stepSize": "0.001",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "contractMultiplier": "1",
                "venue": "BINANCE_USDM",
            },
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.001")], [("100.1", "0.001")]),
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    inspection = inspect_stream(path).as_dict()

    assert inspection["records"] == 2
    assert inspection["counts_by_type"] == {"exchangeInfo": 1, "snapshot": 1}
    assert inspection["counts_by_symbol"] == {"BTCUSDT": 2}
    assert inspection["duration_seconds"] == pytest.approx(1.0)
    assert inspection["symbol_specs"]["BTCUSDT"] == {
        "tick_size": "0.1",
        "step_size": "0.001",
        "quantity_unit": "BTC",
        "price_currency": "USDT",
        "contract_multiplier": "1",
        "venue": "BINANCE_USDM",
    }
    assert inspection["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_iter_records_rejects_malformed_optional_exchange_info_metadata(tmp_path: Path) -> None:
    path = tmp_path / "bad_exchange_info_metadata.ndjson"
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="exchangeInfo",
        data={"tickSize": "0.1", "stepSize": "0.001", "quoteAsset": 123},
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    assert "exchangeInfo.quoteAsset must be a string" in str(exc.value)


def test_iter_records_rejects_malformed_contract_multiplier(tmp_path: Path) -> None:
    path = tmp_path / "bad_contract_multiplier.ndjson"
    record = NDJSONRecord(
        ts_local=1.0,
        symbol="BTCUSDT",
        type="exchangeInfo",
        data={"tickSize": "0.1", "stepSize": "0.001", "contractMultiplier": "not-numeric"},
    )
    path.write_text(record.to_json() + "\n", encoding="utf-8")

    with pytest.raises(RecordValidationError) as exc:
        list(iter_records(path))

    assert "exchangeInfo.contractMultiplier must be numeric" in str(exc.value)
