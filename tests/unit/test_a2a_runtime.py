from __future__ import annotations

import pytest

from app.a2a.cards import EXAMINER_AGENT_CARD
from app.a2a.cards import (
    KNOWLEDGE_AGENT_CARD,
    REVIEWER_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
)
from app.a2a.client import InProcessA2AClient
from app.a2a.comparison import (
    DeterministicArtifactComparator,
    DualPathRunner,
)
from app.a2a.contracts import A2AAgentError, FollowupArtifactPayload
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.server import LocalA2AServer
from app.a2a.protocol import A2ATask
from app.a2a.bridge import ExaminerAgentBridge
from app.a2a.official_cards import OFFICIAL_AGENT_CARDS
from app.a2a.invocation.context import InvocationContext
from app.a2a.invocation.execution_context import build_agent_execution_context
from app.services.agent_runtime import AgentExecutionContext


def make_followup_artifact():
    return FollowupArtifactPayload(
        question_id="q1",
        followup_text="请补充一个关键取舍。",
        reason_code="gap",
        policy_version="adaptive_v1",
    )


def test_local_invoker_rejects_unregistered_skill():
    invoker = LocalAgentInvoker()
    with pytest.raises(A2AAgentError):
        invoker.invoke(
            agent_id="interview-examiner",
            skill="generate-followup",
            request={},
        )


def test_a2a_server_completes_task_with_domain_artifact():
    server = LocalA2AServer()
    artifact = make_followup_artifact()
    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: artifact,
    )
    client = InProcessA2AClient(server=server)

    result = client.send_task(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1"},
    )

    assert result == artifact
    snapshot = server.observability.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["status"] == "completed"
    assert snapshot[0]["output_artifact_type"] == "followup-artifact"


def test_deterministic_comparator_ignores_transport_metadata():
    local = make_followup_artifact()
    remote = local.model_copy(update={"created_at": "2099-01-01T00:00:00Z"})
    result = DeterministicArtifactComparator().compare(local, remote)
    assert result.matches is True
    assert result.differences == []


def test_registered_server_skills_match_agent_card():
    server = LocalA2AServer()
    artifact = make_followup_artifact()
    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: artifact,
    )
    registered = {
        (agent_id, skill)
        for agent_id, skill in server.registered_skills
    }
    card_skills = {
        ("interview-examiner", skill.name)
        for skill in EXAMINER_AGENT_CARD.skills
    }
    assert card_skills == registered


def test_dual_path_runner_executes_same_request_on_both_paths():
    server = LocalA2AServer()
    artifact = make_followup_artifact()
    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: make_followup_artifact(),
    )
    local = LocalAgentInvoker()
    for agent_id, skill in server.registered_skills:
        handler = server.get_handler(agent_id=agent_id, skill=skill)
        if handler is not None:
            local.register(agent_id=agent_id, skill=skill, handler=handler)
    client = InProcessA2AClient(server=server)
    from app.a2a.invocation.a2a import A2AAgentInvoker

    result = DualPathRunner(
        local=local,
        a2a=A2AAgentInvoker(client=client),
    ).run(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1"},
    )
    assert result.comparison.matches is True
    assert result.local.artifact_type == result.a2a.artifact_type


def test_idempotency_reuses_completed_task():
    server = LocalA2AServer()
    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: make_followup_artifact(),
    )
    first = server.submit(
        A2ATask(
            task_id="task-1",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="exam:q1:1",
        )
    )
    second = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="exam:q1:1",
        )
    )
    assert second.task.task_id == first.task.task_id
    assert second.task.status == "completed"


def test_cancel_updates_existing_task():
    server = LocalA2AServer()
    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: make_followup_artifact(),
    )
    server.submit(
        A2ATask(
            task_id="task-1",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
        )
    )
    task = A2ATask(task_id="task-1", agent_id="interview-examiner", skill="generate-followup")
    result = server.cancel("task-1", reason="user_canceled")
    assert result.task.status == "completed"
    assert result.task.task_id == "task-1"


def test_cancel_unknown_task_raises():
    server = LocalA2AServer()
    with pytest.raises(A2AAgentError):
        server.cancel("missing-task", reason="user_canceled")


def test_examiner_bridge_uses_execution_context_question_id():
    invoker = LocalAgentInvoker()
    invoker.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: FollowupArtifactPayload(
            question_id=request["question_id"],
            followup_text="请补充一个关键取舍。",
            reason_code="gap",
            policy_version="adaptive_v1",
        ),
    )
    bridge = ExaminerAgentBridge(invoker)
    context = AgentExecutionContext(
        correlation_id="corr-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        question_id="q1",
        evidence_ids=["e1"],
    )
    text = bridge.generate_followup(
        context=[],
        focus="depth",
        execution_context=context,
    )
    assert text == "请补充一个关键取舍。"


def test_examiner_bridge_fails_without_question_id():
    invoker = LocalAgentInvoker()
    bridge = ExaminerAgentBridge(invoker)
    context = AgentExecutionContext(
        correlation_id="corr-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
    )
    with pytest.raises(A2AAgentError):
        bridge.generate_followup(context=[], focus="depth", execution_context=context)


def test_official_cards_match_registered_agent_ids():
    assert set(OFFICIAL_AGENT_CARDS) == {
        "interview-examiner",
        "knowledge-and-grounding",
        "interview-reviewer",
        "report-coach",
    }


def test_official_agent_cards_expose_independent_skills():
    assert [skill.name for skill in EXAMINER_AGENT_CARD.skills] == [
        "generate-followup"
    ]
    assert [skill.name for skill in KNOWLEDGE_AGENT_CARD.skills] == [
        "generate-interview-plan"
    ]
    assert [skill.name for skill in REVIEWER_AGENT_CARD.skills] == [
        "evaluate-answer",
        "evaluate-interview",
    ]
    assert [skill.name for skill in REPORT_COACH_AGENT_CARD.skills] == [
        "generate-report"
    ]


def test_invocation_context_uses_session_as_a2a_context_id():
    context = AgentExecutionContext(
        correlation_id="prep-123",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="session-1",
    )
    invocation = InvocationContext.from_execution_context(context)
    assert invocation.context_id == "session-1"
    assert invocation.correlation_id == "prep-123"


def test_invocation_context_falls_back_to_correlation_without_session():
    context = AgentExecutionContext(
        correlation_id="prep-123",
        agent="knowledge",
        operation="generate_plan",
        phase="prep",
    )
    invocation = InvocationContext.from_execution_context(context)
    assert invocation.context_id == "prep-123"


def test_invocation_context_builds_examiner_execution_context():
    context = InvocationContext(
        context_id="session-1",
        correlation_id="corr-1",
        causation_id="cmd-parent",
        command_id="cmd-1",
        question_id="q1",
        evidence_ids=["e1", "e2"],
    )
    execution = build_agent_execution_context(
        agent_id="interview-examiner",
        skill="generate-followup",
        invocation_context=context,
        request={},
    )
    assert execution.agent == "examiner"
    assert execution.operation == "generate_followup"
    assert execution.phase == "interview"
    assert execution.session_id == "session-1"
    assert execution.question_id == "q1"
    assert execution.correlation_id == "corr-1"
    assert execution.command_id == "cmd-1"
    assert execution.evidence_ids == ["e1", "e2"]


def test_invocation_context_builds_reviewer_execution_context():
    execution = build_agent_execution_context(
        agent_id="interview-reviewer",
        skill="evaluate-interview",
        invocation_context=InvocationContext(context_id="session-1"),
        request={},
    )
    assert execution.agent == "shadow_reviewer"
    assert execution.phase == "review"
    assert execution.session_id == "session-1"


def test_local_invoker_builds_execution_context_from_invocation_context():
    invoker = LocalAgentInvoker()
    seen = {}

    def handler(request, execution_context):
        seen["execution_context"] = execution_context
        return make_followup_artifact()

    invoker.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=handler,
    )
    invoker.invoke(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1"},
        invocation_context=InvocationContext(
            context_id="session-1",
            correlation_id="corr-1",
        ),
    )
    execution = seen["execution_context"]
    assert execution is not None
    assert execution.agent == "examiner"
    assert execution.session_id == "session-1"
