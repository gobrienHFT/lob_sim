"""Hand-counted source projection/freshness tests on SYNTHETIC mechanics."""

from copy import deepcopy
from hashlib import sha256

import pytest

from lob_sim.record.envelope import EventEnvelope
from lob_sim.record.segmented import SegmentedCaptureWriter
from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import iter_records
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_view import source_envelopes, view_specification, write_day_view
from lob_sim.research.replay_engine import ReplayWindow, ResearchReplayEngine, research_config
from lob_sim.research.view_reader import verify_day_view
from test_research_capture_intervals import capture, cfg, SECOND as S, WALL


def view(tmp_path):
    source = capture(tmp_path)
    contract = view_specification(
        file_sha256(source),
        "3" * 64,
        "4" * 64,
        "2025-01-01",
        CaptureClock(0, WALL),
        ReplayWindow("BTCUSDT", S, 2 * S, 11 * S),
    )
    directory = tmp_path / "source_day"
    entry = write_day_view(source, directory, contract, minimum_free_disk_bytes=0)
    path = directory / entry["path"]
    return source, path, entry


def test_every_original_receipt_is_linked_without_inventing_prices_or_exchange_ids(tmp_path):
    source, path, entry = view(tmp_path)
    result = verify_day_view(source, path, entry)
    assert result["source_records"] == len(list(iter_records(source))) - 1  # endpoint source trailer excluded
    derived = list(iter_records(path))
    assert any(row.type == "exchangeInfo" and row.symbol == "ETHUSDT" for row in derived)
    assert any(row.data.get("event") == "research_excluded_source" for row in derived)
    engine = ResearchReplayEngine(
        research_config(
            cfg(),
            mm_enabled=True,
            mm_strategy_profile="research_mm",
            sim_order_latency_ms=0.0,
            sim_cancel_latency_ms=0.0,
        ),
        ReplayWindow(**entry["contract"]["window"]),
        retain_event_trace=True,
        retain_audit_rows=False,
    )
    metrics = engine.run(path)
    assert metrics.quote_count > 0 and set(engine._specs) == {"BTCUSDT", "ETHUSDT"}
    decisions = [row for row in engine.event_trace if row["event_type"] == "decision"]
    assert decisions and all(row["symbol"] == "BTCUSDT" for row in decisions)
    assert not any(6 <= row["ts_local"] < 7 for row in decisions)
    assert any(row["source"] == "research_joint_validity" for row in engine.event_trace)
    assert engine._receive_sequence_gaps == 0 and engine._clock_regressions == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows[1]["payload"]["_research_original"].update(recv_seq=999),
        lambda rows: rows[0].update(instrument="ETHUSDT"),
        lambda rows: rows[-1].update(recv_monotonic_ns=12 * S, recv_wall_ns=WALL + 12 * S),
        lambda rows: next(r for r in rows if r["payload"].get("event") == "research_day_start").update(
            recv_monotonic_ns=2 * S, recv_wall_ns=WALL + 2 * S
        ),
    ],
)
def test_rehashed_view_cannot_change_source_projection_or_control_causality(tmp_path, mutation):
    source, path, entry = view(tmp_path)
    rows = [row.as_dict() for row in source_envelopes(path)]
    mutation(rows)
    repaired = []
    for row in rows:
        row["raw_payload_checksum"] = None
        repaired.append(EventEnvelope.from_dict(row).as_dict())
    with SegmentedCaptureWriter(tmp_path / "tampered", entry["view_id"], compression="none") as writer:
        for row in repaired:
            writer.write(EventEnvelope.from_dict(row))
    path = writer.manifest_path
    entry = deepcopy(entry)
    entry["sha256"] = file_sha256(path)
    entry["events_sha256"] = sha256(
        "".join(EventEnvelope.from_dict(row).to_json() + "\n" for row in repaired).encode()
    ).hexdigest()
    with pytest.raises(ValueError):
        verify_day_view(source, path, entry)


def test_no_unobserved_tail_can_be_created_or_finalized(tmp_path):
    source = capture(tmp_path)
    contract = view_specification(
        file_sha256(source),
        "3" * 64,
        "4" * 64,
        "2025-01-01",
        CaptureClock(0, WALL),
        ReplayWindow("BTCUSDT", S, 2 * S, 12 * S),
    )
    path = tmp_path / "tail"
    with pytest.raises(ValueError, match="beyond actual source"):
        write_day_view(source, path, contract, minimum_free_disk_bytes=0)
    assert not list(path.glob("*.manifest.json")) and list(path.glob("*.partial"))


def test_stale_book_cannot_mark_open_inventory_as_zero_or_resolve_markouts():
    from decimal import Decimal
    from lob_sim.book.local_book import LocalOrderBook
    from lob_sim.book.types import SymbolSpec
    from lob_sim.research.replay_engine import ResearchMetrics
    from lob_sim.sim.metrics import PositionState

    spec = SymbolSpec("BTCUSDT", "0.1", "0.001")
    book = LocalOrderBook("BTCUSDT", spec, 20)
    book.bids, book.asks = {999: 100}, {1001: 100}
    metrics = ResearchMetrics(cfg(), lambda symbol: False)
    metrics.position["BTCUSDT"] = PositionState(1, Decimal("100"))
    metrics.update_unrealized({"BTCUSDT": book}, specs={"BTCUSDT": spec})
    assert metrics.missing_mark_symbols == ("BTCUSDT",)
    assert metrics.inventory_lots("BTCUSDT") == 1
