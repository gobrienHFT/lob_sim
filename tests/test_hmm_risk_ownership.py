"""Independent caller/sink isolation guards for risk-row copy optimization."""

from copy import deepcopy

import pytest

from lob_sim.record.envelope import ValidityState
from lob_sim.regime.risk import RegimeRiskAudit
from test_hmm_risk import row


def boundary(time):
    result = row(time)
    result["stage"]["validity"] = ValidityState(True, True, True, True).as_dict()
    return result


def damage(data):
    data["inventory_lots"] = 999
    data["stage"]["epochs"][0] = 999
    data["stage"]["posterior"][0] = 0.0
    data["stage"]["validity"]["book_valid"] = False


def test_risk_anchor_owns_every_mutable_field_from_caller():
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    supplied = boundary(10)
    subject.observe(supplied)
    before = deepcopy(subject.checkpoint())
    damage(supplied)
    assert subject.checkpoint() == before


def test_custom_sink_cannot_change_anchor_hash_or_next_interval():
    class MutatingSink:
        memory_bounded = True

        def write(self, value):
            damage(value)

    reference = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2, MutatingSink())
    for time in (10, 20, 30):
        reference.observe(boundary(time))
        subject.observe(boundary(time))
    assert subject.checkpoint() == reference.checkpoint()
    assert subject.summary() == reference.summary()


def test_invalid_row_remains_atomic_and_checkpoint_anchors_remain_isolated():
    subject = RegimeRiskAudit("a" * 64, "BTCUSDT", 2)
    subject.observe(boundary(10))
    before = deepcopy(subject.checkpoint())
    invalid = boundary(20)
    invalid["live_bid_lots"] = True
    with pytest.raises(ValueError):
        subject.observe(invalid)
    assert subject.checkpoint() == before
    clone = subject.validated_copy(before)
    damage(before["anchor"])
    assert clone.checkpoint() == subject.checkpoint()
