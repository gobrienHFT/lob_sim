from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence

from lob_sim.record.envelope import LogicalTime
from lob_sim.sim.synthetic_exchange import SyntheticExchange


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CHECKPOINT_INTERVAL = 257

SyntheticOperation = tuple[int, int, int, bool, int | None, int, bool, bool]


def _python_apply_batch(
    bids: dict[int, int],
    asks: dict[int, int],
    changes: list[tuple[bool, int, int]],
) -> tuple[dict[int, int], dict[int, int]]:
    candidate_bids = dict(bids)
    candidate_asks = dict(asks)
    for is_bid, price_tick, qty_lots in changes:
        if price_tick <= 0:
            raise ValueError("price tick must be positive")
        if qty_lots < 0:
            raise ValueError("quantity lots must be non-negative")
        levels = candidate_bids if is_bid else candidate_asks
        if qty_lots == 0:
            levels.pop(price_tick, None)
        else:
            levels[price_tick] = qty_lots
    if candidate_bids and candidate_asks and max(candidate_bids) >= min(candidate_asks):
        raise ValueError("batch would cross the book")
    return candidate_bids, candidate_asks


def _synthetic_state_sha256(exchange: SyntheticExchange) -> str:
    """Hash the exact-MBO state using the Rust kernel's public parity encoding."""

    snapshot = exchange.snapshot()
    pieces: list[str] = []
    for side in ("bid", "ask"):
        levels = snapshot["bids" if side == "bid" else "asks"]
        for level in sorted(levels, key=lambda row: int(row["price_tick"])):
            order_ids = ",".join(str(int(order["order_id"])) for order in level["orders"])
            pieces.append(f"{side}:{int(level['price_tick'])}:{order_ids};")
    orders = snapshot["orders"]
    for order_id in sorted(orders, key=int):
        order = orders[order_id]
        price = "none" if order["price_tick"] is None else str(int(order["price_tick"]))
        pieces.append(
            f"order:{int(order_id)}:{int(order['participant_id'])}:{order['side']}:"
            f"{price}:{int(order['original_lots'])}:{int(order['remaining_lots'])}:"
            f"{int(order['arrival_sequence'])}:{order['time_in_force']}:"
            f"{str(bool(order['post_only'])).lower()}:{order['state']};"
        )
    return hashlib.sha256("".join(pieces).encode("utf-8")).hexdigest()


def _generated_synthetic_operations(rng: random.Random, cases: int) -> list[SyntheticOperation]:
    operations: list[SyntheticOperation] = []
    attempted_order_ids: list[int] = []
    next_order_id = 1
    for _ in range(cases):
        if attempted_order_ids and rng.randrange(100) < 24:
            if rng.randrange(100) < 82:
                order_id = rng.choice(attempted_order_ids)
            else:
                order_id = next_order_id + rng.randrange(1, 1000)
            operations.append((1, order_id, 0, True, None, 0, False, False))
            continue

        if attempted_order_ids and rng.randrange(100) < 12:
            order_id = rng.choice(attempted_order_ids)
        else:
            order_id = next_order_id
            next_order_id += 1
            attempted_order_ids.append(order_id)
        price_draw = rng.randrange(100)
        if price_draw < 7:
            price_tick = None
        elif price_draw < 11:
            price_tick = 0
        else:
            price_tick = rng.randrange(97, 106)
        quantity_draw = rng.randrange(100)
        qty_lots = 0 if quantity_draw < 5 else (-1 if quantity_draw < 7 else rng.randrange(1, 6))
        operations.append(
            (
                0,
                order_id,
                rng.randrange(1, 6),
                bool(rng.randrange(2)),
                price_tick,
                qty_lots,
                rng.randrange(100) < 24,
                rng.randrange(100) < 31,
            )
        )
    return operations


def _python_synthetic_trace(
    operations: list[SyntheticOperation],
    *,
    checkpoint_interval: int,
) -> list[tuple[bool, str, str | None, list[tuple[int, int, int, int]], str | None]]:
    exchange = SyntheticExchange(retain_transition_log=False)
    rows: list[tuple[bool, str, str | None, list[tuple[int, int, int, int]], str | None]] = []
    for index, operation in enumerate(operations):
        kind, order_id, participant_id, is_bid, price_tick, qty_lots, post_only, ioc = operation
        logical_time = LogicalTime(index + 1, index + 1)
        if kind == 0:
            result = exchange.submit_new(
                order_id=str(order_id),
                participant_id=str(participant_id),
                side="bid" if is_bid else "ask",
                qty_lots=qty_lots,
                price_tick=price_tick,
                time_in_force="IOC" if ioc else "GTC",
                post_only=post_only,
                time=logical_time,
            )
        elif kind == 1:
            result = exchange.cancel(str(order_id), time=logical_time)
        else:
            raise ValueError(f"unsupported synthetic operation kind: {kind}")
        reason = None
        if not result.accepted:
            reason = next((transition.reason for transition in result.transitions if transition.reason), None)
        fills = [
            (
                int(fill.maker_order_id),
                int(fill.taker_order_id),
                fill.price_tick,
                fill.qty_lots,
            )
            for fill in result.fills
        ]
        ordinal = index + 1
        checkpoint = (
            _synthetic_state_sha256(exchange)
            if ordinal % checkpoint_interval == 0 or ordinal == len(operations)
            else None
        )
        rows.append((result.accepted, result.terminal_state, reason, fills, checkpoint))
    return rows


def _build_extension(cargo: str, directory: Path) -> Path:
    environment = dict(os.environ)
    cargo_path = Path(cargo)
    if cargo_path.parent != Path("."):
        environment["PATH"] = str(cargo_path.resolve().parent) + os.pathsep + environment.get("PATH", "")
    command = [
        sys.executable,
        "-m",
        "maturin",
        "build",
        "--manifest-path",
        "rust/lob_core/Cargo.toml",
        "--release",
        "--features",
        "python",
        "--out",
        str(directory),
    ]
    subprocess.run(command, cwd=REPO_ROOT, env=environment, check=True)
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    return wheels[0]


def _run_loaded_parity(*, cases: int) -> dict[str, Any]:
    if cases <= 0:
        raise ValueError("cases must be positive")
    lob_core = importlib.import_module("lob_core")
    rng = random.Random(17)
    logical_time_cases = 0
    uncrossed_cases = 0
    for _ in range(cases):
        monotonic_ns = rng.randrange(0, 10**15)
        sequence = rng.randrange(0, 10**9)
        assert tuple(lob_core.logical_time_key(monotonic_ns, sequence)) == (monotonic_ns, sequence)
        logical_time_cases += 1
        best_bid = rng.choice([None, rng.randrange(1, 200)])
        best_ask = rng.choice([None, rng.randrange(1, 200)])
        expected_uncrossed = best_bid is None or best_ask is None or best_bid < best_ask
        assert lob_core.uncrossed(best_bid, best_ask) is expected_uncrossed
        uncrossed_cases += 1

    bids = {100: 2}
    asks = {102: 3}
    accepted_batches = 0
    rejected_batches = 0
    for _ in range(cases):
        changes = [
            (bool(rng.randrange(2)), rng.randrange(97, 106), rng.randrange(0, 6)) for _ in range(rng.randrange(1, 5))
        ]
        before = (dict(bids), dict(asks))
        try:
            python_result = _python_apply_batch(bids, asks, changes)
        except ValueError:
            try:
                lob_core.apply_book_batch(sorted(bids.items()), sorted(asks.items()), changes)
            except ValueError:
                rejected_batches += 1
                assert (bids, asks) == before
            else:
                raise AssertionError("Rust accepted a batch rejected by the Python oracle")
        else:
            rust_bids, rust_asks = lob_core.apply_book_batch(
                sorted(bids.items()),
                sorted(asks.items()),
                changes,
            )
            bids, asks = python_result
            assert dict(rust_bids) == bids
            assert dict(rust_asks) == asks
            accepted_batches += 1

    final_state = {"bids": sorted(bids.items()), "asks": sorted(asks.items())}
    final_hash = hashlib.sha256(
        json.dumps(final_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    operations = _generated_synthetic_operations(rng, cases)
    operation_corpus_sha256 = hashlib.sha256(json.dumps(operations, separators=(",", ":")).encode("utf-8")).hexdigest()
    python_trace = _python_synthetic_trace(
        operations,
        checkpoint_interval=SYNTHETIC_CHECKPOINT_INTERVAL,
    )
    rust_trace = list(lob_core.run_synthetic_trace(operations, SYNTHETIC_CHECKPOINT_INTERVAL))
    if len(rust_trace) != len(python_trace):
        raise AssertionError(f"synthetic trace length differs: python={len(python_trace)}, rust={len(rust_trace)}")
    for index, (python_row, rust_row) in enumerate(zip(python_trace, rust_trace, strict=True)):
        if python_row != rust_row:
            raise AssertionError(
                "synthetic exchange divergence at operation "
                f"{index}: operation={operations[index]!r}, python={python_row!r}, rust={rust_row!r}"
            )
    synthetic_accepted = sum(1 for row in python_trace if row[0])
    synthetic_fill_count = sum(len(row[3]) for row in python_trace)
    synthetic_checkpoint_count = sum(1 for row in python_trace if row[4] is not None)
    synthetic_final_hash = python_trace[-1][4]
    assert synthetic_final_hash is not None

    return {
        "schema_version": "lob_sim.rust_python_parity.v2",
        "ok": True,
        "seed": 17,
        "logical_time_cases": logical_time_cases,
        "uncrossed_cases": uncrossed_cases,
        "book_batches": cases,
        "accepted_batches": accepted_batches,
        "rejected_batches": rejected_batches,
        "final_state_sha256": final_hash,
        "synthetic_operations": len(operations),
        "synthetic_operation_corpus_sha256": operation_corpus_sha256,
        "synthetic_checkpoint_interval": SYNTHETIC_CHECKPOINT_INTERVAL,
        "synthetic_accepted_operations": synthetic_accepted,
        "synthetic_rejected_operations": len(operations) - synthetic_accepted,
        "synthetic_fill_count": synthetic_fill_count,
        "synthetic_checkpoint_count": synthetic_checkpoint_count,
        "synthetic_final_state_sha256": synthetic_final_hash,
        "scope": (
            "logical time, uncrossed invariant, atomic fixed-point book batches, and exact synthetic "
            "MBO new/cancel lifecycle with fills and periodic full-state hashes"
        ),
        "remaining_full_engine_scope": [
            "public-L2 execution scenarios",
            "latency scheduler",
            "risk reservations",
            "accounting and markouts",
            "run manifests",
        ],
        "full_engine_parity": False,
    }


def run_parity(*, cargo: str, cases: int) -> dict[str, Any]:
    with TemporaryDirectory(prefix="lob_sim_rust_parity_") as temp_dir:
        temporary = Path(temp_dir)
        wheel = _build_extension(cargo, temporary)
        extracted = temporary / "extracted"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(extracted)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(extracted) + os.pathsep + environment.get("PYTHONPATH", "")
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", "--cases", str(cases)],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if child.returncode != 0:
            raise RuntimeError(f"parity child failed\nstdout:\n{child.stdout}\nstderr:\n{child.stderr}")
        return json.loads(child.stdout)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the PyO3 wheel and check Python/Rust differential parity")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--cases", type=int, default=10_000)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--expected", type=Path, help="Fail if the result differs from a committed JSON report")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = _run_loaded_parity(cases=args.cases) if args.child else run_parity(cargo=args.cargo, cases=args.cases)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.expected:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        if result != expected:
            raise AssertionError(f"parity result differs from committed report: {args.expected}")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
