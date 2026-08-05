from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.interview_plan_revision import canonical_sha256


ReportJobKind = Literal["initial", "rescore"]
ReportJobStatus = Literal["queued", "running", "retrying", "completed", "failed"]
GenerationStatus = Literal["complete", "degraded"]
GenerationReasonCode = Literal[
    "normal",
    "summary_generation_failed",
    "provider_timeout",
    "invalid_provider_output",
    "runtime_quality_rejected",
    "persistence_failed",
]
ScoreStatus = Literal["scored", "partial", "unscored"]
ScoreReasonCode = Literal[
    "sufficient_evidence",
    "partial_evidence",
    "insufficient_evidence",
    "scoring_input_invalid",
    "scoring_generation_failed",
    "legacy_unknown",
]
CoverageStatus = Literal["complete", "partial", "none"]
ReportPath = Literal["microbatch", "full_session", "heuristic", "legacy"]


class ImmutableReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportJobV2(ImmutableReportModel):
    job_id: str
    session_id: str = Field(min_length=1)
    job_kind: ReportJobKind
    parent_job_id: str | None = None
    source_report_id: str | None = None
    activate_on_success: bool
    status: ReportJobStatus
    idempotency_key: str = Field(min_length=1, max_length=200)
    lease_owner: str | None = None
    lease_token: str | None = None
    fencing_version: int = Field(default=0, ge=0)
    error_code: str | None = None
    report_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_job(self):
        _uuid_text(self.job_id, "job_id")
        for field_name in ("parent_job_id", "source_report_id", "lease_token", "report_id"):
            value = getattr(self, field_name)
            if value is not None:
                _uuid_text(value, field_name)
        if self.job_kind == "initial" and self.source_report_id is not None:
            raise ValueError("initial report job cannot have source_report_id")
        if self.job_kind == "rescore" and self.source_report_id is None:
            raise ValueError("rescore report job requires source_report_id")
        return self


class ReportArtifact(ImmutableReportModel):
    report_id: str
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    schema_version: str = Field(min_length=1)
    scoring_rubric_version: str = Field(min_length=1)
    generation_status: GenerationStatus
    generation_reason_code: GenerationReasonCode
    score_status: ScoreStatus
    score_reason_code: ScoreReasonCode
    coverage_status: CoverageStatus
    report_path: ReportPath
    payload: dict[str, Any]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_report_id: str | None = None
    supersedes_report_id: str | None = None
    source_job_id: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_artifact(self):
        for field_name in (
            "report_id",
            "source_report_id",
            "supersedes_report_id",
            "source_job_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _uuid_text(value, field_name)
        if report_artifact_sha256(self.payload) != self.artifact_sha256:
            raise ValueError("artifact_sha256 does not match payload")
        if self.source_report_id == self.report_id or self.supersedes_report_id == self.report_id:
            raise ValueError("report artifact cannot reference itself")
        return self


class ReportHead(ImmutableReportModel):
    session_id: str = Field(min_length=1)
    active_report_id: str | None = None
    latest_job_id: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_head(self):
        for field_name in ("active_report_id", "latest_job_id"):
            value = getattr(self, field_name)
            if value is not None:
                _uuid_text(value, field_name)
        return self


class PublishReportArtifact(ImmutableReportModel):
    schema_version: str = Field(min_length=1)
    scoring_rubric_version: str = Field(min_length=1)
    generation_status: GenerationStatus
    generation_reason_code: GenerationReasonCode
    score_status: ScoreStatus
    score_reason_code: ScoreReasonCode
    coverage_status: CoverageStatus
    report_path: ReportPath
    payload: dict[str, Any]


def report_artifact_sha256(payload: dict[str, Any]) -> str:
    return canonical_sha256(payload)


def _uuid_text(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
