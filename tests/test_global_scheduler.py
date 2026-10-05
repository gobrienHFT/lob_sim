"""Schema-v3 timers must not wait for the next receipt of their own symbol."""

import json
from dataclasses import replace

import pytest

from lob_sim.sim.engine import SimulationEngine
from test_hmm_dataset import cfg
from test_hmm_economics import two_symbol_tape


def configuration():
    return replace(cfg(), symbols=("BTCUSDT", "ETHUSDT"))


@pytest.mark.parametrize("quiet_symbol", [None, "BTCUSDT", "ETHUSDT"])
def test_all_active_timers_run_before_next_global_market_observation(tmp_path, quiet_symbol):
    path = two_symbol_tape(tmp_path / "input.ndjson")
    if quiet_symbol is not None:
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records = [
            r
            for r in records
            if r["symbol"] != quiet_symbol or r["data"]["_capture"]["recvMonotonicNs"] <= 2_000_000_000
        ]
        for seq, record in enumerate(records):
            record["data"]["_capture"]["recvSeq"] = seq
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    engine = SimulationEngine(configuration())  # No HMM: this is a core regression.
    engine.run(path)
    assert engine.metrics.book_gap_count == engine._receive_sequence_gaps == 0
    decisions = [r for r in engine.event_trace if r["event_type"] == "decision"]
    assert {r["symbol"] for r in decisions} == {"BTCUSDT", "ETHUSDT"}
    assert all(a["ts_local"] <= b["ts_local"] for a, b in zip(engine.event_trace, engine.event_trace[1:]))
    first_later_market = next(
        i for i, r in enumerate(engine.event_trace) if r["event_type"] == "market_record" and r["ts_local"] == 3
    )
    for symbol in ("BTCUSDT", "ETHUSDT"):
        assert any(
            r["symbol"] == symbol and r["event_type"] == "decision" and r["ts_local"] == 2.04
            for r in engine.event_trace[:first_later_market]
        )


@pytest.mark.parametrize("cut", [13, 17])
def test_global_timer_progress_is_checkpoint_reproducible(tmp_path, cut):
    path = two_symbol_tape(tmp_path / "input.ndjson")
    complete, partial, resumed = (SimulationEngine(configuration()) for _ in range(3))
    complete.run(path)
    checkpoint = tmp_path / "state.json"
    partial.run(path, checkpoint_path=checkpoint, stop_after_records=cut)
    resumed.run(path, resume_from=checkpoint)
    assert resumed.state_sha256() == complete.state_sha256()
    assert resumed.event_trace == complete.event_trace
