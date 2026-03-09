from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SUMMARY = REPO_ROOT / "docs" / "sample_outputs" / "toxic_flow_seed7" / "summary.json"
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_committed_sample_summary_output_files_exist() -> None:
    summary = json.loads(SAMPLE_SUMMARY.read_text(encoding="utf-8"))
    for label, relative_path in summary["output_files"].items():
        target = REPO_ROOT / relative_path
        assert target.exists(), f"missing committed sample artifact for {label}: {relative_path}"


def test_repo_markdown_links_to_sample_artifacts_resolve() -> None:
    files = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "sample_outputs" / "README.md",
    ]
    for file in files:
        text = file.read_text(encoding="utf-8")
        for link in MARKDOWN_LINK_PATTERN.findall(text):
            if "://" in link or link.startswith("#"):
                continue
            link_target = link.split("#", 1)[0]
            if "docs/sample_outputs/" not in link_target and "sample_outputs/" not in link_target:
                continue
            target = (file.parent / link_target).resolve()
            assert target.exists(), f"broken sample-artifact link in {file}: {link}"
