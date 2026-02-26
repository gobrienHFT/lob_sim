from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class NDJSONRecord:
    ts_local: float
    symbol: str
    type: str
    data: dict

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts_local": self.ts_local,
                "symbol": self.symbol,
                "type": self.type,
                "data": self.data,
            },
            separators=(",", ":"),
        )


def snapshot_payload(last_update_id: int, bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> dict:
    return {
        "lastUpdateId": last_update_id,
        "bids": bids,
        "asks": asks,
    }
