from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.adapters import BinanceUsdMReplayAdapter
from lob_sim.replay.reader import RecordedEvent
from lob_sim.replay.runner import symbol_spec_from_record
from lob_sim.sim.engine import SimulationEngine


class TestVenueReplayAdapter(BinanceUsdMReplayAdapter):
    name = "test_l2"
    venue_label = "TEST_L2"


def test_adapter_boundary_can_tag_non_binance_venue_metadata() -> None:
    record = RecordedEvent(
        ts_local=1.0,
        symbol="TESTUSD",
        type="exchangeInfo",
        data={"tickSize": "0.01", "stepSize": "1", "contractMultiplier": "100"},
    )

    spec = symbol_spec_from_record(record, adapter=TestVenueReplayAdapter())

    assert spec is not None
    assert spec.venue == "TEST_L2"
    assert str(spec.contract_multiplier) == "100"


def test_simulation_engine_uses_injected_replay_adapter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path))
    replay_path = tmp_path / "test_venue.ndjson"
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="TESTUSD",
            type="exchangeInfo",
            data={"tickSize": "0.01", "stepSize": "1"},
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="TESTUSD",
            type="snapshot",
            data=snapshot_payload(100, [("10.00", "3")], [("10.01", "4")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="TESTUSD",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["10.00", "3"]], "a": [["10.01", "4"]]},
        ),
    ]
    replay_path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")

    cfg = replace(load_config(".env.example"), mm_enabled=False, record_dir=tmp_path)
    engine = SimulationEngine(cfg, adapter=TestVenueReplayAdapter())
    metrics = engine.run(replay_path)
    summary = metrics.get_summary(engine._books)

    assert engine._specs["TESTUSD"].venue == "TEST_L2"
    assert summary["event_counts"]["records_processed"] == 3
    assert summary["event_counts"]["depth_changes_applied"] == 0
