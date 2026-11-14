"""Copy an explicit bounded list of diagnostic evidence bytes without rewriting.

The resulting inventory binds bytes, not benchmark authenticity or private
execution truth. Source reports retain their own producer identities and paths.
It deliberately cannot recursively copy a raw tape or simulation directory.
"""

import argparse
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lob_sim.regime.dataset import publish_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import retain_failure

MAX_FILE_BYTES = 8 * 1024**2
MAX_TOTAL_BYTES = 64 * 1024**2


def publish(output, bindings):
    if not 1 <= len(bindings) <= 64:
        raise ValueError("select 1..64 evidence files explicitly")
    output = output.resolve()
    selected, names, total = [], set(), 0
    for label, source in bindings:
        source = source.resolve()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", label) is None or label in names:
            raise ValueError("unsafe or duplicate evidence label")
        names.add(label)
        if not source.is_relative_to(ROOT) or source.is_relative_to(output):
            raise ValueError("evidence must come from this repository outside the new publication")
        size = source.stat().st_size
        total += size
        if not source.is_file() or size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("evidence file/total byte budget exceeded")
        selected.append((label, source, size, file_sha256(source)))
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    try:
        for label, source, size, digest in selected:
            with source.open("rb") as src, (output / label).open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if source.stat().st_size != size or file_sha256(source) != digest or file_sha256(output / label) != digest:
                raise ValueError("evidence changed during publication")
            entries.append(
                {"path": label, "source": source.relative_to(ROOT).as_posix(), "sha256": digest, "size_bytes": size}
            )
        # Recheck the entire census, not only each file immediately after copy.
        if any(p.stat().st_size != n or file_sha256(p) != sha for _, p, n, sha in selected):
            raise ValueError("evidence parents changed before publication")
        result = {
            "schema_version": "lob_sim.research_scale_evidence_inventory.v1",
            "complete": True,
            "claim_ready": False,
            "publisher_sha256": file_sha256(Path(__file__)),
            "files": entries,
            "scope": "byte-identical selected diagnostic reports;producer identities remain inside reports;not independently authenticated measurements,full native run bundles,empirical admission or release completion",
        }
        publish_json(output / "inventory.json", result)
        return result
    except BaseException as exc:
        retain_failure(
            output, exc, schema="lob_sim.research_scale_evidence_publication_failure.v1", copied_files=len(entries)
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence", action="append", required=True, metavar="NAME=PATH")
    args = parser.parse_args()
    bindings = []
    for value in args.evidence:
        label, separator, path = value.partition("=")
        if not separator or not path:
            parser.error("each evidence argument requires NAME=PATH")
        bindings.append((label, Path(path)))
    publish(args.out, bindings)
