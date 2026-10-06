"""Cross-revision behavioral probes for the repaired simulation core.

These fixture-scale hashes cover every continuation field, not source identity.
Run on the independently repaired core before running on the HMM branch.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lob_sim.config import Config, load_config
from lob_sim.sim.checkpoint import checkpoint_code_identity, encode
from lob_sim.sim.engine import SimulationEngine
from lob_sim.sim.metrics import PositionState
from lob_sim.sim.run_manifest import config_snapshot, source_state

SECOND = 1_000_000_000
WALL = 1_735_689_600 * SECOND
SCHEMA = "lob_sim.repaired_core_behavior.v1"


def configuration(**changes: Any) -> Config:
    return replace(load_config(str(ROOT / ".env.example"), inherit_environment=False), **changes)


def write_tape(path: Path, *, scenario: str = "valid", symbols: tuple[str, ...] = ("BTCUSDT",)) -> Path:
    """Independent synthetic receipts; no HMM/model/trainer dependency."""
    rows: list[dict[str, Any]] = []

    def emit(ns: int, kind: str, data: dict[str, Any], route: str, symbol: str) -> None:
        rows.append(
            {
                "ts_local": (WALL + ns) / SECOND,
                "symbol": symbol,
                "type": kind,
                "data": {
                    **data,
                    "_capture": {
                        "recvSeq": len(rows),
                        "recvMonotonicNs": ns,
                        "recvWallNs": WALL + ns,
                        "route": route,
                        "streamEpoch": 0,
                        "syncEpoch": 0,
                    },
                },
            }
        )

    emit(0, "captureMeta", {"schemaVersion": 3, "clock": "receive_time"}, "control", "*")
    for symbol in symbols:
        emit(100_000_000, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001"}, "control", symbol)
        emit(200_000_000, "captureEvent", {"event": "connect", "route": "public"}, "public", symbol)
        emit(300_000_000, "captureEvent", {"event": "connect", "route": "market"}, "market", symbol)
    # Setup must be globally ordered, including two-symbol same-time receipts.
    rows.sort(key=lambda row: row["data"]["_capture"]["recvMonotonicNs"])
    ids = {symbol: 100 for symbol in symbols}
    for symbol in symbols:
        emit(
            SECOND,
            "snapshot",
            {"lastUpdateId": 100, "bids": [["99.9", "0.01"]], "asks": [["100.1", "0.01"]]},
            "public",
            symbol,
        )
    for time in range(2, 14):
        for symbol in symbols:
            if scenario == "quiet_" + symbol and time > 2:
                continue
            previous = ids[symbol]
            ids[symbol] += 1
            data: dict[str, Any] = {
                "U": previous if time == 2 else ids[symbol],
                "u": ids[symbol],
                "pu": previous,
                "b": [["99.9", "0.012"]],
                "a": [["100.1", "0.01"]],
            }
            if scenario == "gap" and time == 7:
                data["pu"] = previous + 10
            if scenario == "off_grid" and time == 7:
                data["b"][0][0] = "99.91"
            emit(time * SECOND, "depthUpdate", data, "public", symbol)
            if time == 6:
                for maker in (False, True):
                    emit(
                        time * SECOND,
                        "aggTrade",
                        {"p": "99.9" if maker else "100.1", "q": "0.1", "m": maker},
                        "market",
                        symbol,
                    )
        if time == 6 and scenario in {"depth_disconnect", "trade_disconnect", "overflow"}:
            route = {"depth_disconnect": "public", "trade_disconnect": "market", "overflow": "control"}[scenario]
            emit(
                6_100_000_000,
                "captureEvent",
                {"event": "overflow" if route == "control" else "disconnect", "route": route},
                route,
                "*" if route == "control" else "BTCUSDT",
            )
    emit(14 * SECOND, "captureEvent", {"event": "capture_trailer", "route": "control"}, "control", "*")
    for sequence, row in enumerate(rows):
        row["data"]["_capture"]["recvSeq"] = sequence
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def digest(value: Any) -> str:
    # No dropped risk/accounting/clock fields, rounding, or HMM-key stripping.
    return sha256(
        json.dumps(encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def fingerprint(engine: SimulationEngine) -> dict[str, Any]:
    # get_summary updates valuation/sample statistics. Read a detached metrics
    # snapshot rather than accidentally perturbing checkpoint comparisons.
    summary = copy.deepcopy(engine.metrics).get_summary(engine._books, engine._specs)
    return {
        "state_sha256": engine.state_sha256(),
        "continuation_sha256": digest(engine._checkpoint_mutable_state()),
        "events_sha256": digest(engine.event_trace),
        "metrics_sha256": digest(summary),
        "annotations_sha256": digest(engine._summary_annotations()),
        "fills_sha256": engine.metrics.fill_audit_sha256,
        "markouts_sha256": engine.metrics.markout_audit_sha256,
        "events": len(engine.event_trace),
        "fills": engine.metrics.fill_count,
        "markouts": engine.metrics.markout_event_count,
    }


def collect() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="lob-core-probe-") as temporary:
        directory = Path(temporary)
        for profile in ("baseline", "layered_mm", "research_mm"):
            for fill in ("trade", "depth"):
                for latency in ("fixed", "empirical", "stress_tail"):
                    cfg = configuration(
                        mm_strategy_profile=profile,
                        sim_fill_model=fill,
                        sim_latency_mode=latency,
                        sim_latency_samples_ms=(0.0, 1.0, 5.0, 25.0),
                        sim_order_latency_ms=5.0,
                        sim_cancel_latency_ms=10.0,
                        sim_latency_stress_multiplier=2.0,
                        mm_half_spread_bps=Decimal(0),
                        mm_order_qty=Decimal("0.01"),
                        mm_requote_ms=40,
                    )
                    for scenario in ("valid", "gap", "off_grid", "depth_disconnect", "trade_disconnect", "overflow"):
                        case_id = f"{profile}/{fill}/{latency}/{scenario}"
                        tape = write_tape(directory / (case_id.replace("/", "_") + ".ndjson"), scenario=scenario)
                        engine = SimulationEngine(cfg)
                        engine.run(tape)
                        checkpoints = []
                        for cut in (1, 2, 3, 4, 13, 14):
                            checkpoint = directory / (case_id.replace("/", "_") + f"_{cut}.json")
                            paused = SimulationEngine(cfg)
                            paused.run(tape, checkpoint_path=checkpoint, stop_after_records=cut)
                            if paused._last_event_index != cut:
                                raise AssertionError(f"checkpoint skipped boundary {case_id}/{cut}")
                            checkpoints.append({"cut": cut, **fingerprint(paused)})
                            resumed = SimulationEngine(cfg)
                            resumed.run(tape, resume_from=checkpoint)
                            if fingerprint(resumed) != fingerprint(engine):
                                raise AssertionError(f"resume changed behavior {case_id}/{cut}")
                        cases.append(
                            {
                                "case": case_id,
                                "input_sha256": sha256(tape.read_bytes()).hexdigest(),
                                "config": config_snapshot(cfg),
                                "complete": fingerprint(engine),
                                "checkpoints": checkpoints,
                            }
                        )
        for scenario in ("valid", "quiet_BTCUSDT", "quiet_ETHUSDT"):
            tape = write_tape(directory / (scenario + "_two.ndjson"), scenario=scenario, symbols=("BTCUSDT", "ETHUSDT"))
            cfg = configuration(symbols=("BTCUSDT", "ETHUSDT"), mm_requote_ms=40)
            engine = SimulationEngine(cfg)
            engine.run(tape)
            cases.append(
                {
                    "case": "two_symbols/" + scenario,
                    "input_sha256": sha256(tape.read_bytes()).hexdigest(),
                    "config": config_snapshot(cfg),
                    "complete": fingerprint(engine),
                }
            )
        legacy = ROOT / "docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson"
        cfg = configuration()
        engine = SimulationEngine(cfg)
        engine.run(legacy)
        cases.append(
            {
                "case": "legacy_golden",
                "input_sha256": sha256(legacy.read_bytes()).hexdigest(),
                "config": config_snapshot(cfg),
                "complete": fingerprint(engine),
            }
        )
        risk = SimulationEngine(configuration(mm_max_portfolio_notional=Decimal("100")))
        risk.metrics.position["UNKNOWN"] = PositionState(lot_size=1)
        reservation = risk._portfolio_notional_reservation()
        if reservation != (None, {}, ("UNKNOWN",)):
            raise AssertionError("unknown instrument exposure must fail closed")
    return {"schema": SCHEMA, "cases": cases, "missing_units_reservation": encode(reservation)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args(argv)
    if args.expected is None and args.write is None:
        parser.error("provide --expected and/or --write")
    result: dict[str, Any] = {
        "producer": {"source": source_state(), "package": checkpoint_code_identity()},
        "behavior": collect(),
    }
    result["behavior_sha256"] = digest(result["behavior"])
    if args.expected is not None:
        reference = json.loads(args.expected.read_text(encoding="utf-8"))
        if reference["behavior_sha256"] != digest(reference["behavior"]):
            raise ValueError("baseline content identity mismatch")
        if result["behavior"] != reference["behavior"]:
            raise AssertionError("HMM-off differs from the independently repaired core")
    if args.write is not None:
        with args.write.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    print(
        json.dumps(
            {
                "cases": len(result["behavior"]["cases"]),
                "behavior_sha256": result["behavior_sha256"],
                "matches": args.expected is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
