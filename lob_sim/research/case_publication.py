"""Zero-copy publication of a previously verified case.

The caller must independently verify both the source and destination. Native
bytes are not rewritten to hide prior locations. Cross-volume hard-link
failure is explicit; no implicit copy/re-serialization fallback is used.
"""

from pathlib import Path
import os
from typing import Any


def materialize_case(source_root: Path, target_root: Path, record: dict[str, Any]) -> Path:
    source_root, target_root = Path(source_root).resolve(), Path(target_root).resolve()
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("publication root cannot contain or be contained by the case workspace")
    relative = record["run_directory"]
    source = (source_root / relative).resolve()
    target = (target_root / relative).resolve()
    if not source.is_relative_to(source_root) or not target.is_relative_to(target_root):
        raise ValueError("case publication path escapes its root")
    names = [binding["path"] for binding in record["artifacts"].values()]
    if record["tables"] is not None:
        names.append(record["tables"]["path"])
    if len(names) != len(set(names)) or any(not isinstance(n, str) or Path(n).name != n for n in names):
        raise ValueError("case publication artifact names alias or escape")
    target.mkdir(parents=True, exist_ok=False)
    for name in names:
        original, destination = (source / name).resolve(), target / name
        if not original.is_relative_to(source):
            raise ValueError("case source artifact escapes its workspace")
        os.link(original, destination)
    return target
