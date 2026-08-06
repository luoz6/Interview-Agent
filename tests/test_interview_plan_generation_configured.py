from __future__ import annotations

from collections import Counter
from itertools import cycle, islice
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.routes as route_module
from app.main import app
from app.services.agent_runtime import AgentExecutionRunner
from app.services.context_budget import PLAN_CONTEXT_POLICY
from app.services.interview_plan_budget import QUESTION_TYPE_ORDER
from app.services.interview_plan_regenerator import (
    PlanRegenerationFailed,
    ProviderPlanRegenerator,
)
from app.services.interview_plan_revision import (
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    PlanSourcePayload,
    default_plan_configuration,
    plan_configuration_sha256,
    plan_payload_sha256,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.llm import OpenAIInterviewLLM
from app.services.prep import (
    bind_prepared_plan_revision,
    enforce_generated_interview_plan,
    InterviewPlan,
    InterviewQuestion,
    PlanGenerationValidationError,
    prepare_interview,
    prepared_plan_revision,
)
from app.services.trace_sanitization import sanitize_agent_safe_metadata


PROFILE_QUESTION_COUNTS = {15: 3, 30: 5, 45: 7, 60: 9}
FOCUS_TYPE_ORDER = {
    "technical_depth": (
        "technical",
        "project",
        "system-design",
        "behavioral",
    ),
    "system_design": (
        "system-design",
        "technical",
        "project",
        "behavioral",
    ),
    "project_review": (
        "project",
        "technical",
        "behavioral",
        "system-design",
    ),
    "balanced": (
        "project",
        "technical",
        "system-design",
        "behavioral",
    ),
}


def configured_snapshot(
    *,
    duration: int = 30,
    difficulty: str = "intermediate",
    focus: str = "balanced",
    question_count: int | None = None,
    expected_followups: int | None = None,
) -> PlanConfigurationSnapshot:
    count = question_count or PROFILE_QUESTION_COUNTS[duration]
    ordered_types = list(
        islice(cycle(FOCUS_TYPE_ORDER[focus]), count)
    )
    type_budget = dict(Counter(ordered_types))
    return PlanConfigurationSnapshot(
        difficulty=difficulty,
        target_duration_minutes=duration,
        focus_preset=focus,
        question_type_budget=type_budget,
        expected_followup_budget=(
            count if expected_followups is None else expected_followups
        ),
        generator_version="plan-generator-v2",
        followup_policy_version="fixed_v1",
    )


def plan_for_configuration(
    configuration: PlanConfigurationSnapshot,
    *,
    extra_kinds: tuple[str, ...] = (),
    job_marker: str = "synthetic",
) -> InterviewPlan:
    kinds = [
        question_type
        for question_type in QUESTION_TYPE_ORDER
        for _ in range(configuration.question_type_budget.get(question_type, 0))
    ]
    kinds.extend(extra_kinds)
    return InterviewPlan(
        title=(
            f"{configuration.target_duration_minutes}-minute "
            f"{configuration.difficulty} {configuration.focus_preset} plan"
        ),
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind=kind,
                prompt=(
                    f"{job_marker} {configuration.difficulty} "
                    f"{configuration.focus_preset} {kind} question {index}"
                ),
                focus=f"{configuration.focus_preset} focus {index}",
            )
            for index, kind in enumerate(kinds, start=1)
        ],
    )


class ConfigAwarePlanLLM:
    def __init__(self) -> None:
        self.calls = []

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
        knowledge_context=None,
        configuration=None,
    ) -> InterviewPlan:
        self.calls.append(
            {
                "job_description": job_description,
                "resume_text": resume_text,
                "knowledge_context": knowledge_context,
                "configuration": configuration,
            }
        )
        return plan_for_configuration(
            configuration,
            job_marker=(
                "中文岗位" if "岗位" in job_description else "English role"
            ),
        )


class CapturingRecorder:
    def __init__(self) -> None:
        self.records = []

    def record(self, record) -> None:
        self.records.append(record)


@pytest.mark.parametrize("duration", (15, 30, 45, 60))
@pytest.mark.parametrize(
    "difficulty", ("foundation", "intermediate", "advanced")
)
@pytest.mark.parametrize(
    "focus",
    ("technical_depth", "system_design", "project_review", "balanced"),
)
def test_configuration_matrix_changes_generation_and_preserves_snapshot(
    duration,
    difficulty,
    focus,
):
    configuration = configured_snapshot(
        duration=duration,
        difficulty=difficulty,
        focus=focus,
    )
    llm = ConfigAwarePlanLLM()

    plan = prepare_interview(
        job_description="Backend role with Redis and PostgreSQL",
        resume_text="Built a cache-backed API",
        llm=llm,
        configuration=configuration,
    )
    revision_plan = prepared_plan_revision(plan, configuration)

    assert len(plan.questions) == PROFILE_QUESTION_COUNTS[duration]
    assert Counter(item.kind for item in plan.questions) == Counter(
        configuration.question_type_budget
    )
    assert revision_plan.configuration_snapshot == configuration
    assert all(
        item.difficulty == difficulty for item in revision_plan.questions
    )
    assert [item.position for item in revision_plan.questions] == list(
        range(1, len(revision_plan.questions) + 1)
    )
    assert all(
        not item.question_id.startswith("q")
        for item in revision_plan.questions
    )
    assert focus in plan.title
    assert llm.calls[0]["configuration"] == configuration
    prompt = OpenAIInterviewLLM._build_plan_prompt(
        SimpleNamespace(),
        job_description="Backend role",
        resume_text="Backend resume",
        configuration=configuration,
    )
    assert f"Return exactly {len(plan.questions)} questions" in prompt
    assert f"difficulty: {difficulty}" in prompt
    assert f"focus_preset: {focus}" in prompt


def test_agent_trace_hashes_are_the_exact_persistable_revision_hashes():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    configuration = configured_snapshot(
        duration=60,
        difficulty="advanced",
        focus="system_design",
        question_count=10,
    )

    plan = prepare_interview(
        "Backend role",
        "Distributed systems resume",
        llm=ConfigAwarePlanLLM(),
        execution_runner=runner,
        configuration=configuration,
    )
    revision_plan = prepared_plan_revision(plan, configuration)
    metadata = recorder.records[0].safe_metadata

    assert metadata["configuration_sha256"] == plan_configuration_sha256(
        configuration
    )
    assert metadata["plan_sha256"] == plan_payload_sha256(revision_plan)
    assert metadata["question_count"] == 10
    assert metadata["target_duration_minutes"] == 60
    assert metadata["generation_enforcement_action"] == "accepted"
    assert metadata["provider_question_count"] == 10
    assert metadata["retained_question_count"] == 10


def test_bound_revision_rejects_later_legacy_plan_mutation():
    configuration = configured_snapshot(duration=15)
    plan = bind_prepared_plan_revision(
        plan_for_configuration(configuration),
        configuration,
    )
    plan.questions[0].prompt = "Mutated after V2 binding"

    with pytest.raises(PlanGenerationValidationError) as error:
        prepared_plan_revision(plan, configuration)

    assert error.value.code == "prepared_plan_payload_mismatch"


def test_provider_under_budget_is_rejected_and_initial_prep_falls_back():
    configuration = configured_snapshot(duration=30)
    under_budget = plan_for_configuration(configuration).model_copy(
        update={
            "questions": plan_for_configuration(configuration).questions[:-1]
        }
    )

    with pytest.raises(
        PlanGenerationValidationError,
        match="fewer questions",
    ) as error:
        enforce_generated_interview_plan(under_budget, configuration)
    assert error.value.code == "provider_question_count_under_budget"

    class UnderBudgetLLM:
        def generate_plan(self, *_args, **_kwargs):
            return under_budget

    recorder = CapturingRecorder()
    fallback = prepare_interview(
        "Backend role",
        "Backend resume",
        llm=UnderBudgetLLM(),
        configuration=configuration,
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )

    assert len(fallback.questions) == 5
    assert Counter(item.kind for item in fallback.questions) == Counter(
        configuration.question_type_budget
    )
    assert recorder.records[0].status == "degraded"
    assert (
        recorder.records[0].fallback_reason
        == "provider_question_count_under_budget"
    )


def test_provider_over_budget_uses_only_a_valid_consecutive_prefix():
    configuration = configured_snapshot(duration=15)
    over_budget = plan_for_configuration(
        configuration,
        extra_kinds=("technical",),
    )

    enforced = enforce_generated_interview_plan(over_budget, configuration)

    assert [item.id for item in enforced.questions] == ["q1", "q2", "q3"]
    assert enforced._generation_enforcement == {
        "action": "trimmed",
        "provider_question_count": 4,
        "retained_question_count": 3,
    }

    mismatched = InterviewPlan(
        title="Mismatched prefix",
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind=kind,
                prompt=f"Distinct {kind} {index}",
                focus=f"Focus {index}",
            )
            for index, kind in enumerate(
                ("project", "technical", "behavioral", "system-design"),
                start=1,
            )
        ],
    )
    with pytest.raises(
        PlanGenerationValidationError,
        match="exact budget",
    ) as error:
        enforce_generated_interview_plan(mismatched, configuration)
    assert error.value.code == "provider_question_type_budget_mismatch"


def test_provider_sequence_duplicates_and_above_safe_maximum_fail_closed():
    configuration = configured_snapshot(duration=15)
    duplicate_id = plan_for_configuration(configuration)
    duplicate_id.questions[1].id = "q1"
    with pytest.raises(PlanGenerationValidationError) as duplicate_error:
        enforce_generated_interview_plan(duplicate_id, configuration)
    assert duplicate_error.value.code == "provider_question_sequence_invalid"

    duplicate_prompt = plan_for_configuration(configuration)
    duplicate_prompt.questions[1].prompt = duplicate_prompt.questions[0].prompt
    with pytest.raises(PlanGenerationValidationError) as prompt_error:
        enforce_generated_interview_plan(duplicate_prompt, configuration)
    assert prompt_error.value.code == "provider_duplicate_question"

    eleven = plan_for_configuration(
        configuration,
        extra_kinds=(
            "technical",
            "technical",
            "technical",
            "technical",
            "technical",
            "technical",
            "technical",
            "technical",
        ),
    )
    with pytest.raises(PlanGenerationValidationError) as maximum_error:
        enforce_generated_interview_plan(eleven, configuration)
    assert (
        maximum_error.value.code
        == "provider_question_count_above_safe_maximum"
    )

    revision = prepared_plan_revision(
        bind_prepared_plan_revision(
            plan_for_configuration(configuration),
            configuration,
        ),
        configuration,
    )
    invalid_positions = revision.model_dump(mode="json")
    invalid_positions["questions"][1]["position"] = 1
    with pytest.raises(ValidationError, match="position must be unique"):
        InterviewPlanV2.model_validate(invalid_positions)


def test_invalid_configuration_stops_before_provider_and_no_fallback_hides_it():
    configuration = configured_snapshot(
        duration=15,
        expected_followups=7,
    )
    llm = ConfigAwarePlanLLM()

    with pytest.raises(PlanGenerationValidationError) as error:
        prepare_interview(
            "Backend role",
            "Backend resume",
            llm=llm,
            configuration=configuration,
        )

    assert error.value.code == "configured_followup_budget_exceeds_hard_limit"
    assert llm.calls == []

    unsupported_generator = configuration.model_copy(
        update={"generator_version": "plan-generator-v999"}
    )
    with pytest.raises(PlanGenerationValidationError) as generator_error:
        prepare_interview(
            "Backend role",
            "Backend resume",
            llm=llm,
            configuration=unsupported_generator,
        )
    assert generator_error.value.code == "unsupported_plan_generator_version"
    assert llm.calls == []


@pytest.mark.parametrize(
    ("job_description", "resume_text", "marker"),
    (
        ("后端岗位，要求 Redis。", "实现过缓存服务。", "中文岗位"),
        ("Backend role requiring Redis.", "Built a cache service.", "English role"),
    ),
)
def test_chinese_and_english_sources_reach_configured_generation_unchanged(
    job_description,
    resume_text,
    marker,
):
    configuration = configured_snapshot(duration=15)
    llm = ConfigAwarePlanLLM()

    plan = prepare_interview(
        job_description,
        resume_text,
        llm=llm,
        configuration=configuration,
    )

    assert llm.calls[0]["job_description"] == job_description
    assert llm.calls[0]["resume_text"] == resume_text
    assert marker in plan.questions[0].prompt


class NoopChatModel:
    def with_structured_output(self, _schema, method=None):
        raise AssertionError("context budget test does not call Provider")


def test_sixty_minute_prompt_and_grounding_context_remain_inside_budget():
    configuration = configured_snapshot(
        duration=60,
        difficulty="advanced",
        focus="system_design",
        question_count=10,
    )
    llm = OpenAIInterviewLLM(chat_model=NoopChatModel())
    knowledge = [
        {
            "evidence_id": f"evidence-{index}",
            "title": f"Evidence {index}",
            "candidate_summary": "reliability capacity consistency " * 80,
        }
        for index in range(100)
    ]

    fitted_jd, fitted_resume, fitted_knowledge = llm._fit_plan_inputs(
        job_description="后端可靠性 Backend reliability role " * 5000,
        resume_text="分布式系统 Distributed systems project " * 5000,
        knowledge_context=knowledge,
    )
    prompt = llm._build_plan_prompt(
        job_description=fitted_jd,
        resume_text=fitted_resume,
        knowledge_context=fitted_knowledge,
        configuration=configuration,
    )
    measurement = llm._guard_prompt(prompt, PLAN_CONTEXT_POLICY)

    assert measurement.estimated_input_tokens <= measurement.available_input_tokens
    assert fitted_knowledge is not None
    assert len(fitted_knowledge) < len(knowledge)
    assert "Return exactly 10 questions" in prompt
    assert '"q10"' in prompt
    assert "max_followups_per_question: 2" in prompt

    chat_model = StructuredOnlyChatModel(
        plan_for_configuration(configuration)
    )
    configured_llm = OpenAIInterviewLLM(chat_model=chat_model)
    generated = configured_llm.generate_plan(
        "后端可靠性 Backend reliability role " * 5000,
        "分布式系统 Distributed systems project " * 5000,
        knowledge_context=knowledge,
        configuration=configuration,
    )
    assert len(generated.questions) == 10
    assert chat_model.structured_prompt is not None
    assert "evidence-99" not in chat_model.structured_prompt


class StructuredPlanModel:
    def __init__(self, plan: InterviewPlan, owner=None) -> None:
        self.plan = plan
        self.owner = owner

    def invoke(self, prompt):
        if self.owner is not None:
            self.owner.structured_prompt = prompt
        return self.plan


class StructuredOnlyChatModel:
    def __init__(self, plan: InterviewPlan) -> None:
        self.plan = plan
        self.raw_calls = 0
        self.structured_prompt = None

    def with_structured_output(self, _schema, method=None):
        return StructuredPlanModel(self.plan, self)

    def invoke(self, _prompt):
        self.raw_calls += 1
        return SimpleNamespace(content="{}")


def test_budget_validation_failure_does_not_amplify_into_raw_provider_retry():
    configuration = configured_snapshot(duration=30)
    under_budget = plan_for_configuration(configuration)
    under_budget.questions.pop()
    chat_model = StructuredOnlyChatModel(under_budget)
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    with pytest.raises(PlanGenerationValidationError):
        llm.generate_plan(
            "Backend JD",
            "Backend resume",
            configuration=configuration,
        )

    assert chat_model.raw_calls == 0

    with pytest.raises(PlanGenerationValidationError):
        prepare_interview(
            "Backend JD",
            "Backend resume",
            llm=SimpleNamespace(generate_plan=lambda *_args, **_kwargs: under_budget),
            configuration=configuration,
            allow_fallback=False,
        )


def test_trace_hash_allowlist_accepts_only_canonical_sha256_values():
    valid_hash = "a" * 64
    result = sanitize_agent_safe_metadata(
        {
            "configuration_sha256": valid_hash,
            "plan_sha256": "not-a-hash",
        }
    )

    assert result.value == {"configuration_sha256": valid_hash}
    assert "invalid_sha256" in result.rejection_categories


def test_prep_api_persists_requested_configuration_and_same_plan_hash(monkeypatch):
    store = InMemoryInterviewPlanRevisionStore()
    llm = ConfigAwarePlanLLM()
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    configuration = configured_snapshot(
        duration=60,
        difficulty="advanced",
        focus="system_design",
    )
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: store
    monkeypatch.setattr(
        route_module,
        "get_agent_execution_runner",
        lambda: runner,
    )
    monkeypatch.setattr(
        route_module,
        "prepare_interview",
        lambda job_description,
        resume_text,
        execution_runner=None,
        configuration=None: prepare_interview(
            job_description,
            resume_text,
            llm=llm,
            execution_runner=execution_runner,
            configuration=configuration,
        ),
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Distributed systems resume",
                "configuration": configuration.model_dump(mode="json"),
            },
        )
        invalid_configuration = configuration.model_dump(mode="json")
        invalid_configuration["generator_version"] = "plan-generator-v999"
        rejected = client.post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Distributed systems resume",
                "configuration": invalid_configuration,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    saved = store.get_by_id(payload["plan_revision_id"])
    assert len(payload["plan"]["questions"]) == 9
    assert [item["id"] for item in payload["questions"]] == [
        item["question_id"] for item in payload["plan"]["questions"]
    ]
    assert [item["id"] for item in payload["legacy_plan"]["questions"]] == [
        item["question_id"] for item in payload["plan"]["questions"]
    ]
    assert payload["plan"]["configuration_snapshot"] == configuration.model_dump(
        mode="json"
    )
    assert payload["plan_sha256"] == saved.plan_sha256
    assert recorder.records[0].safe_metadata["plan_sha256"] == saved.plan_sha256
    assert recorder.records[0].safe_metadata[
        "configuration_sha256"
    ] == plan_configuration_sha256(configuration)
    assert payload["budget_assessment"]["launch_allowed"] is True
    assert llm.calls[0]["configuration"] == configuration
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == (
        "unsupported_plan_generator_version"
    )
    assert len(llm.calls) == 1


def test_regeneration_reuses_frozen_configuration_and_exact_budget():
    configuration = configured_snapshot(duration=15)
    initial_legacy = bind_prepared_plan_revision(
        plan_for_configuration(configuration),
        configuration,
    )
    store = InMemoryInterviewPlanRevisionStore()
    current = store.create_initial(
        source_payload=PlanSourcePayload(
            job_description="Backend role",
            resume_text="Backend resume",
        ),
        plan=prepared_plan_revision(initial_legacy, configuration),
        retention_policy="test-v1",
        generator_version=configuration.generator_version,
    )
    observed = []

    def planner(job_description, resume_text, received_configuration):
        observed.append(received_configuration)
        return plan_for_configuration(received_configuration)

    regenerated = ProviderPlanRegenerator(planner).regenerate_all(
        current=current,
        source=store.get_source(current.source_id).protected_payload,
    )

    assert observed == [configuration]
    assert regenerated.configuration_snapshot == configuration
    assert len(regenerated.questions) == 3

    already_bound = bind_prepared_plan_revision(
        enforce_generated_interview_plan(
            plan_for_configuration(configuration),
            configuration,
        ),
        configuration,
    )
    bound_hash = plan_payload_sha256(
        prepared_plan_revision(already_bound, configuration)
    )
    reused = ProviderPlanRegenerator(
        lambda _job, _resume, _configuration: already_bound
    ).regenerate_all(
        current=current,
        source=store.get_source(current.source_id).protected_payload,
    )
    assert plan_payload_sha256(reused) == bound_hash

    under_budget = plan_for_configuration(configuration)
    under_budget.questions.pop()
    failing = ProviderPlanRegenerator(
        lambda _job, _resume, _configuration: under_budget
    )
    with pytest.raises(PlanRegenerationFailed) as error:
        failing.regenerate_all(
            current=current,
            source=store.get_source(current.source_id).protected_payload,
        )
    assert error.value.code == "provider_question_count_under_budget"


def test_new_prep_default_is_the_30_minute_five_question_configuration():
    configuration = default_plan_configuration()

    assert configuration.target_duration_minutes == 30
    assert sum(configuration.question_type_budget.values()) == 5
    assert configuration.expected_followup_budget == 5
    assert configuration.max_followups_per_question == 2
