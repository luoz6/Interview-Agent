from __future__ import annotations

import pytest

from app.a2a.cards import EXAMINER_AGENT_CARD
from app.a2a.client import InProcessA2AClient
from app.a2a.comparison import (
    DeterministicArtifactComparator,
    DualPathRunner,
)
from app.a2a.contracts import A2AAgentError, FollowupArtifactPayload
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.server import LocalA2AServer
from app.a2a.protocol import A2ATask


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
    task = A2ATask(task_id="task-1", agent_id="interview-examiner", skill="generate-followup")
    result = server.cancel("task-1", reason="user_canceled")
    assert result.task.status == "canceled"
    assert result.task.task_id == "task-1"
