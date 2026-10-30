"""Short public diagnostics must not counterfeit admission or joint metadata."""

from copy import deepcopy

import pytest

from experiments.diagnose_short_capture_execution import validate_diagnostic_report


def report():
    return {
        "code_identity": {"sha256": "code"},
        "input_sha256": "input",
        "research_usable": True,
        "symbols": {"BTCUSDT": {}, "ETHUSDT": {}},
        "instruments": {"BTCUSDT": {}, "ETHUSDT": {}},
        "first_logical_ns": 100,
        "last_logical_ns": 69_000_000_100,
    }


def test_short_joint_public_clip_is_diagnostic_only():
    original = report()
    before = deepcopy(original)
    assert validate_diagnostic_report(original, "input", {"sha256": "code"}) is None
    assert original == before  # Validation never upgrades eligibility/provenance.


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_identity", {"sha256": "changed"}),
        ("input_sha256", "changed"),
        ("research_usable", False),
        ("research_usable", 1),
        ("symbols", {"BTCUSDT": {}}),
        ("instruments", {"BTCUSDT": {}}),
        ("last_logical_ns", 30_000_000_100),
        ("last_logical_ns", 600_000_000_101),
    ],
)
def test_refuses_changed_unusable_unpaired_or_out_of_scope_report(field, value):
    broken = report()
    broken[field] = value
    with pytest.raises(ValueError):
        validate_diagnostic_report(broken, "input", {"sha256": "code"})
