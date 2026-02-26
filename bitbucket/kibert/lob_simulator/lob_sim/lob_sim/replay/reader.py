from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class RecordedEvent:
    ts_local: float
    symbol: str
    type: str
    data: dict


def iter_records(path: str | Path) -> Iterator[RecordedEvent]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Replay file missing: {p}")

    opener = gzip.open if p.suffix == ".gz" else open
    mode = "rt"
    with opener(p, mode, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            yield RecordedEvent(
                ts_local=float(obj["ts_local"]),
                symbol=str(obj["symbol"]),
                type=str(obj["type"]),
                data=obj["data"],
            )
