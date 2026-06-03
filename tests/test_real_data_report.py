from __future__ import annotations

import json
from pathlib import Path

import pytest

from lob_sim.replay.inspection import file_sha256
from scripts.run_real_data_report import RAW_DATA_POLICY, REAL_DATA_REPORT_SCHEMA_VERSION, REPO_ROOT, run_report


FIXTURE = REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "input_fixture.ndjson"


def test_real_data_report_generation_writes_schema_and_report_only_publish(tmp_path: Path) -> None:
    publish_dir = tmp_path / "published"

    paths = run_report(
        input_path=FIXTURE,
        env_path=".env.example",
        out_dir=tmp_path / "outputs",
        label="tiny_fixture",
        runs=1,
        publish_dir=publish_dir,
    )

    assert paths["pack_dir"].is_dir()
    assert paths["inspection_json"].is_file()
    assert paths["audit_json"].is_file()
    assert paths["benchmark_json"].is_file()
    assert paths["published_report_md"] == publish_dir / "tiny_fixture.md"
    assert paths["published_report_json"] == publish_dir / "tiny_fixture.json"
    assert sorted(path.name for path in publish_dir.iterdir()) == ["tiny_fixture.json", "tiny_fixture.md"]

    payload = json.loads(paths["published_report_json"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == REAL_DATA_REPORT_SCHEMA_VERSION
    assert payload["raw_data_policy"] == RAW_DATA_POLICY
    assert payload["input"]["sha256"] == file_sha256(FIXTURE)
    assert payload["input"]["file_size_bytes"] == FIXTURE.stat().st_size
    assert payload["input"]["symbol"] == "BTCUSDT"
    assert payload["event_counts"]["records_processed"] == 6
    assert payload["public_trade_source_counts"] == {"unknown": 1}
    assert payload["fills"]["fill_count"] == 1
    assert set(payload["fills"]["fill_source_counts"]) == {"depth_update", "agg_trade", "taker_order"}
    assert "quote_fill_probability" in payload["fills"]
    assert "fills_per_quote_request" in payload["fills"]
    assert "fills_per_arrived_order" in payload["fills"]
    assert set(payload["markout_by_fill_source"]) == {"depth_update", "agg_trade", "taker_order"}
    assert payload["target_window"]["meets_target"] is False
    assert payload["target_window"]["env_overrides"]["COLLECT_SECONDS"] == "1800"
    assert payload["target_window"]["longer_run_commands"]
    assert payload["audit"]["ok"] is True
    assert payload["benchmark"]["schema_version"] == "lob_sim.reviewer_benchmark.v1"

    markdown = paths["published_report_md"].read_text(encoding="utf-8")
    assert "Plain Interpretation" in markdown
    assert "Negative or positive PnL is not the point" in markdown
    assert "Raw public trade event types inside `aggTrade` records" in markdown
    assert "raw NDJSON tape is not committed" in markdown
    assert "Meets 10-30 minute target: `false`" in markdown
    assert "python scripts/run_real_data_report.py" in markdown
    assert not any(path.suffix in {".csv", ".ndjson", ".gz"} for path in publish_dir.rglob("*"))


def test_real_data_report_validates_input_and_keeps_local_packs_out_of_docs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Real-data input does not exist"):
        run_report(
            input_path=tmp_path / "missing.ndjson",
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="missing",
            runs=1,
        )

    wrong_suffix = tmp_path / "raw.txt"
    wrong_suffix.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="NDJSON or NDJSON.GZ"):
        run_report(
            input_path=wrong_suffix,
            env_path=".env.example",
            out_dir=tmp_path / "outputs",
            label="wrong_suffix",
            runs=1,
        )

    with pytest.raises(ValueError, match="--out-dir writes local audit packs"):
        run_report(
            input_path=FIXTURE,
            env_path=".env.example",
            out_dir=REPO_ROOT / "docs" / "real_data_runs" / "bad_local_pack",
            label="bad",
            runs=1,
        )
