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
from scripts import run_t63_performance_acceptance as t63_runner
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
            authorization_id="interview-quality-v1-20260807-unlimited-02",
            provider="DeepSeek",
            authorized_model="deepseek-v4-pro",
            status="NOT_RUN_PROVIDER_QUALITY",
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
        "INSUFFICIENT_BASELINE",
        "NOT_RUN_PROVIDER_QUALITY",
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


def test_t63_new_authorized_model_not_run_cannot_fabricate_usage():
    with pytest.raises(ValidationError, match="cannot fabricate usage"):
        T63ProviderEvidence(
            authorization_id="interview-quality-v1-20260807-unlimited-02",
            provider="DeepSeek",
            authorized_model="deepseek-v4-pro",
            status="NOT_RUN_PROVIDER_QUALITY",
            provider_called=False,
            first_data_request_sent=False,
            actual_usage_artifact_available=False,
            provider_calls=0,
            estimated_cost=0,
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
    root = Path(__file__).resolve().parents[2]
    checked_in = json.loads((root / DEFAULT_OUTPUT).read_text(encoding="utf-8"))

    assert checked_in == build_acceptance()
    validate_acceptance(checked_in, root=root)
    assert checked_in["requirement_count"] == 22
    assert checked_in["unique_test_node_count"] == 21
    assert checked_in["planned_scenario_count"] == 432


def test_t63_acceptance_rejects_identity_drift():
    root = Path(__file__).resolve().parents[2]
    drifted = build_acceptance()
    drifted["acceptance_id"] = "t63-performance-acceptance-v1"

    with pytest.raises(ValueError, match="identity drifted"):
        validate_acceptance(drifted, root=root)


def test_t63_local_runner_artifacts_satisfy_formal_validator_without_provider(
    tmp_path,
    monkeypatch,
):
    root = Path(__file__).resolve().parents[2]
    run_id = "t63-runner-validator-contract"
    revision = "a" * 40
    base_samples = _samples()
    samples = [
        base_samples[index % len(base_samples)].model_copy(
            update={"sample_id": f"runner-sample-{index:04d}"}
        )
        for index in range(318)
    ]
    database_contract = _artifact().active_get_database_contract
    observed = {
        "preflight": [],
        "migrate": [],
        "measure": [],
        "capacity": [],
        "cleanup": [],
    }

    def fake_preflight(dsn):
        observed["preflight"].append(dsn)

    def fake_migrate(**kwargs):
        observed["migrate"].append(kwargs)

    def fake_measure(dsn, prefix):
        observed["measure"].append((dsn, prefix))
        return samples, database_contract

    def fake_capacity_main(argv):
        runtime_prefix = t63_runner.os.environ["INTERVIEW_RUNTIME_TABLE_PREFIX"]
        vector_prefix = t63_runner.os.environ["PGVECTOR_TABLE"]
        observed["capacity"].append(
            {
                "argv": tuple(argv),
                "runtime_prefix": runtime_prefix,
                "vector_prefix": vector_prefix,
            }
        )
        output = Path(argv[argv.index("--output") + 1])
        assert t63_runner.SAFE_PREFIX.fullmatch(runtime_prefix)
        assert t63_runner.SAFE_PREFIX.fullmatch(vector_prefix)
        output.write_text(
            json.dumps({"status": "ELIGIBLE_FOR_CAPACITY_CANARY"}),
            encoding="utf-8",
        )
        return 0

    def fake_cleanup(dsn, prefix, vector_prefix):
        observed["cleanup"].append((dsn, prefix, vector_prefix))

    monkeypatch.setenv("POSTGRES_DSN", "postgresql://synthetic-t63-contract")
    monkeypatch.setenv(
        "INTERVIEW_RUNTIME_TABLE_PREFIX",
        "preexisting-runtime-sentinel",
    )
    monkeypatch.setenv("PGVECTOR_TABLE", "preexisting-vector-sentinel")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(t63_runner, "_preflight_postgres", fake_preflight)
    monkeypatch.setattr(t63_runner.platform, "system", lambda: "Windows")
    monkeypatch.setattr(t63_runner, "migrate_postgres_runtime", fake_migrate)
    monkeypatch.setattr(t63_runner, "_measure_local_operations", fake_measure)
    monkeypatch.setattr(t63_runner, "capacity_main", fake_capacity_main)
    monkeypatch.setattr(t63_runner, "_drop_isolated_relations", fake_cleanup)
    monkeypatch.setattr(t63_runner, "_git_revision", lambda: revision)

    assert t63_runner.main(["--out", str(tmp_path), "--run-id", run_id]) == 0

    run_dir = tmp_path / run_id
    artifact_files = _validate_run_artifacts(
        run_dir,
        acceptance=build_acceptance(),
        root=root,
        expected_revision=revision,
        expected_run_id=run_id,
    )
    assert set(artifact_files) == {
        "manifest.json",
        "metrics.json",
        "performance-artifact.json",
        "postgres-capacity.json",
        "scenario-matrix.json",
    }
    assert all(item["bytes"] > 0 for item in artifact_files.values())
    assert all(len(item["sha256"]) == 64 for item in artifact_files.values())

    artifact = json.loads(
        (run_dir / "performance-artifact.json").read_text(encoding="utf-8")
    )
    provider = artifact["provider_evidence"]
    assert provider["authorization_id"] == (
        "interview-quality-v1-20260807-unlimited-02"
    )
    assert provider["authorized_model"] == "deepseek-v4-pro"
    assert provider["status"] == "NOT_RUN_PROVIDER_QUALITY"
    assert provider["provider_called"] is False
    assert provider["first_data_request_sent"] is False
    assert provider["actual_usage_artifact_available"] is False
    assert provider["provider_calls"] == 0
    assert provider["input_tokens"] is None
    assert provider["output_tokens"] is None
    assert provider["estimated_cost"] is None
    assert provider["session_count"] == 0
    assert provider["usage_artifact_path"] is None
    assert provider["usage_artifact_sha256"] is None
    assert provider["automatic_model_substitution_used"] is False

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["engineering_status"] == "PASS"
    assert metrics["quality_status"] == "BLOCKED"
    assert metrics["overall_status"] == "BLOCKED"
    assert metrics["engineering_failures"] == []
    assert metrics["quality_blockers"] == build_acceptance()[
        "required_quality_blockers"
    ]
    assert metrics["provider_calls"] == 0

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_revision"] == revision
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["provider_calls"] == 0
    assert manifest["automatic_model_substitution_used"] is False

    assert len(observed["preflight"]) == 1
    assert len(observed["migrate"]) == 1
    assert len(observed["measure"]) == 1
    assert len(observed["capacity"]) == 1
    assert len(observed["cleanup"]) == 1
    migration = observed["migrate"][0]
    measure_dsn, measure_prefix = observed["measure"][0]
    capacity = observed["capacity"][0]
    cleanup_dsn, cleanup_prefix, cleanup_vector_prefix = observed["cleanup"][0]
    assert migration["dsn"] == "postgresql://synthetic-t63-contract"
    assert measure_dsn == migration["dsn"] == cleanup_dsn
    assert (
        migration["table_prefix"]
        == measure_prefix
        == capacity["runtime_prefix"]
        == cleanup_prefix
    )
    assert (
        migration["pgvector_table"]
        == capacity["vector_prefix"]
        == cleanup_vector_prefix
    )
    assert cleanup_prefix != cleanup_vector_prefix
    assert t63_runner.SAFE_PREFIX.fullmatch(cleanup_prefix)
    assert t63_runner.SAFE_PREFIX.fullmatch(cleanup_vector_prefix)
    assert migration["run_checkpointer_setup"] is False
    assert migration["embedding_provider"].provider_name == "disabled"
    assert migration["embedding_provider"].model_name == "disabled"
    assert migration["embedding_provider"].dimension == 3
    assert (
        t63_runner.os.environ["INTERVIEW_RUNTIME_TABLE_PREFIX"]
        == "preexisting-runtime-sentinel"
    )
    assert t63_runner.os.environ["PGVECTOR_TABLE"] == "preexisting-vector-sentinel"


def test_t63_official_gate_fails_closed_without_postgres(monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    assert run_t63_gate([]) == 3
    result = json.loads(capsys.readouterr().out.strip())
    assert result["preflight_status"] == "BLOCKED_POSTGRES_UNAVAILABLE"
    assert result["provider_calls"] == 0


def test_t63_official_gate_rejects_missing_run_artifacts(tmp_path):
    root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValueError, match="run artifacts are missing"):
        _validate_run_artifacts(
            tmp_path,
            acceptance=build_acceptance(),
            root=root,
            expected_revision="a" * 40,
        )


def test_t63_official_gate_rejects_unexpected_quality_blocker(tmp_path):
    root = Path(__file__).resolve().parents[2]
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
    root = Path(__file__).resolve().parents[2]
    acceptance, revision = _write_valid_run_artifacts(tmp_path, root)

    with pytest.raises(ValueError, match="requested run-id"):
        _validate_run_artifacts(
            tmp_path,
            acceptance=acceptance,
            root=root,
            expected_revision=revision,
            expected_run_id="another-run",
        )
