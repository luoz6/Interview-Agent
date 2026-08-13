from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_knowledge_eval_v3_annotation_package import (
    build_package,
    validate_package,
)


def _copy_instructions(target, source):
    target.mkdir(parents=True)
    for name in ("README.md", "annotation-protocol.md"):
        (target / name).write_bytes((source / name).read_bytes())


def test_builds_blank_family_isolated_75_25_package(tmp_path):
    repository_package = Path("eval/knowledge-v3/authoring")
    target = tmp_path / "authoring"
    _copy_instructions(target, repository_package)

    summary = build_package(
        target,
        manifest_path=Path("app/data/knowledge_v2/manifest.json"),
        baseline_revision="a" * 40,
    )

    assert summary == {
        "status": "valid_blank_authoring_scaffold",
        "package_version": "knowledge-eval-v3-authoring-rmqv4-2026-08-13-v1",
        "slot_count": 100,
        "tuning_count": 75,
        "holdout_count": 25,
        "case_type_count": 14,
        "family_count": 100,
        "historical_case_id_overlap_count": 0,
        "runnable_v3_dataset": False,
        "independent_evaluation_complete": False,
    }
    manifest = json.loads((target / "package-manifest.json").read_text("utf-8"))
    assert manifest["corpus_version"] == "memory-p1-zh-v4"
    assert manifest["runnable_v3_dataset"] is False
    assert manifest["independent_evaluation_complete"] is False


def test_validation_rejects_prefilled_query_or_changed_file(tmp_path):
    repository_package = Path("eval/knowledge-v3/authoring")
    target = tmp_path / "authoring"
    _copy_instructions(target, repository_package)
    build_package(
        target,
        manifest_path=Path("app/data/knowledge_v2/manifest.json"),
        baseline_revision="a" * 40,
    )

    tuning = target / "tuning-authoring-template.jsonl"
    lines = tuning.read_text("utf-8").splitlines()
    first = json.loads(lines[0])
    first["query_text"] = "这是一条不应预填的查询"
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
    tuning.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="file SHA-256 mismatch"):
        validate_package(target)


def test_repository_package_is_valid_blank_scaffold():
    summary = validate_package(Path("eval/knowledge-v3/authoring"))
    assert summary["slot_count"] == 100
    assert summary["historical_case_id_overlap_count"] == 0
