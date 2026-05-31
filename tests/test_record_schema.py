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
