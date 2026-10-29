"""SYNTHETIC performance input, never empirical day admission or venue truth.

Generate once and reuse the exact finalized bytes for matched measurements.
Event content is deterministic; writer creation-time metadata is not. Save
both event-content and actual file identities rather than promising identical
compressed bytes across separate generation invocations.
"""

from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentedCaptureWriter
from lob_sim.regime.validation import canonical_json

WALL_NS = 1_735_689_600_000_000_000
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def generate(directory: Path, *, rounds: int, levels: int = 20, period_ns: int = 10_000_000):
    for name, value, cap in (
        ("rounds", rounds, 250_000),
        ("levels", levels, 2_000),
        ("period_ns", period_ns, 1_000_000_000),
    ):
        if type(value) is not int or not 1 <= value <= cap:
            raise ValueError("unsupported synthetic workload " + name)
    directory.mkdir(parents=True, exist_ok=False)
    sequence, digest = 0, sha256()
    with SegmentedCaptureWriter(directory, "synthetic_scale_v1", compression="zstd") as writer:

        def emit(t, kind, payload, symbol="*", route="control"):
            nonlocal sequence
            sequence += 1
            row = EventEnvelope(
                "synthetic_scale_v1", SCHEMA_V3, "BINANCE_USDM", symbol, kind, route, sequence, WALL_NS + t, t, payload
            )
            digest.update(canonical_json(row.as_dict()).encode("utf-8"))
            writer.write(row)

        emit(
            0,
            "captureMeta",
            {
                "schemaVersion": 3,
                "clock": "receive_time",
                "source_kind": "synthetic_performance_fixture",
                "generator": "fixed_period_two_symbol_depth_trade_v1",
            },
        )
        for symbol in SYMBOLS:
            emit(0, "exchangeInfo", {"tickSize": "0.1", "stepSize": "0.001", "venue": "BINANCE_USDM"}, symbol)
            for route in ("public", "market"):
                emit(0, "captureEvent", {"event": "connect", "route": route}, symbol, route)
        for symbol in SYMBOLS:
            emit(
                0,
                "snapshot",
                {
                    "lastUpdateId": 100,
                    "bids": [[f"{(9999 - i) // 10}.{(9999 - i) % 10}", "0.010"] for i in range(levels)],
                    "asks": [[f"{(10001 + i) // 10}.{(10001 + i) % 10}", "0.010"] for i in range(levels)],
                },
                symbol,
                "public",
            )
        for index in range(1, rounds + 1):
            for symbol in SYMBOLS:
                emit(
                    index * period_ns,
                    "depthUpdate",
                    {
                        "U": 100 if index == 1 else 100 + index,
                        "u": 100 + index,
                        "pu": 99 if index == 1 else 99 + index,
                        "b": [["999.9", "0.012" if index % 2 else "0.008"]],
                        "a": [["1000.1", "0.008" if index % 2 else "0.012"]],
                    },
                    symbol,
                    "public",
                )
                emit(
                    index * period_ns,
                    "aggTrade",
                    {"p": "999.9" if index % 2 else "1000.1", "q": "0.020", "m": bool(index % 2)},
                    symbol,
                    "market",
                )
        emit((rounds + 1) * period_ns, "captureEvent", {"event": "capture_trailer", "route": "control"})
        writer.update_manifest_metadata(
            {
                "writer": {"complete": True, "overflow_count": 0},
                "synthetic_performance_spec": {
                    "rounds": rounds,
                    "levels": levels,
                    "period_ns": period_ns,
                    "event_count": sequence,
                    "event_content_sha256": digest.hexdigest(),
                },
            }
        )
    return writer.manifest_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--levels", type=int, default=20)
    parser.add_argument("--period-ns", type=int, default=10_000_000)
    args = parser.parse_args()
    print(generate(args.out, rounds=args.rounds, levels=args.levels, period_ns=args.period_ns))
