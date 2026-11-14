"""Finite aggregate CSV field budget, restored after the reader closes.

Regime summary cells contain bounded state/source/horizon tensors and can
exceed Python's 128 KiB default. Detail-row readers keep their existing limit.
The CSV setting is process-global; the lock serializes our aggregate readers,
not arbitrary concurrent third-party CSV readers.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import csv
from threading import RLock

MAX_AGGREGATE_FIELD_CHARS = 8 * 1024 * 1024
_LOCK = RLock()


@contextmanager
def aggregate_csv_fields() -> Iterator[None]:
    with _LOCK:
        previous = csv.field_size_limit()
        csv.field_size_limit(MAX_AGGREGATE_FIELD_CHARS)
        try:
            yield
        finally:
            csv.field_size_limit(previous)
