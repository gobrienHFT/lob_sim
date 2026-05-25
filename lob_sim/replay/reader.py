from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..record.schema import RecordValidationError, validate_record_object


@dataclass(frozen=True)
class RecordedEvent:
    ts_local: float
    symbol: str
    type: str
    data: dict


def iter_records(path: str | Path, *, validate: bool = True) -> Iterator[RecordedEvent]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Replay file missing: {p}")

    opener = gzip.open if p.suffix == ".gz" else open
    mode = "rt"
    with opener(p, mode, encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecordValidationError(
                    f"invalid JSON: {exc.msg}",
                    path=p,
                    line_number=line_number,
                ) from exc
            if validate:
                validate_record_object(obj, path=p, line_number=line_number)
            yield RecordedEvent(
                ts_local=float(obj["ts_local"]),
                symbol=str(obj["symbol"]),
                type=str(obj["type"]),
                data=obj["data"],
            )
