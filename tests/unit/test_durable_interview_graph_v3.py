from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    commit_rendered_main_question,
    generate_main_question_node,
    prepare_main_question,
)
from app.graphs.durable_interview_state_v3 import make_durable_initial_state_v3
from app.services.interview_plan_revision import (
    InterviewPlanV3,
    default_plan_configuration,
    legacy_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
)
from app.services.session_plan_binding import SessionPlanBinding
from app.services.main_question_generation import (
    MAIN_QUESTION_GENERATION_PROMPT_SHA256,
    MAIN_QUESTION_GENERATION_PROMPT_VERSION,
)
from app.domain.interview.question_intent import QuestionIntentV1
import app.graphs.durable_interview_graph as durable_graph_module


def _plan():
    return InterviewPlanV3(
        title="JIT",
        configuration_snapshot=default_plan_configuration(),
        knowledge_scope=legacy_interview_knowledge_scope_snapshot(),
        questions=(
            QuestionIntentV1(
                question_id="q1",
                position=1,
                kind="project",
                focus="Redis 库存一致性",
                difficulty="advanced",
                assessment_goals=("failure_mode", "recovery"),
                expected_minutes=5,
                expected_followups=1,
                knowledge_binding={},
            ),
            QuestionIntentV1(
                question_id="q2",
                position=2,
                kind="technical",
                focus="RocketMQ 重试",
                difficulty="intermediate",
                assessment_goals=("reliability",),
                expected_minutes=5,
                expected_followups=0,
                knowledge_binding={},
            ),
        ),
    )


def _state():
    plan = _plan()
    binding = SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id="00000000-0000-4000-8000-000000000001",
        plan_family_id="00000000-0000-4000-8000-000000000002",
        revision=1,
        plan_sha256=plan_payload_sha256(plan),
        configuration_snapshot=plan.configuration_snapshot.model_dump(mode="json"),
        plan_snapshot=plan.model_dump(mode="json"),
    )
    return make_durable_initial_state_v3("session-1", plan, plan_binding=binding)


class FakeGenerationStore:
    def __init__(self):
        self.rows = {}
        self.completed = []
        self.failed = []

    def prepare_generation(self, **kwargs):
        generation_id = "generation-" + kwargs["identity_sha256"][:12]
        row = self.rows.get(kwargs["source_command_id"])
        if row is None:
            row = SimpleNamespace(
                generation_id=generation_id,
                session_id=kwargs["session_id"],
                source_command_id=kwargs["source_command_id"],
                question_id=kwargs["question_id"],
                status="pending",
                active_attempt=1,
                final_text=None,
                generation_kind=kwargs["generation_kind"],
                identity_sha256=kwargs["identity_sha256"],
                intent_sha256=kwargs["intent_sha256"],
                context_sha256=kwargs["context_sha256"],
                knowledge_scope_sha256=kwargs["knowledge_scope_sha256"],
                generator_version=kwargs["generator_version"],
                generation_prompt_version=kwargs["generation_prompt_version"],
                generation_prompt_sha256=kwargs["generation_prompt_sha256"],
                result_mode=None,
                failure_reason_code=None,
                provider_invocation_count=None,
                generation_latency_ms=None,
                fallback_used=None,
                safe_reason_code=None,
            )
            self.rows[kwargs["source_command_id"]] = row
            self.rows[generation_id] = row
        return row

    def get_by_id(self, generation_id):
        return self.rows[generation_id]

    def start_or_reclaim_attempt(self, generation_id, attempt_number, **kwargs):
        return SimpleNamespace(
            generation_id=generation_id,
            attempt_number=attempt_number,
            lease_token="token",
            fencing_version=1,
        )

    def append_chunk(self, *args, **kwargs):
        return None

    def fail_attempt(self, generation_id, attempt_number, reason_code, **kwargs):
        row = self.rows[generation_id]
        row.status = "pending"
        row.active_attempt = attempt_number + 1
        row.failure_reason_code = reason_code
        self.failed.append((generation_id, attempt_number, reason_code))

    def complete_attempt(self, generation_id, attempt_number, text, **kwargs):
        row = self.rows[generation_id]
        row.status = "completed"
        row.active_attempt = attempt_number
        row.final_text = text
        row.result_mode = kwargs.get("result_mode")
        row.failure_reason_code = kwargs.get("failure_reason_code")
        row.provider_invocation_count = kwargs.get("provider_invocation_count")
        row.generation_latency_ms = kwargs.get("generation_latency_ms")
        row.fallback_used = kwargs.get("fallback_used")
        row.safe_reason_code = kwargs.get("safe_reason_code")
        self.completed.append((generation_id, text))


class FakeExaminer:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def generate_main_question_attempt(self, **kwargs):
        self.calls.append(kwargs)
        return self.text


class FailingExaminer:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def generate_main_question_attempt(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


class SequenceExaminer:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_main_question_attempt(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs[len(self.calls) - 1]
        if isinstance(output, Exception):
            raise output
        return output


class RecoveredSecondAttemptStore(FakeGenerationStore):
    def start_or_reclaim_attempt(self, generation_id, attempt_number, **kwargs):
        return SimpleNamespace(
            generation_id=generation_id,
            attempt_number=2,
            lease_token="token",
            fencing_version=2,
            reclaimed_after_expiry=False,
        )


class ReclaimedAttemptStore(FakeGenerationStore):
    def start_or_reclaim_attempt(self, generation_id, attempt_number, **kwargs):
        return SimpleNamespace(
            generation_id=generation_id,
            attempt_number=attempt_number,
            lease_token="reclaimed-token",
            fencing_version=2,
            reclaimed_after_expiry=True,
        )


def test_v3_main_question_is_generated_committed_and_used_as_context():
    state = _state()
    generations = FakeGenerationStore()
    examiner = FakeExaminer("如果 Redis 扣减成功但消息投递失败，你会如何恢复？")
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    prepared = prepare_main_question(state, deps)
    state.update(prepared)
    generated = generate_main_question_node(state, deps)
    state.update(generated)
    committed = commit_rendered_main_question(state)
    state.update(committed)

    assert state["messages"][0]["content"] == examiner.text
    assert state["rendered_questions"]["q1"]["text"] == examiner.text
    assert state["question_generation_result"] is None
    assert state["current_rendered_question_id"] == "q1"
    assert examiner.calls[0]["intent"].question_id == "q1"
    assert generations.completed
    rendered = state["rendered_questions"]["q1"]
    assert rendered["provider_invocation_count"] == 1
    assert rendered["generation_latency_ms"] >= 0
    assert rendered["fallback_used"] is False
    assert rendered["safe_reason_code"] == "generated"


def test_v3_second_question_projection_contains_published_question_and_answer():
    state = _state()
    state["current_index"] = 0
    state["rendered_questions"] = {
        "q1": {
            "question_id": "q1",
            "text": "你如何处理 Redis 一致性？",
        }
    }
    state["messages"] = [
        {
            "role": "interviewer",
            "content": "你如何处理 Redis 一致性？",
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "content": "我使用补偿和对账。",
            "question_id": "q1",
        },
    ]
    state["pending_main_question_index"] = 1
    state["decision_answer_state"] = "complete"
    generations = FakeGenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=FakeExaminer(
            "RocketMQ 投递失败时，你会怎样设计重试与死信？"
        )
    )

    prepared = prepare_main_question(state, deps)
    state.update(prepared)
    generation = generations.get_by_id(state["question_generation_id"])

    assert generation.context_sha256
    assert generation.identity_sha256
    examiner = deps.examiner
    state.update(generate_main_question_node(state, deps))
    assert examiner.calls[0]["conversation"][0]["content"] == (
        "你如何处理 Redis 一致性？"
    )
    assert examiner.calls[0]["conversation"][1]["content"] == (
        "我使用补偿和对账。"
    )


def test_v3_next_question_excludes_off_topic_answer_even_for_generic_reason():
    state = _state()
    state["current_index"] = 0
    state["rendered_questions"] = {
        "q1": {"question_id": "q1", "text": "你如何处理 Redis 一致性？"}
    }
    state["messages"] = [
        {
            "role": "interviewer",
            "content": "你如何处理 Redis 一致性？",
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "content": "这段跑题内容不能成为下一题的候选人事实。",
            "question_id": "q1",
        },
    ]
    state["pending_main_question_index"] = 1
    state["decision_answer_state"] = "off_topic"
    state["decision_reason_code"] = "low_confidence"
    generations = FakeGenerationStore()
    examiner = FakeExaminer("RocketMQ 重试失败后如何恢复？")
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))

    contents = [item["content"] for item in examiner.calls[0]["conversation"]]
    assert "这段跑题内容不能成为下一题的候选人事实。" not in contents


def test_main_question_generation_source_matches_public_command_stream_key():
    state = _state()
    state["active_command_id"] = "command-1"
    state["pending_main_question_index"] = 1
    generations = FakeGenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(),
        generation_store=generations,
        examiner=FakeExaminer("RocketMQ 重试失败后如何恢复？"),
    )

    prepare_main_question(state, deps)

    assert generations.rows["command-1"].generation_kind == "main_question"


@pytest.mark.parametrize(
    ("error_factory", "reason_code"),
    [
        (
            lambda request: openai.APITimeoutError(request=request),
            "provider_timeout",
        ),
        (
            lambda request: openai.APIConnectionError(request=request),
            "provider_unavailable",
        ),
        (
            lambda request: openai.RateLimitError(
                "limited",
                response=httpx.Response(429, request=request),
                body=None,
            ),
            "provider_rate_limited",
        ),
    ],
)
def test_v3_real_provider_failures_commit_classified_fallback(
    error_factory, reason_code
):
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    state = _state()
    generations = FakeGenerationStore()
    examiner = FailingExaminer(error_factory(request))
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))
    state.update(commit_rendered_main_question(state))

    rendered = state["rendered_questions"]["q1"]
    assert rendered["render_mode"] == "fallback"
    assert rendered["fallback_reason_code"] == reason_code
    assert rendered["provider_invocation_count"] == 1
    assert rendered["fallback_used"] is True
    assert rendered["safe_reason_code"] == reason_code
    assert len(examiner.calls) == 1


def test_v3_invalid_provider_output_retries_once_then_commits_valid_question(
    monkeypatch,
):
    monkeypatch.setenv("MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS", "2")
    state = _state()
    generations = FakeGenerationStore()
    examiner = SequenceExaminer(
        [
            "Redis 如何保证一致性？失败后如何恢复？",
            "如果 Redis 库存扣减成功但消息投递失败，你会如何恢复？",
        ]
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))
    state.update(commit_rendered_main_question(state))

    rendered = state["rendered_questions"]["q1"]
    assert rendered["render_mode"] == "generated"
    assert rendered["generation_attempt"] == 2
    assert rendered["fallback_reason_code"] is None
    assert rendered["provider_invocation_count"] == 2
    assert rendered["fallback_used"] is False
    assert rendered["safe_reason_code"] == "generated"
    assert len(examiner.calls) == 2
    assert generations.failed[0][2] == "multiple_questions"


def test_v3_total_timeout_commits_fallback_without_provider_call(monkeypatch):
    timestamps = iter((100.0, 131.0, 131.0))
    monkeypatch.setattr(durable_graph_module, "monotonic", lambda: next(timestamps))
    monkeypatch.setenv("MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS", "30")
    state = _state()
    generations = FakeGenerationStore()
    examiner = SequenceExaminer([])
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))
    state.update(commit_rendered_main_question(state))

    rendered = state["rendered_questions"]["q1"]
    assert rendered["render_mode"] == "fallback"
    assert rendered["fallback_reason_code"] == "total_timeout"
    assert rendered["provider_invocation_count"] == 0
    assert rendered["generation_latency_ms"] == 31000
    assert rendered["fallback_used"] is True
    assert rendered["safe_reason_code"] == "total_timeout"
    assert examiner.calls == []


def test_v3_programming_error_is_not_published_as_provider_fallback():
    state = _state()
    generations = FakeGenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(),
        generation_store=generations,
        examiner=FailingExaminer(AssertionError("implementation defect")),
    )

    state.update(prepare_main_question(state, deps))

    with pytest.raises(AssertionError, match="implementation defect"):
        generate_main_question_node(state, deps)

    assert generations.completed == []


def test_v3_recovered_second_attempt_cannot_trigger_third_provider_call():
    state = _state()
    generations = RecoveredSecondAttemptStore()
    examiner = SequenceExaminer(["Redis 如何保证一致性？失败后如何恢复？"])
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))
    state.update(commit_rendered_main_question(state))

    rendered = state["rendered_questions"]["q1"]
    assert rendered["generation_attempt"] == 2
    assert rendered["render_mode"] == "fallback"
    assert rendered["fallback_reason_code"] == "multiple_questions"
    assert rendered["provider_invocation_count"] == 2
    assert len(examiner.calls) == 1


def test_v3_expired_attempt_reclaim_falls_back_without_provider_call():
    state = _state()
    generations = ReclaimedAttemptStore()
    examiner = SequenceExaminer([])
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(), generation_store=generations, examiner=examiner
    )

    state.update(prepare_main_question(state, deps))
    state.update(generate_main_question_node(state, deps))
    state.update(commit_rendered_main_question(state))

    rendered = state["rendered_questions"]["q1"]
    assert rendered["render_mode"] == "fallback"
    assert rendered["fallback_reason_code"] == "provider_interrupted"
    assert rendered["provider_invocation_count"] == 1
    assert examiner.calls == []


def test_v3_completed_generation_replay_preserves_diagnostics_without_provider_call():
    generations = FakeGenerationStore()
    first_examiner = FakeExaminer(
        "如果 Redis 库存扣减成功但消息投递失败，你会如何恢复？"
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(),
        generation_store=generations,
        examiner=first_examiner,
    )
    first = _state()
    first.update(prepare_main_question(first, deps))
    first.update(generate_main_question_node(first, deps))
    first.update(commit_rendered_main_question(first))
    expected = {
        key: first["rendered_questions"]["q1"][key]
        for key in (
            "provider_invocation_count",
            "generation_latency_ms",
            "fallback_used",
            "safe_reason_code",
        )
    }

    replay_examiner = FakeExaminer("不得再次调用 Provider")
    replay_deps = DurableInterviewGraphDependencies(
        workflow_store=object(),
        generation_store=generations,
        examiner=replay_examiner,
    )
    replay = _state()
    replay.update(prepare_main_question(replay, replay_deps))
    replay.update(generate_main_question_node(replay, replay_deps))
    replay.update(commit_rendered_main_question(replay))

    assert replay_examiner.calls == []
    assert {
        key: replay["rendered_questions"]["q1"][key]
        for key in expected
    } == expected
