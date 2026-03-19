from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.refresh_futures_showcase import refresh_futures_showcase


def test_futures_showcase_refresh_produces_non_empty_outputs(tmp_path: Path) -> None:
    showcase_dir = tmp_path / "futures_replay_walkthrough"
    paths = refresh_futures_showcase(showcase_dir)

    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["fill_count"] >= 1
    assert summary["quote_count"] >= 1
    assert summary["output_files"]["summary"].endswith("summary.json")
    assert summary["output_files"]["trades"].endswith("trades.csv")

    with paths["trades"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"ts_local", "symbol", "side", "price", "qty", "queue_ahead_lots"} <= set(rows[0])
