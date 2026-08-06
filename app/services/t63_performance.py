from __future__ import annotations

from itertools import product
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.followup_performance import nearest_rank


T63_OPERATIONS = (
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
T63_QUESTION_COUNTS = (3, 5, 8, 10)
T63_FOLLOWUP_COUNTS = (0, 1, 2)
T63_SCORE_STATUSES = ("scored", "partial", "unscored")
T63_HISTORY_COUNTS = (1, 5, 20)
T63_STARTUP_CLASSES = ("cold", "warm")
T63_PLATFORMS = ("windows-11-x64", "ubuntu-24.04-x64")


class T63OperationSample(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    sample_id: str = Field(pattern=r"^[a-z0-9_.:-]+$")
    operation: Literal[
        "prep_plan_generation",
        "plan_revision_write",
        "plan_revision_read",
        "session_start",
        "report_job_repository_commit",
        "artifact_list",
        "artifact_get",
        "artifact_pdf",
        "active_report_get",
    ]
    duration_seconds: float = Field(ge=0)
    platform: Literal["windows-11-x64", "ubuntu-24.04-x64"]
    cold_or_warm: Literal["cold", "warm"]
    question_count: Literal[3, 5, 8, 10]
    followup_count: Literal[0, 1, 2]
    score_status: Literal["scored", "partial", "unscored"]
    history_count: Literal[1, 5, 20]
    measurement_source: Literal[
        "deterministic_fixture",
        "postgres_local",
        "local_pdf",
    ]
    provider_calls: int = Field(default=0, ge=0)
    database_query_count: int | None = Field(default=None, ge=0)
    database_rows_materialized: int | None = Field(default=None, ge=0)
    output_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_operation_contract(self):
        if self.operation == "session_start" and self.provider_calls != 0:
            raise ValueError("session_start must use zero Provider calls")
        if self.operation == "active_report_get":
            if self.database_query_count is None:
                raise ValueError("active_report_get requires database query count")
            if self.database_rows_materialized is None:
                raise ValueError("active_report_get requires materialized row count")
        return self


class T63PlatformExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["windows-11-x64", "ubuntu-24.04-x64"]
    status: Literal["MEASURED", "NOT_RUN"]
    reason: str | None = None

    @model_validator(mode="after")
    def require_reason_for_not_run(self):
        if self.status == "NOT_RUN" and not self.reason:
            raise ValueError("NOT_RUN platform requires a reason")
        if self.status == "MEASURED" and self.reason is not None:
            raise ValueError("MEASURED platform cannot carry a not-run reason")
        return self


class T63ProviderEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_id: str
    provider: Literal["DeepSeek"]
    authorized_model: Literal["deepseek-chat"]
    status: Literal["PASS", "BLOCKED_MODEL_VERSION_DRIFT"]
    provider_called: bool
    first_data_request_sent: bool
    actual_usage_artifact_available: bool
    provider_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    session_count: int = Field(default=0, ge=0)
    usage_artifact_path: str | None = None
    usage_artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    automatic_model_substitution_used: bool = False

    @model_validator(mode="after")
    def validate_provider_boundary(self):
        if self.status == "BLOCKED_MODEL_VERSION_DRIFT":
            if self.provider_called or self.first_data_request_sent or self.provider_calls:
                raise ValueError("model drift must stop before the first Provider request")
            if self.actual_usage_artifact_available:
                raise ValueError("blocked Provider preflight cannot claim actual usage")
            if any(
                value is not None
                for value in (self.input_tokens, self.output_tokens, self.estimated_cost)
            ):
                raise ValueError("blocked Provider preflight cannot fabricate usage")
            if self.session_count or self.usage_artifact_path or self.usage_artifact_sha256:
                raise ValueError("blocked Provider preflight cannot claim a usage artifact")
        if self.status == "PASS":
            if not (
                self.provider_called
                and self.first_data_request_sent
                and self.actual_usage_artifact_available
                and self.provider_calls > 0
                and self.session_count > 0
                and self.usage_artifact_path
                and self.usage_artifact_sha256
            ):
                raise ValueError("Provider PASS requires a bound actual usage artifact")
            if any(
                value is None
                for value in (self.input_tokens, self.output_tokens, self.estimated_cost)
            ):
                raise ValueError("Provider PASS requires token and cost metering")
        if self.automatic_model_substitution_used:
            raise ValueError("automatic model substitution is prohibited")
        return self


class T63ActiveGetDatabaseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_get_query_count: Literal[3]
    active_get_rows_materialized: Literal[3]
    latest_job_limit: Literal[1]
    latest_job_plan_uses_index: bool
    artifact_history_plan_uses_index: bool
    n_plus_one_detected: bool


class T63ReportCompletionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: Literal["PASS", "FAIL", "INSUFFICIENT_BASELINE"]
    source_kind: Literal["live_provider", "saved_provider_replay", "not_run"]
    comparable_cohort: bool
    sample_count: int = Field(ge=0)
    baseline_sample_count: int = Field(ge=0)
    p95_seconds: float | None = Field(default=None, ge=0)
    baseline_p95_seconds: float | None = Field(default=None, ge=0)
    absolute_threshold_seconds: float = Field(default=120.0, gt=0)
    baseline_multiplier: Literal[1.2] = 1.2

    @model_validator(mode="after")
    def validate_report_gate(self):
        if self.status == "PASS":
            if self.source_kind == "not_run" or not self.comparable_cohort:
                raise ValueError("report PASS requires a comparable Provider cohort")
            if self.sample_count < 30 or self.baseline_sample_count < 30:
                raise ValueError("report PASS requires at least 30 samples per cohort")
            if self.p95_seconds is None or self.baseline_p95_seconds is None:
                raise ValueError("report PASS requires measured and baseline p95")
            threshold = min(
                self.absolute_threshold_seconds,
                self.baseline_p95_seconds * self.baseline_multiplier,
            )
            if self.p95_seconds > threshold:
                raise ValueError("report p95 exceeds its comparable frozen threshold")
        elif self.status == "INSUFFICIENT_BASELINE":
            if self.comparable_cohort:
                raise ValueError("insufficient baseline cannot claim a comparable cohort")
            if self.source_kind == "not_run" and any(
                value is not None
                for value in (self.p95_seconds, self.baseline_p95_seconds)
            ):
                raise ValueError("not-run report evidence cannot fabricate latency")
        return self


class T63PerformanceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interview-quality-v1-t63-performance-v1"] = (
        "interview-quality-v1-t63-performance-v1"
    )
    run_id: str = Field(pattern=r"^[a-z0-9_.:-]+$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    gate_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples: list[T63OperationSample] = Field(min_length=1)
    platform_execution: list[T63PlatformExecution] = Field(min_length=2)
    followup_gate: dict[str, Any]
    postgres_capacity: dict[str, Any]
    report_completion_evidence: T63ReportCompletionEvidence
    active_get_database_contract: T63ActiveGetDatabaseContract
    provider_evidence: T63ProviderEvidence
    planned_scenario_count: int = Field(ge=1)
    planned_scenario_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    privacy_violations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_artifact_contract(self):
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("T63 sample IDs must be unique")
        platforms = [item.platform for item in self.platform_execution]
        if sorted(platforms) != sorted(T63_PLATFORMS):
            raise ValueError("T63 platform execution must cover Windows and Ubuntu once")
        if len(platforms) != len(set(platforms)):
            raise ValueError("T63 platform execution contains duplicates")
        if self.planned_scenario_count != 432:
            raise ValueError("T63 planned scenario matrix must contain 432 combinations")
        return self


def build_t63_scenario_matrix() -> list[dict[str, Any]]:
    rows = []
    for values in product(
        T63_QUESTION_COUNTS,
        T63_FOLLOWUP_COUNTS,
        T63_SCORE_STATUSES,
        T63_HISTORY_COUNTS,
        T63_STARTUP_CLASSES,
        T63_PLATFORMS,
    ):
        question_count, followups, score_status, history, startup, platform = values
        rows.append(
            {
                "scenario_id": (
                    f"q{question_count}-f{followups}-{score_status}-h{history}-"
                    f"{startup}-{platform}"
                ),
                "question_count": question_count,
                "followup_count": followups,
                "score_status": score_status,
                "history_count": history,
                "cold_or_warm": startup,
                "platform": platform,
            }
        )
    return rows


def summarize_t63_samples(samples: list[T63OperationSample]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for operation in T63_OPERATIONS:
        group = [sample for sample in samples if sample.operation == operation]
        if not group:
            continue
        durations = [sample.duration_seconds for sample in group]
        maximum = max(group, key=lambda item: item.duration_seconds)
        summaries.append(
            {
                "operation": operation,
                "sample_count": len(group),
                "p50_seconds": nearest_rank(durations, 0.50),
                "p95_seconds": nearest_rank(durations, 0.95),
                "max_seconds": maximum.duration_seconds,
                "max_sample_id": maximum.sample_id,
            }
        )
    return summaries


def evaluate_t63_performance(artifact: T63PerformanceArtifact) -> dict[str, Any]:
    failures: list[str] = []
    blockers: list[str] = []
    samples = artifact.samples

    measured_windows = [
        item
        for item in artifact.platform_execution
        if item.platform == "windows-11-x64" and item.status == "MEASURED"
    ]
    if len(measured_windows) != 1:
        failures.append("WINDOWS_MEASUREMENT_MISSING")
    ubuntu = next(
        item
        for item in artifact.platform_execution
        if item.platform == "ubuntu-24.04-x64"
    )
    if ubuntu.status != "MEASURED":
        blockers.append("UBUNTU_MEASUREMENT_NOT_RUN")
    for execution in artifact.platform_execution:
        if execution.status == "MEASURED" and not any(
            sample.platform == execution.platform for sample in samples
        ):
            failures.append(f"{execution.platform.upper().replace('-', '_')}_SAMPLES_MISSING")

    for operation in T63_OPERATIONS:
        if not any(sample.operation == operation for sample in samples):
            failures.append(f"MISSING_OPERATION_{operation.upper()}")
    for dimension, expected in (
        ("question_count", set(T63_QUESTION_COUNTS)),
        ("followup_count", set(T63_FOLLOWUP_COUNTS)),
        ("score_status", set(T63_SCORE_STATUSES)),
        ("history_count", set(T63_HISTORY_COUNTS)),
        ("cold_or_warm", set(T63_STARTUP_CLASSES)),
    ):
        observed = {getattr(sample, dimension) for sample in samples}
        if observed != expected:
            failures.append(f"INCOMPLETE_DIMENSION_{dimension.upper()}")

    if any(sample.provider_calls for sample in samples if sample.operation == "session_start"):
        failures.append("SESSION_START_PROVIDER_CALL")

    active = [sample for sample in samples if sample.operation == "active_report_get"]
    if any(sample.database_query_count != 3 for sample in active):
        failures.append("ACTIVE_GET_QUERY_COUNT_DRIFT")
    if any(sample.database_rows_materialized != 3 for sample in active):
        failures.append("ACTIVE_GET_MATERIALIZED_ROWS_DRIFT")
    active_twenty = [sample.duration_seconds for sample in active if sample.history_count == 20]
    if not active_twenty or nearest_rank(active_twenty, 0.95) > 0.5:
        failures.append("ACTIVE_GET_HISTORY_P95_UNACCEPTABLE")

    if artifact.followup_gate.get("engineering_status") != "PASS":
        failures.append("FOLLOWUP_ENGINEERING_GATE_NOT_PASS")
    if artifact.postgres_capacity.get("status") != "ELIGIBLE_FOR_CAPACITY_CANARY":
        failures.append("POSTGRES_CAPACITY_NOT_ELIGIBLE")
    database_contract = artifact.active_get_database_contract
    if (
        not database_contract.latest_job_plan_uses_index
        or not database_contract.artifact_history_plan_uses_index
        or database_contract.n_plus_one_detected
    ):
        failures.append("ACTIVE_GET_DATABASE_CONTRACT_FAILED")
    if artifact.privacy_violations:
        failures.append("PERFORMANCE_ARTIFACT_PRIVACY_VIOLATION")

    if artifact.report_completion_evidence.status != "PASS":
        blockers.append(artifact.report_completion_evidence.status)
    if not artifact.provider_evidence.actual_usage_artifact_available:
        blockers.append("ACTUAL_PROVIDER_USAGE_ARTIFACT_MISSING")
    if artifact.provider_evidence.status != "PASS":
        blockers.append(artifact.provider_evidence.status)

    failures = sorted(set(failures))
    blockers = sorted(set(blockers))
    engineering_status = "PASS" if not failures else "FAIL"
    quality_status = "PASS" if not failures and not blockers else "BLOCKED"
    overall_status = (
        "FAIL" if failures else "PASS" if quality_status == "PASS" else "BLOCKED"
    )
    return {
        "schema_version": "interview-quality-v1-t63-evaluation-v1",
        "engineering_status": engineering_status,
        "quality_status": quality_status,
        "overall_status": overall_status,
        "engineering_failures": failures,
        "quality_blockers": blockers,
        "operation_summaries": summarize_t63_samples(samples),
        "sample_count": len(samples),
        "planned_scenario_count": artifact.planned_scenario_count,
        "provider_calls": artifact.provider_evidence.provider_calls,
        "actual_provider_usage_artifact_available": (
            artifact.provider_evidence.actual_usage_artifact_available
        ),
    }
