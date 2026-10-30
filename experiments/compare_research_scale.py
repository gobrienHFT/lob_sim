"""Strict matched-workload comparison, not a trading-latency or release gate.

No matching is rerun. Audit-sink comparisons independently reopen native
bundles and compare all detail/model/table bytes plus every non-provenance
summary field. Code identities and output paths intentionally differ between
processing revisions; manifest byte equality is not claimed.
"""

import argparse
from hashlib import sha256
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.benchmark_research_scale import input_identity
from experiments.benchmark_hmm_overhead import percentiles
from lob_sim.audit.streaming_bundle import audit_streaming_bundle, EXPECTED_FILES
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import canonical_json, strict_json, integer, identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json


def measured(report):
    integer(report["warmups"], "benchmark warmups")
    integer(report["repetitions"], "benchmark repetitions", minimum=1)
    rows = report["rows"]
    if len(rows) != report["warmups"] + report["repetitions"]:
        raise ValueError("benchmark trial census differs")
    for index, row in enumerate(rows):
        if integer(row["index"], "benchmark trial index") != index:
            raise ValueError("benchmark trial ordering differs")
        if type(row["warmup"]) is not bool or row["warmup"] != (index < report["warmups"]):
            raise ValueError("benchmark warmup census differs")
        for field in ("wall_ns", "cpu_ns", "records", "os_process_peak_rss_bytes"):
            integer(row[field], "benchmark " + field, minimum=1)
    return [row for row in rows if not row["warmup"]]


def summaries(rows):
    result = {
        key: {
            **percentiles([r[key] for r in rows]),
            "minimum": min(r[key] for r in rows),
            "maximum": max(r[key] for r in rows),
            "sample_stdev": statistics.stdev(r[key] for r in rows) if len(rows) > 1 else None,
        }
        for key in ("wall_ns", "cpu_ns", "records_per_second")
    }
    result["os_process_peak_rss_bytes"] = max(r["os_process_peak_rss_bytes"] for r in rows)
    result["output_bytes_per_case"] = [r["output_bytes"] for r in rows]
    result["measured_repetitions"] = len(rows)
    result["confidence_interval"] = None
    result["quantile_scope"] = (
        "empirical run quantiles;small diagnostic samples,not event-latency tails or dedicated-host release confidence"
    )
    return result


def native_fingerprint(report_path, report, source):
    if source is None or input_identity(source) != report["identities"]["input"]:
        raise ValueError("audit comparison requires the unchanged explicit source")
    manifests = sorted((report_path.parent / "runs").rglob("manifest.json"))
    if len(manifests) != len(report["rows"]):
        raise ValueError("native pack/trial census differs")
    expected = dict(EXPECTED_FILES)
    if report["configuration"].get("hmm") is not None:
        expected.update(
            hmm_model="hmm_model.json",
            regime_trace="regime_trace.csv",
            regime_execution="regime_execution.csv",
            regime_quotes="regime_quotes.csv",
            regime_risk="regime_risk.csv",
        )
    fingerprints = []
    table_bindings = {canonical_json(row["table_binding"]) for row in report["rows"]}
    if len(table_bindings) != 1:
        raise ValueError("native repeated table identities differ")
    table = strict_json(next(iter(table_bindings)))
    for path in manifests:
        folder = path.parent.resolve()
        if not folder.is_relative_to(report_path.parent.resolve()):
            raise ValueError("native pack escapes benchmark root")
        proof = audit_streaming_bundle(folder, input_override=source)
        if proof["ok"] is not True:
            raise ValueError("native comparison audit failed: " + repr(proof["issues"]))
        summary = read_json(folder / "summary.json")
        summary.pop("output_files")
        summary.pop("code_identity")
        files = {}
        for label, name in expected.items():
            file = (folder / name).resolve()
            if not file.is_relative_to(folder):
                raise ValueError("native file escapes its pack")
            if label not in {"manifest", "summary", "summary_csv"}:
                files[label] = file_sha256(file)
        if table is not None:
            if table["path"] != "clock_periods.jsonl" or file_sha256(folder / table["path"]) != table["sha256"]:
                raise ValueError("native period-table byte identity differs")
            files["clock_periods"] = table["sha256"]
        fingerprints.append(identity({"summary": summary, "detail_and_model_bytes": files}))
    if len(set(fingerprints)) != 1:
        raise ValueError("native repeated non-provenance artifacts differ")
    return {
        "native_packs_independently_verified": len(manifests),
        "non_provenance_sha256": fingerprints[0],
        "scope": "all event/fill/markout/regime/risk/quote/model/table bytes plus full summary except code_identity/output_files;summary CSV independently audited;not provenance-manifest byte equality",
    }


def compare(before_path, after_path, target, source=None):
    before_path, after_path = before_path.resolve(), after_path.resolve()
    a, b = (read_json(p) for p in (before_path, after_path))
    if (
        a["schema_version"] != "lob_sim.research_scale_worker.v1"
        or a["mode"] not in {"baseline", "observe", "policy"}
        or a["workload"] not in {"raw", "null", "audit"}
    ):
        raise ValueError("unsupported benchmark report")
    controls = (
        "schema_version",
        "mode",
        "workload",
        "configuration",
        "warmups",
        "repetitions",
        "profiled",
        "memory_traced",
        "host",
    )
    if canonical_json({k: a[k] for k in controls}) != canonical_json({k: b[k] for k in controls}):
        raise ValueError("benchmark workload/configuration/instrumentation differs")
    parents_a, parents_b = (dict(r["identities"]) for r in (a, b))
    code_a, code_b = parents_a.pop("code_identity"), parents_b.pop("code_identity")
    if canonical_json(parents_a) != canonical_json(parents_b):
        raise ValueError("benchmark input/model/contract/environment/worker identity differs")
    if canonical_json(a["probe"]) != canonical_json(b["probe"]):
        raise ValueError("benchmark state/fill/markout/accounting probe differs")
    left, right = measured(a), measured(b)
    if {row["records"] for row in left + right} != {
        a["probe"]["records_processed"]
        if "records_processed" in a["probe"]
        else a["probe"]["event_counts"]["records_processed"]
    }:
        raise ValueError("benchmark observation census differs")
    left_summary, right_summary = summaries(left), summaries(right)
    native = None
    if a["workload"] == "audit":
        native_a = native_fingerprint(before_path, a, source)
        native_b = native_fingerprint(after_path, b, source)
        if native_a["non_provenance_sha256"] != native_b["non_provenance_sha256"]:
            raise ValueError("native non-provenance artifact parity differs")
        native = {"before": native_a, "after": native_b}
    report = {
        "schema_version": "lob_sim.research_scale_comparison.v1",
        "complete": True,
        "before_report_sha256": file_sha256(before_path),
        "after_report_sha256": file_sha256(after_path),
        "worker_sha256": parents_a["script_sha256"],
        "before_processing_code": code_a,
        "after_processing_code": code_b,
        "matched_controls_sha256": identity({k: a[k] for k in controls}),
        "probe_sha256": identity(a["probe"]),
        "before": left_summary,
        "after": right_summary,
        "median_wall_change_percent": 100
        * (right_summary["wall_ns"]["median"] / left_summary["wall_ns"]["median"] - 1),
        "median_cpu_change_percent": 100 * (right_summary["cpu_ns"]["median"] / left_summary["cpu_ns"]["median"] - 1),
        "native_artifact_parity": native,
        "scope": "matched diagnostic workload and recorded probes;small unpinned shared-host samples,not certified release speedup,full-grid feasibility or trading latency",
    }
    report["comparison_sha256"] = sha256(canonical_json(report).encode()).hexdigest()
    publish_json(target, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    compare(args.before, args.after, args.out, args.file)
