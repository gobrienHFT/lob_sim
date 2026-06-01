from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.replay.inspection import file_sha256
from scripts import check_futures_determinism as determinism


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_fixture(path: Path) -> Path:
    records = [
        NDJSONRecord(
            ts_local=0.5,
            symbol="BTCUSDT",
            type="exchangeInfo",
            data={
                "symbol": "BTCUSDT",
                "tickSize": "0.1",
                "stepSize": "0.001",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "contractMultiplier": "1",
            },
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(100, [("100.0", "0.002")], [("100.1", "0.003")]),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 95, "u": 105, "pu": 94, "b": [["100.0", "0.001"]], "a": [["100.1", "0.003"]]},
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
    ]
    path.write_text("\n".join(record.to_json() for record in records) + "\n", encoding="utf-8")
    return path


def test_canonical_sha256_is_key_order_stable() -> None:
    left = {"b": [2, 1], "a": {"x": 1}}
    right = {"a": {"x": 1}, "b": [2, 1]}

    assert determinism.canonical_sha256(left) == determinism.canonical_sha256(right)
    assert determinism.canonical_sha256(left) != determinism.canonical_sha256({"a": {"x": 2}, "b": [2, 1]})


def test_check_determinism_hashes_repeated_simulation_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "data"))
    input_file = _write_fixture(tmp_path / "fixture.ndjson")

    result = determinism.check_determinism(input_file, str(REPO_ROOT / ".env.example"), runs=2)

    assert result["schema_version"] == determinism.DETERMINISM_SCHEMA_VERSION
    assert result["deterministic"] is True
    assert result["runs"] == 2
    assert result["mismatches"] == []
    assert result["compared_surfaces"] == ["metrics_summary", "event_trace"]
    assert result["feed_adapter"] == {
        "name": "binance_usdm",
        "venue_label": "BINANCE_USDM",
        "supported_record_types": ["aggTrade", "depthUpdate", "exchangeInfo", "snapshot"],
    }
    assert result["instrument_specs"]["BTCUSDT"]["contract_multiplier"] == "1"
    assert result["baseline"]["summary_sha256"] == result["per_run"][0]["summary_sha256"]
    assert result["baseline"]["event_trace_sha256"] == result["per_run"][0]["event_trace_sha256"]
    assert result["per_run"][0]["summary_sha256"] == result["per_run"][1]["summary_sha256"]
    assert result["per_run"][0]["event_trace_sha256"] == result["per_run"][1]["event_trace_sha256"]
    assert result["per_run"][0]["event_trace_count"] > 0
    assert "market_record" in result["per_run"][0]["event_trace_type_counts"]
    assert result["input_sha256"] == file_sha256(input_file)
    assert result["config_digest"]
    assert set(result["source"]) == {"git_commit", "git_branch", "git_dirty"}


def test_check_determinism_flags_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "data"))
    input_file = tmp_path / "fixture.ndjson"
    input_file.write_text("{}\n", encoding="utf-8")
    calls = 0

    def fake_run_once(_input_file: Path, _cfg: Any, _adapter: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        summary = {
            "fill_count": calls,
            "quote_count": 1,
            "markout_events": [],
            "fill_source_counts": {},
            "order_lifecycle_counts": {},
        }
        event_trace = [{"event_type": "market_record", "seq": calls}]
        return {
            "summary": summary,
            "event_trace": event_trace,
            "summary_sha256": determinism.canonical_sha256(summary),
            "event_trace_sha256": determinism.canonical_sha256(event_trace),
            "instrument_specs": {},
        }

    monkeypatch.setattr(determinism, "_run_once", fake_run_once)

    result = determinism.check_determinism(input_file, str(REPO_ROOT / ".env.example"), runs=2)

    assert result["deterministic"] is False
    assert result["mismatches"] == [
        {
            "run_index": 2,
            "summary_matches": False,
            "event_trace_matches": False,
            "summary_sha256": result["per_run"][1]["summary_sha256"],
            "event_trace_sha256": result["per_run"][1]["event_trace_sha256"],
        }
    ]


def test_check_determinism_requires_at_least_two_runs(tmp_path: Path) -> None:
    input_file = tmp_path / "fixture.ndjson"
    input_file.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least 2"):
        determinism.check_determinism(input_file, str(REPO_ROOT / ".env.example"), runs=1)
