"""Bounded native audit export for an opt-in, source/day study window."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from lob_sim.research.replay_engine import ResearchConfig, ResearchReplayEngine, ReplayWindow
from lob_sim.sim.export import StreamingSimulationExport


class GuardedResearchEngine(ResearchReplayEngine):
    """Check disk at bounded emission intervals; keep native sinks unchanged."""

    def __init__(self, cfg: ResearchConfig, window: ReplayWindow, *, minimum_free: int, **kwargs: Any):
        self._resource_root, self._minimum_free, self._resource_count = cfg.output_dir, minimum_free, 0
        super().__init__(cfg, window, **kwargs)

    def _trace(self, *args: Any, **kwargs: Any) -> None:
        if self._resource_count % 10_000 == 0 and shutil.disk_usage(self._resource_root).free < self._minimum_free:
            raise RuntimeError("registered study disk floor reached; incomplete evidence retained")
        self._resource_count += 1
        super()._trace(*args, **kwargs)


def run_study_replay(
    cfg: ResearchConfig, path: Path, window: ReplayWindow, *, minimum_free_disk_bytes: int
) -> tuple[dict[str, Path], dict[str, Any]]:
    export = StreamingSimulationExport.create(path, cfg)
    with export:
        engine = GuardedResearchEngine(
            cfg,
            window,
            minimum_free=minimum_free_disk_bytes,
            event_sink=export.event_sink,
            fill_sink=export.fill_sink,
            markout_sink=export.markout_sink,
            retain_event_trace=False,
            retain_audit_rows=False,
            regime_sink=export.regime_sink,
            regime_execution_sink=export.regime_execution_sink,
            regime_quote_sink=export.regime_quote_sink,
            regime_risk_sink=export.regime_risk_sink,
        )
        metrics = engine.run(path)
    export.assert_row_counts(
        event_trace=int(engine.event_trace_retention()["rows_emitted"]),
        fills=metrics.fill_count,
        markouts=metrics.markout_event_count,
    )
    files, summary = engine.finalize_streaming_outputs(path, metrics, export.output_files, export.manifest_seed)
    export.mark_complete()
    return files, summary
