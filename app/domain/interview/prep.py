from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from app.domain.interview.plan_knowledge import PlanQuestionKnowledgeBinding
from app.domain.interview.question_intent import AssessmentGoal
from app.domain.knowledge.engine import (
    LegacyKnowledgeEngineAssignment,
    RuntimeEngineExecution,
    execution_from_legacy_assignment,
)
from app.domain.knowledge.evidence import BaseEvidenceBundle, QuestionEvidenceBinding
from app.domain.knowledge.profile import RoleProfile


class KnowledgeEvidenceRef(BaseModel):
    evidence_id: str
    title: str
    domain: str
    source_type: str
    score: float | None = None
    content_sha256: str
    corpus_manifest_sha256: str
    candidate_summary: str


class KnowledgeQuerySnapshot(BaseModel):
    query_id: str
    topic_id: str
    filters: dict[str, list[str] | str | int | float | bool | None] = Field(
        default_factory=dict
    )
    top_k: int = 5
    hit_ids: list[str] = Field(default_factory=list)
    hit_content_sha256: dict[str, str] = Field(default_factory=dict)
    status: Literal["completed", "empty", "degraded"] = "completed"
    degraded_reason: str | None = None
    engine_execution: RuntimeEngineExecution | None = None


class KnowledgeBindingSnapshot(BaseModel):
    prep_run_id: str
    corpus_manifest_sha256: str
    queries: list[KnowledgeQuerySnapshot] = Field(default_factory=list)
    status: Literal["completed", "empty", "degraded"]
    degraded_reason: str | None = None
    knowledge_engine_execution: RuntimeEngineExecution | None = None
    knowledge_engine_assignment: LegacyKnowledgeEngineAssignment | None = None
    base_evidence_bundle: BaseEvidenceBundle | None = None
    question_evidence_bindings: list[QuestionEvidenceBinding] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def migrate_legacy_engine_assignment(self):
        if (
            self.knowledge_engine_execution is None
            and self.knowledge_engine_assignment is not None
        ):
            self.knowledge_engine_execution = execution_from_legacy_assignment(
                self.knowledge_engine_assignment
            )
        return self


class PrepKnowledgeTopic(BaseModel):
    id: str
    label: str
    source: Literal[
        "jd_keyword",
        "resume_keyword",
        "jd_resume_keyword",
        "fallback",
        "retrieval",
        "keyword_fallback",
    ]
    evidence: str
    tags: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    candidate_summary: str = ""


class PrepQuestionHint(BaseModel):
    question_id: str
    topic_ids: list[str] = Field(default_factory=list)
    follow_up_hints: list[str] = Field(default_factory=list)
    evidence_titles: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PrepContext(BaseModel):
    summary: str
    schema_version: Literal["v1", "v2"] = "v1"
    knowledge_status: Literal["keyword", "completed", "empty", "degraded"] = (
        "keyword"
    )
    topics: list[PrepKnowledgeTopic] = Field(default_factory=list)
    question_hints: list[PrepQuestionHint] = Field(default_factory=list)
    role_profile: RoleProfile | None = None
    evidence_refs: list[KnowledgeEvidenceRef] = Field(default_factory=list)
    binding_snapshot: KnowledgeBindingSnapshot | None = None
    question_bindings: dict[str, PlanQuestionKnowledgeBinding] = Field(
        default_factory=dict
    )


class InterviewQuestion(BaseModel):
    id: str = Field(description="题目唯一标识")
    kind: Literal["project", "technical", "system-design", "behavioral"] = Field(
        description="题目类型"
    )
    prompt: str = Field(description="面试官要问的问题")
    focus: str = Field(description="本题重点考察方向")

    @field_validator("id", "prompt", "focus", mode="before")
    @classmethod
    def strip_required_text(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value.strip()


class InterviewIntentDraftQuestion(BaseModel):
    id: str = Field(pattern=r"^q[1-9][0-9]*$")
    kind: Literal["project", "technical", "system-design", "behavioral"]
    focus: str = Field(min_length=1, max_length=1000)
    difficulty: Literal["foundation", "intermediate", "advanced"]
    assessment_goals: tuple[AssessmentGoal, ...] = Field(min_length=1, max_length=4)

    @field_validator("focus", mode="before")
    @classmethod
    def normalize_focus(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("intent focus must not be blank")
        return " ".join(value.split())

    @field_validator("assessment_goals", mode="before")
    @classmethod
    def normalize_assessment_goals(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("assessment_goals must be a list")
        goals = tuple(str(item).strip() for item in value)
        if len(goals) != len(set(goals)):
            raise ValueError("assessment_goals must be unique")
        return goals


class InterviewIntentDraftPlan(BaseModel):
    title: str = Field(min_length=1)
    questions: tuple[InterviewIntentDraftQuestion, ...] = Field(
        min_length=1,
        max_length=10,
    )

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class InterviewPlan(BaseModel):
    title: str
    questions: list[InterviewQuestion]
    prep_context: PrepContext | None = None
    _revision_plan: Any = PrivateAttr(default=None)
    _generation_enforcement: dict[str, Any] = PrivateAttr(default_factory=dict)
    _intent_draft_questions: tuple[InterviewIntentDraftQuestion, ...] = PrivateAttr(
        default=()
    )

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class PlanGenerationValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def v2_plan_to_legacy(plan):
    """Project a domain plan revision into the legacy prep contract."""

    from app.domain.interview.plan_revision import (
        InterviewPlanV2,
        synchronize_plan_knowledge_context,
    )

    if not isinstance(plan, InterviewPlanV2):
        raise ValueError(
            "v3 intent plans cannot be projected to legacy final-question plans"
        )
    plan = synchronize_plan_knowledge_context(plan)
    questions = [
        InterviewQuestion(
            id=item.question_id,
            kind=item.question_type,
            prompt=item.question_text,
            focus=item.focus,
        )
        for item in plan.questions
    ]
    context_payload = deepcopy(plan.prep_context)
    if context_payload is not None:
        context_payload["question_bindings"] = {
            item.question_id: item.knowledge_binding
            for item in plan.questions
        }
    context = PrepContext.model_validate(context_payload) if context_payload else None
    return InterviewPlan(title=plan.title, questions=questions, prep_context=context)


def validate_bound_plan_revision(plan: InterviewPlan):
    """Revalidate an already-bound immutable revision and its legacy projection."""

    from app.domain.interview.plan_revision import (
        InterviewPlanV2,
        InterviewPlanV3,
        parse_interview_plan,
    )

    revision_plan = plan._revision_plan
    if revision_plan is None:
        raise PlanGenerationValidationError(
            "prepared_plan_revision_missing",
            "prepared plan does not have a bound revision",
        )
    validated = parse_interview_plan(
        revision_plan.model_dump(mode="json", warnings=False)
    )
    if isinstance(validated, InterviewPlanV3):
        return validated
    validated = InterviewPlanV2.model_validate(validated)
    current_legacy = InterviewPlan.model_validate(
        plan.model_dump(mode="json", warnings=False)
    )
    round_tripped_legacy = v2_plan_to_legacy(validated)
    if [item.id for item in current_legacy.questions] != [
        item.question_id for item in validated.questions
    ]:
        raise PlanGenerationValidationError(
            "prepared_plan_identity_mismatch",
            "prepared legacy plan identity changed after its V2 revision was bound",
        )
    if _legacy_plan_semantics(current_legacy) != _legacy_plan_semantics(
        round_tripped_legacy
    ):
        raise PlanGenerationValidationError(
            "prepared_plan_payload_mismatch",
            "prepared legacy plan changed after its V2 revision was bound",
        )
    return validated


def public_interview_plan_payload(plan: InterviewPlan) -> dict:
    payload = plan.model_dump(mode="json", exclude_none=True)
    sanitize_public_prep_context(payload.get("prep_context"))
    return payload


def public_interview_plan_v2_payload(plan) -> dict:
    payload = plan.model_dump(mode="json", exclude_none=True)
    sanitize_public_prep_context(payload.get("prep_context"))
    scope = payload.get("knowledge_scope")
    if isinstance(scope, dict):
        payload["knowledge_scope"] = {
            "schema_version": scope.get("schema_version"),
            "include_system_knowledge": scope.get(
                "include_system_knowledge", True
            ),
            "selected_documents": [
                {"document_id": item["document_id"]}
                for item in scope.get("selected_documents", [])
                if isinstance(item, dict) and "document_id" in item
            ],
        }
    for question in payload.get("questions", []):
        binding = question.get("knowledge_binding")
        if not isinstance(binding, dict):
            continue
        question["knowledge_binding"] = {
            key: binding[key]
            for key in (
                "schema_version",
                "status",
                "evidence_ids",
                "reason_code",
            )
            if key in binding
        }
    return payload


def sanitize_public_prep_context(context: object) -> None:
    if not isinstance(context, dict):
        return

    public_evidence = []
    for evidence in context.get("evidence_refs", []):
        public_evidence.append(
            {
                key: evidence[key]
                for key in (
                    "evidence_id",
                    "title",
                    "domain",
                    "source_type",
                    "candidate_summary",
                )
                if key in evidence
            }
        )
    context["evidence_refs"] = public_evidence
    context.pop("binding_snapshot", None)
    context.pop("question_bindings", None)

    role_profile = context.get("role_profile")
    if isinstance(role_profile, dict):
        role_profile.pop("resume_signals", None)


def _legacy_plan_semantics(plan: InterviewPlan) -> dict[str, Any]:
    return {
        "title": plan.title,
        "questions": [
            {
                "kind": question.kind,
                "prompt": question.prompt,
                "focus": question.focus,
            }
            for question in plan.questions
        ],
        "prep_context": (
            plan.prep_context.model_dump(mode="json")
            if plan.prep_context is not None
            else None
        ),
    }
