"""Independent host-telemetry re-reader, separate from the capture producer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lob_sim.regime.validation import identity, integer, require_keys, strict_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.evidence_io import read_json, require_finalized
from lob_sim.research.interval_reader import digest


def verify_soak(directory: Path, *, capture_manifest: Path | None = None) -> dict[str, Any]:
    root = directory.resolve()
    require_finalized(root, "completion.json")
    request = read_json(root / "request.json")
    completion = read_json(root / "completion.json")
    if (
        request.get("schema_version") != "lob_sim.public_soak_request.v1"
        or request.get("source_kind") != "public_binance_network"
    ):
        raise ValueError("unsupported public soak request")
    require_keys(
        completion,
        {
            "schema_version",
            "complete",
            "request_sha256",
            "capture_manifest",
            "capture_manifest_sha256",
            "host_telemetry",
            "writer",
            "configured_seconds",
            "non_claims",
            "completion_sha256",
        },
        "soak completion",
    )
    for record, key in ((request, "request_sha256"), (completion, "completion_sha256")):
        if record.get(key) != identity({k: v for k, v in record.items() if k != key}):
            raise ValueError("soak artifact content identity mismatch")
    if (
        completion["schema_version"] != "lob_sim.public_soak_completion.v1"
        or completion["complete"] is not True
        or completion["request_sha256"] != request["request_sha256"]
    ):
        raise ValueError("incomplete or mismatched soak completion")
    name = completion["capture_manifest"]
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".manifest.json") or "\\" in name:
        raise ValueError("unsafe capture manifest binding")
    path = (root / name).resolve()
    if capture_manifest is not None and capture_manifest.resolve() != path:
        raise ValueError("capture manifest differs from its soak completion binding")
    if not path.is_relative_to(root) or file_sha256(path) != digest(
        completion["capture_manifest_sha256"], "soak capture identity"
    ):
        raise ValueError("soak capture file identity mismatch")
    manifest = read_json(path)
    runtime = manifest.get("capture_runtime", {})
    if (
        runtime.get("request_sha256") != request["request_sha256"]
        or runtime.get("runtime") != request["runtime"]
        or runtime.get("writer") != completion["writer"]
        or runtime.get("host_telemetry") != completion["host_telemetry"]
    ):
        raise ValueError("soak capture runtime parent mismatch")
    settings = require_keys(
        request["settings"],
        {"duration_seconds", "sample_seconds", "minimum_disk_free_bytes", "maximum_rss_bytes"},
        "soak settings",
    )
    for key, value in settings.items():
        integer(value, key, minimum=1)
    if (
        completion["configured_seconds"] != settings["duration_seconds"]
        or type(completion["configured_seconds"]) is not int
    ):
        raise ValueError("soak configured duration mismatch")
    host = completion["host_telemetry"]
    require_keys(
        host,
        {
            "schema_version",
            "path",
            "sha256",
            "sample_count",
            "sample_seconds",
            "maximum_observed_rss_bytes",
            "rss_unavailable_samples",
            "minimum_observed_disk_free_bytes",
            "maximum_observed_loop_lag_ns",
            "writer",
            "limits",
        },
        "host telemetry summary",
    )
    if (
        host["schema_version"] != "lob_sim.capture_host_telemetry.v1"
        or host["path"] != "telemetry.jsonl"
        or host["sample_seconds"] != settings["sample_seconds"]
    ):
        raise ValueError("host telemetry contract mismatch")
    samples = missing = max_lag = 0
    maximum_rss = minimum_free = last_mono = None
    row_writer_counts = (0, 0)
    import hashlib

    checksum = hashlib.sha256()
    with (root / "telemetry.jsonl").open("rb") as handle:
        while raw := handle.readline(16 * 1024 + 1):
            if len(raw) > 16 * 1024 or not raw.endswith(b"\n"):
                raise ValueError("host telemetry row exceeds limit or is incomplete")
            checksum.update(raw)
            row = require_keys(
                strict_json(raw.decode("utf-8")),
                {
                    "schema_version",
                    "sample_index",
                    "recv_monotonic_ns",
                    "recv_wall_ns",
                    "loop_lag_ns",
                    "resources",
                    "writer",
                },
                "host sample",
            )
            samples += 1
            if (
                row["schema_version"] != "lob_sim.capture_host_sample.v1"
                or integer(row["sample_index"], "sample index") != samples
            ):
                raise ValueError("host sample census mismatch")
            mono = integer(row["recv_monotonic_ns"], "host monotonic time")
            integer(row["recv_wall_ns"], "host wall time")
            if last_mono is not None and mono < last_mono:
                raise ValueError("host monotonic sample clock regressed")
            last_mono = mono
            max_lag = max(max_lag, integer(row["loop_lag_ns"], "loop lag"))
            resources = require_keys(row["resources"], {"disk_free_bytes", "rss_bytes", "rss_basis"}, "host resources")
            free = integer(resources["disk_free_bytes"], "sampled disk free")
            rss = resources["rss_bytes"]
            if free < settings["minimum_disk_free_bytes"] or (
                rss is not None and integer(rss, "sampled RSS") > settings["maximum_rss_bytes"]
            ):
                raise ValueError("sampled resource limit violated")
            if rss is None:
                missing += 1
            else:
                maximum_rss = max(maximum_rss or 0, rss)
            minimum_free = min(minimum_free if minimum_free is not None else free, free)
            writer = row["writer"]
            counts = (
                integer(writer["records_enqueued"], "writer enqueued"),
                integer(writer["records_written"], "writer written"),
            )
            if (
                counts[1] > counts[0]
                or any(a < b for a, b in zip(counts, row_writer_counts))
                or writer["failure_type"] is not None
                or integer(writer["overflow_count"], "sample overflow") != 0
            ):
                raise ValueError("host sample records a failed or regressing writer")
            row_writer_counts = counts
    if samples < 1 or checksum.hexdigest() != digest(host["sha256"], "host telemetry file identity"):
        raise ValueError("host telemetry bytes/count mismatch")
    for key, value in (
        ("sample_count", samples),
        ("rss_unavailable_samples", missing),
        ("maximum_observed_rss_bytes", maximum_rss),
        ("minimum_observed_disk_free_bytes", minimum_free),
        ("maximum_observed_loop_lag_ns", max_lag),
    ):
        if host[key] != value or (value is not None and type(host[key]) is not int):
            raise ValueError("host telemetry census differs: " + key)
    for summary, count in ((host["writer"], samples), (completion["writer"], manifest["event_count"])):
        if (
            summary.get("complete") is not True
            or integer(summary.get("overflow_count"), "final overflow") != 0
            or summary.get("failure_type") is not None
        ):
            raise ValueError("soak writer did not complete")
        if (
            integer(summary.get("records_enqueued"), "final enqueued") != count
            or integer(summary.get("records_written"), "final written") != count
        ):
            raise ValueError("soak final writer census mismatch")
    if row_writer_counts[0] > completion["writer"]["records_enqueued"]:
        raise ValueError("sample exceeds final writer census")
    return {
        "schema_version": "lob_sim.public_soak_verification.v1",
        "verified": True,
        "completion_sha256": completion["completion_sha256"],
        "sample_count": samples,
        "scope": "independent host telemetry and finalization census;raw receipts/native validity require capture-audit",
    }
