from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _latest_summary(outputs_dir: Path) -> Path:
    candidates = sorted(outputs_dir.glob("summary_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No summary_*.json found in {outputs_dir}")
    return candidates[0]


def _fmt_num(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.6f}" if isinstance(value, float) else str(value)
    return str(value)


def _build_markdown(summary: dict, summary_file: Path) -> str:
    fills = summary.get("fills", [])
    sample_fills = fills[:5]

    lines: list[str] = []
    lines.append("# Strategy Run Summary")
    lines.append("")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Source summary: `{summary_file.as_posix()}`")
    lines.append("")
    lines.append("## Headline Metrics")
    lines.append("")
    lines.append(f"- `total_pnl`: {_fmt_num(summary.get('total_pnl', 0))}")
    lines.append(f"- `realized_pnl`: {_fmt_num(summary.get('realized_pnl', 0))}")
    lines.append(f"- `unrealized_pnl`: {_fmt_num(summary.get('unrealized_pnl', 0))}")
    lines.append(f"- `max_drawdown`: {_fmt_num(summary.get('max_drawdown', 0))}")
    lines.append(f"- `fill_count`: {_fmt_num(summary.get('fill_count', 0))}")
    lines.append(f"- `fill_rate`: {_fmt_num(summary.get('fill_rate', 0))}")
    lines.append(f"- `avg_spread_captured`: {_fmt_num(summary.get('avg_spread_captured', 0))}")
    lines.append(f"- `avg_inventory`: {_fmt_num(summary.get('avg_inventory', 0))}")
    lines.append(f"- `inventory_stdev`: {_fmt_num(summary.get('inventory_stdev', 0))}")
    lines.append(f"- `total_fees`: {_fmt_num(summary.get('total_fees', 0))}")
    lines.append("")
    lines.append("## Sample Fills")
    lines.append("")
    if not sample_fills:
        lines.append("- No fills captured in this run.")
    else:
        lines.append("| ts_local | symbol | side | price | qty | maker |")
        lines.append("|---|---|---|---:|---:|---|")
        for fill in sample_fills:
            lines.append(
                f"| {fill.get('ts_local', '')} | {fill.get('symbol', '')} | {fill.get('side', '')} | "
                f"{fill.get('price', '')} | {fill.get('qty', '')} | {fill.get('maker', '')} |"
            )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- This is an offline replay of L2 + aggTrade data with queue-aware passive fill approximation.")
    lines.append("- No live orders are sent and no real capital is at risk.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate recruiter-facing summary.md from simulator output")
    parser.add_argument("--summary", help="Path to summary JSON (default: latest in data/outputs)")
    parser.add_argument("--out", default="summary.md", help="Output markdown path")
    parser.add_argument("--outputs-dir", default="data/outputs", help="Directory holding summary_*.json")
    args = parser.parse_args()

    outputs_dir = Path(args.outputs_dir)
    summary_file = Path(args.summary) if args.summary else _latest_summary(outputs_dir)

    with summary_file.open("r", encoding="utf-8") as fh:
        summary = json.load(fh)

    out_path = Path(args.out)
    out_path.write_text(_build_markdown(summary, summary_file), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
