"""Fresh-process resource probe with explicitly managed audit temporary files.

The sampled high-water counts unique managed file identities, including
temporary SQLite indexes, not unrelated machine writes. It can miss sub-sample
peaks and excludes filesystem metadata/physical cluster overhead. Do not mix
this monitored probe with uninstrumented timing repetitions.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import strict_json
from lob_sim.replay.inspection import file_sha256


def managed_bytes(root):
    identities, total, count = set(), 0, 0
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                if not path.resolve().is_relative_to(root):
                    raise ValueError("managed resource file escapes the probe")
                stat = path.stat()
            except FileNotFoundError:
                continue  # A native atomic rename/temporary cleanup can race a sample.
            count += 1
            if count > 512:
                raise ValueError("resource-probe managed file-count cap exceeded")
            key = (stat.st_dev, stat.st_ino) if stat.st_ino else str(path)
            if key not in identities:
                identities.add(key)
                total += stat.st_size
    return total


def run(args):
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    temporary = root / "temporary"
    temporary.mkdir()
    script = ROOT / "experiments/benchmark_research_scale.py"
    worker_sha = file_sha256(script)
    command = [
        sys.executable,
        str(script),
        "--file",
        str(args.file.resolve()),
        "--contract",
        str(args.contract.resolve()),
        "--model",
        str(args.model.resolve()),
        "--out",
        str(root / "worker"),
        "--mode",
        args.mode,
        "--workload",
        "audit",
        "--warmups",
        "0",
        "--repetitions",
        "1",
    ]
    environment = dict(os.environ, TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    process = None
    started, peak, samples = time.perf_counter(), 0, 0
    try:
        with (root / "stdout.log").open("x") as stdout, (root / "stderr.log").open("x") as stderr:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            while process.poll() is None:
                peak = max(peak, managed_bytes(root))
                samples += 1
                if time.perf_counter() - started > args.timeout_seconds:
                    raise TimeoutError("bounded audit resource probe exceeded its wall-time limit")
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        peak = max(peak, managed_bytes(root))
        if process.returncode != 0:
            raise RuntimeError("audit worker failed; stdout/stderr and partial evidence retained")
        if file_sha256(script) != worker_sha:
            raise ValueError("resource worker changed during probe")
        report = strict_json((root / "worker/report.json").read_text())
        result = {
            "schema_version": "lob_sim.research_scale_resources.v1",
            "complete": True,
            "worker_report_sha256": file_sha256(root / "worker/report.json"),
            "worker_script_sha256": worker_sha,
            "controller_sha256": file_sha256(Path(__file__)),
            "outer_process_wall_seconds": time.perf_counter() - started,
            "sampled_peak_managed_file_bytes": peak,
            "disk_sample_count": samples,
            "disk_sample_interval_seconds": 0.2,
            "managed_temporary_files": True,
            "os_process_peak_rss_bytes": report["rows"][0]["os_process_peak_rss_bytes"],
            "scope": "one monitored fresh process;unique managed file lengths including temporary audit indexes;not physical disk allocation,continuous peak,exclusive-host throughput or trading latency",
        }
        publish_json(root / "resources.json", result)
        return result
    except BaseException as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        publish_json(
            root / "resource_failure.json",
            {
                "complete": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "sampled_peak_managed_file_bytes": peak,
            },
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "observe", "policy"), required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 60 <= args.timeout_seconds <= 3600:
        parser.error("resource probe timeout must be 60..3600 seconds")
    run(args)
