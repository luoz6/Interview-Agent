from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "interview-quality-v1-t63-performance-acceptance-v2"
ACCEPTANCE_ID = "t63-performance-acceptance-v2"
DEFAULT_OUTPUT = Path(
    "tests/golden/interview_quality_v1/t63-performance-acceptance-v2.json"
)


def _requirement(identifier, requirement, evidence_codes, test_nodes):
    return {
        "id": identifier,
        "requirement": requirement,
        "evidence_codes": evidence_codes,
        "test_nodes": test_nodes,
    }


T63_CONTRACT = "tests/acceptance/test_t63_performance_acceptance.py"
FOLLOWUP = "tests/unit/test_followup_performance.py"
REQUIREMENTS: tuple[dict[str, Any], ...] = (
    _requirement(
        "T63-M01",
        "measure prep plan generation",
        ["prep_plan_generation_measured"],
        [f"{T63_CONTRACT}::test_t63_operation_summaries_use_nearest_rank_and_safe_case_id"],
    ),
    _requirement(
        "T63-M02",
        "measure plan revision reads and writes",
        ["plan_revision_read_measured", "plan_revision_write_measured"],
        ["tests/integration/postgres/test_postgres_plan_revision_store.py::test_postgres_schema_is_idempotent_and_revision_round_trips"],
    ),
    _requirement(
        "T63-M03",
        "measure session start and enforce zero Provider calls",
        ["session_start_measured", "session_start_provider_calls_zero"],
        [f"{T63_CONTRACT}::test_t63_session_start_cannot_report_a_provider_call"],
    ),
    _requirement(
        "T63-M04",
        "record Decision p50 and p95 without fabricating fixed Decision latency",
        ["decision_p50_p95", "fixed_decision_absent"],
        [
            f"{FOLLOWUP}::test_nearest_rank_uses_frozen_non_interpolated_definition",
            f"{FOLLOWUP}::test_fixed_policy_cannot_fabricate_zero_decision_baseline",
        ],
    ),
    _requirement(
        "T63-M05",
        "record Generation TTFT and completion separately",
        ["generation_ttft_measured", "generation_complete_measured"],
        [f"{FOLLOWUP}::test_synthetic_fixture_passes_engineering_but_not_provider_quality"],
    ),
    _requirement(
        "T63-M06",
        "measure SSE recovery without duplicate Provider usage",
        ["sse_resume_measured", "sse_provider_calls_zero"],
        [f"{FOLLOWUP}::test_sse_disconnect_resume_uses_cursor_without_duplicate_or_payload_leak"],
    ),
    _requirement(
        "T63-M07",
        "measure report job repository commit while preserving missing Provider baseline",
        ["report_repository_commit_measured", "report_provider_baseline_not_fabricated"],
        [f"{T63_CONTRACT}::test_t63_report_pass_requires_comparable_thirty_sample_gate"],
    ),
    _requirement(
        "T63-M08",
        "measure Artifact list get and PDF paths",
        ["artifact_list_measured", "artifact_get_measured", "artifact_pdf_measured"],
        ["tests/unit/test_report_artifact_api.py::test_historical_pdf_is_bound_to_requested_artifact_after_active_pointer_moves"],
    ),
    _requirement(
        "T63-M09",
        "measure PostgreSQL concurrent connection capacity",
        ["postgres_capacity_measured", "connection_domains_bounded"],
        [
            "tests/integration/postgres/test_stage48_postgres_capacity.py::test_business_pool_reuses_connection_and_never_exceeds_max",
            "tests/integration/postgres/test_stage48_postgres_capacity.py::test_telemetry_saturation_does_not_consume_business_capacity",
        ],
    ),
    _requirement(
        "T63-M10",
        "require an actual per-session Provider call token and cost artifact for Quality PASS",
        ["provider_usage_bound", "provider_blocker_preserved"],
        [
            f"{T63_CONTRACT}::test_t63_provider_pass_requires_bound_metered_usage_artifact",
            f"{T63_CONTRACT}::test_t63_new_authorized_model_not_run_cannot_fabricate_usage",
            f"{T63_CONTRACT}::test_t63_local_runner_artifacts_satisfy_formal_validator_without_provider",
        ],
    ),
    _requirement(
        "T63-M11",
        "measure retry amplification and enforce the frozen threshold",
        ["retry_amplification_measured"],
        [f"{FOLLOWUP}::test_token_and_fallback_accounting_is_internally_consistent"],
    ),
    *(
        _requirement(
            identifier,
            requirement,
            [evidence],
            [f"{T63_CONTRACT}::test_t63_scenario_matrix_covers_every_required_dimension_cross_product"],
        )
        for identifier, requirement, evidence in (
            ("T63-S01", "cover 3 5 8 and 10 main questions", "question_count_coverage"),
            ("T63-S02", "cover 0 1 and 2 follow-ups", "followup_count_coverage"),
            ("T63-S03", "cover scored partial and unscored reports", "score_status_coverage"),
            ("T63-S04", "cover 1 5 and 20 Artifact histories", "artifact_history_coverage"),
            ("T63-S05", "separate cold and warm measurements", "startup_class_coverage"),
            ("T63-S06", "track Windows and Ubuntu execution separately", "platform_coverage"),
        )
    ),
    _requirement(
        "T63-A01",
        "evaluate every section 5.6 Gate without converting missing evidence to PASS",
        ["gate_5_6_evaluated", "missing_evidence_blocked"],
        [
            f"{T63_CONTRACT}::test_t63_engineering_pass_preserves_real_quality_blockers",
            f"{FOLLOWUP}::test_missing_exact_fixed_cohort_is_insufficient_baseline_not_pass",
        ],
    ),
    _requirement(
        "T63-A02",
        "detect and prevent obvious N plus one report reads",
        ["active_get_constant_queries", "n_plus_one_absent"],
        ["tests/unit/test_report_artifact_api.py::test_active_report_get_does_not_materialize_job_history"],
    ),
    _requirement(
        "T63-A03",
        "keep active get acceptable with 20 historical Artifacts",
        ["active_get_history_p95", "latest_job_limit_one", "index_plan_valid"],
        [f"{T63_CONTRACT}::test_t63_database_or_capacity_drift_is_engineering_fail_not_quality_blocker"],
    ),
    _requirement(
        "T63-A04",
        "publish actual Provider usage only when calls tokens cost and source hash are bound",
        ["actual_provider_artifact_required"],
        [f"{T63_CONTRACT}::test_t63_provider_pass_requires_bound_metered_usage_artifact"],
    ),
    _requirement(
        "T63-A05",
        "never claim Quality PASS when performance error retry or data boundaries are exceeded",
        ["quality_fail_closed"],
        [
            f"{T63_CONTRACT}::test_t63_database_or_capacity_drift_is_engineering_fail_not_quality_blocker",
            f"{FOLLOWUP}::test_gate_failure_is_reported_with_maximum_case_evidence",
        ],
    ),
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_acceptance() -> dict[str, Any]:
    requirements = [dict(item) for item in REQUIREMENTS]
    unique_nodes = sorted(
        {node for requirement in requirements for node in requirement["test_nodes"]}
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": ACCEPTANCE_ID,
        "plan_task": "T63",
        "postgresql_required": True,
        "platforms_required": list(("windows-11-x64", "ubuntu-24.04-x64")),
        "skip_policy": "forbidden",
        "provider_calls_expected": 0,
        "expected_engineering_status": "PASS",
        "expected_quality_status": "BLOCKED",
        "expected_overall_status": "BLOCKED",
        "required_quality_blockers": [
            "ACTUAL_PROVIDER_USAGE_ARTIFACT_MISSING",
            "INSUFFICIENT_BASELINE",
            "NOT_RUN_PROVIDER_QUALITY",
            "UBUNTU_MEASUREMENT_NOT_RUN",
        ],
        "planned_scenario_count": 432,
        "requirement_count": len(requirements),
        "unique_test_node_count": len(unique_nodes),
        "requirements": requirements,
        "unique_test_nodes": unique_nodes,
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def validate_acceptance(payload: dict[str, Any], *, root: Path) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("T63 acceptance schema drifted")
    if payload.get("acceptance_id") != ACCEPTANCE_ID:
        raise ValueError("T63 acceptance identity drifted")
    if payload.get("plan_task") != "T63":
        raise ValueError("T63 acceptance task binding drifted")
    if payload != build_acceptance():
        raise ValueError("T63 acceptance content differs from the frozen builder")
    if payload.get("requirement_count") != len(REQUIREMENTS) != 0:
        raise ValueError("T63 requirement count drifted")
    expected_ids = [item["id"] for item in REQUIREMENTS]
    if [item.get("id") for item in payload.get("requirements", [])] != expected_ids:
        raise ValueError("T63 requirement IDs or order drifted")
    nodes = payload.get("unique_test_nodes", [])
    if len(nodes) != len(set(nodes)) or payload.get("unique_test_node_count") != len(nodes):
        raise ValueError("T63 unique test-node projection drifted")
    for requirement in payload["requirements"]:
        if not requirement.get("evidence_codes") or not requirement.get("test_nodes"):
            raise ValueError("T63 requirement lacks evidence or tests")
        for node in requirement["test_nodes"]:
            path = node.split("::", 1)[0]
            if not (root / path).is_file():
                raise ValueError(f"T63 test node file is missing: {path}")
    copy = dict(payload)
    digest = copy.pop("canonical_sha256", None)
    if digest != _canonical_sha256(copy):
        raise ValueError("T63 acceptance canonical hash drifted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    payload = build_acceptance()
    validate_acceptance(payload, root=root)
    output = args.output if args.output.is_absolute() else root / args.output
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != serialized:
            raise SystemExit("checked-in T63 acceptance differs from deterministic builder")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8", newline="\n")
    print(payload["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
