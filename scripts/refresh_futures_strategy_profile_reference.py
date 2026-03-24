from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.compare_futures_strategy_profiles import COMPARISON_FIELDS, compare_profiles


COMMITTED_INPUT = REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "input_clip.ndjson"
REFERENCE_DOC = REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md"
ENV_PATH = ".env.example"
CANDIDATE_PROFILE = "layered_mm"


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _comparison_table(result: dict) -> str:
    rows = [
        "| Metric | Baseline | `layered_mm` |",
        "|---|---:|---:|",
    ]
    for _label, key in COMPARISON_FIELDS:
        rows.append(
            f"| {key} | {_format_value(result['baseline'][key])} | {_format_value(result['candidate'][key])} |"
        )
    rows.extend(
        [
            f"| total_pnl | {_format_value(result['baseline']['total_pnl'])} | {_format_value(result['candidate']['total_pnl'])} |",
            f"| fill_rate | {_format_value(result['baseline']['fill_rate'])} | {_format_value(result['candidate']['fill_rate'])} |",
            f"| adverse_fill_rate_1s | {_format_value(result['baseline']['adverse_fill_rate_1s'])} | {_format_value(result['candidate']['adverse_fill_rate_1s'])} |",
        ]
    )
    return "\n".join(rows)


def _build_reference_doc(result: dict) -> str:
    return "\n".join(
        [
            "# Futures Strategy Profile Reference",
            "",
            "- Input file: `docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson`",
            "- Input status: committed recorded BTCUSDT clip",
            "- Compared profiles: `baseline` vs `layered_mm`",
            "- Exact command:",
            "",
            "```bash",
            "python experiments/compare_futures_strategy_profiles.py --file docs/sample_outputs/futures_recorded_clip_case/input_clip.ndjson --env .env.example",
            "```",
            "",
            "## Baseline vs Layered",
            "",
            _comparison_table(result),
            "",
            "## Interpretation",
            "",
            "This committed recorded clip is short, so the comparison mostly shows quoting intensity and fill selection rather than a broad performance claim. On this input, `layered_mm` refreshed and quoted more aggressively than the baseline and picked up one fill where the baseline stayed flat, so this is a strategy-profile comparison rather than a claim of alpha.",
            "",
        ]
    )


def main() -> int:
    if not COMMITTED_INPUT.exists():
        raise FileNotFoundError(f"Missing committed strategy-profile input: {COMMITTED_INPUT}")

    result = compare_profiles(COMMITTED_INPUT, ENV_PATH, CANDIDATE_PROFILE)
    REFERENCE_DOC.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_DOC.write_text(_build_reference_doc(result), encoding="utf-8")

    print(f"Refreshed strategy-profile reference in {REFERENCE_DOC}")
    print(f"- input: {COMMITTED_INPUT}")
    print(f"- output: {REFERENCE_DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
