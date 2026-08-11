from __future__ import annotations

import csv
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from lob_sim.sim.export import MARKOUT_AUDIT_FIELDS, iter_markout_audit_rows
from lob_sim.sim.sinks import AggregateMetricsSink, NullSink, StreamingCsvSink, StreamingParquetSink


def test_null_and_aggregate_sinks_do_not_retain_events() -> None:
    null = NullSink()
    aggregate = AggregateMetricsSink()
    for event in (
        {"event_type": "fill", "symbol": "BTCUSDT"},
        {"event_type": "fill", "symbol": "ETHUSDT"},
        {"event_type": "cancel", "symbol": "BTCUSDT"},
    ):
        null.write(event)
        aggregate.write(event)

    assert aggregate.snapshot() == {
        "count": 3,
        "by_event_type": {"cancel": 1, "fill": 2},
        "by_symbol": {"BTCUSDT": 2, "ETHUSDT": 1},
    }


def test_streaming_parquet_sink_is_batch_bounded_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "audit.parquet"
    with StreamingParquetSink(path, batch_size=2) as sink:
        sink.write({"seq": 1, "symbol": "BTCUSDT", "qty_lots": 2})
        sink.write({"seq": 2, "symbol": "BTCUSDT", "qty_lots": 1})
        assert sink._rows == []
        sink.write({"seq": 3, "symbol": "ETHUSDT", "qty_lots": 4})

    assert path.exists()
    assert not (tmp_path / "audit.parquet.partial").exists()
    assert pq.read_table(path).to_pylist() == [
        {"seq": 1, "symbol": "BTCUSDT", "qty_lots": 2},
        {"seq": 2, "symbol": "BTCUSDT", "qty_lots": 1},
        {"seq": 3, "symbol": "ETHUSDT", "qty_lots": 4},
    ]


def test_failed_parquet_context_never_finalizes(tmp_path: Path) -> None:
    path = tmp_path / "failed.parquet"
    try:
        with StreamingParquetSink(path, batch_size=1) as sink:
            sink.write({"seq": 1})
            raise RuntimeError("stop")
    except RuntimeError:
        pass

    assert not path.exists()
    assert (tmp_path / "failed.parquet.partial").exists()


def test_streaming_csv_sink_fsyncs_and_atomically_publishes_canonical_rows(tmp_path: Path) -> None:
    path = tmp_path / "audit.csv"
    with StreamingCsvSink(path, ("seq", "details", "missing"), flush_every=1) as sink:
        sink.write({"seq": 1, "details": {"z": 2, "a": [1, None]}, "missing": None})
        assert sink.count == 1
        assert sink.partial_path.exists()
        assert not path.exists()

    assert path.exists()
    assert not sink.partial_path.exists()
    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row == {"seq": "1", "details": '{"a":[1,null],"z":2}', "missing": ""}
    assert json.loads(row["details"]) == {"a": [1, None], "z": 2}


def test_failed_csv_context_leaves_visible_partial_and_no_final(tmp_path: Path) -> None:
    path = tmp_path / "failed.csv"
    with pytest.raises(RuntimeError, match="stop"):
        with StreamingCsvSink(path, ("seq",)) as sink:
            sink.write({"seq": 1})
            raise RuntimeError("stop")

    assert not path.exists()
    assert sink.partial_path.exists()
    with pytest.raises(RuntimeError, match="closed"):
        sink.write({"seq": 2})


def test_streaming_csv_sink_supports_two_phase_finalize(tmp_path: Path) -> None:
    path = tmp_path / "two_phase.csv"
    sink = StreamingCsvSink(path, ("seq",))
    sink.write({"seq": 1})
    sink.prepare()

    assert sink.partial_path.exists()
    assert not path.exists()
    sink.commit()
    assert path.exists()
    assert not sink.partial_path.exists()


def test_streaming_csv_sink_refuses_to_overwrite_stale_partial(tmp_path: Path) -> None:
    path = tmp_path / "stale.csv"
    partial = tmp_path / "stale.csv.partial"
    partial.write_text("incomplete\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        StreamingCsvSink(path, ("seq",))


def test_invalidated_markout_round_trips_nulls_for_audit_hashing(tmp_path: Path) -> None:
    path = tmp_path / "markouts.csv"
    event = {
        "symbol": "BTCUSDT",
        "side": "bid",
        "fill_source": "agg_trade",
        "regime": "tight_sell",
        "fill_price": "100.0",
        "price_tick": 1000,
        "qty": "0.001",
        "qty_lots": 1,
        "order_id": "o1",
        "fill_mid": "100.05",
        "mid_after": None,
        "markout": None,
        "contract_multiplier": "1",
        "adverse": None,
        "horizon": 1.0,
        "ts_local": 3.0,
        "deadline_ts": 4.0,
        "markout_ts_local": 3.5,
        "resolution_lag_seconds": None,
        "status": "invalidated",
        "invalid_reason": "book_gap",
    }
    with StreamingCsvSink(path, MARKOUT_AUDIT_FIELDS) as sink:
        sink.write(event)

    assert list(iter_markout_audit_rows(path)) == [event]
