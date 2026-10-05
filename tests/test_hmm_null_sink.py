"""No-op copy elimination must preserve canonical state and real sink isolation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from lob_sim.regime import execution, observation, quotes, risk
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.sinks import NullSink
from test_hmm_execution import fill_row, stage
from test_hmm_observation import settings
from test_hmm_policy import configuration, policy_tape
from test_hmm_risk import row as risk_row

COMPONENTS = ("observation", "risk", "quotes", "execution")


class CustomDiscard(NullSink):
    """Subclass deliberately exercises the old copying/write path."""

    def __init__(self, *, mutate=False):
        self.writes = 0
        self.mutate = mutate

    def write(self, value):
        self.writes += 1
        if self.mutate:
            # Erase the sink-owned row, including any nested references.
            for item in value.values():
                if isinstance(item, (dict, list)):
                    item.clear()
            value.clear()


def emit(component, sink):
    if component == "observation":
        subject = observation.RegimeObserver(settings(), sink)
        # Low-level emission contract; full valid causal observations below.
        subject._write({"posterior": [0.9, 0.1], "features": [1.0]})
    elif component == "risk":
        subject = risk.RegimeRiskAudit("a" * 64, "BTCUSDT", 2, sink)
        subject.observe(risk_row(1))
    elif component == "quotes":
        subject = quotes.RegimeQuoteAudit("BTCUSDT", ("STATE_0", "STATE_1", "UNCONFIRMED", "UNAVAILABLE"), 4, sink)
        subject.schedule("STATE_0", 1, "bid", "base", 3)
    else:
        subject = execution.RegimeExecutionAudit("a" * 64, "BTCUSDT", 2, (), 4, sink)
        subject.on_fill(1, fill_row(), subject.at_fill(None, stage(0, 3)))
    return subject


@pytest.mark.parametrize("component", COMPONENTS)
def test_exact_null_eliminates_only_the_discarded_copy(component, monkeypatch):
    module = {"observation": observation, "risk": risk, "quotes": quotes, "execution": execution}[component]
    original = module.deepcopy
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(module, "deepcopy", counted)
    null = emit(component, NullSink())
    null_calls = len(calls)
    calls.clear()
    sink = CustomDiscard()
    custom = emit(component, sink)
    custom_calls = len(calls)
    monkeypatch.setattr(module, "deepcopy", original)
    assert null_calls == custom_calls - 1
    assert sink.writes == 1
    assert null._trace_count == custom._trace_count == 1
    assert null._trace_sha256 == custom._trace_sha256
    assert null.checkpoint() == custom.checkpoint()


@pytest.mark.parametrize("mode", ["observe", "policy"])
@pytest.mark.parametrize("mutate", [False, True])
def test_custom_subclasses_keep_writes_isolation_full_state_and_traces(tmp_path, mode, mutate):
    path = policy_tape(tmp_path / "market.ndjson")
    config = configuration(mm_requote_ms=500)
    if mode == "observe":
        config = replace(
            config, hmm=replace(config.hmm, mode="observe", policy=None), mm_strategy_profile="research_mm"
        )
    baseline = SimulationEngine(config)
    baseline.run(path)
    sinks = [CustomDiscard(mutate=mutate) for _ in COMPONENTS]
    other = SimulationEngine(
        config,
        regime_sink=sinks[0],
        regime_risk_sink=sinks[1],
        regime_quote_sink=sinks[2],
        regime_execution_sink=sinks[3],
    )
    other.run(path)
    assert all(sink.writes > 0 for sink in sinks)
    assert baseline.metrics.fill_count > 0
    assert other.event_trace == baseline.event_trace
    assert other.state_sha256() == baseline.state_sha256()
    assert other._checkpoint_mutable_state() == baseline._checkpoint_mutable_state()
    for left, right in (
        (baseline.regime, other.regime),
        (baseline.hmm_risk, other.hmm_risk),
        (baseline.hmm_execution, other.hmm_execution),
    ):
        assert right.checkpoint() == left.checkpoint()
        assert right.summary() == left.summary()


@pytest.mark.parametrize("component", COMPONENTS)
def test_failing_null_subclass_is_not_skipped(component):
    class Failing(NullSink):
        def write(self, value):
            raise OSError("intentional audit failure")

    with pytest.raises(OSError, match="intentional audit failure"):
        emit(component, Failing())
