"""Real process interruption of native matching, not empirical-day evidence."""

from copy import deepcopy
import csv
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread

import pytest

from lob_sim.audit.csv_limits import aggregate_csv_fields
from lob_sim.regime.validation import canonical_json, identity, strict_json
from lob_sim.replay.inspection import file_sha256
from research_study_recovery_worker import reduced_grid

pytest_plugins = ["test_research_study_graph"]


def worker(root, output, store, mode):
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("research_study_recovery_worker.py")),
            str(root),
            str(output),
            str(store),
            mode,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def completed(root, output, store, mode):
    process = worker(root, output, store, mode)
    try:
        stdout, stderr = process.communicate(timeout=600)
        assert process.returncode == 0, stdout + stderr
        assert stdout.strip() == "VERIFIED_COMPLETE"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        process.stdout.close()
        process.stderr.close()
    return strict_json((output / "manifest.json").read_text())


def equivalent_artifacts(left_root, left, right_root, right):
    """Exclude only path/creation/mtime metadata and derived hashes/sizes.

    All native bundles are independently audited before comparison. Event,
    fill, markout, regime, policy, risk, model and clock-table bytes must match.
    No economic, validity, lifecycle or clock field is normalized.
    """
    assert left["case"] == right["case"] and left["configuration"] == right["configuration"]
    assert left["core_audits"] == right["core_audits"]
    assert left["tables"] == right["tables"]
    a, b = left_root / left["run_directory"], right_root / right["run_directory"]
    assert set(left["artifacts"]) == set(right["artifacts"])
    for label in left["artifacts"]:
        name = left["artifacts"][label]["path"]
        assert name == right["artifacts"][label]["path"]
        if label == "summary":
            x, y = strict_json((a / name).read_text()), strict_json((b / name).read_text())
            assert set(x.pop("output_files")) == set(y.pop("output_files"))
            assert canonical_json(x) == canonical_json(y)
        elif label == "summary_csv":
            with aggregate_csv_fields(), (a / name).open(newline="") as x, (b / name).open(newline="") as y:
                rows_a, rows_b = list(csv.DictReader(x)), list(csv.DictReader(y))
            for row in rows_a + rows_b:
                row.pop("output_files", None)
            assert rows_a == rows_b
        elif label == "manifest":
            x, y = strict_json((a / name).read_text()), strict_json((b / name).read_text())
            for data in (x, y):
                data.pop("created_at_utc")
                data["outputs"] = sorted(data["outputs"])
                for artifact_label, artifact in data["output_artifacts"].items():
                    artifact["path"] = artifact_label
                    artifact.pop("modified_at_utc", None)
                    if artifact_label in {"summary", "summary_csv"}:
                        artifact["sha256"] = "path-dependent-summary"
                        artifact["size_bytes"] = "path-dependent-summary"
                data.pop("artifact_bundle", None)  # Derived from audited artifact byte identities above.
            assert canonical_json(x) == canonical_json(y)
        else:
            assert (a / name).read_bytes() == (b / name).read_bytes(), label


@pytest.mark.long_research
def test_kill_native_matching_resume_and_clean_run_equivalence(graph_inputs, monkeypatch):
    from lob_sim.research.study_reader import verify_registered_study
    import lob_sim.research.study_reader as reader

    args, protocol = graph_inputs
    root = args[0].parent
    store, interrupted, resumed, clean, fresh = [
        root / n for n in ("recovery_store", "interrupted", "resumed", "clean", "clean_store")
    ]
    process = worker(root, interrupted, store, "interrupt")
    signal = Queue()
    Thread(target=lambda: signal.put(process.stdout.readline()), daemon=True).start()
    try:
        message = signal.get(timeout=300).strip()
        assert message == "MATCHING_READY_TO_KILL", message + (
            process.stderr.read() if process.poll() is not None else ""
        )
        assert process.poll() is None
        assert len(list(store.rglob("completed.json"))) == 1
        partials = {p: file_sha256(p) for p in store.rglob("*.partial")}
        assert partials
        process.kill()
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        process.stdout.close()
        process.stderr.close()
    assert not (interrupted / "manifest.json").exists()
    assert (interrupted / "attempts.jsonl.partial").exists()
    result = completed(root, resumed, store, "resume")
    assert all(file_sha256(p) == sha for p, sha in partials.items())
    original = completed(root, clean, fresh, "clean")
    for left, right in zip(result["results"], original["results"], strict=True):
        equivalent_artifacts(resumed, left, clean, right)
    assert result["comparisons"] == original["comparisons"]
    for binding in result["comparisons"]:
        assert (resumed / binding["path"]).read_bytes() == (clean / binding["path"]).read_bytes()

    # A third publication must perform zero matching at all, yet re-read the
    # complete native graph and recover exactly the original artifact bytes.
    reused_root = root / "reused"
    reused = completed(root, reused_root, store, "reuse")
    assert reused["results"] == result["results"] and reused["comparisons"] == result["comparisons"]
    monkeypatch.setattr(reader, "expected_cases", reduced_grid)
    verify_args = (resumed, *args[1:])
    proof = verify_registered_study(*verify_args, registry_sha256=protocol.digest)
    assert proof["execution"]["verified"] and proof["cases"] == 3
    history = strict_json((resumed / result["execution"]["cases"][1]["history"]["path"]).read_text())
    assert len(history["attempts"]) == 2 and history["attempts"][0]["partial_artifacts"]

    # Rehashing the top-level graph cannot hide a changed frozen census.
    path = resumed / "manifest.json"
    prior = path.read_bytes()
    changed = deepcopy(result)
    changed["execution"]["cases"].pop()
    changed["study_sha256"] = identity({k: v for k, v in changed.items() if k != "study_sha256"})
    path.write_text(canonical_json(changed))
    try:
        with pytest.raises(ValueError, match="execution plan/case census"):
            verify_registered_study(*verify_args, registry_sha256=protocol.digest)
    finally:
        path.write_bytes(prior)

    reverse_root = root / "reverse_order"
    reversed_result = completed(root, reverse_root, root / "reverse_store", "reverse")
    for left, right in zip(reversed_result["results"], original["results"], strict=True):
        equivalent_artifacts(reverse_root, left, clean, right)
    assert reversed_result["comparisons"] == original["comparisons"]
