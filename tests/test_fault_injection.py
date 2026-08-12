from __future__ import annotations

from scripts.check_fault_injection import run_fault_matrix


def test_fault_matrix_is_green_and_explicitly_non_claiming() -> None:
    result = run_fault_matrix()

    assert result["schema_version"] == "lob_sim.fault_injection.v1"
    assert result["ok"] is True
    assert result["case_count"] == 6
    assert all(case["pass"] is True for case in result["cases"])
    assert any("zero venue-side packet loss" in item for item in result["non_claims"])
