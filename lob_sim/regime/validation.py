"""Strict, dependency-free numeric and JSON contracts for frozen models."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def identity(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        parsed = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be a finite number")
    return parsed


def integer(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def vector(values: Sequence[object], name: str, size: int | None = None) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        raise ValueError(f"{name} must be a numeric array")
    result = tuple(finite(value, name) for value in values)
    if not result or (size is not None and len(result) != size):
        raise ValueError(f"{name} dimension mismatch")
    return result


def probabilities(values: Sequence[object], name: str, size: int) -> tuple[float, ...]:
    result = vector(values, name, size)
    if any(value < 0 or value > 1 for value in result) or not math.isclose(
        math.fsum(result), 1.0, rel_tol=0, abs_tol=1e-10
    ):
        raise ValueError(f"{name} must contain normalized nonnegative probabilities")
    return result


def require_keys(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} has missing or unknown fields")
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def strict_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    return json.loads(
        text,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=lambda value: finite(float(value), "JSON number"),
    )
