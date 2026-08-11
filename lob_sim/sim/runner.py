"""High-level simulation runners with explicit retention contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..replay.adapters import DEFAULT_REPLAY_ADAPTER, ReplayFeedAdapter
from .engine import SimulationEngine
from .export import StreamingSimulationExport


def run_bounded_simulation(
    cfg: Config,
    file_path: str | Path,
    *,
    verbose: bool = False,
    progress_every: int = 5000,
    adapter: ReplayFeedAdapter = DEFAULT_REPLAY_ADAPTER,
) -> tuple[dict[str, Path], dict[str, Any]]:
    """Run with bounded audit retention and transactionally finalize artifacts."""

    export = StreamingSimulationExport.create(file_path, cfg, adapter=adapter)
    with export:
        engine = SimulationEngine(
            cfg,
            adapter=adapter,
            event_sink=export.event_sink,
            fill_sink=export.fill_sink,
            markout_sink=export.markout_sink,
            retain_event_trace=False,
            retain_audit_rows=False,
        )
        metrics = engine.run(file_path, verbose=verbose, progress_every=progress_every)
    export.assert_row_counts(
        event_trace=int(engine.event_trace_retention()["rows_emitted"]),
        fills=metrics.fill_count,
        markouts=metrics.markout_event_count,
    )
    output_files, summary = engine.finalize_streaming_outputs(
        file_path,
        metrics,
        export.output_files,
        export.manifest_seed,
    )
    export.mark_complete()
    return output_files, summary
