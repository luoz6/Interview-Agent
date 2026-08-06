import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.t63_performance import (
    T63ActiveGetDatabaseContract,
    T63OperationSample,
    T63PerformanceArtifact,
    T63PlatformExecution,
    T63ProviderEvidence,
    T63ReportCompletionEvidence,
    build_t63_scenario_matrix,
    evaluate_t63_performance,
    summarize_t63_samples,
)
from scripts.build_t63_performance_acceptance import (
    DEFAULT_OUTPUT,
    build_acceptance,
    validate_acceptance,
)
from scripts.run_t63_performance_acceptance import main as run_t63
from scripts.run_t63_performance_gate import (
    _pytest_exit_code,
    _validate_run_artifacts,
    main as run_t63_gate,
)


OPERATIONS = (
    "prep_plan_generation",
    "plan_revision_write",
    "plan_revision_read",
    "session_start",
    "report_job_repository_commit",
    "artifact_list",
    "artifact_get",
    "artifact_pdf",
    "active_report_get",
)


def _samples():
    question_counts = (3, 5, 8, 10, 3, 5, 8, 10, 10)
    followups = (0, 1, 2, 0, 1, 2, 0, 1, 2)
    scores = (
        "scored",
        "partial",
        "unscored",
        "scored",
        "partial",
        "unscored",
        "scored",
        "partial",
        "unscored",
    )
    histories = (1, 5, 20, 1, 5, 20, 1, 5, 20)
    result = []
    for index, operation in enumerate(OPERATIONS):
        extra = {}
        if operation == "active_report_get":
            extra = {"database_query_count": 3, "database_rows_materialized": 3}
        result.append(
            T63OperationSample(
                sample_id=f"sample-{index}",
                operation=operation,
                duration_seconds=0.01,
                platform="windows-11-x64",
                cold_or_warm="cold" if index % 2 == 0 else "warm",
                question_count=question_counts[index],
                followup_count=followups[index],
                score_status=scores[index],
                history_count=histories[index],
                measurement_source=(
                    "deterministic_fixture"
                    if operation == "prep_plan_generation"
                    else "local_pdf"
                    if operation == "artifact_pdf"
                    else "postgres_local"
                ),
                provider_calls=0,
                **extra,
            )
        )
    return result


def _artifact(**updates):
    matrix = build_t63_scenario_matrix()
    payload = {
        "run_id": "t63-test",
        "source_revision": "a" * 40,
        "gate_config_sha256": "b" * 64,
        "authorization_sha256": "c" * 64,
        "samples": _samples(),
        "platform_execution": [
            T63PlatformExecution(platform="windows-11-x64", status="MEASURED"),
            T63PlatformExecution(
                platform="ubuntu-24.04-x64",
                status="NOT_RUN",
                reason="environment_not_available",
            ),
        ],
        "followup_gate": {"engineering_status": "PASS"},
        "postgres_capacity": {"status": "ELIGIBLE_FOR_CAPACITY_CANARY"},
        "report_completion_evidence": T63ReportCompletionEvidence(
            status="INSUFFICIENT_BASELINE",
            source_kind="not_run",
            comparable_cohort=False,
            sample_count=0,
            baseline_sample_count=0,
        ),
        "active_get_database_contract": T63ActiveGetDatabaseContract(
            active_get_query_count=3,
            active_get_rows_materialized=3,
            latest_job_limit=1,
            latest_job_plan_uses_index=True,
            artifact_history_plan_uses_index=True,
            n_plus_one_detected=False,
        ),
        "provider_evidence": T63ProviderEvidence(
            authorization_id="authorization-1",
            provider="DeepSeek",
            authorized_model="deepseek-chat",
            status="BLOCKED_MODEL_VERSION_DRIFT",
            provider_called=False,
            first_data_request_sent=False,
            actual_usage_artifact_available=False,
            provider_calls=0,
        ),
        "planned_scenario_count": len(matrix),
        "planned_scenario_sha256": hashlib.sha256(
            json.dumps(matrix, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    payload.update(updates)
    return T63PerformanceArtifact.model_validate(payload)


def _write_valid_run_artifacts(tmp_path: Path, root: Path):
    matrix = build_t63_scenario_matrix()
    base_samples = _samples()
    samples = [
        base_samples[index % len(base_samples)].model_copy(
            update={"sample_id": f"sample-{index:04d}"}
        )
        for index in range(318)
    ]
    revision = "a" * 40
    gate_sha = hashlib.sha256(
        (root / "config" / "interview_quality_v1_gate.json").read_bytes()
    ).hexdigest()
    authorization_sha = hashlib.sha256(
        (
            root / "config" / "interview_quality_v1_provider_authorization.json"
        ).read_bytes()
    ).hexdigest()
    capacity = {"status": "ELIGIBLE_FOR_CAPACITY_CANARY"}
    artifact = _artifact(
        source_revision=revision,
        gate_config_sha256=gate_sha,
        authorization_sha256=authorization_sha,
        samples=samples,
        postgres_capacity=capacity,
    )
    metrics = evaluate_t63_performance(artifact)
    acceptance = build_acceptance()
    scenario_sha = hashlib.sha256(
        json.dumps(matrix, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "interview-quality-v1-t63-run-manifest-v1",
        "task": "T63",
        "status": "BLOCKED",
        "engineering_status": "PASS",
        "quality_status": "BLOCKED",
        "run_id": "t63-test",
        "source_revision": revision,
        "gate_config_sha256": gate_sha,
        "authorization_sha256": authorization_sha,
        "provider_called": False,
        "first_data_request_sent": False,
        "provider_calls": 0,
        "automatic_model_substitution_used": False,
        "sample_count": 318,
        "planned_scenario_count": 432,
        "planned_scenario_sha256": scenario_sha,
        "postgresql_required": True,
        "platform_measured": "windows-11-x64",
        "ubuntu_status": "NOT_RUN",
    }
    payloads = {
        "manifest.json": manifest,
        "performance-artifact.json": artifact.model_dump(mode="json"),
        "metrics.json": metrics,
        "postgres-capacity.json": capacity,
        "scenario-matrix.json": matrix,
    }
    for name, payload in payloads.items():
        (tmp_path / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return acceptance, revision


def test_t63_scenario_matrix_covers_every_required_dimension_cross_product():
    matrix = build_t63_scenario_matrix()

    assert len(matrix) == len({row["scenario_id"] for row in matrix}) == 432
    assert {row["question_count"] for row in matrix} == {3, 5, 8, 10}
    assert {row["followup_count"] for row in matrix} == {0, 1, 2}
    assert {row["score_status"] for row in matrix} == {
        "scored",
        "partial",
        "unscored",
    }
    assert {row["history_count"] for row in matrix} == {1, 5, 20}
    assert {row["cold_or_warm"] for row in matrix} == {"cold", "warm"}
    assert {row["platform"] for row in matrix} == {
        "windows-11-x64",
        "ubuntu-24.04-x64",
    }


def test_t63_engineering_pass_preserves_real_quality_blockers():
    result = evaluate_t63_performance(_artifact())

    assert result["engineering_status"] == "PASS"
    assert result["quality_status"] == "BLOCKED"
    assert result["overall_status"] == "BLOCKED"
    assert result["engineering_failures"] == []
    assert result["quality_blockers"] == [
        "ACTUAL_PROVIDER_USAGE_ARTIFACT_MISSING",
        "BLOCKED_MODEL_VERSION_DRIFT",
        "INSUFFICIENT_BASELINE",
        "UBUNTU_MEASUREMENT_NOT_RUN",
    ]


def test_t63_session_start_cannot_report_a_provider_call():
    with pytest.raises(ValidationError, match="session_start must use zero"):
        T63OperationSample(
            sample_id="bad-session-start",
            operation="session_start",
            duration_seconds=0.01,
            platform="windows-11-x64",
            cold_or_warm="warm",
            question_count=3,
            followup_count=0,
            score_status="scored",
            history_count=1,
            measurement_source="postgres_local",
            provider_calls=1,
        )


def test_t63_blocked_model_drift_cannot_fabricate_usage():
    with pytest.raises(ValidationError, match="cannot fabricate usage"):
        T63ProviderEvidence(
            authorization_id="authorization-1",
            provider="DeepSeek",
            authorized_model="deepseek-chat",
            status="BLOCKED_MODEL_VERSION_DRIFT",
            provider_called=False,
            first_data_request_sent=False,
            actual_usage_artifact_available=False,
            provider_calls=0,
            input_tokens=0,
        )


def test_t63_provider_pass_requires_bound_metered_usage_artifact():
    with pytest.raises(ValidationError, match="bound actual usage artifact"):
        T63ProviderEvidence(
            authorization_id="authorization-1",
            provider="DeepSeek",
            authorized_model="deepseek-chat",
            status="PASS",
            provider_called=True,
            first_data_request_sent=True,
            actual_usage_artifact_available=True,
            provider_calls=1,
            input_tokens=100,
            output_tokens=20,
            estimated_cost=0.001,
        )


def test_t63_report_pass_requires_comparable_thirty_sample_gate():
    with pytest.raises(ValidationError, match="at least 30"):
        T63ReportCompletionEvidence(
            status="PASS",
            source_kind="saved_provider_replay",
            comparable_cohort=True,
            sample_count=29,
            baseline_sample_count=30,
            p95_seconds=1.0,
            baseline_p95_seconds=1.0,
        )


def test_t63_database_or_capacity_drift_is_engineering_fail_not_quality_blocker():
    artifact = _artifact(
        postgres_capacity={"status": "FAILED_LOAD"},
        active_get_database_contract=T63ActiveGetDatabaseContract(
            active_get_query_count=3,
            active_get_rows_materialized=3,
            latest_job_limit=1,
            latest_job_plan_uses_index=False,
            artifact_history_plan_uses_index=True,
            n_plus_one_detected=False,
        ),
    )

    result = evaluate_t63_performance(artifact)

    assert result["engineering_status"] == "FAIL"
    assert result["overall_status"] == "FAIL"
    assert result["engineering_failures"] == [
        "ACTIVE_GET_DATABASE_CONTRACT_FAILED",
        "POSTGRES_CAPACITY_NOT_ELIGIBLE",
    ]


def test_t63_operation_summaries_use_nearest_rank_and_safe_case_id():
    summaries = summarize_t63_samples(_samples())

    assert len(summaries) == len(OPERATIONS)
    assert all(item["sample_count"] == 1 for item in summaries)
    assert all(item["p50_seconds"] == item["p95_seconds"] == 0.01 for item in summaries)


def test_t63_runner_fails_closed_without_postgres(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    assert run_t63(["--out", str(tmp_path), "--run-id", "no-postgres"]) == 3
    manifest = json.loads(
        (tmp_path / "no-postgres" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema_version": "interview-quality-v1-t63-run-manifest-v1",
        "status": "BLOCKED_POSTGRES_UNAVAILABLE",
        "provider_called": False,
        "first_data_request_sent": False,
        "provider_calls": 0,
    }


def test_t63_checked_in_acceptance_matches_deterministic_builder():
    root = Path(__file__).resolve().parents[1]
    checked_in = json.loads((root / DEFAULT_OUTPUT).read_text(encoding="utf-8"))

    assert checked_in == build_acceptance()
    validate_acceptance(checked_in, root=root)
    assert checked_in["requirement_count"] == 22
    assert checked_in["unique_test_node_count"] == 20
    assert checked_in["planned_scenario_count"] == 432


def test_t63_official_gate_fails_closed_without_postgres(monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    assert run_t63_gate([]) == 3
    result = json.loads(capsys.readouterr().out.strip())
    assert result["preflight_status"] == "BLOCKED_POSTGRES_UNAVAILABLE"
    assert result["provider_calls"] == 0


def test_t63_official_gate_rejects_missing_run_artifacts(tmp_path):
    root = Path(__file__).resolve().parents[1]

    with pytest.raises(ValueError, match="run artifacts are missing"):
        _validate_run_artifacts(
            tmp_path,
            acceptance=build_acceptance(),
            root=root,
            expected_revision="a" * 40,
        )


def test_t63_official_gate_rejects_unexpected_quality_blocker(tmp_path):
    root = Path(__file__).resolve().parents[1]
    acceptance, revision = _write_valid_run_artifacts(tmp_path, root)
    metrics_path = tmp_path / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["quality_blockers"].append("UNEXPECTED_BLOCKER")
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    with pytest.raises(ValueError, match="fresh artifact evaluation"):
        _validate_run_artifacts(
            tmp_path,
            acceptance=acceptance,
            root=root,
            expected_revision=revision,
        )


def test_t63_official_gate_rejects_any_skip():
    assert _pytest_exit_code(0, skipped=1) == 4
    assert _pytest_exit_code(0, skipped=0) == 0
    assert _pytest_exit_code(1, skipped=1) == 1


def test_t63_official_gate_binds_artifacts_to_requested_run_id(tmp_path):
    root = Path(__file__).resolve().parents[1]
    acceptance, revision = _write_valid_run_artifacts(tmp_path, root)

    with pytest.raises(ValueError, match="requested run-id"):
        _validate_run_artifacts(
            tmp_path,
            acceptance=acceptance,
            root=root,
            expected_revision=revision,
            expected_run_id="another-run",
        )
