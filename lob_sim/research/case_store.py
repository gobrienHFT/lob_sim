"""Sequential immutable case storage, independently verified before reuse.

Plan and completion records are immutable. Incomplete attempts are retained
outside the eventual published study. An OS lock, not a PID/stale lock file,
excludes other executors and is released by real process termination.
Study orchestration supplies the authoritative full census and artifact
verifier. This component does not grant empirical admission or interpret
market data. POSIX parent-directory fsync supplements file fsync; Windows
power-loss durability remains dependent on OS/filesystem guarantees.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import os
import re
import sys
from threading import get_ident
from typing import Any

from lob_sim.regime.dataset import publish_json
from lob_sim.regime.validation import canonical_json, identity, require_keys, strict_json
from lob_sim.research.evidence_io import read_json, STUDY_JSON_LIMIT

MAX_CASES, MAX_ATTEMPTS = 5184, 128


def _publish(path: Path, value: Mapping[str, Any]) -> None:
    publish_json(path, value)
    if sys.platform != "win32":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if acquired:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class CaseStore:
    def __init__(self, root: Path, specification: Mapping[str, Any], *, resume: bool = False):
        if type(resume) is not bool:
            raise ValueError("resume must be boolean")
        self.root = Path(root).resolve()
        # Snapshot caller-owned state so aliases cannot change the plan.
        self._spec_json = canonical_json(specification)
        if len(self._spec_json.encode()) > STUDY_JSON_LIMIT:
            raise ValueError("case plan exceeds its publication budget")
        self.digest = identity(strict_json(self._spec_json))
        cases = strict_json(self._spec_json)["cases"]
        if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
            raise ValueError("case plan count outside fixed limit")
        self._cases: dict[str, str] = {}
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError("registered case must be an object")
            key = case.get("case_id")
            if not isinstance(key, str) or re.fullmatch("[0-9a-f]{64}", key) is None or key in self._cases:
                raise ValueError("invalid or duplicate registered case ID")
            self._cases[key] = canonical_json(case)
        self.resume = resume
        self._locked = False
        self._owner: tuple[int, int] | None = None

    def _guard(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("case store path escapes root")

    @contextmanager
    def opened(self) -> Iterator[CaseStore]:
        self._guard(self.root / "plan.json")
        self._guard(self.root / "executor.lock")
        if self.resume:
            if not (self.root / "plan.json").is_file():
                raise ValueError("resume requires an already frozen plan")
        else:
            self.root.mkdir(parents=True, exist_ok=False)
        with _exclusive_lock(self.root / "executor.lock"):
            plan = {
                "schema_version": "lob_sim.durable_case_plan.v1",
                "specification": strict_json(self._spec_json),
                "plan_sha256": self.digest,
            }
            if self.resume:
                if canonical_json(read_json(self.root / "plan.json", maximum_bytes=STUDY_JSON_LIMIT)) != canonical_json(
                    plan
                ):
                    raise ValueError("immutable case plan changed")
            else:
                _publish(self.root / "plan.json", plan)
            self._locked = True
            self._owner = (os.getpid(), get_ident())
            try:
                yield self
            finally:
                self._locked = False
                self._owner = None

    def run_case(
        self,
        case_id: str,
        produce: Callable[[Path], dict[str, Any]],
        verify: Callable[[Path, dict[str, Any]], object],
    ) -> tuple[Path, dict[str, Any], bool]:
        if self._owner != (os.getpid(), get_ident()):
            raise ValueError("case execution must remain with its sequential executor owner")
        if not self._locked or case_id not in self._cases:
            raise ValueError("case is not registered under an active executor lock")
        case = strict_json(self._cases[case_id])
        root = self.root / "attempts" / case_id
        self._guard(root)
        root.mkdir(parents=True, exist_ok=True)
        attempts = sorted(root.iterdir())
        if (
            len(attempts) > MAX_ATTEMPTS
            or any(not p.is_dir() or re.fullmatch("attempt_[0-9]{6}", p.name) is None for p in attempts)
            or [p.name for p in attempts] != [f"attempt_{i:06d}" for i in range(len(attempts))]
        ):
            raise ValueError("attempt journal contains invalid entries")
        completed = []
        for attempt in attempts:
            self._guard(attempt)
            if (attempt / "completed.json").exists():
                completed.append(attempt)
        if len(completed) > 1:
            raise ValueError("duplicate case completions")
        if completed:
            attempt = completed[0]
            if (attempt / "failed.json").exists() or canonical_json(
                read_json(attempt / "started.json")
            ) != canonical_json({"plan_sha256": self.digest, "case": case}):
                raise ValueError("completed attempt journal changed or conflicts")
            receipt = read_json(attempt / "completed.json")
            require_keys(
                receipt, {"schema_version", "plan_sha256", "case_id", "record", "record_sha256"}, "completed case"
            )
            if (
                receipt["schema_version"] != "lob_sim.durable_case_completion.v1"
                or receipt["plan_sha256"] != self.digest
                or receipt["case_id"] != case_id
                or receipt["record_sha256"] != identity(receipt["record"])
                or canonical_json(receipt["record"].get("case")) != self._cases[case_id]
            ):
                raise ValueError("case completion binding changed")
            verify(attempt / "work", receipt["record"])
            return attempt / "work", receipt["record"], True
        if len(attempts) >= MAX_ATTEMPTS:
            raise ValueError("case retry cap reached; prior evidence retained")
        attempt = root / f"attempt_{len(attempts):06d}"
        attempt.mkdir(exist_ok=False)
        work = attempt / "work"
        work.mkdir(exist_ok=False)
        _publish(attempt / "started.json", {"plan_sha256": self.digest, "case": case})
        try:
            record = produce(work)
            if canonical_json(record.get("case")) != self._cases[case_id]:
                raise ValueError("producer returned a different registered case")
            verify(work, record)
            _publish(
                attempt / "completed.json",
                {
                    "schema_version": "lob_sim.durable_case_completion.v1",
                    "plan_sha256": self.digest,
                    "case_id": case_id,
                    "record": record,
                    "record_sha256": identity(record),
                },
            )
        except BaseException as exc:
            try:
                _publish(attempt / "failed.json", {"case_id": case_id, "error_type": type(exc).__name__})
            except BaseException as secondary:
                exc.add_note("Failed attempt receipt could not be written: " + type(secondary).__name__)
            raise
        return work, record, False
