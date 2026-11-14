"""Generate-once synthetic benchmark tapes/views with retained byte identities."""

import argparse
from dataclasses import asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.research_scale_fixture import generate, WALL_NS
from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import identity
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.day_view import view_specification, write_day_view
from lob_sim.research.replay_engine import ReplayWindow
from lob_sim.sim.checkpoint import checkpoint_code_identity


def prepare(root, rounds, levels, period_ns=10_000_000):
    root.mkdir(parents=True, exist_ok=False)
    processor = checkpoint_code_identity()
    start = time.perf_counter()
    source = generate(root / "raw", rounds=rounds, levels=levels, period_ns=period_ns)
    print(f"raw generated: {4 * rounds + 10} records in {time.perf_counter() - start:.2f}s", flush=True)
    spec = {
        "synthetic_performance_only": True,
        "rounds": rounds,
        "levels": levels,
        "period_ns": period_ns,
        "scope": "fixed-period synthetic;never an eligible empirical day",
    }
    window = ReplayWindow("BTCUSDT", period_ns, 30_000_000_000 + period_ns, (rounds + 1) * period_ns)
    contract = view_specification(
        file_sha256(source), identity(spec), identity(spec), "2025-01-01", CaptureClock(0, WALL_NS), window
    )
    # A performance fixture is not an admitted research registration. Keep
    # that distinction structural, not only a prose disclaimer.
    contract["schema_version"] = "lob_sim.synthetic_benchmark_view.v1"
    contract["registry_sha256"] = None
    contract["diagnostic_plan_sha256"] = identity(spec)
    contract["scope"] += ";SYNTHETIC_PERFORMANCE_ONLY;not registered empirical research"
    entry = write_day_view(source, root / "view", contract, minimum_free_disk_bytes=2 * 1024**3)
    publish_json(root / "contract.json", contract)
    if checkpoint_code_identity() != processor:
        raise ValueError("processing code changed during fixture preparation")
    publish_json(
        root / "fixture.json",
        {
            "schema_version": "lob_sim.synthetic_scale_fixture.v1",
            "processor": processor,
            "preparer_sha256": file_sha256(Path(__file__)),
            "generator_sha256": file_sha256(Path(generate.__code__.co_filename)),
            "specification": spec,
            "source": {"path": source.relative_to(root).as_posix(), "sha256": file_sha256(source)},
            "view": {"path": "view/" + entry["path"], "sha256": entry["sha256"]},
            "window": asdict(window),
            "contract_sha256": file_sha256(root / "contract.json"),
            "preparation_seconds": time.perf_counter() - start,
        },
    )
    print(root / "fixture.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--levels", type=int, default=20)
    parser.add_argument("--period-ns", type=int, default=10_000_000)
    args = parser.parse_args()
    if (
        not 1 <= args.rounds <= 250_000
        or not 1 <= args.period_ns <= 1_000_000_000
        or not 30_000_000_000 < args.rounds * args.period_ns <= 3_600_000_000_000
    ):
        parser.error("need unchanged 30s warmup and <=1h bounded synthetic duration")
    prepare(args.out, args.rounds, args.levels, args.period_ns)
