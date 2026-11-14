"""Mocked I/O tests of soak mechanics only, never real-market evidence."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from lob_sim.regime.validation import identity
from lob_sim.research.capture_receipts import audit_receipts
from lob_sim.research.capture_telemetry import CaptureResourceError, SoakSettings, process_rss_bytes, resource_sample
from lob_sim.research.soak import instrument_entries, run_soak
from lob_sim.research.soak_reader import verify_soak
from test_research_capture_intervals import cfg


def exchange():
    return {
        "symbols": [
            {
                "symbol": symbol,
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "baseAsset": symbol.removesuffix("USDT"),
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
            for symbol in cfg().symbols
        ]
    }


@pytest.fixture
def mocked_io(monkeypatch):
    import lob_sim.research.soak as module
    import lob_sim.cli as cli
    import lob_sim.research.capture_telemetry as telemetry

    class Rest:
        def __init__(self, config):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_exchange_info(self):
            return exchange()

    async def collect(symbol, spec, config, rest, writer, stop, verbose, next_sequence):
        # No manufactured book/prices here. The test exercises finalization,
        # metadata and writer census; its audit has zero valid book time.
        await stop.wait()

    monkeypatch.setattr(module, "BinanceRESTClient", Rest)
    monkeypatch.setattr(cli, "_collect_symbol", collect)
    monkeypatch.setattr(telemetry, "process_rss_bytes", lambda: (10_000, "mocked_test_measurement"))


def test_resource_reader_is_explicit_about_unavailable_rss():
    value, basis = process_rss_bytes()
    assert value is None or (type(value) is int and value > 0)
    assert isinstance(basis, str) and basis


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_seconds", True),
        ("sample_seconds", 0),
        ("duration_seconds", 172_801),
        ("sample_seconds", 61),
        ("minimum_disk_free_bytes", -1),
        ("maximum_rss_bytes", 0),
    ],
)
def test_resource_policy_cannot_coerce_or_unbound_limits(field, value):
    with pytest.raises(ValueError):
        replace(SoakSettings(), **{field: value})


@pytest.mark.parametrize("entry", ["status", "contractType", "marginAsset"])
def test_instrument_contract_unavailability_is_not_silently_accepted(entry):
    data = exchange()
    data["symbols"][0][entry] = "unsupported"
    with pytest.raises(ValueError):
        instrument_entries(data)


def test_filter_duplicates_or_missing_notional_are_rejected():
    for mutate in (lambda filters: filters.append(deepcopy(filters[0])), lambda filters: filters.pop()):
        data = exchange()
        mutate(data["symbols"][0]["filters"])
        with pytest.raises(ValueError, match="filters"):
            instrument_entries(data)


def test_guard_rejects_disk_and_rss_without_deleting_anything(tmp_path, monkeypatch):
    import lob_sim.research.capture_telemetry as module

    marker = tmp_path / "keep.txt"
    marker.write_text("forensic")
    monkeypatch.setattr(module, "process_rss_bytes", lambda: (1024, "mocked_test_measurement"))
    with pytest.raises(CaptureResourceError, match="RSS"):
        resource_sample(tmp_path, SoakSettings(maximum_rss_bytes=1))
    with pytest.raises(CaptureResourceError, match="disk"):
        resource_sample(tmp_path, SoakSettings(minimum_disk_free_bytes=10**18))
    assert marker.read_text() == "forensic"


def test_completed_mock_run_reopens_all_telemetry_and_preserves_old_outputs(tmp_path, mocked_io):
    root = tmp_path / "soak"
    result = asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1, sample_seconds=1)))
    assert result["complete"] and result["writer"]["complete"]
    proof = verify_soak(root)
    assert proof["verified"] and proof["sample_count"] >= 1
    receipts = audit_receipts(root / result["capture_manifest"], cfg().symbols)
    assert receipts["integrity"]["ok"] and receipts["stop_ns"] is not None
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1)))
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    with pytest.raises(ValueError, match="binding"):
        verify_soak(root, capture_manifest=root / "different.manifest.json")


def test_guard_failure_keeps_request_and_prevents_complete_capture(tmp_path, mocked_io):
    root = tmp_path / "failed"
    with pytest.raises(CaptureResourceError):
        asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1, minimum_disk_free_bytes=10**18)))
    assert (root / "request.json").exists() and (root / "failure.json").exists()
    assert not (root / "completion.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        verify_soak(root)


def test_interrupt_then_restart_never_appends_or_rewrites_parent(tmp_path, mocked_io, monkeypatch):
    import lob_sim.cli as cli

    root = tmp_path / "interrupted"

    async def interrupted():
        started = asyncio.Event()

        async def collect(*args):
            started.set()
            await args[5].wait()

        monkeypatch.setattr(cli, "_collect_symbol", collect)
        task = asyncio.create_task(run_soak(cfg(), root, SoakSettings(duration_seconds=60)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(interrupted())
    assert (root / "failure.json").exists() and list(root.glob("*.partial"))
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = asyncio.run(run_soak(cfg(), tmp_path / "restarted", SoakSettings(duration_seconds=1), restart_from=root))
    assert result["complete"]
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert json.loads((tmp_path / "restarted" / "request.json").read_text())["restart"]["continuity"].startswith(
        "new capture"
    )


def test_rehashed_telemetry_summary_is_not_a_statistical_self_audit(tmp_path, mocked_io):
    root = tmp_path / "soak"
    result = asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1)))
    path = root / "completion.json"
    complete = json.loads(path.read_text())
    complete["host_telemetry"]["maximum_observed_rss_bytes"] += 1
    complete["completion_sha256"] = identity({k: v for k, v in complete.items() if k != "completion_sha256"})
    path.write_text(json.dumps(complete))
    manifest_path = root / result["capture_manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["capture_runtime"]["host_telemetry"] = complete["host_telemetry"]
    manifest["manifest_sha256"] = identity({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    manifest_path.write_text(json.dumps(manifest))
    from lob_sim.replay.inspection import file_sha256

    complete["capture_manifest_sha256"] = file_sha256(manifest_path)
    complete["completion_sha256"] = identity({k: v for k, v in complete.items() if k != "completion_sha256"})
    path.write_text(json.dumps(complete))
    with pytest.raises(ValueError, match="census differs"):
        verify_soak(root)


def test_completion_publication_failure_cannot_admit_finalized_raw_capture(tmp_path, mocked_io, monkeypatch):
    import lob_sim.research.soak as module

    original = module.publish_json

    def fail_last(path, value):
        if path.name == "completion.json":
            raise OSError("simulated publication failure")
        original(path, value)

    monkeypatch.setattr(module, "publish_json", fail_last)
    root = tmp_path / "failed"
    with pytest.raises(OSError):
        asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1)))
    assert list(root.glob("*.manifest.json")) and (root / "failure.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        audit_receipts(next(root.glob("*.manifest.json")), cfg().symbols)


def test_collector_failure_keeps_segment_tail(tmp_path, mocked_io, monkeypatch):
    import lob_sim.cli as cli

    async def failing(*args):
        raise ValueError("simulated parser boundary")

    monkeypatch.setattr(cli, "_collect_symbol", failing)
    root = tmp_path / "failed"
    with pytest.raises(ExceptionGroup):
        asyncio.run(run_soak(cfg(), root, SoakSettings(duration_seconds=1)))
    assert list(root.glob("*.partial")) and (root / "failure.json").exists()
    assert not (root / "completion.json").exists()
