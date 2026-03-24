from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.compare_futures_strategy_profiles import compare_profiles


REFERENCE_DOC = REPO_ROOT / "docs" / "strategy_results" / "futures_strategy_profile_reference.md"
ENV_PATH = REPO_ROOT / ".env.example"
INPUT_CANDIDATES = (
    REPO_ROOT / "docs" / "sample_outputs" / "futures_recorded_clip_case" / "input_clip.ndjson",
    REPO_ROOT / "docs" / "sample_outputs" / "futures_replay_walkthrough" / "input_fixture.ndjson",
)
REFRESH_COMMAND = "python scripts/refresh_futures_strategy_profile_reference.py"
COMPARISON_COMMAND_TEMPLATE = (
    "python experiments/compare_futures_strategy_profiles.py --file {input_file} --env .env.example"
)
REFERENCE_FIELDS: list[tuple[str, str]] = [
    ("quote_count", "quote_count"),
    ("cancel_count", "cancel_count"),
    ("fill_count", "fill_count"),
    ("fill_rate", "fill_rate"),
    ("fill_from_top_count", "fill_from_top_count"),
    ("avg_queue_ahead_lots", "avg_queue_ahead_lots"),
    ("avg_markout_1s", "avg_markout_1s"),
    ("adverse_fill_rate_1s", "adverse_fill_rate_1s"),
    ("inventory_stdev", "inventory_stdev"),
    ("realized_pnl", "realized_pnl"),
    ("unrealized_pnl", "unrealized_pnl"),
    ("total_pnl", "total_pnl"),
    ("kill_switch_triggered", "kill_switch_triggered"),
]


def _repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _find_committed_input() -> Path:
    for candidate in INPUT_CANDIDATES:
        if candidate.exists():
            return candidate
    expected = ", ".join(_repo_relative(path) for path in INPUT_CANDIDATES)
    raise FileNotFoundError(f"Missing committed strategy-profile input. Expected one of: {expected}")
def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _render_table(result: dict) -> str:
    rows = [
        "| Metric | Baseline | `layered_mm` |",
        "|---|---:|---:|",
    ]
    for label, key in REFERENCE_FIELDS:
        rows.append(
            f"| {label} | {_format_value(result['baseline'][key])} | {_format_value(result['candidate'][key])} |"
        )
    return "\n".join(rows)


def _render_reference_doc(result: dict, input_path: Path) -> str:
    input_file = _repo_relative(input_path)
    comparison_command = COMPARISON_COMMAND_TEMPLATE.format(input_file=input_file)
    interpretation = (
        "On this short committed BTCUSDT clip, `layered_mm` quotes and refreshes more often than the baseline "
        f"({result['baseline']['quote_count']} quotes versus {result['candidate']['quote_count']}). It also changes "
        f"fill frequency ({result['baseline']['fill_count']} baseline fills versus "
        f"{result['candidate']['fill_count']} for `layered_mm`) and the resulting inventory/PnL mix. The clip is "
        "intentionally small, so the comparison is useful for inspecting profile behavior, not for making broad "
        "performance claims."
    )
    return "\n".join(
        [
            "# Futures Strategy Profile Reference",
            "",
            f"- Compared profiles: `{result['baseline_profile']}` vs `{result['candidate_profile']}`",
            f"- Committed input: `{input_file}`",
            "- Input note: this committed recorded clip is short, so the comparison is intentionally modest.",
            "- Refresh command:",
            "",
            "```bash",
            REFRESH_COMMAND,
            "```",
            "",
            "- Underlying comparison command:",
            "",
            "```bash",
            comparison_command,
            "```",
            "",
            "## Baseline vs Layered",
            "",
            _render_table(result),
            "",
            "## Interpretation",
            "",
            interpretation,
            "",
            "## Scope Note",
            "",
            "This is a strategy-profile comparison on one committed replay input. It is not a claim of alpha, "
            "production profitability, or stronger fill realism than the repo's existing passive-fill assumptions.",
            "",
        ]
    )


def refresh_reference(reference_doc: Path = REFERENCE_DOC) -> dict[str, Path]:
    input_path = _find_committed_input()
    result = compare_profiles(input_path, str(ENV_PATH), "layered_mm")

    if result["baseline"]["quote_count"] <= 0:
        raise RuntimeError("Baseline profile did not produce any quotes on the committed input.")
    if result["candidate"]["quote_count"] <= 0:
        raise RuntimeError("layered_mm did not produce any quotes on the committed input.")

    reference_doc.parent.mkdir(parents=True, exist_ok=True)
    reference_doc.write_text(_render_reference_doc(result, input_path), encoding="utf-8")
    return {
        "input": input_path,
        "reference": reference_doc,
    }


def main() -> int:
    paths = refresh_reference()
    print("Refreshed futures strategy profile reference")
    for label, path in paths.items():
        print(f"- {label}: {_repo_relative(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
