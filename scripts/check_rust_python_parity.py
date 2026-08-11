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


REPO_ROOT = Path(__file__).resolve().parents[1]


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
    return {
        "schema_version": "lob_sim.rust_python_parity.v1",
        "ok": True,
        "seed": 17,
        "logical_time_cases": logical_time_cases,
        "uncrossed_cases": uncrossed_cases,
        "book_batches": cases,
        "accepted_batches": accepted_batches,
        "rejected_batches": rejected_batches,
        "final_state_sha256": final_hash,
        "scope": "logical time, uncrossed invariant, and atomic fixed-point book batches",
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
            check=True,
        )
        return json.loads(child.stdout)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the PyO3 wheel and check Python/Rust primitive parity")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--cases", type=int, default=10_000)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = _run_loaded_parity(cases=args.cases) if args.child else run_parity(cargo=args.cargo, cases=args.cases)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
