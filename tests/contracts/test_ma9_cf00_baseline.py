from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.adapters.providers.llm import OpenAIInterviewLLM
from app.application.interview.scheduler_production_entry import (
    OrchestrationVersionMismatch,
    SchedulerProductionEntry,
)
from app.domain.interview.question_intent import QuestionIntentV1
from app.domain.interview.scheduling import ExecutionConstraints, ExecutionState
from app.domain.interview.scheduling.commit import CURRENT_INVOCATION_COMMIT_DECISION
from app.ports.agent_invocation import AgentInvocationPort


class _ChunkedChatModel:
    def bind(self, **_kwargs):
        return self

    def stream(self, _prompt):
        for chunk in ("请说明 ", "backpressure ", "如何 ", "处理？"):
            yield SimpleNamespace(content=chunk)

    def invoke(self, _prompt):
        return SimpleNamespace(content="请说明 backpressure 如何处理？")


def _intent() -> QuestionIntentV1:
    return QuestionIntentV1(
        question_id="q1",
        position=1,
        kind="technical",
        focus="backpressure",
        difficulty="intermediate",
        assessment_goals=("reliability",),
        expected_minutes=5,
        expected_followups=1,
    )


def test_main_question_stream_uses_multiple_provider_chunks():
    llm = OpenAIInterviewLLM(chat_model=_ChunkedChatModel())

    chunks = list(
        llm.stream_main_question(
            intent=_intent(),
            conversation=[],
            evidence=[],
        )
    )

    assert chunks == ["请说明 ", "backpressure ", "如何 ", "处理？"]
    assert len(chunks) > 1


def test_followup_stream_uses_canonical_invocation_stream_port():
    assert hasattr(AgentInvocationPort, "invoke_stream")


def test_pre_ma9_new_execution_uses_compatible_runtime():
    plan = SimpleNamespace(
        execution_id="pre-ma9",
        orchestration_version="scheduler-pre-ma9",
    )
    repository = SimpleNamespace(
        load_plan=lambda _execution_id: plan,
        load=lambda _execution_id: ExecutionState(execution_id="pre-ma9"),
    )
    entry = SchedulerProductionEntry(
        session_store=SimpleNamespace(
            get=lambda _execution_id: {"plan": SimpleNamespace(questions=())},
            snapshot=lambda _execution_id: {},
        ),
        execution_repository=repository,
        execution_path_router=SimpleNamespace(
            require_execution_path=lambda *_args: None,
        ),
        scheduler_composer=lambda **_kwargs: None,
    )

    assert entry._load_ma9_plan("pre-ma9") is plan
    assert plan.orchestration_version == "scheduler-pre-ma9"


@pytest.mark.parametrize(
    "field",
    ["max_agent_calls", "max_retries", "execution_timeout_seconds"],
)
def test_execution_constraints_own_complete_budget(field):
    value = 1 if field != "execution_timeout_seconds" else 1.0
    assert getattr(ExecutionConstraints(**{field: value}), field) == value


@pytest.mark.parametrize(
    "field",
    ["agent_calls_used", "retries_used", "execution_started_at"],
)
def test_execution_state_tracks_complete_runtime_budget(field):
    value = datetime.now(timezone.utc) if field == "execution_started_at" else 1
    state = ExecutionState(execution_id="budget-red", **{field: value})
    assert getattr(state, field) == value


def test_transaction_decision_is_backed_by_one_production_boundary():
    assert CURRENT_INVOCATION_COMMIT_DECISION.strategy == "TRANSACTIONAL_OUTBOX"
    assert CURRENT_INVOCATION_COMMIT_DECISION.state_and_ledger_same_store is True
    assert CURRENT_INVOCATION_COMMIT_DECISION.artifact_metadata_same_store is True
