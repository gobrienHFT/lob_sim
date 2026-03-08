from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OUTPUT = Path("docs") / "futures_replay_summary.md"


def _latest_summary(outputs_dir: Path) -> Path:
    candidates = sorted(outputs_dir.glob("summary_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No summary_*.json found in {outputs_dir}")
    return candidates[0]


def _fmt_num(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _build_markdown(summary: dict[str, object], summary_file: Path) -> str:
    fills = list(summary.get("fills", [])) if isinstance(summary.get("fills"), list) else []
    sample_fills = fills[:5]

    lines = [
        "# Futures replay summary",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Source summary: `{summary_file.as_posix()}`",
        "",
        "## Headline metrics",
        "",
        f"- `total_pnl`: {_fmt_num(summary.get('total_pnl', 0))}",
        f"- `realized_pnl`: {_fmt_num(summary.get('realized_pnl', 0))}",
        f"- `unrealized_pnl`: {_fmt_num(summary.get('unrealized_pnl', 0))}",
        f"- `max_drawdown`: {_fmt_num(summary.get('max_drawdown', 0))}",
        f"- `fill_count`: {_fmt_num(summary.get('fill_count', 0))}",
        f"- `fill_rate`: {_fmt_num(summary.get('fill_rate', 0))}",
        f"- `avg_spread_captured`: {_fmt_num(summary.get('avg_spread_captured', 0))}",
        f"- `avg_inventory`: {_fmt_num(summary.get('avg_inventory', 0))}",
        f"- `inventory_stdev`: {_fmt_num(summary.get('inventory_stdev', 0))}",
        f"- `total_fees`: {_fmt_num(summary.get('total_fees', 0))}",
        "",
        "## Sample fills",
        "",
    ]
    if not sample_fills:
        lines.append("- No fills captured in this run.")
    else:
        lines.extend(
            [
                "| ts_local | symbol | side | price | qty | maker |",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for fill in sample_fills:
            lines.append(
                f"| {fill.get('ts_local', '')} | {fill.get('symbol', '')} | {fill.get('side', '')} | "
                f"{fill.get('price', '')} | {fill.get('qty', '')} | {fill.get('maker', '')} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is an offline replay of L2 and aggTrade data with queue-aware passive fill approximation.",
            "- No live orders are sent and no real capital is at risk.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate docs/futures_replay_summary.md from simulator output")
    parser.add_argument("--summary", help="Path to summary JSON (default: latest in data/outputs)")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Output markdown path")
    parser.add_argument("--outputs-dir", default="data/outputs", help="Directory holding summary_*.json")
    args = parser.parse_args()

    outputs_dir = Path(args.outputs_dir)
    summary_file = Path(args.summary) if args.summary else _latest_summary(outputs_dir)

    with summary_file.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_build_markdown(summary, summary_file), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
