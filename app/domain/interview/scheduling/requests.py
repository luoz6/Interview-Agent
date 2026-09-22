from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentRequest(BaseModel):
    """Base for typed Agent requests at the neutral core boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    contract_id: ClassVar[str]
    contract_version: ClassVar[str] = "v1"

    @classmethod
    def contract_key(cls) -> str:
        return f"{cls.contract_id}:{cls.contract_version}"


class GenerateInterviewPlanRequest(AgentRequest):
    contract_id: ClassVar[str] = "generate-interview-plan-request"

    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    prep_run_id: str | None = Field(default=None, min_length=1)
    configuration: dict[str, Any] | None = None
    knowledge_source_scope: dict[str, Any] | None = None


class GenerateMainQuestionRequest(AgentRequest):
    contract_id: ClassVar[str] = "generate-main-question-request"

    intent: dict[str, Any]
    conversation: tuple[dict[str, str], ...] = ()
    evidence: tuple[dict[str, str], ...] = ()
    timeout_seconds: float | None = Field(default=None, gt=0)


class GenerateFollowupRequest(AgentRequest):
    contract_id: ClassVar[str] = "generate-followup-request"

    question_id: str = Field(min_length=1)
    context: tuple[dict[str, str], ...] = ()
    focus: str = ""
    gap_id: str | None = Field(default=None, min_length=1)
    reason_code: str = Field(default="gap", min_length=1)
    policy_version: str = Field(default="adaptive_v1", min_length=1)
    evidence_ids: tuple[str, ...] = ()


class EvaluateAnswerRequest(AgentRequest):
    contract_id: ClassVar[str] = "evaluate-answer-request"

    state: dict[str, Any]
    question_id: str | None = Field(default=None, min_length=1)
    answer_artifact_ref: str | None = Field(default=None, min_length=1)
    question_artifact_ref: str | None = Field(default=None, min_length=1)


class EvaluateInterviewRequest(AgentRequest):
    contract_id: ClassVar[str] = "evaluate-interview-request"

    state: dict[str, Any]


class GenerateReportRequest(AgentRequest):
    contract_id: ClassVar[str] = "generate-report-request"

    plan: dict[str, Any]
    session_id: str = Field(min_length=1)
    evaluation_items: tuple[dict[str, Any], ...] | None = None
    evaluation_artifacts: tuple[dict[str, Any], ...] | None = None
    question_text_by_id: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_evaluation_input(self) -> "GenerateReportRequest":
        if self.evaluation_items is None and self.evaluation_artifacts is None:
            raise ValueError(
                "evaluation_items or evaluation_artifacts is required"
            )
        return self


RequestType = TypeVar("RequestType", bound=AgentRequest)

REQUEST_CONTRACTS: dict[str, type[AgentRequest]] = {
    "generate-interview-plan": GenerateInterviewPlanRequest,
    "generate-main-question": GenerateMainQuestionRequest,
    "generate-followup": GenerateFollowupRequest,
    "evaluate-answer": EvaluateAnswerRequest,
    "evaluate-interview": EvaluateInterviewRequest,
    "generate-report": GenerateReportRequest,
}


def parse_agent_request(skill: str, payload: dict[str, Any]) -> AgentRequest:
    """Validate one skill payload into its declared typed request contract."""

    try:
        request_type = REQUEST_CONTRACTS[skill]
    except KeyError as exc:
        raise ValueError(f"unsupported Agent skill: {skill}") from exc
    return request_type.model_validate(payload)


__all__ = [
    "AgentRequest",
    "EvaluateAnswerRequest",
    "EvaluateInterviewRequest",
    "GenerateFollowupRequest",
    "GenerateInterviewPlanRequest",
    "GenerateMainQuestionRequest",
    "GenerateReportRequest",
    "REQUEST_CONTRACTS",
    "parse_agent_request",
]
