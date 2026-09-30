from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from lob_sim.cli import cmd_collect
from lob_sim.config import load_config
from lob_sim.record.envelope import EventEnvelope, SCHEMA_V3
from lob_sim.record.segmented import SegmentIntegrityError, SegmentedCaptureWriter, recover_valid_envelopes
from lob_sim.replay.reader import iter_records


def _event() -> EventEnvelope:
    return EventEnvelope(
        capture_id="preserve-1",
        schema_version=SCHEMA_V3,
        venue="BINANCE_USDM",
        instrument="BTCUSDT",
        event_kind="aggTrade",
        route="market",
        recv_seq=1,
        recv_wall_ns=1_000_000_000,
        recv_monotonic_ns=100,
        payload={"p": "100", "q": "1", "m": False},
    )


@pytest.mark.parametrize("compression", ["none", "zstd"])
def test_completed_capture_cannot_be_overwritten(tmp_path: Path, compression: str) -> None:
    if compression == "zstd":
        pytest.importorskip("zstandard")
    with SegmentedCaptureWriter(tmp_path, "preserve-1", compression=compression) as writer:
        writer.write(_event())
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(SegmentIntegrityError, match="already exists"):
        SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert len(list(iter_records(writer.manifest_path))) == 1
    assert not list(tmp_path.glob("*.reserve"))


def test_manifest_finalization_is_idempotent_before_close(tmp_path: Path) -> None:
    writer = SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")
    writer.write(_event())
    writer.finalize()
    manifest = writer.write_manifest()
    before = manifest.read_bytes()

    assert writer.write_manifest() == manifest
    writer.close()
    writer.close()

    assert manifest.read_bytes() == before
    assert not list(tmp_path.glob("*.reserve"))
    with pytest.raises(SegmentIntegrityError, match="after manifest finalization"):
        writer.update_manifest_metadata({"writer": {"complete": False}})


def test_manifest_cannot_finalize_a_still_open_segment(tmp_path: Path) -> None:
    with SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none") as writer:
        writer.write(_event())
        with pytest.raises(SegmentIntegrityError, match="finalize the current segment"):
            writer.write_manifest()
        assert not writer.manifest_path.exists()
    assert len(list(iter_records(writer.manifest_path))) == 1


def test_capture_identity_is_reserved_across_simultaneous_writers(tmp_path: Path) -> None:
    with SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none") as writer:
        writer.write(_event())
        with pytest.raises(SegmentIntegrityError, match="already reserved"):
            SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")
    assert len(list(iter_records(writer.manifest_path))) == 1


@pytest.mark.parametrize(
    "filename",
    [
        "preserve-1_000000.ndjson",
        "preserve-1_000000.ndjson.partial",
        "preserve-1_000001.ndjson.zst",
        "preserve-1.manifest.json",
        "preserve-1.manifest.json.partial",
    ],
)
def test_orphaned_capture_files_are_preserved(tmp_path: Path, filename: str) -> None:
    existing = tmp_path / filename
    existing.write_bytes(b"original forensic bytes")

    with pytest.raises(SegmentIntegrityError, match="already exists"):
        SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")

    assert existing.read_bytes() == b"original forensic bytes"
    assert list(tmp_path.iterdir()) == [existing]


@pytest.mark.parametrize("capture_id", ["../escape", "..\\escape", "C:\\escape", "", ".", "bad*id"])
def test_capture_identity_cannot_escape_or_glob_the_output_directory(tmp_path: Path, capture_id: str) -> None:
    with pytest.raises(ValueError, match="single filename component"):
        SegmentedCaptureWriter(tmp_path, capture_id, compression="none")
    assert not list(tmp_path.iterdir())


def test_segment_publish_cannot_replace_a_new_destination(tmp_path: Path) -> None:
    final = tmp_path / "preserve-1_000000.ndjson"
    with pytest.raises(SegmentIntegrityError, match="already exists"):
        with SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none") as writer:
            writer.write(_event())
            final.write_bytes(b"concurrent destination")

    assert final.read_bytes() == b"concurrent destination"
    assert list(tmp_path.glob("*.ndjson.partial"))
    assert list(tmp_path.glob("*.reserve"))
    assert not list(tmp_path.glob("*.manifest.json"))


def test_unsupported_atomic_publish_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unsupported_link(_source: object, _target: object) -> None:
        raise OSError("filesystem does not support links")

    monkeypatch.setattr("lob_sim.record.segmented.os.link", unsupported_link)
    with pytest.raises(SegmentIntegrityError, match="cannot atomically publish"):
        with SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none") as writer:
            writer.write(_event())

    assert list(tmp_path.glob("*.ndjson.partial"))
    assert list(tmp_path.glob("*.reserve"))
    assert not list(tmp_path.glob("*.ndjson"))
    assert not list(tmp_path.glob("*.manifest.json"))
    with pytest.raises(SegmentIntegrityError, match="writer failed"):
        writer.close()


def test_interrupted_capture_retains_its_identity_reservation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="interrupted"):
        with SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none") as writer:
            writer.write(_event())
            raise RuntimeError("interrupted")

    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(SegmentIntegrityError, match="already reserved"):
        SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_partial_event_write_failure_cannot_later_publish_a_complete_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none")
    original_write_json = writer._write_json

    def fail_after_event_write(value: dict[str, object]) -> None:
        original_write_json(value)
        if value.get("record") == "event":
            raise OSError("event write failed after partial progress")

    monkeypatch.setattr(writer, "_write_json", fail_after_event_write)
    with pytest.raises(OSError, match="event write failed"):
        writer.write(_event())
    for operation in (writer.close, writer.write_manifest, lambda: writer.write(_event())):
        with pytest.raises(SegmentIntegrityError, match="capture writer failed"):
            operation()

    assert not list(tmp_path.glob("*.ndjson"))
    assert not list(tmp_path.glob("*.manifest.json"))
    assert list(tmp_path.glob("*.reserve"))
    partial = next(tmp_path.glob("*.ndjson.partial"))
    assert [event.recv_seq for event in recover_valid_envelopes(partial)] == [1]


def test_rotation_open_failure_cannot_publish_an_empty_success_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = SegmentedCaptureWriter(tmp_path, "preserve-1", compression="none", max_bytes=1)
    writer.write(_event())

    def fail_open() -> None:
        raise OSError("new segment could not open")

    monkeypatch.setattr(writer, "_open_segment", fail_open)
    with pytest.raises(OSError, match="new segment could not open"):
        writer.write(replace(_event(), recv_seq=2))
    with pytest.raises(SegmentIntegrityError, match="capture writer failed"):
        writer.close()

    assert list(tmp_path.glob("*.ndjson"))
    assert not list(tmp_path.glob("*.manifest.json"))
    assert list(tmp_path.glob("*.reserve"))


@pytest.mark.parametrize("schema_version", [1, 3])
def test_capture_names_are_unique_when_wall_clock_is_the_same(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_version: int,
) -> None:
    class EmptyREST:
        async def __aenter__(self) -> EmptyREST:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_exchange_info(self) -> dict:
            return {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                        ],
                    }
                ]
            }

    async def idle_stream(
        _symbol: object,
        _spec: object,
        _config: object,
        _rest: object,
        _writer: object,
        stop_event: asyncio.Event,
        _verbose: bool,
        _sequence: object,
    ) -> None:
        await stop_event.wait()

    monkeypatch.setattr("lob_sim.cli.BinanceRESTClient", lambda _config: EmptyREST())
    monkeypatch.setattr("lob_sim.cli._collect_symbol", idle_stream)
    monkeypatch.setattr("lob_sim.cli.time.time", lambda: 123.0)
    config = replace(
        load_config(".env.example"),
        symbols=("BTCUSDT",),
        collect_seconds=1,
        record_dir=tmp_path,
        record_gzip=False,
        capture_schema_version=schema_version,
    )

    async def captures() -> None:
        await asyncio.gather(cmd_collect(config), cmd_collect(config))

    asyncio.run(captures())
    pattern = "capture_123_*.manifest.json" if schema_version >= 3 else "raw_123_*.ndjson"
    captures_on_disk = list(tmp_path.glob(pattern))
    assert len(captures_on_disk) == 2
    assert all(len(list(iter_records(path))) == 3 for path in captures_on_disk)
