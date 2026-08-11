from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from lob_sim.sim.sinks import AggregateMetricsSink, NullSink, StreamingParquetSink


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
