"""OS-kill/restart component proof, not study/matching recovery evidence."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
import json

import pytest

from lob_sim.research.case_store import CaseStore
from research_case_store_worker import CASES, PLAN, record, verify


def test_real_process_termination_releases_lock_and_preserves_completed_case(tmp_path):
    root = tmp_path / "store"
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("research_case_store_worker.py")), str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    signal = Queue()
    Thread(target=lambda: signal.put(proc.stdout.readline()), daemon=True).start()
    try:
        assert signal.get(timeout=30).strip() == "READY_TO_KILL"
        assert proc.poll() is None
        # A concurrent writer is excluded while the owner is demonstrably live.
        with pytest.raises(OSError):
            with CaseStore(root, PLAN, resume=True).opened():
                pytest.fail("two executors acquired the same OS lock")
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()
    assert proc.returncode is not None
    prior_partial = list(root.rglob("payload.bin.partial"))
    assert len(prior_partial) == 1
    old_bytes = prior_partial[0].read_bytes()

    def forbidden(work):
        raise AssertionError("a verified completed case must not rerun")

    with CaseStore(root, PLAN, resume=True).opened() as store:
        _, saved, reused = store.run_case(CASES[0]["case_id"], forbidden, verify)
        assert reused and saved["case"] == CASES[0]
        _, saved, reused = store.run_case(CASES[1]["case_id"], lambda work: record(work, CASES[1]), verify)
        assert not reused and saved["case"] == CASES[1]
    assert prior_partial[0].read_bytes() == old_bytes
    assert len(list((root / "attempts" / CASES[1]["case_id"]).iterdir())) == 2
    assert len(list(root.rglob("completed.json"))) == 2


def test_completed_case_corruption_and_plan_drift_cannot_trigger_silent_rerun(tmp_path):
    root = tmp_path / "store"
    with CaseStore(root, PLAN).opened() as store:
        work, _, _ = store.run_case(CASES[0]["case_id"], lambda work: record(work, CASES[0]), verify)
    changed = deepcopy(PLAN)
    changed["fixture"] = "different"
    with pytest.raises(ValueError, match="plan changed"):
        with CaseStore(root, changed, resume=True).opened():
            pass
    (work / "payload.bin").write_bytes(b"corrupted")
    with CaseStore(root, PLAN, resume=True).opened() as store:
        with pytest.raises(ValueError, match="corrupt"):
            store.run_case(CASES[0]["case_id"], lambda work: pytest.fail("corrupt case reran"), verify)


def test_boolean_cannot_replace_integer_in_serialized_plan(tmp_path):
    root = tmp_path / "store"
    with CaseStore(root, PLAN).opened():
        pass
    path = root / "plan.json"
    changed = json.loads(path.read_text())
    changed["specification"]["cases"][0]["number"] = False
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="plan changed"):
        with CaseStore(root, PLAN, resume=True).opened():
            pass


def test_executor_rejects_cross_thread_case_execution(tmp_path):
    root = tmp_path / "store"
    with CaseStore(root, PLAN).opened() as store, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(store.run_case, CASES[0]["case_id"], lambda work: record(work, CASES[0]), verify)
        with pytest.raises(ValueError, match="owner"):
            pending.result(timeout=30)
