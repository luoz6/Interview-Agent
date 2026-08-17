from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.services.interview_plan_revision import PlanConfigurationSnapshot


class PrepKnowledgeScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    include_system_knowledge: bool = True
    selected_document_ids: tuple[str, ...] = ()

    @field_validator("selected_document_ids", mode="before")
    @classmethod
    def validate_selected_document_ids_shape(
        cls, value: object
    ) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("selected_document_ids must be a list or tuple")
        return tuple(value)


class PrepRequest(BaseModel):
    model_config = {"extra": "forbid"}

    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    configuration: PlanConfigurationSnapshot | None = None
    knowledge_scope: PrepKnowledgeScopeRequest | None = None

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PrepPlanPatchRequest(BaseModel):
    expected_version: int = Field(ge=1)
    operations: list[dict] = Field(min_length=1, max_length=20)


class PrepQuestionRegenerateRequest(BaseModel):
    expected_version: int = Field(ge=1)


class PracticePlanRequest(BaseModel):
    focus_dimension: str
    session_question_ids: list[str]
    mode: Literal["targeted"] = "targeted"

    @field_validator("session_question_ids")
    @classmethod
    def validate_session_question_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value]


class StartInterviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    plan_revision_id: str | None = Field(default=None, min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    principal_memory_mode: Literal["inherit", "ignore"] = "inherit"
    plan_id: str | None = None
    expected_plan_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    # Temporary compatibility window for pre-V15 clients. New clients must
    # send the authoritative plan tuple above.
    job_description: str | None = None
    resume_text: str | None = None

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_launch_contract(self):
        revision_values = (
            self.plan_revision_id,
            self.expected_revision,
            self.plan_sha256,
            self.request_id,
        )
        if any(value is not None for value in revision_values):
            if any(value is None for value in revision_values):
                raise ValueError("plan revision launch tuple is incomplete")
            if self.plan_id is not None:
                raise ValueError(
                    "plan_id and plan_revision_id are mutually exclusive"
                )
        elif self.principal_memory_mode != "inherit":
            raise ValueError(
                "principal_memory_mode requires a revision-bound launch"
            )
        return self


class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None


class RescoreReportRequest(BaseModel):
    activate_on_success: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class PrincipalConsentRequest(BaseModel):
    allowed_purposes: list[
        Literal[
            "proposal_write",
            "fact_storage",
            "read_shadow",
            "local_consume",
        ]
    ] = Field(min_length=1)


class PrincipalFactActionRequest(BaseModel):
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_value: dict[str, str]
    expected_version: int = Field(ge=1)


class PrincipalFactDeclareRequest(BaseModel):
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_value: dict[str, str]


class PrincipalFactRefActionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class PrincipalFactCorrectionRequest(PrincipalFactRefActionRequest):
    normalized_value: dict[str, str]


class DraftRequest(BaseModel):
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    title: str | None = None
    job_tags: list[str] | None = None
    plan_family_id: str | None = None
    latest_plan_revision_id: str | None = None
    clear_plan: bool = False

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_plan_binding(self):
        if (self.plan_family_id is None) != (
            self.latest_plan_revision_id is None
        ):
            raise ValueError(
                "plan_family_id and latest_plan_revision_id must be provided together"
            )
        if self.clear_plan and self.plan_family_id is not None:
            raise ValueError(
                "clear_plan cannot be combined with a plan revision"
            )
        return self


__all__ = [
    "AnswerRequest",
    "DraftRequest",
    "PracticePlanRequest",
    "PrepKnowledgeScopeRequest",
    "PrepPlanPatchRequest",
    "PrepQuestionRegenerateRequest",
    "PrepRequest",
    "PrincipalConsentRequest",
    "PrincipalFactActionRequest",
    "PrincipalFactCorrectionRequest",
    "PrincipalFactDeclareRequest",
    "PrincipalFactRefActionRequest",
    "RescoreReportRequest",
    "SessionCommandRequest",
    "StartInterviewRequest",
]
