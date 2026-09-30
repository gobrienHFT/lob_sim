"""Portable, JSON-only state encoding for deterministic simulation checkpoints.

Checkpoints are intentionally not pickles.  A checkpoint is an auditable JSON
document with canonical bytes, so a reviewer can inspect or hash it without
executing arbitrary code.  Dataclass restoration is restricted to this
package; untrusted checkpoint files must still be treated as data, not as a
venue or execution authority.
"""

from __future__ import annotations

import base64
import importlib
import inspect
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..oracle import canonical_bytes
from ..replay.adapters import ReplayFeedAdapter, adapter_metadata

CHECKPOINT_SCHEMA_VERSION = "lob_sim.simulation_checkpoint.v3"


def checkpoint_code_identity(package_root: Path | None = None) -> dict[str, Any]:
    """Identify Python source bytes without requiring a Git checkout.

    Include untracked modules and installed-wheel source, but exclude bytecode,
    generated outputs and documentation. Frame sorted relative names and source
    digests so moving an identical package does not change its identity.
    """

    root = package_root if package_root is not None else Path(__file__).resolve().parents[1]
    sources = sorted(root.rglob("*.py"), key=lambda path: path.relative_to(root).as_posix())
    if not sources:
        raise ValueError("checkpoint requires readable Python package source")
    digest = sha256()
    for path in sources:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        source = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                source.update(chunk)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(source.digest())
    return {
        "schema_version": "lob_sim.checkpoint_code_identity.v1",
        "algorithm": "sha256",
        "file_count": len(sources),
        "sha256": digest.hexdigest(),
    }


def checkpoint_adapter_identity(adapter: ReplayFeedAdapter) -> dict[str, Any]:
    """Bind the declared adapter contract and its class's source module."""

    adapter_type = type(adapter)
    try:
        source_path = inspect.getsourcefile(adapter_type)
        if source_path is None:
            raise ValueError("adapter has no readable Python source")
        source = sha256()
        with Path(source_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                source.update(chunk)
    except (OSError, TypeError) as exc:
        raise ValueError("checkpoint adapter requires readable Python source") from exc
    return {
        "schema_version": "lob_sim.checkpoint_adapter_identity.v1",
        "class": f"{adapter_type.__module__}:{adapter_type.__qualname__}",
        "source_sha256": source.hexdigest(),
        "metadata": adapter_metadata(adapter),
    }


def _qualified_name(value: object) -> str:
    value_type = type(value)
    return f"{value_type.__module__}:{value_type.__qualname__}"


def encode(value: Any) -> Any:
    """Encode mutable engine state using JSON-compatible tagged values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite float cannot enter a simulation checkpoint")
        return value
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": "dataclass",
            "class": _qualified_name(value),
            "fields": {field.name: encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Mapping):
        items = [[encode(key), encode(item)] for key, item in value.items()]
        items.sort(key=lambda pair: canonical_bytes(pair[0]))
        return {"__type__": "mapping", "items": items}
    if isinstance(value, deque):
        return {"__type__": "deque", "items": [encode(item) for item in value]}
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [encode(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = [encode(item) for item in value]
        items.sort(key=canonical_bytes)
        return {"__type__": "set", "items": items}
    if isinstance(value, list):
        return [encode(item) for item in value]
    raise TypeError(f"unsupported checkpoint value: {type(value)!r}")


def _resolve_class(qualified_name: str) -> type[Any]:
    module_name, separator, qualname = qualified_name.partition(":")
    if not separator or not module_name.startswith("lob_sim."):
        raise ValueError(f"checkpoint dataclass is outside the lob_sim package: {qualified_name!r}")
    value: Any = importlib.import_module(module_name)
    for component in qualname.split("."):
        value = getattr(value, component)
    if not isinstance(value, type) or not is_dataclass(value):
        raise ValueError(f"checkpoint class is not a dataclass: {qualified_name!r}")
    return value


def decode(value: Any) -> Any:
    """Decode values produced by :func:`encode`, rejecting unknown tags."""

    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict) or "__type__" not in value:
        if isinstance(value, dict):
            return {key: decode(item) for key, item in value.items()}
        return value

    value_type = value.get("__type__")
    if value_type == "decimal":
        return Decimal(str(value["value"]))
    if value_type == "bytes":
        return base64.b64decode(str(value["value"]).encode("ascii"), validate=True)
    if value_type == "mapping":
        return {decode(pair[0]): decode(pair[1]) for pair in value["items"]}
    if value_type == "deque":
        return deque(decode(item) for item in value["items"])
    if value_type == "tuple":
        return tuple(decode(item) for item in value["items"])
    if value_type == "set":
        return set(decode(item) for item in value["items"])
    if value_type == "dataclass":
        cls = _resolve_class(str(value["class"]))
        return cls(**{str(key): decode(item) for key, item in value["fields"].items()})
    raise ValueError(f"unknown checkpoint value tag: {value_type!r}")


def dumps_state(value: Mapping[str, Any]) -> str:
    """Return canonical JSON for diagnostics and independent hash checks."""

    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
