"""Resource monitoring accounts for atomic hardlinks and preserves failures."""

from argparse import Namespace
from pathlib import Path
import os

import pytest

import experiments.research_scale_resources as resources
from lob_sim.regime.validation import strict_json


def test_managed_logical_bytes_deduplicate_publication_hardlinks(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"abc")
    os.link(original, tmp_path / "published")
    assert resources.managed_bytes(tmp_path) == 3


def test_managed_file_census_has_a_hard_cap(tmp_path):
    for index in range(513):
        (tmp_path / str(index)).touch()
    with pytest.raises(ValueError, match="file-count cap"):
        resources.managed_bytes(tmp_path)


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_or_timed_out_worker_keeps_logs_and_incomplete_receipt(tmp_path, monkeypatch, timeout):
    class Process:
        returncode = None if timeout else 2
        killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -1

        def wait(self, timeout):
            return self.returncode

    process = Process()
    monkeypatch.setattr(resources.subprocess, "Popen", lambda *a, **k: process)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(resources.time, "perf_counter", lambda: next(ticks))
    output = tmp_path / "output"
    args = Namespace(
        out=output,
        file=Path("input"),
        contract=Path("contract"),
        model=Path("model"),
        mode="observe",
        timeout_seconds=1,
    )
    with pytest.raises(TimeoutError if timeout else RuntimeError):
        resources.run(args)
    failure = strict_json((output / "resource_failure.json").read_text())
    assert failure["complete"] is False
    assert failure["error_type"] == ("TimeoutError" if timeout else "RuntimeError")
    assert (output / "stdout.log").is_file() and (output / "stderr.log").is_file()
    assert not (output / "resources.json").exists()
    assert process.killed is timeout
