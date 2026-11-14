"""Real native artifact relocation; no matching or manifest rewrite on reuse."""

from dataclasses import replace
import pytest
from lob_sim.research.study_run_reader import verify_case
from lob_sim.research.case_publication import materialize_case

pytest_plugins = ["test_research_study_replay"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_materialized_native_case_reverifies_without_rewriting_any_bytes(bounded_pair, tmp_path, index):
    source_root, record, case, cfg, view, unit = bounded_pair[index]
    original = verify_case(source_root, record, case, cfg, view, unit)
    target_root = tmp_path / "publication"
    target = materialize_case(source_root, target_root, record)
    target_cfg = replace(cfg, record_dir=target_root / "runs" / case["case_id"])
    assert verify_case(target_root, record, case, target_cfg, view, unit) == original
    for path in target.iterdir():
        assert path.samefile(source_root / record["run_directory"] / path.name)
    with pytest.raises(FileExistsError):
        materialize_case(source_root, target_root, record)
