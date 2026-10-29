"""Component-only real-kill worker; payload bytes are not simulated fills."""

from hashlib import sha256
from pathlib import Path
import sys
import time

from lob_sim.research.case_store import CaseStore

CASES = [{"case_id": str(i) * 64, "number": i} for i in range(2)]
PLAN = {"cases": CASES, "fixture": "component_only_not_market_evidence"}


def record(work, case):
    data = b"durable-component-payload"
    (work / "payload.bin").write_bytes(data)
    return {"case": case, "sha256": sha256(data).hexdigest()}


def verify(work, saved):
    if sha256((work / "payload.bin").read_bytes()).hexdigest() != saved["sha256"]:
        raise ValueError("component artifact is corrupt")


def worker(root):
    with CaseStore(root, PLAN).opened() as store:
        store.run_case(CASES[0]["case_id"], lambda work: record(work, CASES[0]), verify)

        def interrupt(work):
            (work / "payload.bin.partial").write_bytes(b"incomplete-component-payload")
            print("READY_TO_KILL", flush=True)
            while True:
                time.sleep(0.1)

        store.run_case(CASES[1]["case_id"], interrupt, verify)


if __name__ == "__main__":
    worker(Path(sys.argv[1]))
