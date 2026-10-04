"""Freeze behavioral hashes before regime integration; code provenance may change."""

from pathlib import Path

from lob_sim.config import load_config
from lob_sim.replay.adapters import DEFAULT_REPLAY_ADAPTER
from scripts.check_futures_determinism import _run_once


def test_hmm_disabled_preserves_preimplementation_behavioral_hashes() -> None:
    result = _run_once(
        Path("docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson"),
        load_config(".env.example", inherit_environment=False),
        DEFAULT_REPLAY_ADAPTER,
    )
    assert result["summary_sha256"] == "0b66f7bfa1c7d5b87b5659d7ee67f4d6c5d678e127ed5e75001520a45eaf67b5"
    assert result["event_trace_sha256"] == "ee237a382b3957b36bea2a372a9e9c794708d6140b1e3abe34b19ea376f968d0"
