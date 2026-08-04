import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JSON_RECORD = ROOT / "docs" / "local-v1-long-term-memory-acceptance.json"
MARKDOWN_RECORD = ROOT / "docs" / "local-v1-long-term-memory-acceptance.md"


def test_acceptance_record_is_complete_and_does_not_preclaim_task_14():
    record = json.loads(JSON_RECORD.read_text(encoding="utf-8"))

    assert record["local_memory_acceptance"] == "PASS"
    assert record["overall_program_state"] == "COMPLETE"
    assert record["hosted_v2"] == "NO_GO_FOR_NOW"
    assert record["real_provider_evaluation"] == "NOT_RUN"
    assert record["real_candidate_production_processing"] == "PROHIBITED"
    assert record["next_required_task"] == "NONE"
    assert set(record["definition_of_done"]) == {
        f"DOD-{index:02d}" for index in range(1, 27)
    }
    assert record["definition_of_done"]["DOD-23"] == "PASS"
    assert record["definition_of_done"]["DOD-26"] == "PASS"
    assert set(record["definition_of_done"].values()) == {"PASS"}


def test_acceptance_counts_match_executed_evidence():
    evidence = json.loads(JSON_RECORD.read_text(encoding="utf-8"))["evidence"]

    assert evidence["full_python_postgres"] == {
        "passed": 2123,
        "skipped": 1,
        "failed": 0,
        "skip_scope": "real_provider_evaluation",
    }
    assert evidence["full_browser"] == {
        "passed": 86,
        "skipped": 38,
        "failed": 0,
    }
    assert evidence["memory_center_browser"] == {
        "desktop_passed": 8,
        "mobile_passed": 8,
        "failed": 0,
    }
    assert evidence["long_context"] == {
        "pytest_passed": 14,
        "deterministic_cases": 3,
        "hard_invariant_pass_rate": 1.0,
        "failed": 0,
    }
    assert evidence["privacy_firewall_isolation"] == {
        "passed": 75,
        "failed": 0,
    }
    assert evidence["postgres_test_relation_residue"] == 0
    assert evidence["test_listener_residue"] == 0


def test_public_acceptance_artifacts_contain_no_private_locators():
    rendered = JSON_RECORD.read_text(encoding="utf-8") + MARKDOWN_RECORD.read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "postgresql://",
        "OPENAI_API_KEY=",
        "local-owner",
        "principal_id",
        "fact_id",
        "session_id",
        "source_manifest_sha256",
        "source_excerpt_sha256",
        "BEGIN PRIVATE KEY",
    ):
        assert forbidden not in rendered


def test_markdown_has_all_requirement_rows_and_stable_final_status():
    text = MARKDOWN_RECORD.read_text(encoding="utf-8")

    for index in range(1, 27):
        assert f"| {index} |" in text
    for expected in (
        "LOCAL_MEMORY_ACCEPTANCE=PASS",
        "LOCAL_V1_LONG_TERM_MEMORY=COMPLETE",
        "LOCAL_MEMORY_CONSUMPTION=AVAILABLE_BUT_DEFAULT_OFF",
        "NEXT_REQUIRED_TASK=NONE",
        "REAL_PROVIDER_EVALUATION=NOT_RUN",
        "REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED",
    ):
        assert expected in text
