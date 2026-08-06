from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

PROVENANCE_SCHEMA_VERSION = "lob_sim.provenance.v1"
_SENSITIVE_NAME_FRAGMENTS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def utc_now_iso() -> str:
    """Return a timezone-explicit UTC timestamp suitable for JSON output."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the entire fixture into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    digest = hashlib.sha256()
    with Path(path).open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_provenance(path: str | Path) -> dict[str, Any]:
    """Return stable identity metadata for an input fixture."""
    fixture_path = Path(path).resolve()
    stat = fixture_path.stat()
    if not fixture_path.is_file():
        raise ValueError(f"Fixture is not a regular file: {fixture_path}")
    return {
        "path": str(fixture_path),
        "size_bytes": stat.st_size,
        "sha256": sha256_file(fixture_path),
    }


def _is_sensitive_name(name: str) -> bool:
    normalized = name.casefold().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_NAME_FRAGMENTS)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
            if not _is_sensitive_name(field.name)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_sensitive_name(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Unsupported configuration value for provenance: {type(value)!r}")


def config_provenance(config: object) -> dict[str, Any]:
    """Serialize and fingerprint configuration while omitting secret fields."""
    if is_dataclass(config) and not isinstance(config, type):
        raw_items = [(field.name, getattr(config, field.name)) for field in fields(config)]
    elif hasattr(config, "__dict__"):
        raw_items = list(vars(config).items())
    else:
        raise TypeError("config must be a dataclass or expose __dict__")

    excluded_fields = sorted(name for name, _ in raw_items if _is_sensitive_name(name))
    values = {name: _json_safe(value) for name, value in sorted(raw_items) if not _is_sensitive_name(name)}
    canonical = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "fingerprint_algorithm": "sha256",
        "fingerprint_sha256": hashlib.sha256(canonical).hexdigest(),
        "excluded_fields": excluded_fields,
        "values": values,
    }


def runtime_provenance() -> dict[str, Any]:
    """Describe the interpreter, operating system, and available CPU identity."""
    cpu_model = (
        platform.processor()
        or os.environ.get("PROCESSOR_IDENTIFIER")
        or platform.uname().processor
        or "unknown"
    )
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "platform_string": platform.platform(),
        },
        "cpu": {
            "model": cpu_model,
            "logical_count": os.cpu_count(),
        },
    }


def source_tree_provenance(source_root: str | Path | None = None) -> dict[str, Any]:
    """Fingerprint the current Python source tree in stable path order."""
    root = Path(source_root).resolve() if source_root is not None else Path(__file__).resolve().parent
    source_files = sorted(path for path in root.rglob("*.py") if path.is_file())
    digest = hashlib.sha256()
    for source_file in source_files:
        relative_path = source_file.relative_to(root).as_posix()
        file_digest = sha256_file(source_file)
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return {
        "root": str(root),
        "python_file_count": len(source_files),
        "fingerprint_algorithm": "sha256(sorted relative path + NUL + file sha256 + newline)",
        "fingerprint_sha256": digest.hexdigest(),
    }


def build_run_provenance(input_path: str | Path, config: object) -> dict[str, Any]:
    """Build provenance reusable by benchmarks and simulation output writers."""
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "created_at_utc": utc_now_iso(),
        "fixture": fixture_provenance(input_path),
        "configuration": config_provenance(config),
        "environment": runtime_provenance(),
        "code": source_tree_provenance(),
    }
