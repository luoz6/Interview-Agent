from __future__ import annotations

import pytest
from threading import Event, Thread

from app.a2a.contracts import A2AAgentError, FollowupArtifactPayload
from app.a2a.idempotency import build_agent_idempotency_key
from app.a2a.invocation.context import InvocationContext
from app.a2a.protocol import A2ATask
from app.a2a.server import LocalA2AServer


def test_stable_idempotency_key_is_canonical():
    left = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"a": 1, "b": 2},
        invocation_context=InvocationContext(session_id="s1"),
    )
    right = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"b": 2, "a": 1},
        invocation_context=InvocationContext(session_id="s1"),
    )
    assert left == right


def test_different_state_version_produces_different_key():
    left = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={},
        invocation_context=InvocationContext(session_id="s1", state_version=2),
    )
    right = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={},
        invocation_context=InvocationContext(session_id="s1", state_version=3),
    )
    assert left != right


def test_completed_idempotent_task_replays_without_reexecution():
    server = LocalA2AServer()
    calls = []

    def handler(request, execution_context):
        calls.append(1)
        return FollowupArtifactPayload(
            question_id="q1",
            followup_text="追问",
            reason_code="gap",
            policy_version="adaptive_v1",
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=handler,
    )
    first = server.submit(
        A2ATask(
            task_id="task-1",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-1",
        )
    )
    second = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-1",
        )
    )
    assert first.task.status == "completed"
    assert second.task.task_id == first.task.task_id
    assert len(calls) == 1


def test_cancel_unknown_task_raises():
    server = LocalA2AServer()
    with pytest.raises(A2AAgentError):
        server.cancel("missing", reason="user_canceled")


def test_late_result_does_not_complete_canceled_task():
    server = LocalA2AServer()
    started = Event()
    release = Event()

    def blocking_handler(request, execution_context):
        started.set()
        release.wait(timeout=5)
        return FollowupArtifactPayload(
            question_id="q1",
            followup_text="late result",
            reason_code="gap",
            policy_version="adaptive_v1",
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=blocking_handler,
    )
    task = A2ATask(
        task_id="task-1",
        agent_id="interview-examiner",
        skill="generate-followup",
        input={"question_id": "q1"},
    )
    results = []
    thread = Thread(target=lambda: results.append(server.submit(task)))
    thread.start()
    started.wait(timeout=5)
    canceled = server.cancel("task-1", reason="user_canceled").task
    release.set()
    thread.join(timeout=5)
    completed = results[0].task
    assert canceled.status == "canceled"
    assert completed.status == "canceled"
    assert completed.output_artifact is None
