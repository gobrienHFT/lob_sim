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
from typing import Any, Literal, Sequence, cast

from lob_sim.oracle_kernel import (
    AccountingMarkoutOracle,
    DeterministicSchedulerOracle,
    OracleDecision,
    PortfolioNotionalReservationOracle,
    RiskReservationOracle,
    ScenarioLatencyOracle,
)
from lob_sim.record.envelope import LogicalTime
from lob_sim.sim.synthetic_exchange import SyntheticExchange


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CHECKPOINT_INTERVAL = 257

SyntheticOperation = tuple[int, int, int, bool, int | None, int, bool, bool]
SchedulerOperation = tuple[int, int, int, int, bool]
RiskOperation = tuple[int, int, bool, int]
PortfolioOperation = tuple[int, int, int, bool, int]
AccountingOperation = tuple[int, int, bool, int, int, int]
LatencyTraceRow = tuple[int, int]


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
        action_draw = rng.randrange(100)
        if attempted_order_ids and action_draw < 20:
            if rng.randrange(100) < 82:
                order_id = rng.choice(attempted_order_ids)
            else:
                order_id = next_order_id + rng.randrange(1, 1000)
            operations.append((1, order_id, 0, True, None, 0, False, False))
            continue
        if attempted_order_ids and action_draw < 34:
            if rng.randrange(100) < 82:
                order_id = rng.choice(attempted_order_ids)
            else:
                order_id = next_order_id + rng.randrange(1, 1000)
            if rng.randrange(100) < 24:
                new_order_id = rng.choice(attempted_order_ids)
            else:
                new_order_id = next_order_id
                next_order_id += 1
                attempted_order_ids.append(new_order_id)
            replace_price = 0 if rng.randrange(100) < 7 else rng.randrange(97, 106)
            replace_quantity_draw = rng.randrange(100)
            replace_qty = 0 if replace_quantity_draw < 5 else (-1 if replace_quantity_draw < 7 else rng.randrange(1, 6))
            operations.append(
                (
                    2,
                    order_id,
                    new_order_id,
                    True,
                    replace_price,
                    replace_qty,
                    rng.randrange(100) < 24,
                    False,
                )
            )
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
        elif kind == 2:
            assert price_tick is not None
            result = exchange.replace(
                str(order_id),
                new_order_id=str(participant_id),
                price_tick=price_tick,
                qty_lots=qty_lots,
                post_only=post_only,
                time=logical_time,
            )
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


def _generated_scheduler_operations(rng: random.Random, cases: int) -> list[SchedulerOperation]:
    operations: list[SchedulerOperation] = []
    attempted_action_ids: list[int] = []
    next_action_id = 1
    structured_target = cases // 2
    while len(operations) + 6 <= structured_target:
        first_id = next_action_id
        second_id = next_action_id + 1
        next_action_id += 2
        attempted_action_ids.extend((first_id, second_id))
        due_ns = 10_000 + len(operations)
        operations.extend(
            [
                (0, first_id, due_ns, 7, False),
                (0, second_id, due_ns, 7, False),
                (1, 0, due_ns, 7, False),
                (2, first_id, 0, 0, False),
                (1, 0, due_ns, 7, True),
                (0, first_id, due_ns + 1, 8, False),
            ]
        )
    while len(operations) < cases:
        draw = rng.randrange(100)
        monotonic_ns = rng.randrange(0, max(10, cases // 2))
        recv_seq = rng.randrange(0, 64)
        if draw < 55:
            if attempted_action_ids and rng.randrange(100) < 16:
                action_id = rng.choice(attempted_action_ids)
            else:
                action_id = next_action_id
                next_action_id += 1
                attempted_action_ids.append(action_id)
            operations.append((0, action_id, monotonic_ns, recv_seq, False))
        elif draw < 84:
            operations.append((1, 0, monotonic_ns, recv_seq, bool(rng.randrange(2))))
        else:
            action_id = (
                rng.choice(attempted_action_ids)
                if attempted_action_ids and rng.randrange(100) < 82
                else next_action_id + rng.randrange(1, 1000)
            )
            operations.append((2, action_id, 0, 0, False))
    return operations


def _python_scheduler_trace(
    operations: list[SchedulerOperation],
    *,
    checkpoint_interval: int,
) -> list[tuple[bool, str | None, list[int], int, str | None]]:
    scheduler = DeterministicSchedulerOracle()
    rows: list[tuple[bool, str | None, list[int], int, str | None]] = []
    for index, (kind, action_id, monotonic_ns, recv_seq, inclusive) in enumerate(operations):
        time = LogicalTime(monotonic_ns, recv_seq)
        drained: list[int] = []
        if kind == 0:
            decision = scheduler.schedule(action_id, time)
        elif kind == 1:
            drained = list(scheduler.drain(time, inclusive=inclusive))
            decision = OracleDecision(True)
        elif kind == 2:
            decision = scheduler.cancel(action_id)
        else:
            raise ValueError(f"unsupported scheduler operation kind: {kind}")
        ordinal = index + 1
        checkpoint = (
            scheduler.state_sha256() if ordinal % checkpoint_interval == 0 or ordinal == len(operations) else None
        )
        rows.append((decision.accepted, decision.reason, drained, scheduler.pending_count, checkpoint))
    return rows


def _generated_risk_operations(rng: random.Random, cases: int) -> list[RiskOperation]:
    operations: list[RiskOperation] = []
    attempted_order_ids: list[int] = []
    next_order_id = 1
    structured_target = cases // 2
    while len(operations) + 9 <= structured_target:
        bid_id = next_order_id
        ask_id = next_order_id + 1
        next_order_id += 2
        attempted_order_ids.extend((bid_id, ask_id))
        operations.extend(
            [
                (4, 0, False, 0),
                (0, bid_id, True, 2),
                (1, bid_id, False, 0),
                (3, bid_id, False, 1),
                (2, bid_id, False, 0),
                (0, ask_id, False, 2),
                (1, ask_id, False, 0),
                (3, ask_id, False, 1),
                (2, ask_id, False, 0),
            ]
        )
    while len(operations) < cases:
        draw = rng.randrange(100)
        if draw < 50:
            if attempted_order_ids and rng.randrange(100) < 14:
                order_id = rng.choice(attempted_order_ids)
            else:
                order_id = next_order_id
                next_order_id += 1
                attempted_order_ids.append(order_id)
            quantity_draw = rng.randrange(100)
            qty_lots = 0 if quantity_draw < 5 else (-1 if quantity_draw < 7 else rng.randrange(1, 11))
            operations.append((0, order_id, bool(rng.randrange(2)), qty_lots))
        elif draw < 66:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 82
                else next_order_id + rng.randrange(1, 1000)
            )
            operations.append((1, order_id, False, 0))
        elif draw < 79:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 82
                else next_order_id + rng.randrange(1, 1000)
            )
            operations.append((2, order_id, False, 0))
        elif draw < 97:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 86
                else next_order_id + rng.randrange(1, 1000)
            )
            quantity_draw = rng.randrange(100)
            qty_lots = 0 if quantity_draw < 5 else (-1 if quantity_draw < 7 else rng.randrange(1, 11))
            operations.append((3, order_id, False, qty_lots))
        else:
            operations.append((4, 0, False, 0))
    return operations


def _python_risk_trace(
    operations: list[RiskOperation],
    *,
    max_position_lots: int,
    checkpoint_interval: int,
) -> list[tuple[bool, str | None, int, int, int, str | None]]:
    ledger = RiskReservationOracle(max_position_lots)
    rows: list[tuple[bool, str | None, int, int, int, str | None]] = []
    for index, (kind, order_id, is_bid, qty_lots) in enumerate(operations):
        if kind == 0:
            decision = ledger.reserve(order_id, is_bid=is_bid, qty_lots=qty_lots)
        elif kind == 1:
            decision = ledger.request_cancel(order_id)
        elif kind == 2:
            decision = ledger.cancel_ack(order_id)
        elif kind == 3:
            decision = ledger.fill(order_id, qty_lots)
        elif kind == 4:
            decision = ledger.invalidate_epoch()
        else:
            raise ValueError(f"unsupported risk operation kind: {kind}")
        ordinal = index + 1
        checkpoint = ledger.state_sha256() if ordinal % checkpoint_interval == 0 or ordinal == len(operations) else None
        rows.append(
            (
                decision.accepted,
                decision.reason,
                ledger.position_lots,
                ledger.reserved_buy_lots,
                ledger.reserved_sell_lots,
                checkpoint,
            )
        )
    return rows


def _generated_portfolio_operations(rng: random.Random, cases: int) -> list[PortfolioOperation]:
    operations: list[PortfolioOperation] = []
    attempted_order_ids: list[int] = []
    next_order_id = 1
    structured_target = cases // 2
    while len(operations) + 8 <= structured_target:
        symbol_id = rng.randrange(4)
        bid_id = next_order_id
        ask_id = next_order_id + 1
        next_order_id += 2
        attempted_order_ids.extend((bid_id, ask_id))
        operations.extend(
            [
                (5, 0, symbol_id, False, rng.choice((-8, 0, 8))),
                (0, bid_id, symbol_id, True, 10),
                (1, bid_id, symbol_id, False, 0),
                (3, bid_id, symbol_id, False, 4),
                (2, bid_id, symbol_id, False, 0),
                (0, ask_id, (symbol_id + 1) % 4, False, 8),
                (3, ask_id, (symbol_id + 1) % 4, False, 8),
                (4, 0, 0, False, 0),
            ]
        )
    while len(operations) < cases:
        draw = rng.randrange(100)
        if draw < 45:
            if attempted_order_ids and rng.randrange(100) < 14:
                order_id = rng.choice(attempted_order_ids)
            else:
                order_id = next_order_id
                next_order_id += 1
                attempted_order_ids.append(order_id)
            quantity_draw = rng.randrange(100)
            notional_units = 0 if quantity_draw < 5 else (-1 if quantity_draw < 7 else rng.randrange(1, 31))
            operations.append((0, order_id, rng.randrange(4), bool(rng.randrange(2)), notional_units))
        elif draw < 60:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 82
                else next_order_id + rng.randrange(1, 1000)
            )
            operations.append((1, order_id, rng.randrange(4), False, 0))
        elif draw < 73:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 82
                else next_order_id + rng.randrange(1, 1000)
            )
            operations.append((2, order_id, rng.randrange(4), False, 0))
        elif draw < 94:
            order_id = (
                rng.choice(attempted_order_ids)
                if attempted_order_ids and rng.randrange(100) < 86
                else next_order_id + rng.randrange(1, 1000)
            )
            quantity_draw = rng.randrange(100)
            notional_units = 0 if quantity_draw < 5 else (-1 if quantity_draw < 7 else rng.randrange(1, 31))
            operations.append((3, order_id, rng.randrange(4), False, notional_units))
        elif draw < 97:
            operations.append((4, 0, 0, False, 0))
        else:
            operations.append((5, 0, rng.randrange(4), False, rng.randrange(-41, 42)))
    return operations


def _python_portfolio_trace(
    operations: list[PortfolioOperation],
    *,
    max_notional_units: int,
    checkpoint_interval: int,
) -> list[tuple[bool, str | None, int, int, int, str | None]]:
    ledger = PortfolioNotionalReservationOracle(max_notional_units)
    rows: list[tuple[bool, str | None, int, int, int, str | None]] = []
    for index, (kind, order_id, symbol_id, is_bid, notional_units) in enumerate(operations):
        if kind == 0:
            decision = ledger.reserve(
                order_id,
                symbol_id=symbol_id,
                is_bid=is_bid,
                notional_units=notional_units,
            )
        elif kind == 1:
            decision = ledger.request_cancel(order_id)
        elif kind == 2:
            decision = ledger.cancel_ack(order_id)
        elif kind == 3:
            decision = ledger.fill(order_id, notional_units)
        elif kind == 4:
            decision = ledger.invalidate_epoch()
        elif kind == 5:
            decision = ledger.set_inventory(symbol_id, notional_units)
        else:
            raise ValueError(f"unsupported portfolio operation kind: {kind}")
        ordinal = index + 1
        checkpoint = ledger.state_sha256() if ordinal % checkpoint_interval == 0 or ordinal == len(operations) else None
        rows.append(
            (
                decision.accepted,
                decision.reason,
                ledger.gross_inventory_units,
                ledger.reserved_order_units,
                ledger.total_reserved_units,
                checkpoint,
            )
        )
    return rows


def _generated_accounting_operations(rng: random.Random, cases: int) -> list[AccountingOperation]:
    operations: list[AccountingOperation] = []
    for symbol_id in range(4):
        operations.append((1, symbol_id, False, 100 + symbol_id, 0, 0))
    operations.extend(
        [
            (0, 0, True, 100, 3, 7),
            (0, 0, False, 110, 1, -2),
            (3, 0, True, 100, 2, 98),
            (2, 0, False, 0, 0, 0),
            (0, 1, False, 101, 2, 0),
            (0, 1, True, 99, 3, 1),
            (1, 1, False, 100, 0, 0),
        ]
    )
    while len(operations) < cases:
        kind = rng.randrange(100)
        symbol_id = rng.randrange(4)
        if kind < 54:
            price_tick = 0 if rng.randrange(100) < 4 else rng.randrange(90, 111)
            qty_lots = 0 if rng.randrange(100) < 4 else rng.randrange(1, 6)
            operations.append((0, symbol_id, bool(rng.randrange(2)), price_tick, qty_lots, rng.randrange(-5, 6)))
        elif kind < 76:
            price_tick = 0 if rng.randrange(100) < 4 else rng.randrange(90, 111)
            operations.append((1, symbol_id, False, price_tick, 0, 0))
        elif kind < 86:
            operations.append((2, symbol_id, False, 0, 0, 0))
        else:
            fill_price = 0 if rng.randrange(100) < 4 else rng.randrange(90, 111)
            mark_price = 0 if rng.randrange(100) < 4 else rng.randrange(90, 111)
            qty_lots = 0 if rng.randrange(100) < 4 else rng.randrange(1, 6)
            operations.append((3, symbol_id, bool(rng.randrange(2)), fill_price, qty_lots, mark_price))
    return operations


def _python_accounting_trace(
    operations: list[AccountingOperation],
    *,
    checkpoint_interval: int,
) -> list[tuple[bool, str | None, int, int, int, int, int | None, bool, int, int, str | None]]:
    ledger = AccountingMarkoutOracle()
    rows: list[tuple[bool, str | None, int, int, int, int, int | None, bool, int, int, str | None]] = []
    for index, (kind, symbol_id, is_bid, price_tick, qty_lots, fee_or_mark_tick) in enumerate(operations):
        if kind == 0:
            decision = ledger.fill(
                symbol_id,
                is_bid=is_bid,
                price_tick=price_tick,
                qty_lots=qty_lots,
                fee_cash_units=fee_or_mark_tick,
            )
        elif kind == 1:
            decision = ledger.mark(symbol_id, price_tick)
        elif kind == 2:
            decision = ledger.clear_mark(symbol_id)
        elif kind == 3:
            decision = ledger.markout(
                is_bid=is_bid,
                fill_price_tick=price_tick,
                qty_lots=qty_lots,
                mark_price_tick=fee_or_mark_tick,
            )
        else:
            raise ValueError(f"unsupported accounting operation kind: {kind}")
        ordinal = index + 1
        checkpoint = ledger.state_sha256() if ordinal % checkpoint_interval == 0 or ordinal == len(operations) else None
        rows.append(
            (
                decision.accepted,
                decision.reason,
                ledger.position_lots,
                ledger.gross_position_lots,
                ledger.realized_pnl_cash_units,
                ledger.total_fees_cash_units,
                ledger.unrealized_pnl_cash_units,
                ledger.valuation_complete,
                ledger.markout_cash_units,
                ledger.markout_qty_lots,
                checkpoint,
            )
        )
    return rows


def _python_latency_trace(
    *,
    mode: int,
    fixed_new_us: int,
    fixed_cancel_us: int,
    samples_us: tuple[int, ...],
    stress_multiplier_ppm: int,
    seed: int,
    components: list[int],
) -> list[LatencyTraceRow]:
    mode_name = cast(Literal["fixed", "empirical", "stress_tail"], ("fixed", "empirical", "stress_tail")[mode])
    sampler = ScenarioLatencyOracle(
        mode=mode_name,
        fixed_new_us=fixed_new_us,
        fixed_cancel_us=fixed_cancel_us,
        samples_us=samples_us,
        stress_multiplier_ppm=stress_multiplier_ppm,
        seed=seed,
    )
    rows: list[LatencyTraceRow] = []
    for component in components:
        component_name: Literal["new_order", "cancel"] = "new_order" if component == 0 else "cancel"
        rows.append((sampler.draw(component_name), sampler.state))
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
    synthetic_trace_sha256 = hashlib.sha256(json.dumps(python_trace, separators=(",", ":")).encode("utf-8")).hexdigest()
    synthetic_operation_kind_counts = {
        "new": sum(1 for operation in operations if operation[0] == 0),
        "cancel": sum(1 for operation in operations if operation[0] == 1),
        "replace": sum(1 for operation in operations if operation[0] == 2),
    }

    scheduler_operations = _generated_scheduler_operations(rng, cases)
    scheduler_corpus_sha256 = hashlib.sha256(
        json.dumps(scheduler_operations, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    python_scheduler_trace = _python_scheduler_trace(
        scheduler_operations,
        checkpoint_interval=SYNTHETIC_CHECKPOINT_INTERVAL,
    )
    rust_scheduler_trace = list(lob_core.run_scheduler_trace(scheduler_operations, SYNTHETIC_CHECKPOINT_INTERVAL))
    if len(rust_scheduler_trace) != len(python_scheduler_trace):
        raise AssertionError(
            f"scheduler trace length differs: python={len(python_scheduler_trace)}, rust={len(rust_scheduler_trace)}"
        )
    for scheduler_index, (scheduler_python_row, scheduler_rust_row) in enumerate(
        zip(python_scheduler_trace, rust_scheduler_trace, strict=True)
    ):
        if scheduler_python_row != scheduler_rust_row:
            raise AssertionError(
                "scheduler divergence at operation "
                f"{scheduler_index}: operation={scheduler_operations[scheduler_index]!r}, "
                f"python={scheduler_python_row!r}, rust={scheduler_rust_row!r}"
            )
    scheduler_accepted = sum(1 for row in python_scheduler_trace if row[0])
    scheduler_drained = sum(len(row[2]) for row in python_scheduler_trace)
    scheduler_checkpoint_count = sum(1 for row in python_scheduler_trace if row[4] is not None)
    scheduler_final_hash = python_scheduler_trace[-1][4]
    assert scheduler_final_hash is not None
    scheduler_trace_sha256 = hashlib.sha256(
        json.dumps(python_scheduler_trace, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    scheduler_operation_kind_counts = {
        "schedule": sum(1 for operation in scheduler_operations if operation[0] == 0),
        "drain": sum(1 for operation in scheduler_operations if operation[0] == 1),
        "cancel": sum(1 for operation in scheduler_operations if operation[0] == 2),
    }
    scheduler_accepted_operation_kind_counts = {
        name: sum(
            1
            for operation, row in zip(scheduler_operations, python_scheduler_trace, strict=True)
            if operation[0] == kind and row[0]
        )
        for name, kind in (("schedule", 0), ("drain", 1), ("cancel", 2))
    }

    risk_max_position_lots = 25
    risk_operations = _generated_risk_operations(rng, cases)
    risk_corpus_sha256 = hashlib.sha256(json.dumps(risk_operations, separators=(",", ":")).encode("utf-8")).hexdigest()
    python_risk_trace = _python_risk_trace(
        risk_operations,
        max_position_lots=risk_max_position_lots,
        checkpoint_interval=SYNTHETIC_CHECKPOINT_INTERVAL,
    )
    rust_risk_trace = list(
        lob_core.run_risk_trace(
            risk_operations,
            risk_max_position_lots,
            SYNTHETIC_CHECKPOINT_INTERVAL,
        )
    )
    if len(rust_risk_trace) != len(python_risk_trace):
        raise AssertionError(f"risk trace length differs: python={len(python_risk_trace)}, rust={len(rust_risk_trace)}")
    for risk_index, (risk_python_row, risk_rust_row) in enumerate(zip(python_risk_trace, rust_risk_trace, strict=True)):
        if risk_python_row != risk_rust_row:
            raise AssertionError(
                "risk reservation divergence at operation "
                f"{risk_index}: operation={risk_operations[risk_index]!r}, "
                f"python={risk_python_row!r}, rust={risk_rust_row!r}"
            )
    risk_accepted = sum(1 for row in python_risk_trace if row[0])
    risk_checkpoint_count = sum(1 for row in python_risk_trace if row[5] is not None)
    risk_final_hash = python_risk_trace[-1][5]
    assert risk_final_hash is not None
    risk_trace_sha256 = hashlib.sha256(json.dumps(python_risk_trace, separators=(",", ":")).encode("utf-8")).hexdigest()
    risk_operation_kind_counts = {
        "reserve": sum(1 for operation in risk_operations if operation[0] == 0),
        "request_cancel": sum(1 for operation in risk_operations if operation[0] == 1),
        "cancel_ack": sum(1 for operation in risk_operations if operation[0] == 2),
        "fill": sum(1 for operation in risk_operations if operation[0] == 3),
        "epoch_invalidate": sum(1 for operation in risk_operations if operation[0] == 4),
    }
    risk_accepted_operation_kind_counts = {
        name: sum(
            1
            for operation, row in zip(risk_operations, python_risk_trace, strict=True)
            if operation[0] == kind and row[0]
        )
        for name, kind in (
            ("reserve", 0),
            ("request_cancel", 1),
            ("cancel_ack", 2),
            ("fill", 3),
            ("epoch_invalidate", 4),
        )
    }

    portfolio_max_notional_units = 100
    portfolio_operations = _generated_portfolio_operations(random.Random(31), cases)
    portfolio_corpus_sha256 = hashlib.sha256(
        json.dumps(portfolio_operations, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    python_portfolio_trace = _python_portfolio_trace(
        portfolio_operations,
        max_notional_units=portfolio_max_notional_units,
        checkpoint_interval=SYNTHETIC_CHECKPOINT_INTERVAL,
    )
    rust_portfolio_trace = list(
        lob_core.run_portfolio_notional_trace(
            portfolio_operations,
            portfolio_max_notional_units,
            SYNTHETIC_CHECKPOINT_INTERVAL,
        )
    )
    if len(rust_portfolio_trace) != len(python_portfolio_trace):
        raise AssertionError(
            "portfolio-notional trace length differs: "
            f"python={len(python_portfolio_trace)}, rust={len(rust_portfolio_trace)}"
        )
    for portfolio_index, (portfolio_python_row, portfolio_rust_row) in enumerate(
        zip(python_portfolio_trace, rust_portfolio_trace, strict=True)
    ):
        if portfolio_python_row != portfolio_rust_row:
            raise AssertionError(
                "portfolio-notional reservation divergence at operation "
                f"{portfolio_index}: operation={portfolio_operations[portfolio_index]!r}, "
                f"python={portfolio_python_row!r}, rust={portfolio_rust_row!r}"
            )
    portfolio_accepted = sum(1 for row in python_portfolio_trace if row[0])
    portfolio_checkpoint_count = sum(1 for row in python_portfolio_trace if row[5] is not None)
    portfolio_final_hash = python_portfolio_trace[-1][5]
    assert portfolio_final_hash is not None
    portfolio_trace_sha256 = hashlib.sha256(
        json.dumps(python_portfolio_trace, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    portfolio_operation_kind_counts = {
        "reserve": sum(1 for operation in portfolio_operations if operation[0] == 0),
        "request_cancel": sum(1 for operation in portfolio_operations if operation[0] == 1),
        "cancel_ack": sum(1 for operation in portfolio_operations if operation[0] == 2),
        "fill": sum(1 for operation in portfolio_operations if operation[0] == 3),
        "epoch_invalidate": sum(1 for operation in portfolio_operations if operation[0] == 4),
        "set_inventory": sum(1 for operation in portfolio_operations if operation[0] == 5),
    }
    portfolio_accepted_operation_kind_counts = {
        name: sum(
            1
            for operation, row in zip(portfolio_operations, python_portfolio_trace, strict=True)
            if operation[0] == kind and row[0]
        )
        for name, kind in (
            ("reserve", 0),
            ("request_cancel", 1),
            ("cancel_ack", 2),
            ("fill", 3),
            ("epoch_invalidate", 4),
            ("set_inventory", 5),
        )
    }

    accounting_operations = _generated_accounting_operations(random.Random(47), cases)
    accounting_corpus_sha256 = hashlib.sha256(
        json.dumps(accounting_operations, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    python_accounting_trace = _python_accounting_trace(
        accounting_operations,
        checkpoint_interval=SYNTHETIC_CHECKPOINT_INTERVAL,
    )
    rust_accounting_trace = list(lob_core.run_accounting_trace(accounting_operations, SYNTHETIC_CHECKPOINT_INTERVAL))
    if len(rust_accounting_trace) != len(python_accounting_trace):
        raise AssertionError(
            f"accounting trace length differs: python={len(python_accounting_trace)}, rust={len(rust_accounting_trace)}"
        )
    for accounting_index, (accounting_python_row, accounting_rust_row) in enumerate(
        zip(python_accounting_trace, rust_accounting_trace, strict=True)
    ):
        if accounting_python_row != accounting_rust_row:
            raise AssertionError(
                "accounting divergence at operation "
                f"{accounting_index}: operation={accounting_operations[accounting_index]!r}, "
                f"python={accounting_python_row!r}, rust={accounting_rust_row!r}"
            )
    accounting_accepted = sum(1 for row in python_accounting_trace if row[0])
    accounting_checkpoint_count = sum(1 for row in python_accounting_trace if row[10] is not None)
    accounting_final_hash = python_accounting_trace[-1][10]
    assert accounting_final_hash is not None
    accounting_trace_sha256 = hashlib.sha256(
        json.dumps(python_accounting_trace, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    accounting_operation_kind_counts = {
        "fill": sum(1 for operation in accounting_operations if operation[0] == 0),
        "mark": sum(1 for operation in accounting_operations if operation[0] == 1),
        "clear_mark": sum(1 for operation in accounting_operations if operation[0] == 2),
        "markout": sum(1 for operation in accounting_operations if operation[0] == 3),
    }
    accounting_accepted_operation_kind_counts = {
        name: sum(
            1
            for operation, row in zip(accounting_operations, python_accounting_trace, strict=True)
            if operation[0] == kind and row[0]
        )
        for name, kind in (("fill", 0), ("mark", 1), ("clear_mark", 2), ("markout", 3))
    }

    latency_components = [rng.randrange(2) for _ in range(cases)]
    latency_operation_corpus_sha256 = hashlib.sha256(
        json.dumps(latency_components, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    latency_scenarios = {
        "fixed": (0, 25_000, 50_000, (), 1_000_000, 17),
        "empirical": (1, 1_000, 5_000, (1_000, 5_000, 25_000), 1_000_000, 17),
        "stress_tail": (2, 1_000, 5_000, (1_000, 5_000, 25_000), 3_000_000, 17),
    }
    latency_trace_sha256_by_mode: dict[str, str] = {}
    latency_final_state_by_mode: dict[str, int] = {}
    for mode_name, (
        mode,
        fixed_new_us,
        fixed_cancel_us,
        samples_us,
        stress_multiplier_ppm,
        seed,
    ) in latency_scenarios.items():
        python_latency_trace = _python_latency_trace(
            mode=mode,
            fixed_new_us=fixed_new_us,
            fixed_cancel_us=fixed_cancel_us,
            samples_us=samples_us,
            stress_multiplier_ppm=stress_multiplier_ppm,
            seed=seed,
            components=latency_components,
        )
        rust_latency_trace = list(
            lob_core.run_latency_trace(
                mode,
                fixed_new_us,
                fixed_cancel_us,
                list(samples_us),
                stress_multiplier_ppm,
                seed,
                latency_components,
            )
        )
        if rust_latency_trace != python_latency_trace:
            for latency_index, (latency_python_row, latency_rust_row) in enumerate(
                zip(python_latency_trace, rust_latency_trace, strict=False)
            ):
                if latency_python_row != latency_rust_row:
                    raise AssertionError(
                        "latency sampler divergence at operation "
                        f"{latency_index}: mode={mode_name}, component={latency_components[latency_index]}, "
                        f"python={latency_python_row!r}, rust={latency_rust_row!r}"
                    )
            raise AssertionError(f"latency sampler trace length differs for mode={mode_name}")
        latency_trace_sha256_by_mode[mode_name] = hashlib.sha256(
            json.dumps(python_latency_trace, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        latency_final_state_by_mode[mode_name] = python_latency_trace[-1][1] if python_latency_trace else seed

    return {
        "schema_version": "lob_sim.rust_python_parity.v3",
        "ok": True,
        "seed": 17,
        "logical_time_cases": logical_time_cases,
        "uncrossed_cases": uncrossed_cases,
        "book_batches": cases,
        "accepted_batches": accepted_batches,
        "rejected_batches": rejected_batches,
        "final_state_sha256": final_hash,
        "synthetic_operations": len(operations),
        "synthetic_operation_kind_counts": synthetic_operation_kind_counts,
        "synthetic_operation_corpus_sha256": operation_corpus_sha256,
        "synthetic_trace_sha256": synthetic_trace_sha256,
        "synthetic_checkpoint_interval": SYNTHETIC_CHECKPOINT_INTERVAL,
        "synthetic_accepted_operations": synthetic_accepted,
        "synthetic_rejected_operations": len(operations) - synthetic_accepted,
        "synthetic_fill_count": synthetic_fill_count,
        "synthetic_checkpoint_count": synthetic_checkpoint_count,
        "synthetic_final_state_sha256": synthetic_final_hash,
        "scheduler_operations": len(scheduler_operations),
        "scheduler_operation_kind_counts": scheduler_operation_kind_counts,
        "scheduler_accepted_operation_kind_counts": scheduler_accepted_operation_kind_counts,
        "scheduler_operation_corpus_sha256": scheduler_corpus_sha256,
        "scheduler_trace_sha256": scheduler_trace_sha256,
        "scheduler_accepted_operations": scheduler_accepted,
        "scheduler_rejected_operations": len(scheduler_operations) - scheduler_accepted,
        "scheduler_drained_actions": scheduler_drained,
        "scheduler_checkpoint_count": scheduler_checkpoint_count,
        "scheduler_final_state_sha256": scheduler_final_hash,
        "risk_operations": len(risk_operations),
        "risk_operation_kind_counts": risk_operation_kind_counts,
        "risk_accepted_operation_kind_counts": risk_accepted_operation_kind_counts,
        "risk_operation_corpus_sha256": risk_corpus_sha256,
        "risk_trace_sha256": risk_trace_sha256,
        "risk_accepted_operations": risk_accepted,
        "risk_rejected_operations": len(risk_operations) - risk_accepted,
        "risk_checkpoint_count": risk_checkpoint_count,
        "risk_max_position_lots": risk_max_position_lots,
        "risk_final_position_lots": python_risk_trace[-1][2],
        "risk_final_reserved_buy_lots": python_risk_trace[-1][3],
        "risk_final_reserved_sell_lots": python_risk_trace[-1][4],
        "risk_final_state_sha256": risk_final_hash,
        "portfolio_notional_operations": len(portfolio_operations),
        "portfolio_notional_operation_kind_counts": portfolio_operation_kind_counts,
        "portfolio_notional_accepted_operation_kind_counts": portfolio_accepted_operation_kind_counts,
        "portfolio_notional_operation_corpus_sha256": portfolio_corpus_sha256,
        "portfolio_notional_trace_sha256": portfolio_trace_sha256,
        "portfolio_notional_accepted_operations": portfolio_accepted,
        "portfolio_notional_rejected_operations": len(portfolio_operations) - portfolio_accepted,
        "portfolio_notional_checkpoint_count": portfolio_checkpoint_count,
        "portfolio_notional_max_units": portfolio_max_notional_units,
        "portfolio_notional_final_gross_inventory_units": python_portfolio_trace[-1][2],
        "portfolio_notional_final_reserved_order_units": python_portfolio_trace[-1][3],
        "portfolio_notional_final_total_reserved_units": python_portfolio_trace[-1][4],
        "portfolio_notional_final_state_sha256": portfolio_final_hash,
        "accounting_operations": len(accounting_operations),
        "accounting_operation_kind_counts": accounting_operation_kind_counts,
        "accounting_accepted_operation_kind_counts": accounting_accepted_operation_kind_counts,
        "accounting_operation_corpus_sha256": accounting_corpus_sha256,
        "accounting_trace_sha256": accounting_trace_sha256,
        "accounting_accepted_operations": accounting_accepted,
        "accounting_rejected_operations": len(accounting_operations) - accounting_accepted,
        "accounting_checkpoint_count": accounting_checkpoint_count,
        "accounting_final_position_lots": python_accounting_trace[-1][2],
        "accounting_final_gross_position_lots": python_accounting_trace[-1][3],
        "accounting_final_realized_pnl_cash_units": python_accounting_trace[-1][4],
        "accounting_final_fees_cash_units": python_accounting_trace[-1][5],
        "accounting_final_unrealized_pnl_cash_units": python_accounting_trace[-1][6],
        "accounting_final_valuation_complete": python_accounting_trace[-1][7],
        "accounting_final_markout_cash_units": python_accounting_trace[-1][8],
        "accounting_final_markout_qty_lots": python_accounting_trace[-1][9],
        "accounting_final_state_sha256": accounting_final_hash,
        "latency_operations": len(latency_components),
        "latency_operation_corpus_sha256": latency_operation_corpus_sha256,
        "latency_sampler": "splitmix64_v1",
        "latency_resolution_us": 1,
        "latency_trace_sha256_by_mode": latency_trace_sha256_by_mode,
        "latency_final_state_by_mode": latency_final_state_by_mode,
        "scope": (
            "logical time, uncrossed invariant, atomic fixed-point book batches, and exact synthetic "
            "MBO new/cancel/replace lifecycle, deterministic integer-nanosecond scheduling, and per-symbol "
            "live-plus-pending lot reservations, cross-symbol gross-notional reservations, fixed-point fill "
            "accounting, nullable mark valuation, signed markouts, and deterministic fixed/empirical/stress-tail "
            "scenario latency sampling, with transition traces and periodic "
            "full-state hashes"
        ),
        "remaining_full_engine_scope": [
            "public-L2 execution scenarios",
            "engine-integrated latency and portfolio-notional risk",
            "engine-integrated accounting and markouts",
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
