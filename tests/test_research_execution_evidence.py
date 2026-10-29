"""Independent receipt-reader mutations; no matching or empirical admission."""

from copy import deepcopy

import pytest

from lob_sim.regime.validation import canonical_json, identity, strict_json
from lob_sim.replay.inspection import file_sha256
from lob_sim.research.case_store import CaseStore
from lob_sim.research.execution_evidence import ExecutionPublication, execution_specification
from lob_sim.research.execution_reader import verify_execution_evidence


def bundle(tmp_path):
    registered = {"code_identity": {"fixture": "receipt_only"}, "experiment_sha256": "e" * 64}
    cases = [{"case_id": "1" * 64, "latency_ms": 0}]
    root = tmp_path / "publication"
    root.mkdir()
    spec = execution_specification(registered, "a" * 64, "b" * 64, "c" * 64, cases)
    records = []
    with CaseStore(tmp_path / "store", spec).opened() as store:
        publication = ExecutionPublication(root, store)
        work, record, reused = store.run_case(cases[0]["case_id"], lambda work: {"case": cases[0]}, lambda *a: None)
        assert not reused
        records.append(record)
        publication.add(cases[0], record, work.parent)
        binding = publication.manifest()
    args = (registered, "a" * 64, "b" * 64, "c" * 64, cases, records)
    assert verify_execution_evidence(root, binding, *args)["verified"]
    return root, binding, args


@pytest.mark.parametrize(
    "mutation",
    ["omit_case", "alias_receipt", "typed_plan", "chosen_attempt", "typed_size", "escape_partial", "unknown_metadata"],
)
def test_rehashed_execution_graph_cannot_change_typed_plan_receipts_or_history(tmp_path, mutation):
    root, original, args = bundle(tmp_path)
    binding = deepcopy(original)
    if mutation == "omit_case":
        binding["cases"].clear()
    elif mutation == "alias_receipt":
        binding["cases"][0]["receipt"] = binding["plan"]
    else:
        target = binding["plan"] if mutation == "typed_plan" else binding["cases"][0]["history"]
        path = root / target["path"]
        document = strict_json(path.read_text())
        if mutation == "typed_plan":
            document["specification"]["cases"][0]["latency_ms"] = False
            document["plan_sha256"] = identity(document["specification"])
            binding["plan_digest"] = document["plan_sha256"]
        elif mutation == "chosen_attempt":
            document["completed_attempt"] = "attempt_000001"
        elif mutation == "typed_size":
            document["attempts"][0]["metadata"][0]["size_bytes"] = True
        elif mutation == "escape_partial":
            document["attempts"][0]["partial_artifacts"] = [
                {"path": "work/../../outside.partial", "size_bytes": 0, "sha256": "0" * 64}
            ]
        else:
            document["attempts"][0]["metadata"][0]["path"] = "unregistered.json"
        path.write_text(canonical_json(document))
        target["sha256"] = file_sha256(path)
    with pytest.raises(ValueError):
        verify_execution_evidence(root, binding, *args)


def test_execution_reader_does_not_call_executor(tmp_path, monkeypatch):
    root, binding, args = bundle(tmp_path)

    def forbidden(*a, **k):
        raise AssertionError("serialized verification executed a case")

    monkeypatch.setattr(CaseStore, "run_case", forbidden)
    assert verify_execution_evidence(root, binding, *args)["verified"]
