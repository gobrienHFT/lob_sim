"""Hand-authored partition/admission mechanics, NOT certified market evidence."""

from copy import deepcopy
from pathlib import Path

import pytest

from lob_sim.book.types import SymbolSpec
from lob_sim.regime.dataset import instrument_grid_identity, publish_json
from lob_sim.regime.features import FeatureSpec
from lob_sim.regime.validation import canonical_json, identity
from lob_sim.research.capture_clock import CaptureClock
from lob_sim.research.feature_admission import FeatureAdmission
from lob_sim.research.feature_reader import iter_admitted_day, load_prepared, read_fit_partition
from lob_sim.research.feature_storage import PartitionWriter
from lob_sim.research.registered_protocol import FrozenProtocol
from lob_sim.record.envelope import ValidityState
from test_research_registry import mechanical_payload

S = 1_000_000_000
WALL = 1_735_689_600 * S


def intervals():
    # Independent hand oracle: joint valid [0,20), invalid [20,21), new
    # market epoch at 25. No feature window can bridge either boundary.
    return [
        {
            "logical_start_ns": a * S,
            "logical_end_ns": b * S,
            "joint_valid": valid,
            "symbols": {s: {"epochs": [0, 0, epoch]} for s in ("BTCUSDT", "ETHUSDT")},
        }
        for a, b, valid, epoch in ((0, 20, True, 0), (20, 21, False, 0), (21, 25, True, 0), (25, 80, True, 1))
    ]


def patch_intervals(monkeypatch):
    def hand_reader(*args, **kwargs):
        yield from deepcopy(intervals())

    monkeypatch.setattr("lob_sim.research.feature_admission.iter_verified_intervals", hand_reader)
    monkeypatch.setattr("lob_sim.research.feature_reader.iter_verified_intervals", hand_reader)


def mechanical_prepared(tmp_path, monkeypatch):
    """Fake labels test file isolation only; the production registrar is never bypassed."""
    patch_intervals(monkeypatch)
    clock = CaptureClock(0, WALL)
    payload = mechanical_payload()
    payload["sources"][0].update(clock_sha256=clock.digest, research_usable=True)
    payload["registry_sha256"] = identity({k: v for k, v in payload.items() if k != "registry_sha256"})
    protocol = FrozenProtocol(canonical_json(payload))
    registered = protocol.snapshot()
    spec = FeatureSpec.from_dict(registered["experiment"]["features"][0])
    grid = SymbolSpec("BTCUSDT", "0.1", "0.001", venue="BINANCE_USDM")
    units = {
        "symbol": "BTCUSDT",
        "tick_size": "0.1",
        "step_size": "0.001",
        "contract_multiplier": "1",
        "venue": "BINANCE_USDM",
        "price_currency": "USDT",
        "quantity_unit": "BTC",
    }
    parent = {
        "capture_id": "mechanical_only",
        "clock": clock.as_dict(),
        "clock_sha256": clock.digest,
        "input_sha256": "2" * 64,
        "research_usable": True,
        "report_sha256": "3" * 64,
        "first_logical_ns": 0,
        "last_logical_ns": 80 * S,
        "integrity": {"records": 100},
        "instruments": {"BTCUSDT": {"spec": units, "sha256": identity(units)}},
    }
    root = tmp_path / "prepared"
    root.mkdir()
    cadence_root = root / ("cadence_" + str(spec.interval_ns))
    writer = PartitionWriter(cadence_root)
    rows = []
    for sample in (15, 16):
        native = {
            "schema_version": "lob_sim.hmm_feature_row.v1",
            "symbol": "BTCUSDT",
            "sample_ns": sample * S,
            "available_at_ns": (sample + 1) * S,
            "receive_seq": 10,
            "input_row": 10,
            "wall_ns": WALL + sample * S,
            "utc_day": "2025-01-01",
            "clock_basis": "causal_receive_wall_projection",
            "epochs": [0, 0, 0],
            "validity": ValidityState(True, True, True, True).as_dict(),
            "feature_identity": spec.digest,
            "instrument_sha256": instrument_grid_identity(grid, canonical_grid=True),
            "input_sha256": "2" * 64,
            "status": "VALID",
            "features": [1.0] * 12,
            "reset_reason": None,
            "sequence_id": "a" * 64,
        }
        seq = identity(
            {
                "input_sha256": "2" * 64,
                "symbol": "BTCUSDT",
                "utc_day": "2025-01-01",
                "features_sha256": spec.digest,
                "key": ("2025-01-01", (0, 0, 0), "a" * 64),
                "first_sample_ns": 15 * S,
            }
        )
        row = {
            "schema_version": "lob_sim.admitted_feature_row.v1",
            "native": native,
            "registry_sha256": protocol.digest,
            "source_report_sha256": "3" * 64,
            "clock_sha256": clock.digest,
            "instrument_sha256": identity(units),
            "role": "calibration",
            "admitted": True,
            "exclusions": [],
            "sequence_id": seq,
        }
        writer.write(row)
        rows.append(row)
    files = writer.finalize()
    cadences = [
        {
            "directory": "cadence_" + str(f["interval_ns"]),
            "features": f,
            "day_files": files if i == 0 else [],
            "source_records": [
                {
                    "input_sha256": "2" * 64,
                    "status": "prepared",
                    "sample_count": 2 if i == 0 else 0,
                    "native_status_counts": {"VALID": 2} if i == 0 else {},
                }
            ],
            "excluded_unregistered_date_samples": {},
        }
        for i, f in enumerate(registered["experiment"]["features"])
    ]
    manifest = {
        "schema_version": "lob_sim.prepared_real_features.v1",
        "complete": True,
        "registry_sha256": protocol.digest,
        "day_bundle_sha256": registered["day_bundle_sha256"],
        "code_identity": registered["code_identity"],
        "configuration": {**registered["experiment"]["base_configuration"], "mm_enabled": False},
        "cadences": cadences,
        "scope": "mechanical test only",
        "claim_ready": False,
    }
    manifest["prepared_sha256"] = identity(manifest)
    publish_json(root / "manifest.json", manifest)
    publish_json(
        root / "test_access.json",
        {
            "schema_version": "lob_sim.real_market_test_access.v1",
            "registry_sha256": protocol.digest,
            "phase": "fixed_feature_preparation",
            "test_days": registered["split"]["test_days"],
        },
    )
    return root, protocol, manifest, {"2" * 64: (tmp_path, parent)}, rows


def test_trailing_window_and_availability_never_bridge_invalid_or_epoch_intervals(tmp_path, monkeypatch):
    patch_intervals(monkeypatch)
    gate = FeatureAdmission(tmp_path, {"clock": CaptureClock(0, WALL).as_dict()})

    def check(sample, available):
        return gate.exclusions(
            {"sample_ns": sample * S, "available_at_ns": available * S, "status": "VALID"}, 10 * S, {"2025-01-01"}
        )

    assert check(15, 16) == []
    assert "trailing_window_or_availability_not_joint_valid" in check(19, 22)
    assert "trailing_window_or_availability_not_joint_valid" in check(30, 31)
    assert check(35, 36) == []
    gate.finish()


def test_fit_reader_opens_only_calibration_rows_and_rejects_test_before_any_io(tmp_path, monkeypatch):
    root, protocol, _, parents, _ = mechanical_prepared(tmp_path, monkeypatch)
    original = Path.open
    opened = []

    def spy(path, *args, **kwargs):
        if path.suffix == ".jsonl":
            opened.append(path)
            assert "test" not in path.parts and "validation" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    result = read_fit_partition(root, protocol, parents, "calibration", "BTCUSDT", S, registry_sha256=protocol.digest)
    assert result.rows == ((1.0,) * 12,) * 2 and result.lengths == (2,)
    assert len(opened) == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("test-role rejection touched a file")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError, match="never opens test"):
        read_fit_partition(root, protocol, parents, "test", "BTCUSDT", S, registry_sha256=protocol.digest)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(admitted=1),
        lambda r: r.update(exclusions=["fake"]),
        lambda r: r["native"].update(available_at_ns=22 * S),
        lambda r: r["native"].update(instrument_sha256="f" * 64),
        lambda r: r["native"]["validity"].update(trade_stream_valid=False),
        lambda r: r.update(sequence_id="e" * 64),
    ],
)
def test_rehashed_row_semantic_mutations_are_rejected(tmp_path, monkeypatch, change):
    root, protocol, manifest, parents, rows = mechanical_prepared(tmp_path, monkeypatch)
    change(rows[0])
    # Native reader must reject semantics even with an attacker-supplied hash.
    from hashlib import sha256

    cadence = manifest["cadences"][0]
    file = cadence["day_files"][0]
    raw = "".join(canonical_json(r) + "\n" for r in rows).encode()
    (root / cadence["directory"] / file["path"]).write_bytes(raw)
    file["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        list(iter_admitted_day(root, protocol, cadence, file, parents, registry_sha256=protocol.digest))


def test_failed_fsync_preserves_partial_and_closes_handle_on_abandon(tmp_path, monkeypatch):
    writer = PartitionWriter(tmp_path)
    writer.write(
        {
            "native": {"symbol": "BTCUSDT", "utc_day": "2025-01-01"},
            "role": "calibration",
            "admitted": False,
            "exclusions": [],
        }
    )
    handle = writer.open_files["BTCUSDT"].handle

    def fail(*args):
        raise OSError("mechanical disk failure")

    monkeypatch.setattr("lob_sim.research.feature_storage.os.fsync", fail)
    with pytest.raises(OSError) as failure:
        writer.finalize()
    writer.abandon(failure.value)
    assert handle.closed and list(tmp_path.rglob("*.partial")) and not list(tmp_path.rglob("*.jsonl"))


def test_partial_child_prevents_prepared_completion(tmp_path, monkeypatch):
    root, protocol, manifest, _, _ = mechanical_prepared(tmp_path, monkeypatch)
    child = root / manifest["cadences"][0]["directory"] / "calibration" / "leftover.partial"
    child.write_bytes(b"retained failed evidence")
    with pytest.raises(ValueError, match="incomplete day"):
        load_prepared(root, protocol, registry_sha256=protocol.digest)
