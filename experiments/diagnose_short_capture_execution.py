"""Unregistered short-public-capture fill diagnostic, never held-out research.

No admission/fitting override enters the production registered runner. This
explicit diagnostic uses the low-level replay/view contracts, all three
frozen execution profiles and fixed 0/50ms delays. Keep every case and failure.
The model is observation-only; no model selection or parameter tuning occurs.
"""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audit_fill_evidence import audit, source_identity
from lob_sim.config import load_config, FillAssumptionConfig
from lob_sim.regime.artifact import load_model
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_view import view_specification, write_day_view
from lob_sim.research.evidence_io import retain_failure
from lob_sim.research.interval_reader import load_audit_report, iter_verified_intervals
from lob_sim.research.registered_protocol import experiment_specification
from lob_sim.research.replay_engine import ReplayWindow, research_config
from lob_sim.research.study_replay import run_study_replay
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot


def validate_diagnostic_report(report, source_sha, processor):
    if report["code_identity"] != processor or report["input_sha256"] != source_sha:
        raise ValueError("rerun the unchanged native capture audit with this processing code")
    if report["research_usable"] is not True:
        raise ValueError("diagnostic needs declared public provenance and an intact joint-symbol capture")
    if set(report["symbols"]) != {"BTCUSDT", "ETHUSDT"} or set(report["instruments"]) != set(report["symbols"]):
        raise ValueError("diagnostic requires both BTCUSDT/ETHUSDT and their captured instrument metadata")
    start, end = report["first_logical_ns"], report["last_logical_ns"]
    if end - start <= 30_000_000_000 or end - start > 600_000_000_000:
        raise ValueError("this explicitly short diagnostic needs >30s and <=10min;not a full-day study")


def run(source, audit_root, model_path, output):
    source, output = source.resolve(), output.resolve()
    if any(
        p.resolve().is_relative_to(output) or output.is_relative_to(p.resolve())
        for p in (source, audit_root, model_path)
    ):
        raise ValueError("diagnostic output overlaps immutable parents")
    report = load_audit_report(audit_root)
    processor = checkpoint_code_identity()
    raw_identity = source_identity(source)
    validate_diagnostic_report(report, raw_identity["sha256"], processor)
    for _ in iter_verified_intervals(audit_root, report):
        pass
    clock = CaptureClock.from_dict(report["clock"])
    start, end = report["first_logical_ns"], report["last_logical_ns"]
    cfg = replace(
        load_config(str(ROOT / ".env.example"), inherit_environment=False),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        hmm=None,
        sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
        sim_latency_mode="fixed",
        sim_latency_samples_ms=(),
        sim_order_latency_ms=0.0,
        sim_cancel_latency_ms=0.0,
    )
    scenarios = experiment_specification(cfg)["fill_scenarios"]
    model_sha = file_sha256(model_path)
    model = load_model(model_path)
    settings = HMMSettings(model, "BTCUSDT", mode="observe")
    cases = [{"profile": s["name"], "latency_ms": latency} for s in scenarios for latency in (0, 50)]
    plan = {
        "schema_version": "lob_sim.unregistered_short_capture_diagnostic.v1",
        "cases": cases,
        "input_sha256": report["input_sha256"],
        "capture_report_sha256": report["report_sha256"],
        "model_file_sha256": model_sha,
        "code_identity": processor,
        "base_configuration": config_snapshot(cfg),
        "claim_ready": False,
        "scope": "descriptive short-capture modeled fills;not registered,calibrated,validated or held-out evidence;no profitability or private-fill claim",
    }
    window = ReplayWindow("BTCUSDT", start, start + 30_000_000_000, end)
    day = datetime.fromtimestamp(clock.project(start) // 1_000_000_000, timezone.utc).date().isoformat()
    contract = view_specification(report["input_sha256"], report["report_sha256"], identity(plan), day, clock, window)
    contract.update(
        schema_version="lob_sim.unregistered_diagnostic_view.v1",
        registry_sha256=None,
        diagnostic_plan_sha256=identity(plan),
        scope=plan["scope"],
    )
    output.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        publish_json(output / "plan.json", plan)
        entry = write_day_view(source, output / "view", contract, minimum_free_disk_bytes=2 * 1024**3)
        publish_json(output / "contract.json", contract)
        view = output / "view" / entry["path"]
        for case in cases:
            if checkpoint_code_identity() != processor or file_sha256(model_path) != model_sha:
                raise ValueError("diagnostic processing code/model changed")
            scenario = next(s for s in scenarios if s["name"] == case["profile"])
            configuration = research_config(
                cfg,
                hmm=settings,
                fill_assumption=FillAssumptionConfig(**scenario["fill_assumption"]),
                sim_fill_model=scenario["sim_fill_model"],
                sim_order_latency_ms=float(case["latency_ms"]),
                sim_cancel_latency_ms=float(case["latency_ms"]),
                record_dir=output / "runs" / (case["profile"] + "_" + str(case["latency_ms"])),
            )
            files, _ = run_study_replay(configuration, view, window, minimum_free_disk_bytes=2 * 1024**3)
            path = output / (case["profile"] + "_" + str(case["latency_ms"]) + "_samples.json")
            samples = audit(files["summary"].parent, view, path, "public_l2_diagnostic")
            results.append(
                {
                    "case": case,
                    "sample_report": {"path": path.name, "sha256": file_sha256(path)},
                    "run_directory": files["summary"].parent.relative_to(output).as_posix(),
                    "maker_count": samples["fill_populations"]["maker"],
                    "taker_count": samples["fill_populations"]["taker"],
                }
            )
            print(case, samples["fill_populations"], flush=True)
        if (
            checkpoint_code_identity() != processor
            or file_sha256(model_path) != model_sha
            or source_identity(source) != raw_identity
        ):
            raise ValueError("diagnostic parents changed before completion")
        result = {
            "schema_version": "lob_sim.short_capture_diagnostic_result.v1",
            "complete": True,
            "plan_sha256": identity(plan),
            "cases": results,
            "claim_ready": False,
            "scope": plan["scope"],
        }
        publish_json(output / "result.json", result)
        return result
    except BaseException as exc:
        retain_failure(
            output,
            exc,
            schema="lob_sim.short_capture_diagnostic_failure.v1",
            completed_cases=len(results),
            expected_cases=len(cases),
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.file, args.audit, args.model, args.out)
