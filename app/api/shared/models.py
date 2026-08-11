from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PrepRequest(BaseModel):
    job_description: str
    resume_text: str
    draft_id: str | None = None


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
    plan_id: str | None = None
    expected_plan_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    # Temporary compatibility window for pre-V15 clients. New clients must
    # send the authoritative plan tuple above.
    job_description: str | None = None
    resume_text: str | None = None


class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None


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

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


__all__ = [
    "AnswerRequest",
    "DraftRequest",
    "PracticePlanRequest",
    "PrepPlanPatchRequest",
    "PrepQuestionRegenerateRequest",
    "PrepRequest",
    "PrincipalConsentRequest",
    "PrincipalFactActionRequest",
    "PrincipalFactCorrectionRequest",
    "PrincipalFactDeclareRequest",
    "PrincipalFactRefActionRequest",
    "SessionCommandRequest",
    "StartInterviewRequest",
]
