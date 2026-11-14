"""Read-only segment/prefix forensics, never capture completion or admission.

Do not synthesize a missing capture manifest, finalize a partial segment or
alter original files. Native validation establishes structural checks only;
it is not independent book reconstruction or proof of upstream delivery.
"""

from dataclasses import asdict
from pathlib import Path
import argparse
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lob_sim.record.segmented import validate_segment
from lob_sim.regime.dataset import publish_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.sim.checkpoint import checkpoint_code_identity


def audit(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    if target.is_relative_to(source):
        raise ValueError("forensic output must remain outside original evidence")
    if target.exists() or target.with_name(target.name + ".partial").exists():
        raise ValueError("forensic output already exists")
    paths = sorted(source.glob("*.ndjson.zst*"))
    if not paths or any(not p.is_file() or not p.resolve().is_relative_to(source) for p in paths):
        raise ValueError("missing or unsafe original compressed segments")
    initial = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, file_sha256(p)) for p in paths}
    processor = checkpoint_code_identity()
    rows, previous, contiguous, finalized = [], None, True, 0
    start = time.perf_counter()
    for path in paths:
        result = validate_segment(path)
        rows.append(
            {
                **asdict(result),
                "path": path.name,
                "ok": result.ok,
                "size_bytes": initial[path.name][0],
                "file_sha256": initial[path.name][2],
            }
        )
        if result.count:
            if previous is not None and result.first_recv_seq != previous + 1:
                contiguous = False
            if result.last_recv_seq - result.first_recv_seq + 1 != result.count:
                contiguous = False
            previous = result.last_recv_seq
        if path.suffix != ".partial":
            finalized += result.count
        print(f"{len(rows)}/{len(paths)} {path.name}: records={result.count}; ok={result.ok}", flush=True)
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, file_sha256(p)) for p in paths}
    if (
        after != initial
        or set(initial) != {p.name for p in source.glob("*.ndjson.zst*")}
        or processor != checkpoint_code_identity()
    ):
        raise ValueError("source or processing code changed during audit")
    report = {
        "schema_version": "lob_sim.segment_prefix_forensics.v1",
        "processor": processor,
        "script_sha256": file_sha256(Path(__file__)),
        "source_bytes_unchanged": True,
        "completion_record_present": (source / "completion.json").exists(),
        "capture_manifest_present": bool(list(source.glob("*.manifest.json"))),
        "finalized_segment_records": finalized,
        "checked_sequence_ranges_contiguous": contiguous,
        "segments": rows,
        "elapsed_seconds": time.perf_counter() - start,
        "scope": "read-only native segment checks/prefix census;not capture completion,clock validity,book reconstruction,upstream delivery or UTC-day admission",
    }
    publish_json(target, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    audit(args.source, args.out)
