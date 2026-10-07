"""Bounded evidence readers and best-effort failure receipts.

Failure reporting must not replace the original failure or remove partial
evidence. Successful publication uses the existing exclusive fsynced writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import strict_json

JSON_LIMIT = 8 * 1024 * 1024
STUDY_JSON_LIMIT = 64 * 1024 * 1024


def read_json(path: Path, *, maximum_bytes: int = JSON_LIMIT) -> dict[str, Any]:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= STUDY_JSON_LIMIT:
        raise ValueError("unsupported evidence JSON size budget")
    with path.open("rb") as handle:
        raw = handle.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise ValueError("evidence JSON exceeds its explicit size limit")
    value = strict_json(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence JSON must be an object")
    return value


def require_finalized(root: Path, manifest: str) -> None:
    if (root / "failure.json").exists() or any(root.glob("*.partial")) or not (root / manifest).is_file():
        raise ValueError("evidence directory is incomplete")


def retain_failure(root: Path, exc: BaseException, *, schema: str, **context: Any) -> None:
    """Preserve the first failure; only serialize types, not private error text."""
    try:
        publish_json(
            root / "failure.json",
            {"schema_version": schema, "complete": False, "error_type": type(exc).__name__, **context},
        )
    except BaseException as secondary:
        # Failure publication may itself fail (disk full, fsync, race). The
        # original exception remains the one the caller receives. The absent
        # final report or retained partial still prevents admission.
        exc.add_note("Failure receipt could not be published: " + type(secondary).__name__)
