"""Paired current-engine experiment isolating selected old/new hot functions.

Trusted local Git supplies the four old function bodies; all other code,
inputs, models and configuration are shared. Runtime overrides are explicit,
temporary, and restored before publication. This is not a historical binary
benchmark or a dedicated-host/latency certification. Keep every attempted mode.
"""

import argparse
import ast
from contextlib import ExitStack
from dataclasses import asdict, replace
import gc
from hashlib import sha256
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.benchmark_hmm_overhead import replay_probe, percentiles
from experiments.benchmark_research_scale import input_identity, peak_rss
from lob_sim.config import load_config
from lob_sim.record import envelope, segmented
from lob_sim.regime import risk
from lob_sim.regime.artifact import load_model
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import canonical_json, strict_json, identity
from lob_sim.replay import reader
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.replay_engine import ResearchReplayEngine, ReplayWindow, research_config
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot


def old_function(commit, path, name, module, class_name=None):
    result = subprocess.run(
        ["git", "show", commit + ":" + path], cwd=ROOT, check=True, capture_output=True, text=True, timeout=30
    )
    text = result.stdout
    nodes = ast.parse(text).body
    if class_name is not None:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == class_name).body
    function = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)
    body = textwrap.dedent("\n".join(text.splitlines()[function.lineno - 1 : function.end_lineno]))
    namespace = dict(module.__dict__)
    namespace["asdict"] = asdict
    exec(compile(body, "<trusted-git-function:" + commit + ":" + path + ":" + name + ">", "exec"), namespace)
    return namespace[name], {
        "path": path,
        "function": name,
        "git_file_sha256": sha256(text.encode()).hexdigest(),
        "function_sha256": sha256(body.encode()).hexdigest(),
    }


def run(args):
    args.file, args.contract, args.model = (p.resolve() for p in (args.file, args.contract, args.model))
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    parents = {
        "input": input_identity(args.file),
        "contract_sha256": file_sha256(args.contract),
        "model_file_sha256": file_sha256(args.model),
        "environment_template_sha256": file_sha256(ROOT / ".env.example"),
        "code_identity": checkpoint_code_identity(),
        "script_sha256": file_sha256(Path(__file__)),
    }
    old_wire, wire_id = old_function(
        args.before_commit, "lob_sim/record/envelope.py", "as_dict", envelope, "EventEnvelope"
    )
    old_validate, validate_id = old_function(
        args.before_commit, "lob_sim/record/segmented.py", "validate_segment", segmented
    )
    old_recover, recover_id = old_function(
        args.before_commit, "lob_sim/record/segmented.py", "recover_valid_envelopes", segmented
    )
    old_risk, risk_id = old_function(args.before_commit, "lob_sim/regime/risk.py", "_row", risk, "RegimeRiskAudit")
    contract = strict_json(args.contract.read_text())
    window = ReplayWindow(**contract["window"])
    model = load_model(args.model)
    base = replace(
        load_config(str(ROOT / ".env.example"), inherit_environment=False),
        mm_enabled=True,
        mm_strategy_profile="research_mm",
        hmm=None,
        sim_latency_mode="fixed",
        sim_order_latency_ms=0.0,
        sim_cancel_latency_ms=0.0,
        sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
    )
    settings = {
        "baseline": None,
        "observe": HMMSettings(model, "BTCUSDT"),
        "policy": HMMSettings(model, "BTCUSDT", mode="policy"),
    }
    configurations = {
        mode: research_config(
            base, hmm=value, mm_strategy_profile="hmm_regime_mm" if mode == "policy" else "research_mm"
        )
        for mode, value in settings.items()
    }
    modes, rows, prototypes = tuple(settings), [], {}
    try:
        for iteration in range(args.warmups + args.repetitions):
            order = modes[iteration % len(modes) :] + modes[: iteration % len(modes)]
            for mode in order:
                for algorithm in ("before", "after") if iteration % 2 == 0 else ("after", "before"):
                    gc.collect()
                    with ExitStack() as context:
                        if algorithm == "before":
                            context.enter_context(patch.object(envelope.EventEnvelope, "as_dict", old_wire))
                            context.enter_context(patch.object(reader, "validate_segment", old_validate))
                            context.enter_context(patch.object(reader, "recover_valid_envelopes", old_recover))
                            context.enter_context(patch.object(risk.RegimeRiskAudit, "_row", old_risk))
                        start, cpu = time.perf_counter_ns(), time.process_time_ns()
                        engine = ResearchReplayEngine(
                            configurations[mode], window, retain_event_trace=False, retain_audit_rows=False
                        )
                        engine.run(args.file)
                        wall_ns, cpu_ns = time.perf_counter_ns() - start, time.process_time_ns() - cpu
                        probe = replay_probe(engine)
                        probe["full_state_sha256"] = engine.state_sha256()
                    if mode != "baseline" and probe["hmm"]["status_counts"].get("VALID", 0) == 0:
                        raise ValueError("paired experiment never reached valid inference")
                    if mode in prototypes and canonical_json(prototypes[mode]) != canonical_json(probe):
                        raise ValueError("paired full-state/event/fill/markout/accounting identity differs")
                    prototypes[mode] = probe
                    rows.append(
                        {
                            "iteration": iteration,
                            "warmup": iteration < args.warmups,
                            "mode": mode,
                            "algorithm": algorithm,
                            "wall_ns": wall_ns,
                            "cpu_ns": cpu_ns,
                            "records": probe["records_processed"],
                            "probe_sha256": identity(probe),
                        }
                    )
                    print(mode, algorithm, iteration, round(wall_ns / 1e9, 4), flush=True)
        if prototypes["baseline"]["core_sha256"] != prototypes["observe"]["core_sha256"]:
            raise ValueError("paired baseline/observe core equivalence failed")
        after = {
            "input": input_identity(args.file),
            "contract_sha256": file_sha256(args.contract),
            "model_file_sha256": file_sha256(args.model),
            "environment_template_sha256": file_sha256(ROOT / ".env.example"),
            "code_identity": checkpoint_code_identity(),
            "script_sha256": file_sha256(Path(__file__)),
        }
        if after != parents:
            raise ValueError("paired experiment inputs/source changed during execution")
        summaries = {}
        for mode in modes:
            summaries[mode] = {
                algorithm: {
                    metric: percentiles(
                        [
                            r[metric]
                            for r in rows
                            if r["mode"] == mode and r["algorithm"] == algorithm and not r["warmup"]
                        ]
                    )
                    for metric in ("wall_ns", "cpu_ns")
                }
                for algorithm in ("before", "after")
            }
        result = {
            "schema_version": "lob_sim.paired_research_scale.v1",
            "complete": True,
            "identities": parents,
            "before_commit": args.before_commit,
            "selected_before_functions": [wire_id, validate_id, recover_id, risk_id],
            "configurations": {k: config_snapshot(v) for k, v in configurations.items()},
            "rows": rows,
            "warmups": args.warmups,
            "repetitions": args.repetitions,
            "modes": summaries,
            "probes": prototypes,
            "os_process_peak_rss_bytes": peak_rss(),
            "scope": "current engine with explicit trusted old/new function bodies,balanced orders and full-state parity;aggregate process RSS includes all trials;not historical full-binary timing,event latency,private fills or a dedicated-host release threshold",
        }
        publish_json(root / "report.json", result)
        return result
    except BaseException as exc:
        publish_json(
            root / "failure.json",
            {
                "complete": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "completed_trials": len(rows),
                "rows": rows,
            },
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--before-commit", required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if (
        re.fullmatch("[0-9a-f]{40}", args.before_commit) is None
        or not 0 <= args.warmups <= 3
        or not 1 <= args.repetitions <= 30
    ):
        parser.error("need a full trusted local commit SHA,warmups 0..3 and repetitions 1..30")
    run(args)
