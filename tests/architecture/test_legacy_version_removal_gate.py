from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_legacy_versions as scanner  # noqa: E402


EXPECTED_LEGACY_MODULES = {
    "app.domain.knowledge.eval_dataset_v2",
    "app.domain.knowledge.eval_metrics_v2",
    "app.graphs.durable_interview_state",
    "app.graphs.durable_interview_state_v2",
}


def test_legacy_version_scan_is_parse_clean_and_finds_all_known_families():
    result = scanner.scan()

    assert result["metadata"]["parse_error_count"] == 0
    assert result["metadata"]["version_family_count"] == 3
    assert {
        audit["legacy_module"] for audit in result["legacy_module_audits"]
    } == EXPECTED_LEGACY_MODULES


def test_legacy_version_artifact_matches_current_tree():
    current = scanner.scan()
    frozen = json.loads(scanner.JSON_PATH.read_text(encoding="utf-8"))

    assert frozen == current


def test_removal_authorization_requires_all_four_zero_gates():
    result = scanner.scan()

    for audit in result["legacy_module_audits"]:
        expected = all(count == 0 for count in audit["gates"].values())
        assert audit["removal_authorized"] is expected
        assert set(audit["gates"]) == {
            "production_import",
            "runtime_wiring",
            "integration_dependency",
            "migration_dependency",
        }


def test_current_legacy_versions_are_retained_with_concrete_blockers():
    result = scanner.scan()

    assert result["metadata"]["removal_authorized_count"] == 0
    assert result["removal_actions"]["removed"] == []
    assert set(result["removal_actions"]["retained"]) == EXPECTED_LEGACY_MODULES
    for audit in result["legacy_module_audits"]:
        assert audit["action"] == "retain_blocked"
        assert audit["blockers"]
        assert any(audit["evidence"][gate] for gate in audit["blockers"])


def test_durable_v1_and_v2_remain_runtime_wired():
    result = scanner.scan()
    audits = {
        audit["legacy_module"]: audit for audit in result["legacy_module_audits"]
    }

    assert audits["app.graphs.durable_interview_state"]["gates"]["runtime_wiring"] > 0
    assert audits["app.graphs.durable_interview_state_v2"]["gates"]["runtime_wiring"] > 0


def test_knowledge_v2_contracts_remain_migration_dependencies_of_v3():
    result = scanner.scan()
    audits = {
        audit["legacy_module"]: audit for audit in result["legacy_module_audits"]
    }

    assert audits["app.domain.knowledge.eval_dataset_v2"]["gates"]["migration_dependency"] > 0
    assert audits["app.domain.knowledge.eval_metrics_v2"]["gates"]["migration_dependency"] > 0


def test_audit_harness_does_not_create_its_own_integration_evidence():
    result = scanner.scan()

    for audit in result["legacy_module_audits"]:
        assert all(
            item["file"] not in scanner.AUDIT_HARNESS_FILES
            for item in audit["evidence"]["integration_dependency"]
        )
