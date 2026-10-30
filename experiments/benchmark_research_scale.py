"""Diagnostic joint-research workload worker; not a registered empirical study.

Fresh process per invocation; warm trials construct fresh engines but reuse
loaded code/model and OS caches. Cold OS-cache eviction is not attempted.
Allocation tracing and cProfile runs must be invoked separately from timing.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import replace
import gc
import os
from pathlib import Path
import platform
import pstats
import sys
import time
import tracemalloc

IMPORT_START_NS = time.perf_counter_ns()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.benchmark_hmm_overhead import replay_probe
from lob_sim.audit.streaming_bundle import audit_streaming_bundle
from lob_sim.config import load_config
from lob_sim.regime.artifact import load_model
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.settings import HMMSettings
from lob_sim.regime.validation import identity, strict_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.replay.reader import iter_records
from lob_sim.research.replay_engine import research_config, ResearchReplayEngine, ReplayWindow
from lob_sim.research.capture_receipts import load_capture_manifest
from lob_sim.research.study_replay import run_study_replay
from lob_sim.research.study_tables import reduce_tables, publish_tables
from lob_sim.sim.checkpoint import checkpoint_code_identity
from lob_sim.sim.run_manifest import config_snapshot

PACKAGE_IMPORT_NS = time.perf_counter_ns() - IMPORT_START_NS


def peak_rss():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_working",
                    "working",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                    "private",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak_working
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def input_identity(path):
    manifest = load_capture_manifest(path)
    children = []
    root = path.parent.resolve()
    for entry in manifest["segments"]:
        segment = (root / entry["path"]).resolve()
        if not segment.is_relative_to(root):
            raise ValueError("benchmark segment escapes input directory")
        digest, size = file_sha256(segment), segment.stat().st_size
        if digest != entry["file_sha256"] or size != entry["size_bytes"]:
            raise ValueError("benchmark segment identity differs from input manifest")
        children.append({"path": entry["path"], "sha256": digest, "size_bytes": size})
    return {"manifest_sha256": file_sha256(path), "segments": children}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    contract = strict_json(args.contract.read_text())
    path = args.file.resolve()
    window = ReplayWindow(**contract["window"])
    before = {
        "input": input_identity(path),
        "contract_sha256": file_sha256(args.contract),
        "environment_template_sha256": file_sha256(ROOT / ".env.example"),
        "model_file_sha256": file_sha256(args.model),
        "code_identity": checkpoint_code_identity(),
        "script_sha256": file_sha256(Path(__file__)),
    }
    start = time.perf_counter_ns()
    model = load_model(args.model) if args.mode != "baseline" else None
    setting = None if model is None else HMMSettings(model, "BTCUSDT", mode=args.mode)
    cfg = research_config(
        replace(
            load_config(str(ROOT / ".env.example"), inherit_environment=False),
            mm_enabled=True,
            mm_strategy_profile="research_mm",
            hmm=None,
            sim_markout_horizons_ms=(100, 1000, 5000, 30_000),
            sim_latency_mode="fixed",
            sim_order_latency_ms=0.0,
            sim_cancel_latency_ms=0.0,
        ),
        hmm=setting,
        mm_strategy_profile="hmm_regime_mm" if args.mode == "policy" else "research_mm",
        record_dir=args.out / "runs",
    )
    initialization_ns = time.perf_counter_ns() - start
    rows, prototype = [], None
    profiler = cProfile.Profile() if args.profile else None
    for index in range(args.warmups + args.repetitions):
        gc.collect()
        if args.memory:
            tracemalloc.start()
        if profiler:
            profiler.enable()
        start, cpu = time.perf_counter_ns(), time.process_time_ns()
        if args.workload == "raw":
            count = sum(1 for _ in iter_records(path))
            engine, files, summary = None, None, None
        elif args.workload == "null":
            engine = ResearchReplayEngine(cfg, window, retain_event_trace=False, retain_audit_rows=False)
            engine.run(path)
            count = engine.metrics.get_summary(engine._books, engine._specs)["event_counts"]["records_processed"]
            files, summary = None, None
        else:
            engine = None
            files, summary = run_study_replay(cfg, path, window, minimum_free_disk_bytes=2 * 1024**3)
            count = summary["event_counts"]["records_processed"]
        wall_ns, cpu_ns = time.perf_counter_ns() - start, time.process_time_ns() - cpu
        if profiler:
            profiler.disable()
        memory = None
        if args.memory:
            current, peak = tracemalloc.get_traced_memory()
            retained = sum(s.count for s in tracemalloc.take_snapshot().statistics("filename"))
            memory = {
                "traced_current_bytes": current,
                "traced_peak_bytes": peak,
                "live_traced_allocation_count": retained,
                "scope": "traced live allocations,not allocation churn or per-event no-allocation proof",
            }
            tracemalloc.stop()
        probe = replay_probe(engine) if engine is not None else {"records_processed": count}
        if args.workload == "null" and args.mode != "baseline" and probe["hmm"]["status_counts"].get("VALID", 0) == 0:
            raise ValueError("benchmark never reached valid HMM inference")
        if summary is not None:
            probe = {
                k: summary[k]
                for k in (
                    "event_counts",
                    "quote_count",
                    "cancel_count",
                    "fill_count",
                    "order_lifecycle_counts",
                    "audit_retention",
                    "event_trace_retention",
                    "state_sha256",
                )
            }
            probe["hmm"] = summary.get("hmm")
        if prototype is not None and probe != prototype:
            raise ValueError("workload results changed across repetitions")
        prototype = probe
        audit = tables = None
        output_bytes, audit_ns, tables_ns = 0, 0, 0
        if files is not None:
            start = time.perf_counter_ns()
            audit = audit_streaming_bundle(files["summary"].parent, input_override=path)
            if audit["ok"] is not True:
                raise ValueError("benchmark native audit failed: " + repr(audit["issues"]))
            audit_ns = time.perf_counter_ns() - start
            if model is not None:
                start = time.perf_counter_ns()
                tables = publish_tables(
                    files["summary"].parent / "clock_periods.jsonl", reduce_tables(files, summary, contract)
                )
                tables_ns = time.perf_counter_ns() - start
            output_bytes = sum(p.stat().st_size for p in files["summary"].parent.iterdir() if p.is_file())
        rows.append(
            {
                "index": index,
                "first_trial_in_process": index == 0,
                "warmup": index < args.warmups,
                "wall_ns": wall_ns,
                "cpu_ns": cpu_ns,
                "records": count,
                "records_per_second": count * 1e9 / wall_ns,
                "os_process_peak_rss_bytes": peak_rss(),
                "memory": memory,
                "native_verification_wall_ns": audit_ns,
                "table_reduction_wall_ns": tables_ns,
                "audit_ok": audit["ok"] if audit else None,
                "table_binding": tables,
                "output_bytes": output_bytes,
                "bytes_per_record": output_bytes / count if count else None,
            }
        )
        print(f"{args.workload}/{args.mode} {index}: {wall_ns / 1e9:.3f}s, {count} records", flush=True)
    if profiler:
        profiler.dump_stats(str(args.out / "profile.pstats"))
        stats = pstats.Stats(profiler)
        profile = [
            {
                "file": f,
                "line": line,
                "function": name,
                "primitive_calls": v[0],
                "total_calls": v[1],
                "self_seconds": v[2],
                "cumulative_seconds": v[3],
            }
            for (f, line, name), v in stats.stats.items()
        ]
        publish_json(
            args.out / "profile.json",
            {
                "functions": sorted(profile, key=lambda r: -r["cumulative_seconds"]),
                "scope": "overlapping call-tree times are not additive;profile overhead not ordinary throughput",
            },
        )
    after = {
        "input": input_identity(path),
        "contract_sha256": file_sha256(args.contract),
        "environment_template_sha256": file_sha256(ROOT / ".env.example"),
        "model_file_sha256": file_sha256(args.model),
        "code_identity": checkpoint_code_identity(),
        "script_sha256": file_sha256(Path(__file__)),
    }
    if before != after:
        raise ValueError("benchmark inputs,code or worker changed during execution")
    report = {
        "schema_version": "lob_sim.research_scale_worker.v1",
        "identities": before,
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
            "cpu": platform.processor(),
            "logical_processors": os.cpu_count(),
            "pinned_core": False,
            "exclusive_host": False,
        },
        "mode": args.mode,
        "workload": args.workload,
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "initialization_ns": initialization_ns,
        "package_import_wall_ns": PACKAGE_IMPORT_NS,
        "initialization_scope": "package import and first model/configuration load reported separately;every replay constructs a fresh engine;OS cache is not flushed;not total process-spawn latency",
        "configuration": config_snapshot(cfg),
        "probe": prototype,
        "probe_sha256": identity(prototype),
        "rows": rows,
        "profiled": args.profile,
        "memory_traced": args.memory,
        "scope": "synthetic joint-book single-quote research diagnostic;offline CPU/wall cost,not exchange latency;OS peak includes imports and prior trials;no empirical admission",
    }
    publish_json(args.out / "report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "observe", "policy"), required=True)
    parser.add_argument("--workload", choices=("raw", "null", "audit"), required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.warmups <= 3 or not 1 <= args.repetitions <= 30 or (args.profile and args.memory):
        parser.error("bounded runs require warmups 0..3,repetitions 1..30,and separate profile/memory trials")
    try:
        run(args)
    except BaseException as exc:
        if args.out.is_dir() and not (args.out / "failure.json").exists():
            publish_json(
                args.out / "failure.json",
                {
                    "schema_version": "lob_sim.research_scale_failure.v1",
                    "complete": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        raise
