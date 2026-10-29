"""Actual matching/export kill worker on the graph's SYNTHETIC three-case harness.

Only admission/model fitting/grid boundaries are substituted. No production
CLI flag grants these substitutions. Native engine, exports, independent
readers, statistics and frozen code/configuration identity remain active.
"""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import sys
import time
from unittest.mock import patch

from lob_sim.regime.validation import strict_json
from lob_sim.research.study_cases import expected_cases
from test_research_capture_intervals import cfg, SECOND


def reduced_grid(protocol, views):
    return [
        c
        for c in expected_cases(protocol, views)
        if c["interval_ns"] == SECOND and c["latency_ms"] == 0 and c["fill_scenario"] == "conservative"
    ]


def main(root, output, store, mode):
    import lob_sim.research.study as producer
    import lob_sim.research.study_reader as reader
    from lob_sim.research.study_replay import GuardedResearchEngine

    original = GuardedResearchEngine._trace

    def trace(engine, *args, **kwargs):
        if mode == "reuse" or (mode == "resume" and engine.cfg.hmm is None):
            raise AssertionError("completed case was re-executed instead of verified/reused")
        original(engine, *args, **kwargs)
        if mode == "interrupt" and engine.cfg.hmm is not None and engine._resource_count == 100:
            print("MATCHING_READY_TO_KILL", flush=True)
            while True:
                time.sleep(0.1)

    with ExitStack() as context:
        for module in (producer, reader):
            for name in ("verify_prepared", "verify_registered_models", "verify_views"):
                context.enter_context(patch.object(module, name, lambda *a, **k: {"synthetic_test_only": True}))
            context.enter_context(patch.object(module, "admitted_parents", lambda *a, **k: {}))
            context.enter_context(patch.object(module, "expected_cases", reduced_grid))
        context.enter_context(patch.object(GuardedResearchEngine, "_trace", trace))
        registry = root / "registry.json"
        result = producer.run_registered_study(
            (next((root / "raw").glob("*.manifest.json")),),
            root / "dummy_days",
            registry,
            root / "dummy_prepared",
            root / "models",
            root / "views",
            output,
            replace(cfg(), mm_requote_ms=1_000.0),
            registry_sha256=strict_json(registry.read_text())["registry_sha256"],
            case_store=store,
            resume_store=mode in {"resume", "reuse"},
        )
    assert len(result["results"]) == 3
    print("VERIFIED_COMPLETE", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
