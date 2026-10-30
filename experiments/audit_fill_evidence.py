"""Describe modeled execution samples after native bundle verification.

Never infer historical FIFO or private exchange fills. No IID-fill confidence
interval, effective sample size or empirical day admission is manufactured.
Exact ordinal joins use a temporary on-disk ledger, not a tape-sized map.
"""

from contextlib import closing
from fractions import Fraction
from pathlib import Path
import argparse
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lob_sim.audit.streaming_bundle import audit_streaming_bundle
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.execution import iter_execution_rows
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json
from lob_sim.sim.export import iter_fill_audit_rows


def counts(fills, execution, horizons):
    populations = {"maker": 0, "taker": 0}
    groups = {}
    statistics = {
        h: {
            k: {
                "resolved": 0,
                "invalidated": 0,
                "event_bps": Fraction(0),
                "weighted_bps": Fraction(0),
                "resolved_quantity": Fraction(0),
            }
            for k in populations
        }
        for h in horizons
    }
    with (
        TemporaryDirectory(prefix="lob_sim_sample_join_") as temporary,
        closing(sqlite3.connect(str(Path(temporary) / "fills.sqlite3"))) as database,
    ):
        database.execute(
            "CREATE TABLE fills (id INTEGER PRIMARY KEY, price TEXT, qty TEXT, maker INTEGER, source TEXT, order_id TEXT)"
        )
        for ordinal, fill in enumerate(fills, 1):
            price, qty = Fraction(fill["price"]), Fraction(fill["qty"])
            if price <= 0 or qty <= 0 or type(fill["maker"]) is not bool:
                raise ValueError("invalid fill sample")
            kind = "maker" if fill["maker"] else "taker"
            if fill["fill_source"] not in {"agg_trade", "depth_update", "taker_order"}:
                raise ValueError("unknown fill source")
            if fill["maker"] != (fill["fill_source"] != "taker_order"):
                raise ValueError("modeled liquidity classification conflicts with its execution source")
            populations[kind] += 1
            key = (kind, fill["fill_source"], fill["order_state_at_fill"] == "pending_cancel")
            groups[key] = groups.get(key, 0) + 1
            database.execute(
                "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?)",
                (ordinal, str(price), str(qty), int(fill["maker"]), fill["fill_source"], fill["order_id"]),
            )
        seen = 0
        database.execute("CREATE TABLE markouts (fill_id INTEGER, horizon INTEGER, PRIMARY KEY(fill_id,horizon))")
        for row in execution:
            original = database.execute(
                "SELECT price,qty,maker,source,order_id FROM fills WHERE id=?", (row["fill_id"],)
            ).fetchone()
            if original is None or (Fraction(original[1]), original[3], original[4]) != (
                Fraction(row["qty"]),
                row["fill_source"],
                row["order_id"],
            ):
                raise ValueError("execution evidence lost its exact original fill identity")
            if row["event_type"] == "fill":
                seen += 1
                if row["fill_id"] != seen or row["maker"] is not bool(original[2]):
                    raise ValueError("fill order/liquidity correspondence differs")
                continue
            if row["event_type"] != "markout" or row["horizon_ms"] not in statistics or row["maker"] is not None:
                raise ValueError("unknown execution outcome")
            if row["fill_id"] > seen:
                raise ValueError("markout precedes its original fill")
            database.execute("INSERT INTO markouts VALUES (?, ?)", (row["fill_id"], row["horizon_ms"]))
            kind = "maker" if original[2] else "taker"
            cell = statistics[row["horizon_ms"]][kind]
            if row["status"] == "resolved":
                bps, qty = 10_000 * Fraction(row["markout"]) / Fraction(original[0]), Fraction(original[1])
                cell["resolved"] += 1
                cell["event_bps"] += bps
                cell["weighted_bps"] += bps * qty
                cell["resolved_quantity"] += qty
            elif row["status"] == "invalidated":
                cell["invalidated"] += 1
            else:
                raise ValueError("unknown markout status")
        if seen != sum(populations.values()):
            raise ValueError("execution fill census differs")
    outcomes = {}
    for horizon, cells in statistics.items():
        outcomes[str(horizon)] = {}
        for kind, cell in cells.items():
            resolved, invalidated, total = cell["resolved"], cell["invalidated"], populations[kind]
            missing = total - resolved
            if resolved + invalidated > total:
                raise ValueError("markout census exceeds its original fill population")
            event_mean = cell["event_bps"] / resolved if resolved else None
            quantity_mean = cell["weighted_bps"] / cell["resolved_quantity"] if resolved else None
            outcomes[str(horizon)][kind] = {
                "fill_events": total,
                "resolved": resolved,
                "invalidated": invalidated,
                "unresolved": total - resolved - invalidated,
                "missing_rate_rational": str(Fraction(missing, total)) if total else None,
                "event_weighted_mean_bps_rational": str(event_mean) if event_mean is not None else None,
                "quantity_weighted_mean_bps_rational": str(quantity_mean) if quantity_mean is not None else None,
                "quantity_minus_event_mean_bps_rational": str(quantity_mean - event_mean) if resolved else None,
                "resolved_quantity_rational": str(cell["resolved_quantity"]),
            }
    return {
        "fill_populations": populations,
        "queue_inferred_fill_events": sum(v for k, v in groups.items() if k[1] != "taker_order"),
        "immediate_crossing_fill_events": sum(v for k, v in groups.items() if k[1] == "taker_order"),
        "pending_cancel_fill_events": sum(v for k, v in groups.items() if k[2]),
        "groups": [
            {"liquidity": k[0], "fill_source": k[1], "pending_cancel": k[2], "fill_events": v}
            for k, v in sorted(groups.items())
        ],
        "horizons_ms": outcomes,
        "maker_estimand": "insufficient_evidence"
        if not statistics.get(1000, {}).get("maker", {}).get("resolved", 0)
        else "descriptive_only_unregistered_sample",
        "effective_sample_size": None,
        "dependence_reason": "not estimated from IID fills;serially dependent fills need admitted source/day/epoch clock blocks and the frozen registered protocol",
        "interval": None,
        "claim_ready": False,
    }


def source_identity(source):
    result = {"sha256": file_sha256(source), "segments": []}
    if source.name.endswith(".manifest.json"):
        for entry in read_json(source)["segments"]:
            path = (source.parent / entry["path"]).resolve()
            if not path.is_relative_to(source.parent.resolve()):
                raise ValueError("source segment escapes its capture")
            sha, size = file_sha256(path), path.stat().st_size
            if sha != entry["file_sha256"] or size != entry["size_bytes"]:
                raise ValueError("source segment identity differs")
            result["segments"].append({"path": entry["path"], "sha256": sha, "size_bytes": size})
    return result


def artifact_identity(bundle):
    names = (
        "manifest.json",
        "summary.json",
        "summary.csv",
        "event_trace.csv",
        "trades.csv",
        "markouts.csv",
        "hmm_model.json",
        "regime_trace.csv",
        "regime_execution.csv",
        "regime_quotes.csv",
        "regime_risk.csv",
    )
    result = {}
    root = bundle.resolve()
    for name in names:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("diagnostic artifact escapes its bundle")
        result[name] = file_sha256(path)
    return result


def audit(bundle, source, target, label):
    before, input_before = artifact_identity(bundle), source_identity(source)
    proof = audit_streaming_bundle(bundle, input_override=source)
    if proof["ok"] is not True:
        raise ValueError("native bundle verification failed: " + repr(proof["issues"]))
    summary = read_json(bundle / "summary.json")
    if "hmm_execution" not in summary:
        raise ValueError("exact ordinal sample analysis requires the native HMM execution audit")
    report = counts(
        iter_fill_audit_rows(bundle / "trades.csv"),
        iter_execution_rows(bundle / "regime_execution.csv"),
        summary["hmm_execution"]["horizons_ms"],
    )
    if artifact_identity(bundle) != before or source_identity(source) != input_before:
        raise ValueError("native artifacts or source changed during sample analysis")
    report.update(
        schema_version="lob_sim.fill_sample_diagnostic.v1",
        input_label=label,
        manifest_sha256=before["manifest.json"],
        input_sha256=input_before["sha256"],
        artifact_identities=before,
        input_identity=input_before,
        native_audit=proof,
        scope="modeled public-L2 scenarios on labelled input;not exact synthetic MBO matching,private exchange executions,day admission or independent statistical significance",
    )
    publish_json(target, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", choices=("synthetic_diagnostic", "public_l2_diagnostic"), required=True)
    args = parser.parse_args()
    audit(args.bundle, args.file, args.out, args.label)
