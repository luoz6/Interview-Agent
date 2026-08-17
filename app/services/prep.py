from collections import Counter
from typing import Any, TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from app.services.interview_question_quality import (
    QuestionQualityInput,
    assess_interview_question_quality,
)
from app.services.interview_plan_knowledge import PlanQuestionKnowledgeBinding
from app.services.llm import InterviewLLM
from app.domain.knowledge.evidence import BaseEvidenceBundle, QuestionEvidenceBinding
from app.domain.knowledge.engine import (
    LegacyKnowledgeEngineAssignment,
    RuntimeEngineExecution,
    execution_from_legacy_assignment,
)

if TYPE_CHECKING:
    from app.domain.knowledge.source_scope import (
        InterviewKnowledgeScopeSnapshot,
        KnowledgeSourceScope,
    )
    from app.ports.runtime import KnowledgeRepository
    from app.services.interview_plan_revision import PlanConfigurationSnapshot


class RoleProfile(BaseModel):
    role_title: str = ""
    seniority: str = ""
    canonical_tags: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    resume_signals: list[str] = Field(default_factory=list)
    uncovered_technologies: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)


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


class InterviewPlan(BaseModel):
    title: str
    questions: list[InterviewQuestion]
    prep_context: PrepContext | None = None
    _revision_plan: Any = PrivateAttr(default=None)
    _generation_enforcement: dict[str, Any] = PrivateAttr(default_factory=dict)

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


def enforce_generated_interview_question_quality(
    plan: InterviewPlan,
) -> InterviewPlan:
    """Reject only deterministic Hard findings at a generation boundary."""

    report = assess_interview_question_quality(
        tuple(QuestionQualityInput.from_question(item) for item in plan.questions)
    )
    if report.hard_violations:
        violation = report.hard_violations[0]
        raise PlanGenerationValidationError(
            violation.code,
            violation.evidence_summary,
        )
    return plan


def validate_generation_configuration(
    configuration: "PlanConfigurationSnapshot",
) -> "PlanConfigurationSnapshot":
    from app.services.interview_plan_budget import (
        MAX_SAFE_MAIN_QUESTION_COUNT,
        MIN_SAFE_MAIN_QUESTION_COUNT,
    )
    from app.services.interview_plan_revision import (
        DEFAULT_PLAN_GENERATOR_VERSION,
        PlanConfigurationSnapshot,
    )

    validated = PlanConfigurationSnapshot.model_validate(
        configuration.model_dump(mode="json", warnings=False)
    )
    if validated.generator_version != DEFAULT_PLAN_GENERATOR_VERSION:
        raise PlanGenerationValidationError(
            "unsupported_plan_generator_version",
            "requested plan generator version is not deployed",
        )
    question_count = sum(validated.question_type_budget.values())
    if not (
        MIN_SAFE_MAIN_QUESTION_COUNT
        <= question_count
        <= MAX_SAFE_MAIN_QUESTION_COUNT
    ):
        raise PlanGenerationValidationError(
            "configured_question_count_out_of_range",
            "configured generation question count must be 1 to 10",
        )
    if (
        validated.expected_followup_budget
        > question_count * validated.max_followups_per_question
    ):
        raise PlanGenerationValidationError(
            "configured_followup_budget_exceeds_hard_limit",
            "configured expected follow-up budget exceeds the per-question hard limit",
        )
    return validated


def enforce_generated_interview_plan(
    plan: InterviewPlan,
    configuration: "PlanConfigurationSnapshot",
) -> InterviewPlan:
    """Validate and deterministically enforce one configured Provider result.

    Over-budget output may only be trimmed to its consecutive q1..qN prefix.
    The retained prefix must still match the exact per-type generation target;
    otherwise the Provider result is rejected rather than silently rewritten.
    """
    from app.services.interview_plan_budget import (
        MAX_SAFE_MAIN_QUESTION_COUNT,
        QUESTION_TYPE_ORDER,
    )

    prior_enforcement = dict(plan._generation_enforcement)
    configuration = validate_generation_configuration(configuration)
    validated = InterviewPlan.model_validate(
        plan.model_dump(mode="json", warnings=False)
    )
    provider_count = len(validated.questions)
    target_count = sum(configuration.question_type_budget.values())
    expected_ids = [f"q{index}" for index in range(1, provider_count + 1)]
    actual_ids = [question.id for question in validated.questions]
    if actual_ids != expected_ids:
        raise PlanGenerationValidationError(
            "provider_question_sequence_invalid",
            "Provider question IDs must be unique and consecutive q1..qN",
        )
    if provider_count < target_count:
        raise PlanGenerationValidationError(
            "provider_question_count_under_budget",
            "Provider returned fewer questions than the configured target",
        )
    if provider_count > MAX_SAFE_MAIN_QUESTION_COUNT:
        raise PlanGenerationValidationError(
            "provider_question_count_above_safe_maximum",
            "Provider returned more than the safe maximum of 10 questions",
        )

    retained = list(validated.questions[:target_count])
    normalized_prompts = [
        " ".join(question.prompt.casefold().split()) for question in retained
    ]
    if len(normalized_prompts) != len(set(normalized_prompts)):
        raise PlanGenerationValidationError(
            "provider_duplicate_question",
            "Provider returned duplicate question text",
        )
    expected_types = {
        question_type: configuration.question_type_budget.get(question_type, 0)
        for question_type in QUESTION_TYPE_ORDER
    }
    actual_counter = Counter(question.kind for question in retained)
    actual_types = {
        question_type: actual_counter.get(question_type, 0)
        for question_type in QUESTION_TYPE_ORDER
    }
    if actual_types != expected_types:
        raise PlanGenerationValidationError(
            "provider_question_type_budget_mismatch",
            "Provider question types do not match the configured exact budget",
        )

    enforced = validated.model_copy(update={"questions": retained})
    enforced._generation_enforcement = prior_enforcement or {
        "action": "trimmed" if provider_count > target_count else "accepted",
        "provider_question_count": provider_count,
        "retained_question_count": target_count,
    }
    launchable = validate_launchable_interview_plan(enforced, configuration)
    return enforce_generated_interview_question_quality(launchable)


def bind_prepared_plan_revision(
    plan: InterviewPlan,
    configuration: "PlanConfigurationSnapshot | None" = None,
    *,
    knowledge_scope: "InterviewKnowledgeScopeSnapshot | None" = None,
) -> InterviewPlan:
    from app.services.interview_plan_budget import assess_interview_plan_budget
    from app.services.interview_plan_revision import (
        legacy_plan_to_v2,
        v2_plan_to_legacy,
    )

    revision_plan = legacy_plan_to_v2(
        plan,
        configuration_snapshot=configuration,
        knowledge_scope=knowledge_scope,
    )
    assessment = assess_interview_plan_budget(revision_plan)
    if not assessment.launch_allowed:
        raise PlanGenerationValidationError(
            "generated_plan_not_launchable",
            "generated plan violates the launch safety boundary",
        )
    bound_legacy = v2_plan_to_legacy(revision_plan)
    plan.questions = bound_legacy.questions
    plan.prep_context = bound_legacy.prep_context
    plan._revision_plan = revision_plan
    return plan


def prepared_plan_revision(
    plan: InterviewPlan,
    configuration: "PlanConfigurationSnapshot | None" = None,
    *,
    knowledge_scope: "InterviewKnowledgeScopeSnapshot | None" = None,
):
    from app.services.interview_plan_revision import (
        InterviewPlanV2,
        v2_plan_to_legacy,
    )

    revision_plan = plan._revision_plan
    if revision_plan is None:
        bind_prepared_plan_revision(
            plan,
            configuration,
            knowledge_scope=knowledge_scope,
        )
        revision_plan = plan._revision_plan
    validated = InterviewPlanV2.model_validate(
        revision_plan.model_dump(mode="json", warnings=False)
    )
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
    if (
        configuration is not None
        and validated.configuration_snapshot
        != validate_generation_configuration(configuration)
    ):
        raise PlanGenerationValidationError(
            "prepared_configuration_mismatch",
            "prepared plan configuration does not match the requested snapshot",
        )
    if knowledge_scope is not None:
        from app.domain.knowledge.source_scope import (
            InterviewKnowledgeScopeSnapshot,
        )

        requested_scope = InterviewKnowledgeScopeSnapshot.model_validate(
            knowledge_scope.model_dump(mode="json")
        )
        if validated.knowledge_scope != requested_scope:
            raise PlanGenerationValidationError(
                "prepared_knowledge_scope_mismatch",
                "prepared plan knowledge scope does not match the resolved snapshot",
            )
    return validated


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



def validate_launchable_interview_plan(
    plan: InterviewPlan,
    configuration: "PlanConfigurationSnapshot | None" = None,
) -> InterviewPlan:
    validated_plan = InterviewPlan.model_validate(
        plan.model_dump(mode="json", warnings=False)
    )
    question_ids = [question.id for question in validated_plan.questions]
    if configuration is None:
        minimum, maximum = 3, 5
    else:
        from app.services.interview_plan_budget import (
            MAX_SAFE_MAIN_QUESTION_COUNT,
            MIN_SAFE_MAIN_QUESTION_COUNT,
        )
        from app.services.interview_plan_revision import (
            PlanConfigurationSnapshot,
        )

        PlanConfigurationSnapshot.model_validate(
            configuration.model_dump(mode="json", warnings=False)
        )
        minimum = MIN_SAFE_MAIN_QUESTION_COUNT
        maximum = MAX_SAFE_MAIN_QUESTION_COUNT
    if not minimum <= len(question_ids) <= maximum:
        raise ValueError(
            f"launchable interview plans require {minimum} to {maximum} questions"
        )
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("launchable interview question ids must be unique")
    expected_ids = [f"q{index}" for index in range(1, len(question_ids) + 1)]
    if question_ids != expected_ids:
        raise ValueError("launchable interview question ids must be consecutive q1..qN")
    return plan


def public_interview_plan_payload(plan: InterviewPlan) -> dict:
    payload = plan.model_dump(mode="json", exclude_none=True)
    _sanitize_public_prep_context(payload.get("prep_context"))
    return payload


def public_interview_plan_v2_payload(plan) -> dict:
    payload = plan.model_dump(mode="json", exclude_none=True)
    _sanitize_public_prep_context(payload.get("prep_context"))
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


def _sanitize_public_prep_context(context: object) -> None:
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


def prepare_interview(
    job_description: str,
    resume_text: str,
    llm: InterviewLLM | None = None,
    knowledge_store: "KnowledgeRepository | None" = None,
    execution_runner=None,
    configuration: "PlanConfigurationSnapshot | None" = None,
    knowledge_scope: "InterviewKnowledgeScopeSnapshot | None" = None,
    knowledge_source_scope: "KnowledgeSourceScope | None" = None,
    allow_fallback: bool = True,
) -> InterviewPlan:
    job_description = _require_text("job_description", job_description)
    resume_text = _require_text("resume_text", resume_text)
    effective_configuration = (
        validate_generation_configuration(configuration)
        if configuration is not None
        else None
    )

    from app.agents.knowledge import KnowledgeAgent
    from app.services.agent_runtime import (
        AgentExecutionContext,
        AgentExecutionRunner,
        AgentFallback,
        AgentOutcome,
    )

    runner = execution_runner or AgentExecutionRunner()
    correlation_id = f"prep-{uuid4().hex}"
    context = AgentExecutionContext(
        correlation_id=correlation_id,
        agent="knowledge",
        operation="generate_plan",
        phase="prep",
    )
    agent = KnowledgeAgent(llm=llm, vector_store=knowledge_store)

    def fallback_plan(exc: Exception) -> AgentFallback[InterviewPlan]:
        from app.services.job_tags import extract_job_tags

        plan = attach_prep_context(
            fallback_interview_plan(effective_configuration),
            job_description=job_description,
            resume_text=resume_text,
            job_tags=extract_job_tags(job_description),
        )
        return AgentFallback(
            bind_prepared_plan_revision(
                plan,
                effective_configuration,
                knowledge_scope=knowledge_scope,
            ),
            (
                exc.code
                if isinstance(exc, PlanGenerationValidationError)
                else "plan_generation_failed"
            ),
        )

    def invoke_plan() -> InterviewPlan:
        plan = agent.generate_plan(
            job_description=job_description,
            resume_text=resume_text,
            prep_run_id=correlation_id,
            configuration=effective_configuration,
            knowledge_source_scope=knowledge_source_scope,
        )
        return bind_prepared_plan_revision(
            plan,
            effective_configuration,
            knowledge_scope=knowledge_scope,
        )

    def trace_metadata(plan: InterviewPlan) -> dict[str, Any]:
        from app.services.interview_plan_budget import (
            INTERVIEW_PLAN_BUDGET_VERSION,
        )
        from app.services.interview_plan_revision import (
            plan_configuration_sha256,
            plan_payload_sha256,
        )

        revision_plan = prepared_plan_revision(
            plan,
            effective_configuration,
            knowledge_scope=knowledge_scope,
        )
        enforcement = dict(plan._generation_enforcement)
        return {
            "question_count": len(plan.questions),
            "knowledge_status": (
                plan.prep_context.knowledge_status
                if plan.prep_context
                else "legacy"
            ),
            "configuration_sha256": plan_configuration_sha256(
                revision_plan.configuration_snapshot
            ),
            "plan_sha256": plan_payload_sha256(revision_plan),
            "generator_version": (
                revision_plan.configuration_snapshot.generator_version
            ),
            "budget_version": INTERVIEW_PLAN_BUDGET_VERSION,
            "target_duration_minutes": (
                revision_plan.configuration_snapshot.target_duration_minutes
            ),
            "generation_enforcement_action": enforcement.get("action"),
            "provider_question_count": enforcement.get(
                "provider_question_count"
            ),
            "retained_question_count": enforcement.get(
                "retained_question_count"
            ),
        }

    return runner.run(
        context,
        invoke_plan,
        fallback=fallback_plan if allow_fallback else None,
        metadata=trace_metadata,
        classify=lambda plan: (
            AgentOutcome(
                status="degraded",
                reason=(
                    plan.prep_context.binding_snapshot.degraded_reason
                    if plan.prep_context
                    and plan.prep_context.binding_snapshot
                    and plan.prep_context.binding_snapshot.degraded_reason
                    else "knowledge_degraded"
                ),
            )
            if plan.prep_context and plan.prep_context.knowledge_status == "degraded"
            else AgentOutcome()
        ),
    )


def fallback_interview_plan(
    configuration: "PlanConfigurationSnapshot | None" = None,
) -> InterviewPlan:
    if configuration is not None:
        from app.services.interview_plan_revision import (
            PlanConfigurationSnapshot,
        )

        validated_configuration = PlanConfigurationSnapshot.model_validate(
            configuration.model_dump(mode="json", warnings=False)
        )
        return _configured_fallback_interview_plan(validated_configuration)
    return InterviewPlan(
        title="基础模拟面试",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="请从简历中选择一个最能代表你能力的项目，说明业务背景、你的职责和最终结果。",
                focus="项目表达",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="请选择项目中一个核心技术点，说明你当时的设计取舍、失败场景和兜底方案。",
                focus="技术深度",
            ),
            InterviewQuestion(
                id="q3",
                kind="system-design",
                prompt="如果这个项目的流量扩大十倍，你会优先改造哪些模块？为什么？",
                focus="系统设计",
            ),
        ],
    )


_CONFIGURED_FALLBACK_TEMPLATES = {
    "project": (
        (
            "请选择一个最匹配岗位的真实项目，说明背景、你的职责、关键决策和结果。",
            "项目职责与结果",
        ),
        (
            "请复盘一个项目中的困难取舍，说明备选方案、选择依据和验证方式。",
            "项目取舍",
        ),
        (
            "请说明一次跨角色协作经历，以及你如何推动风险收敛和结果交付。",
            "项目协作",
        ),
    ),
    "technical": (
        (
            "请选择一个核心技术点，说明原理、边界、失败场景和兜底方案。",
            "技术深度",
        ),
        (
            "请分析一次性能或稳定性问题，说明证据、根因、修复和复测。",
            "故障诊断",
        ),
        (
            "请比较两个可行技术方案，并说明适用条件、代价和决策依据。",
            "技术取舍",
        ),
    ),
    "system-design": (
        (
            "请设计一个可扩展服务，说明容量、数据、故障隔离和演进路径。",
            "系统设计",
        ),
        (
            "如果流量扩大十倍，请说明瓶颈判断、扩容顺序和可观测性方案。",
            "容量规划",
        ),
        (
            "请设计跨区域故障恢复方案，并说明一致性、可用性和成本取舍。",
            "可靠性设计",
        ),
    ),
    "behavioral": (
        (
            "请说明一次你主动发现并推动解决工程风险的经历。",
            "主动性",
        ),
        (
            "请说明一次意见分歧，以及你如何用事实促成决策。",
            "沟通协作",
        ),
        (
            "请复盘一次未达到预期的结果，以及后续改进如何验证。",
            "复盘成长",
        ),
    ),
}

_DIFFICULTY_PREFIX = {
    "foundation": "请先清楚说明基础概念，再结合实际例子回答：",
    "intermediate": "请结合真实约束和取舍回答：",
    "advanced": "请在复杂约束、失败模式和演进成本下深入回答：",
}


def _configured_fallback_interview_plan(
    configuration: "PlanConfigurationSnapshot",
) -> InterviewPlan:
    from app.services.interview_plan_budget import (
        MAX_SAFE_MAIN_QUESTION_COUNT,
        MIN_SAFE_MAIN_QUESTION_COUNT,
        QUESTION_TYPE_ORDER,
    )

    question_count = sum(configuration.question_type_budget.values())
    if not (
        MIN_SAFE_MAIN_QUESTION_COUNT
        <= question_count
        <= MAX_SAFE_MAIN_QUESTION_COUNT
    ):
        raise ValueError("configured fallback question count must be 1 to 10")
    questions: list[InterviewQuestion] = []
    prefix = _DIFFICULTY_PREFIX[configuration.difficulty]
    for question_type in QUESTION_TYPE_ORDER:
        type_count = configuration.question_type_budget.get(question_type, 0)
        templates = _CONFIGURED_FALLBACK_TEMPLATES[question_type]
        for type_index in range(type_count):
            prompt, focus = templates[type_index % len(templates)]
            if type_index >= len(templates):
                prompt = (
                    f"{prompt} 请使用与前面不同的场景"
                    f"（{type_index + 1}）。"
                )
            questions.append(
                InterviewQuestion(
                    id=f"q{len(questions) + 1}",
                    kind=question_type,
                    prompt=f"{prefix}{prompt}",
                    focus=f"{focus} · {configuration.focus_preset}",
                )
            )
    return InterviewPlan(
        title=(
            f"{configuration.target_duration_minutes} 分钟"
            f"{configuration.difficulty} 模拟面试"
        ),
        questions=questions,
    )


_TOPIC_LABELS = {
    "python": "Python",
    "fastapi": "FastAPI",
    "redis": "Redis",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "java": "Java",
    "spring": "Spring",
    "kafka": "Kafka",
    "rocketmq": "RocketMQ",
    "rabbitmq": "RabbitMQ",
    "system-design": "系统设计",
    "general": "通用后端能力",
}

_TOPIC_HINTS = {
    "python": "追问 Python 运行时、异步模型、异常处理和工程质量。",
    "fastapi": "追问 FastAPI 依赖注入、请求生命周期、异步接口和可测试性。",
    "redis": "追问缓存一致性、失效时机、穿透保护和降级兜底。",
    "postgresql": "追问索引设计、事务隔离、慢查询定位和连接池配置。",
    "mysql": "追问索引设计、事务隔离、慢查询定位和表结构取舍。",
    "java": "追问 JVM、并发模型、集合框架和服务稳定性。",
    "spring": "追问 Spring Bean 生命周期、事务边界和依赖注入。",
    "kafka": "追问消息可靠性、消费语义、重试和积压处理。",
    "rocketmq": "追问消息可靠性、消费语义、重试死信和积压处理。",
    "rabbitmq": "追问消息确认、死信队列、重试和削峰策略。",
    "system-design": "追问容量估算、瓶颈定位、故障隔离和演进方案。",
    "general": "追问项目背景、职责边界、技术取舍和量化结果。",
}


def deterministic_follow_up_hint(tag: str) -> str:
    return _TOPIC_HINTS.get(tag, _TOPIC_HINTS["general"])


def build_prep_context(
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
    plan: InterviewPlan,
) -> PrepContext:
    normalized_tags = _normalize_topic_tags(job_tags)
    topics = [
        _build_topic(tag, job_description=job_description, resume_text=resume_text)
        for tag in normalized_tags
    ]
    question_hints = [
        _build_question_hint(question, topics=topics)
        for question in plan.questions
    ]
    return PrepContext(
        summary=(
            f"Knowledge Agent 预热了 {len(topics)} 个岗位考点，"
            f"并为 {len(question_hints)} 道题生成追问线索。"
        ),
        topics=topics,
        question_hints=question_hints,
    )


def attach_prep_context(
    plan: InterviewPlan,
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewPlan:
    return plan.model_copy(
        update={
            "prep_context": build_prep_context(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                plan=plan,
            )
        }
    )


def _normalize_topic_tags(job_tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in job_tags or ["general"]:
        value = tag.strip().lower()
        if not value:
            continue
        if value not in _TOPIC_LABELS:
            value = "general"
        if value not in normalized:
            normalized.append(value)
    return normalized or ["general"]


def _build_topic(
    tag: str,
    *,
    job_description: str,
    resume_text: str,
) -> PrepKnowledgeTopic:
    label = _TOPIC_LABELS[tag]
    jd_hit = tag != "general" and tag in job_description.lower()
    resume_hit = tag != "general" and tag in resume_text.lower()
    if jd_hit and resume_hit:
        source = "jd_resume_keyword"
        evidence = (
            f"JD 和简历同时命中 {label}，适合作为"
            f"{_topic_evidence_focus(tag)}追问依据。"
        )
    elif jd_hit:
        source = "jd_keyword"
        evidence = f"JD 明确要求 {label}，需要验证候选人是否具备岗位匹配能力。"
    elif resume_hit:
        source = "resume_keyword"
        evidence = f"简历出现 {label}，适合围绕真实项目经历继续深挖。"
    else:
        source = "fallback"
        evidence = "未命中特定技术关键词，先围绕通用后端项目表达和工程实践预热。"
    return PrepKnowledgeTopic(
        id=f"topic-{tag}",
        label=label,
        source=source,
        evidence=evidence,
        tags=[tag],
    )


def _build_question_hint(
    question: InterviewQuestion,
    *,
    topics: list[PrepKnowledgeTopic],
) -> PrepQuestionHint:
    text = f"{question.prompt} {question.focus}".lower()
    matched_topics = [
        topic
        for topic in topics
        if topic.tags[0] == "general"
        or topic.tags[0] in text
        or topic.label.lower() in text
    ]
    if not matched_topics:
        matched_topics = topics[:1]
    return PrepQuestionHint(
        question_id=question.id,
        topic_ids=[topic.id for topic in matched_topics],
        follow_up_hints=[
            _TOPIC_HINTS.get(topic.tags[0], _TOPIC_HINTS["general"])
            for topic in matched_topics
        ],
        evidence_titles=[topic.label for topic in matched_topics],
    )


def _topic_evidence_focus(tag: str) -> str:
    if tag == "redis":
        return "缓存一致性、穿透保护和高并发"
    if tag in {"mysql", "postgresql"}:
        return "索引设计、事务边界和慢查询优化"
    if tag in {"kafka", "rocketmq", "rabbitmq"}:
        return "消息可靠性、重试和削峰"
    if tag == "fastapi":
        return "接口设计、依赖注入和异步服务"
    if tag == "system-design":
        return "容量估算、故障隔离和服务演进"
    return "项目深度、工程实践和技术取舍"


def _require_text(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()
