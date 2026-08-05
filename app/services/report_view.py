from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.report_artifact import ReportArtifact, ReportJobV2


class ReportViewError(ValueError):
    """Raised when a report payload violates the public five-axis contract."""


class EvaluationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["evaluated", "partial", "not_evaluated"]
    score: int | None = Field(default=None, ge=0, le=100)
    evidence_count: int = Field(default=0, ge=0)
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> "EvaluationView":
        if self.status == "not_evaluated" and self.score is not None:
            raise ReportViewError("not_evaluated entries cannot contain a numeric score")
        if self.status == "evaluated" and self.score is None:
            raise ReportViewError("evaluated entries require a numeric score")
        return self


class ReportViewModel(BaseModel):
    """The single public projection of an immutable artifact and job history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    session_id: str
    revision: int = Field(ge=1)
    artifact_sha256: str
    source_report_id: str | None = None
    supersedes_report_id: str | None = None
    source_job_id: str
    created_at: str | None = None
    active: bool
    schema_version: str
    generation_status: str
    generation_reason_code: str
    score_status: str
    score_reason_code: str
    coverage_status: str
    report_path: str
    overall_score: int | None = Field(default=None, ge=0, le=100)
    overall_dimension_scores: dict[str, int | None] | None = None
    evaluated_count: int | None = Field(default=None, ge=0)
    total_eligible_count: int | None = Field(default=None, ge=0)
    evidence_count: int | None = Field(default=None, ge=0)
    dimension_evaluations: dict[str, EvaluationView] = Field(default_factory=dict)
    question_evaluations: dict[str, EvaluationView] = Field(default_factory=dict)
    payload: dict[str, Any]
    latest_job: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_axes(self) -> "ReportViewModel":
        if self.score_status == "unscored":
            if self.overall_score is not None:
                raise ReportViewError("unscored reports cannot contain overall_score")
            if self.overall_dimension_scores is not None and any(
                value is not None for value in self.overall_dimension_scores.values()
            ):
                raise ReportViewError(
                    "unscored reports cannot contain numeric dimension scores"
                )
        if self.score_status == "partial":
            if self.total_eligible_count is None:
                raise ReportViewError("partial reports require total_eligible_count")
            if self.evaluated_count is None:
                raise ReportViewError("partial reports require evaluated_count")
            if self.evaluated_count > self.total_eligible_count:
                raise ReportViewError("evaluated_count cannot exceed total_eligible_count")
        if self.coverage_status == "partial" and self.total_eligible_count is None:
            raise ReportViewError("partial coverage requires a denominator")
        return self


class ReportPayloadParser:
    def __init__(self) -> None:
        self._parsers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {}

    def register(self, schema_version: str, parser: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
        if not schema_version.strip():
            raise ValueError("schema_version must not be blank")
        if schema_version in self._parsers:
            raise ValueError(f"parser already registered for {schema_version}")
        self._parsers[schema_version] = parser

    def parse(self, schema_version: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            parser = self._parsers[schema_version]
        except KeyError as exc:
            raise ReportViewError(f"unsupported report schema: {schema_version}") from exc
        parsed = parser(payload)
        return deepcopy(parsed)


def _identity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return dict(payload)


def _legacy_v1_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read legacy JSON without inventing coverage or score values."""
    result = dict(payload)
    score = result.get("overall_score")
    dimensions = result.get("overall_dimension_scores")
    if isinstance(score, int) and dimensions is not None:
        result.setdefault("score_status", "scored")
        result.setdefault("coverage_status", "complete")
    else:
        result["overall_score"] = None
        result["overall_dimension_scores"] = None
        result.setdefault("score_status", "unscored")
        result.setdefault("coverage_status", "none")
    result.setdefault("generation_status", "complete")
    result.setdefault("generation_reason_code", "normal")
    result.setdefault("score_reason_code", "legacy_unknown")
    result.setdefault("report_path", "legacy")
    return result


DEFAULT_REPORT_PAYLOAD_PARSERS = ReportPayloadParser()
DEFAULT_REPORT_PAYLOAD_PARSERS.register("report-artifact-v2", _identity_payload)
DEFAULT_REPORT_PAYLOAD_PARSERS.register("legacy-v1", _legacy_v1_payload)


def compose_report_view(
    artifact: ReportArtifact,
    *,
    latest_job: ReportJobV2 | None = None,
    active: bool = False,
    parser_registry: ReportPayloadParser = DEFAULT_REPORT_PAYLOAD_PARSERS,
) -> ReportViewModel:
    payload = parser_registry.parse(artifact.schema_version, artifact.payload)
    return ReportViewModel(
        report_id=artifact.report_id,
        session_id=artifact.session_id,
        revision=artifact.revision,
        artifact_sha256=artifact.artifact_sha256,
        source_report_id=artifact.source_report_id,
        supersedes_report_id=artifact.supersedes_report_id,
        source_job_id=artifact.source_job_id,
        created_at=artifact.created_at.isoformat() if artifact.created_at else None,
        active=active,
        schema_version=artifact.schema_version,
        generation_status=payload.get("generation_status", artifact.generation_status),
        generation_reason_code=payload.get(
            "generation_reason_code", artifact.generation_reason_code
        ),
        score_status=payload.get("score_status", artifact.score_status),
        score_reason_code=payload.get("score_reason_code", artifact.score_reason_code),
        coverage_status=payload.get("coverage_status", artifact.coverage_status),
        report_path=payload.get("report_path", artifact.report_path),
        overall_score=payload.get("overall_score"),
        overall_dimension_scores=payload.get("overall_dimension_scores"),
        evaluated_count=payload.get("evaluated_count"),
        total_eligible_count=payload.get("total_eligible_count"),
        evidence_count=payload.get("evidence_count"),
        dimension_evaluations=payload.get("dimension_evaluations", {}),
        question_evaluations=payload.get("question_evaluations", {}),
        payload=payload,
        latest_job=latest_job.model_dump(mode="json") if latest_job else None,
    )
