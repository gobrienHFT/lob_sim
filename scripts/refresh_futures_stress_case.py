from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from lob_sim.book.local_book import LocalOrderBook
from lob_sim.config import load_config
from lob_sim.record.format import NDJSONRecord, snapshot_payload
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.mm_strategy import MarketMakingStrategy, QuoteTarget, StrategyDecision
from lob_sim.sim.run_manifest import output_artifact_snapshot
from lob_sim.util import write_summary_csv


REPO_ROOT = Path(__file__).resolve().parents[1]
STRESS_CASE_DIR = REPO_ROOT / "docs" / "sample_outputs" / "futures_stress_case"
INPUT_STRESS_NAME = "input_stress.ndjson"

STRESS_ENV = {
    "BINANCE_FAPI_BASE": "https://fapi.binance.com",
    "BINANCE_FWS_BASE": "wss://fstream.binance.com",
    "SYMBOLS": "BTCUSDT",
    "DEPTH_STREAM_SUFFIX": "@depth@100ms",
    "TRADE_STREAM_SUFFIX": "@aggTrade",
    "SNAPSHOT_LIMIT": "1000",
    "BOOK_TOP_N": "20",
    "COLLECT_SECONDS": "10",
    "RECORD_FORMAT": "ndjson",
    "RECORD_GZIP": "0",
    "RECORD_FLUSH_EVERY": "300",
    "HTTP_TIMEOUT": "10",
    "HTTP_RETRIES": "2",
    "RATE_LIMIT_REQ_PER_SEC": "8",
    "WS_PING_INTERVAL": "180",
    "WS_PING_TIMEOUT": "600",
    "WS_RECONNECT_MAX_SEC": "30",
    "RESYNC_ON_GAP": "1",
    "SIM_SEED": "1",
    "SIM_ORDER_LATENCY_MS": "0",
    "SIM_CANCEL_LATENCY_MS": "100",
    "SIM_ADVERSE_MARKOUT_SECONDS": "1.0",
    "SIM_KILL_SWITCH_ENABLED": "0",
    "SIM_KILL_MAX_DRAWDOWN": "0",
    "SIM_KILL_MAX_CONSECUTIVE_LOSSES": "0",
    "MM_ENABLED": "1",
    "MM_STRATEGY_PROFILE": "baseline",
    "MM_REQUOTE_MS": "1000",
    "MM_ORDER_QTY": "0.001",
    "MM_MAX_POSITION": "0.05",
    "MM_HALF_SPREAD_BPS": "0.05",
    "MM_LAYERED_INNER_SPREAD_BPS": "0.05",
    "MM_LAYERED_OUTER_SPREAD_BPS": "0.15",
    "MM_SKEW_BPS_PER_UNIT": "0",
    "MM_VOLATILITY_WINDOW": "30",
    "MM_VOLATILITY_SPREAD_FACTOR": "0",
    "MM_QUEUE_REPOST_LOTS": "99",
    "MM_TRADE_IMBALANCE_WINDOW": "12",
    "MM_MICROSTRUCTURE_GATE_THRESHOLD": "0.20",
    "MM_MICROSTRUCTURE_GATE_BPS": "0.10",
    "MM_FEE_FLOOR_BUFFER_BPS": "0.02",
    "MM_TOXICITY_SPREAD_FACTOR": "0",
    "FEES_MAKER_BPS": "0",
    "FEES_TAKER_BPS": "0",
    "LOG_LEVEL": "ERROR",
}


class StressScriptedStrategy(MarketMakingStrategy):
    """Deterministic quote script used only to make the stress fixture auditable."""

    def __init__(self, cfg) -> None:  # type: ignore[no-untyped-def]
        super().__init__(cfg)
        self._decision_count = 0

    def propose(self, book: LocalOrderBook, inventory_qty: Decimal) -> StrategyDecision:
        self._update_volatility(book)
        self._decision_count += 1
        best = book.best_ticks()
        if best is None:
            return StrategyDecision(reason="no_best_quotes")
        bid_tick, ask_tick, mid_ticks, skew_ticks = self._base_quote_inputs(book, inventory_qty)
        size_lots = 1
        diagnostics = self._base_diagnostics(
            book,
            inventory_qty,
            size_lots,
            bid_tick,
            ask_tick,
            mid_ticks,
            skew_ticks,
        )
        diagnostics.update(
            {
                "spread_scale": "1",
                "half_spread_bps": "0.05",
                "half_spread_ticks": "1",
                "script_decision_count": self._decision_count,
            }
        )

        if self._decision_count == 1:
            return StrategyDecision(
                quotes=[
                    QuoteTarget("bid", "passive_bid", 1000, 3, "stress:passive_bid:v1"),
                    QuoteTarget("ask", "own_ask", 1004, 2, "stress:own_ask:v1"),
                ],
                diagnostics=diagnostics,
            )
        if self._decision_count == 2:
            return StrategyDecision(
                quotes=[
                    QuoteTarget("ask", "own_ask", 1004, 2, "stress:own_ask:v1"),
                    QuoteTarget("bid", "cross_venue", 1003, 2, "stress:cross_venue:v1"),
                ],
                diagnostics=diagnostics,
            )
        if self._decision_count == 3:
            return StrategyDecision(
                quotes=[
                    QuoteTarget("ask", "own_ask", 1004, 2, "stress:own_ask:v1"),
                    QuoteTarget("bid", "cross_own", 1005, 1, "stress:cross_own:v1"),
                ],
                diagnostics=diagnostics,
            )
        return StrategyDecision(reason="scripted_cancel_all", diagnostics=diagnostics)


def stress_records() -> list[NDJSONRecord]:
    return [
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
                "venue": "BINANCE_USDM",
            },
        ),
        NDJSONRecord(
            ts_local=1.0,
            symbol="BTCUSDT",
            type="snapshot",
            data=snapshot_payload(
                100,
                [("100.0", "0.002"), ("99.9", "0.005")],
                [("100.2", "0.002"), ("100.3", "0.001")],
            ),
        ),
        NDJSONRecord(
            ts_local=2.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 95,
                "u": 100,
                "pu": 94,
                "b": [["100.0", "0.002"], ["99.9", "0.005"]],
                "a": [["100.2", "0.002"], ["100.3", "0.001"]],
            },
        ),
        NDJSONRecord(
            ts_local=2.2,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 101, "u": 101, "pu": 100, "b": [["100.0", "0.000"]], "a": []},
        ),
        NDJSONRecord(
            ts_local=2.35,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.001", "m": True},
        ),
        NDJSONRecord(
            ts_local=2.5,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 102, "u": 102, "pu": 101, "b": [], "a": [["100.2", "0.001"]]},
        ),
        NDJSONRecord(
            ts_local=2.55,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.2", "q": "0.001", "m": False},
        ),
        NDJSONRecord(
            ts_local=3.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 103, "u": 103, "pu": 102, "b": [["99.9", "0.004"]], "a": []},
        ),
        NDJSONRecord(
            ts_local=3.05,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.0", "q": "0.002", "m": True},
        ),
        NDJSONRecord(
            ts_local=3.4,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 104,
                "u": 104,
                "pu": 103,
                "b": [["99.9", "0.000"], ["99.8", "0.003"]],
                "a": [["100.2", "0.000"], ["100.3", "0.000"], ["100.0", "0.003"]],
            },
        ),
        NDJSONRecord(
            ts_local=4.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={
                "U": 105,
                "u": 105,
                "pu": 104,
                "b": [["100.0", "0.003"]],
                "a": [["100.0", "0.000"], ["100.4", "0.003"]],
            },
        ),
        NDJSONRecord(
            ts_local=4.05,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 106, "u": 106, "pu": 105, "b": [], "a": [["100.4", "0.002"]]},
        ),
        NDJSONRecord(
            ts_local=5.0,
            symbol="BTCUSDT",
            type="depthUpdate",
            data={"U": 107, "u": 107, "pu": 106, "b": [], "a": []},
        ),
        NDJSONRecord(
            ts_local=5.1,
            symbol="BTCUSDT",
            type="aggTrade",
            data={"p": "100.4", "q": "0.001", "m": False},
        ),
    ]


def _write_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in stress_records():
            handle.write(record.to_json())
            handle.write("\n")
    return path


def _path_for_summary(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _event_type_counts(event_trace: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in event_trace:
        event_type = str(row["event_type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    return dict(sorted(counts.items()))


def _assert_stress_coverage(summary: dict[str, Any], event_trace: list[dict[str, Any]]) -> None:
    fill_sources = summary.get("fill_source_counts", {})
    lifecycle = summary.get("order_lifecycle_counts", {})
    public_consumption = summary.get("public_consumption_summary", {})
    markouts = summary.get("markout_by_fill_source", {})

    if summary.get("book_gap_count_by_symbol") != {}:
        raise RuntimeError("Stress case is expected to be a no-gap fixture.")
    for source in ("depth_update", "agg_trade", "taker_order"):
        if int(fill_sources.get(source, 0)) <= 0:
            raise RuntimeError(f"Stress case did not produce a {source} fill.")
    if int(lifecycle.get("self_trade_prevented", 0)) <= 0:
        raise RuntimeError("Stress case did not trigger self-trade prevention.")
    if int(lifecycle.get("cancel_requested", 0)) <= 0 or int(lifecycle.get("cancel_acknowledged", 0)) <= 0:
        raise RuntimeError("Stress case did not exercise cancel latency.")
    if int(summary.get("arrival_with_queue_ahead_count", 0)) <= 0:
        raise RuntimeError("Stress case did not record queue ahead at arrival.")
    if int(public_consumption.get("total_overlap_netted_lots", 0)) <= 0:
        raise RuntimeError("Stress case did not exercise depth/aggTrade overlap netting.")
    adverse_samples = sum(int(data.get("adverse_samples", 0)) for data in markouts.values())
    non_adverse_samples = sum(
        int(data.get("samples", 0)) - int(data.get("adverse_samples", 0)) for data in markouts.values()
    )
    if adverse_samples <= 0 or non_adverse_samples <= 0:
        raise RuntimeError("Stress case needs both adverse and non-adverse markout samples.")
    if not any(
        row["event_type"] == "order_arrival" and row["details"].get("immediate_fills", 0) > 0 for row in event_trace
    ):
        raise RuntimeError("Stress case did not produce a marketable taker arrival.")


def _render_readme(summary: dict[str, Any]) -> str:
    event_counts = summary["event_counts"]
    fill_sources = summary["fill_source_counts"]
    lifecycle = summary["order_lifecycle_counts"]
    public = summary["public_consumption_summary"]
    return "\n".join(
        [
            "# Futures Stress Case",
            "",
            "This is a deterministic synthetic-but-exchange-shaped BTCUSDT fixture. It is intentionally not recorded market data; it is a compact stress pack for queue and event-ordering audit paths that are hard to see in a short real clip.",
            "",
            "The fixture uses Binance USD-M-style `exchangeInfo`, `snapshot`, `depthUpdate`, and `aggTrade` records, then replays them through a scripted strategy that exists only for this evidence pack.",
            "",
            "## Coverage",
            "",
            "- Snapshot-seeded visible queue ahead and partial passive fills.",
            "- Depth/`aggTrade` overlap netting on the same side and price.",
            "- Depth-inferred, `aggTrade`-inferred, and marketable taker fills.",
            "- Adverse and non-adverse post-fill markouts.",
            "- Cancel latency, including an old quote fill before acknowledgement.",
            "- Same-timestamp cancel acknowledgement before public trade consumption.",
            "- Conservative self-trade prevention for a marketable strategy order.",
            "- No-gap replay continuity; `book_gap_count` stays zero.",
            "",
            "## Summary",
            "",
            f"- Records processed: `{event_counts['records_processed']}`",
            f"- Depth updates: `{event_counts['depth_update']}`",
            f"- AggTrade records: `{event_counts['agg_trade']}`",
            f"- Fill-source counts: `{json.dumps(fill_sources, sort_keys=True)}`",
            f"- Order lifecycle counts: `{json.dumps(lifecycle, sort_keys=True)}`",
            f"- Public overlap-netted lots: `{public['total_overlap_netted_lots']}`",
            "",
            "## Files",
            "",
            "- Input: `input_stress.ndjson`",
            "- Summary: `summary.json` and `summary.csv`",
            "- Trades: `trades.csv`",
            "- Event trace: `event_trace.csv`",
            "- Manifest: `manifest.json`",
            "- Notes: `case_notes.md`",
            "",
        ]
    )


def _render_case_notes(summary: dict[str, Any], event_trace: list[dict[str, Any]]) -> str:
    event_type_counts = _event_type_counts(event_trace)
    return "\n".join(
        [
            "# Stress Case Notes",
            "",
            "This pack is synthetic by design. It should be read as an executable invariant fixture, not as a claim about live exchange fill truth or alpha.",
            "",
            "## Event Counts",
            "",
            f"- Event trace rows: `{summary['event_trace_count']}`",
            f"- Event type counts: `{json.dumps(event_type_counts, sort_keys=True)}`",
            f"- Replay event counts: `{json.dumps(summary['event_counts'], sort_keys=True)}`",
            "",
            "## Fill And Queue Evidence",
            "",
            f"- Fill-source mix: `{json.dumps(summary['fill_source_counts'], sort_keys=True)}`",
            f"- Queue consumption: `{json.dumps(summary['public_consumption_summary'], sort_keys=True)}`",
            f"- Markout by source: `{json.dumps(summary['markout_by_fill_source'], sort_keys=True)}`",
            f"- Arrival queue samples: `{summary['resting_arrival_queue_samples']}`",
            f"- Max arrival queue ahead lots: `{summary['max_arrival_queue_ahead_lots']}`",
            "",
            "## Limits",
            "",
            "- The feed rows are exchange-shaped but synthetic.",
            "- Public L2 data cannot prove private queue identity or exchange execution reports.",
            "- The scripted strategy exists only to put rare mechanics in one compact pack.",
            "",
        ]
    )


def refresh_futures_stress_case(output_dir: Path = STRESS_CASE_DIR) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    fixture_path = _write_fixture(output_dir / INPUT_STRESS_NAME)

    with TemporaryDirectory(prefix="lob_sim_futures_stress_case_") as temp_dir:
        env = dict(STRESS_ENV)
        env["RECORD_DIR"] = temp_dir
        with _temporary_env(env):
            cfg = load_config(".env.example")
            engine = SimulationEngine(cfg)
            engine.strategy = StressScriptedStrategy(cfg)
            metrics = engine.run(fixture_path)
            generated_paths, summary = engine.write_outputs(str(fixture_path), metrics)

        event_trace = list(engine.event_trace)
        _assert_stress_coverage(summary, event_trace)
        summary["fixture_provenance"] = {
            "source": "synthetic_exchange_shaped",
            "purpose": "compact deterministic stress coverage for public L2 replay mechanics",
            "script": "scripts/refresh_futures_stress_case.py",
        }
        summary["stress_coverage"] = {
            "queue_ahead": True,
            "partial_fills": True,
            "depth_agg_trade_overlap_netting": True,
            "adverse_and_non_adverse_markouts": True,
            "cancel_latency": True,
            "same_timestamp_cancel_before_trade": True,
            "marketable_taker_fill": True,
            "self_trade_prevention": True,
            "book_gap_count": summary["event_counts"]["book_gap_count"],
        }

        committed_paths = {
            "event_trace": output_dir / "event_trace.csv",
            "summary": output_dir / "summary.json",
            "summary_csv": output_dir / "summary.csv",
            "trades": output_dir / "trades.csv",
            "manifest": output_dir / "manifest.json",
        }
        summary["output_files"] = {name: _path_for_summary(path) for name, path in committed_paths.items()}
        manifest = json.loads(generated_paths["manifest"].read_text(encoding="utf-8"))
        manifest["input"]["path"] = _path_for_summary(fixture_path)
        manifest["outputs"] = dict(summary["output_files"])
        manifest["fixture_provenance"] = dict(summary["fixture_provenance"])
        manifest["stress_coverage"] = dict(summary["stress_coverage"])

        committed_paths["summary"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        write_summary_csv(committed_paths["summary_csv"], summary, exclude_keys={"fills", "markout_events"})
        shutil.copyfile(generated_paths["trades"], committed_paths["trades"])
        shutil.copyfile(generated_paths["event_trace"], committed_paths["event_trace"])
        manifest["output_artifacts"] = output_artifact_snapshot(committed_paths, path_formatter=_path_for_summary)
        committed_paths["manifest"].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    readme_path = output_dir / "README.md"
    notes_path = output_dir / "case_notes.md"
    readme_path.write_text(_render_readme(summary), encoding="utf-8")
    notes_path.write_text(_render_case_notes(summary, event_trace), encoding="utf-8")

    return {
        "input": fixture_path,
        "readme": readme_path,
        "case_notes": notes_path,
        **committed_paths,
    }


def main() -> int:
    paths = refresh_futures_stress_case()
    print(f"Refreshed futures stress case in {STRESS_CASE_DIR}")
    for name, path in paths.items():
        print(f"- {name}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
