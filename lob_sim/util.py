from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (TypeError, InvalidOperation) as exc:
        raise ValueError(f"Could not convert value to Decimal: {value}") from exc


def decimal_to_float(value: Decimal) -> float:
    return float(value)


@dataclass
class TokenBucket:
    rate_per_second: float
    capacity: float | None = None

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            self.rate_per_second = 0.0
            self.capacity = 0.0
        if self.capacity is None:
            self.capacity = self.rate_per_second
        self._tokens = float(self.capacity)
        self._updated = asyncio.get_event_loop().time()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        if self.rate_per_second <= 0:
            return
        async with self._lock:
            while True:
                now = asyncio.get_event_loop().time()
                elapsed = now - self._updated
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
                self._updated = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                missing = n - self._tokens
                sleep_s = missing / self.rate_per_second
                await asyncio.sleep(max(0.0, sleep_s))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
