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
        agent_id="generic-agent",
        skill="echo",
        request={"a": {"x": [1, 2]}, "b": 2},
        invocation_context=InvocationContext(session_id="s1"),
    )
    right = build_agent_idempotency_key(
        agent_id="generic-agent",
        skill="echo",
        request={"b": 2, "a": {"x": [1, 2]}},
        invocation_context=InvocationContext(session_id="s1"),
    )
    assert left == right


def test_idempotency_key_falls_back_to_context_id():
    left = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1", "policy_version": "adaptive_v1"},
        invocation_context=InvocationContext(context_id="context-1"),
    )
    same_context = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1", "policy_version": "adaptive_v1"},
        invocation_context=InvocationContext(context_id="context-1"),
    )
    different_context = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1", "policy_version": "adaptive_v1"},
        invocation_context=InvocationContext(context_id="context-2"),
    )
    assert left == same_context
    assert left != different_context


def test_new_command_generates_new_key():
    left = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1", "policy_version": "adaptive_v1"},
        invocation_context=InvocationContext(
            session_id="s1",
            state_version=2,
            command_id="cmd-1",
        ),
    )
    right = build_agent_idempotency_key(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1", "policy_version": "adaptive_v1"},
        invocation_context=InvocationContext(
            session_id="s1",
            state_version=2,
            command_id="cmd-2",
        ),
    )
    assert left != right


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


def test_unsupported_skill_returns_failed_task():
    server = LocalA2AServer()
    result = server.submit(
        A2ATask(
            task_id="missing-skill",
            agent_id="unknown-agent",
            skill="unknown-skill",
        )
    )
    assert result.task.status == "failed"
    assert result.task.error is not None
    assert result.task.error.code == "unsupported_skill"


def test_same_key_working_does_not_reexecute():
    server = LocalA2AServer()
    started = Event()
    release = Event()
    calls = []

    def blocking_handler(request, execution_context):
        calls.append(1)
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
    results = []
    thread = Thread(
        target=lambda: results.append(
            server.submit(
                A2ATask(
                    task_id="task-1",
                    agent_id="interview-examiner",
                    skill="generate-followup",
                    input={"question_id": "q1"},
                    idempotency_key="key-working",
                )
            )
        )
    )
    thread.start()
    started.wait(timeout=5)

    replay = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-working",
        )
    )
    assert replay.task.status == "working"
    assert len(calls) == 1

    release.set()
    thread.join(timeout=5)
    assert results[0].task.status == "completed"
    assert len(calls) == 1


def test_retryable_failure_advances_attempt_to_2():
    server = LocalA2AServer()
    calls = []

    def handler(request, execution_context):
        calls.append(1)
        if len(calls) == 1:
            raise A2AAgentError(
                code="provider_timeout",
                retryable=True,
                terminal=False,
                fallback_allowed=True,
                public_message="Retry later.",
                internal_reason="transient provider timeout",
            )
        return FollowupArtifactPayload(
            question_id="q1",
            followup_text="second attempt",
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
            idempotency_key="key-retryable",
        )
    )
    second = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-retryable",
        )
    )
    assert first.task.status == "failed"
    assert second.task.status == "completed"
    assert second.task.task_id == first.task.task_id
    assert second.task.attempts == 2
    assert len(calls) == 2


def test_terminal_failure_does_not_retry():
    server = LocalA2AServer()
    calls = []

    def handler(request, execution_context):
        calls.append(1)
        raise A2AAgentError(
            code="artifact_validation_failed",
            retryable=False,
            terminal=True,
            fallback_allowed=False,
            public_message="Invalid artifact.",
            internal_reason="terminal validation failure",
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
            idempotency_key="key-terminal",
        )
    )
    second = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-terminal",
        )
    )
    assert first.task.status == "failed"
    assert second.task.status == "failed"
    assert second.task.task_id == first.task.task_id
    assert second.task.attempts == 1
    assert len(calls) == 1


def test_canceled_same_key_does_not_restart():
    server = LocalA2AServer()
    started = Event()
    release = Event()
    calls = []

    def blocking_handler(request, execution_context):
        calls.append(1)
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
    results = []
    thread = Thread(
        target=lambda: results.append(
            server.submit(
                A2ATask(
                    task_id="task-1",
                    agent_id="interview-examiner",
                    skill="generate-followup",
                    input={"question_id": "q1"},
                    idempotency_key="key-canceled",
                )
            )
        )
    )
    thread.start()
    started.wait(timeout=5)
    server.cancel("task-1", reason="user_canceled")
    release.set()
    thread.join(timeout=5)

    replay = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-canceled",
        )
    )
    assert replay.task.status == "canceled"
    assert replay.task.task_id == "task-1"
    assert len(calls) == 1


def test_duplicate_cancel_is_idempotent():
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
    results = []
    thread = Thread(
        target=lambda: results.append(
            server.submit(
                A2ATask(
                    task_id="task-1",
                    agent_id="interview-examiner",
                    skill="generate-followup",
                    input={"question_id": "q1"},
                )
            )
        )
    )
    thread.start()
    started.wait(timeout=5)
    first_cancel = server.cancel("task-1", reason="user_canceled").task
    second_cancel = server.cancel("task-1", reason="duplicate_cancel").task
    release.set()
    thread.join(timeout=5)
    assert first_cancel.status == "canceled"
    assert second_cancel.status == "canceled"
    assert second_cancel.task_id == first_cancel.task_id
    assert results[0].task.status == "canceled"


def test_completed_task_cannot_be_canceled():
    server = LocalA2AServer()

    def handler(request, execution_context):
        return FollowupArtifactPayload(
            question_id="q1",
            followup_text="completed result",
            reason_code="gap",
            policy_version="adaptive_v1",
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=handler,
    )
    completed = server.submit(
        A2ATask(
            task_id="task-1",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
        )
    ).task
    canceled = server.cancel("task-1", reason="user_canceled").task
    assert canceled.status == "completed"
    assert canceled.output_artifact is not None
    assert canceled.task_id == completed.task_id


def test_failed_task_cannot_be_canceled():
    server = LocalA2AServer()

    def handler(request, execution_context):
        raise A2AAgentError(
            code="domain_validation_failed",
            retryable=False,
            terminal=True,
            fallback_allowed=False,
            public_message="Invalid domain input.",
            internal_reason="failed task cannot be canceled",
        )

    server.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=handler,
    )
    failed = server.submit(
        A2ATask(
            task_id="task-1",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
        )
    ).task
    canceled = server.cancel("task-1", reason="user_canceled").task
    assert canceled.status == "failed"
    assert canceled.error is not None
    assert canceled.task_id == failed.task_id


def test_generic_exception_retry_preserves_logical_task_id():
    server = LocalA2AServer()
    calls = []

    def handler(request, execution_context):
        calls.append(1)
        if len(calls) == 1:
            raise A2AAgentError(
                code="provider_timeout",
                retryable=True,
                terminal=False,
                fallback_allowed=True,
                public_message="Retry later.",
                internal_reason="transient provider timeout",
            )
        raise RuntimeError("unexpected provider crash")

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
            idempotency_key="key-lineage",
        )
    )
    second = server.submit(
        A2ATask(
            task_id="task-2",
            agent_id="interview-examiner",
            skill="generate-followup",
            input={"question_id": "q1"},
            idempotency_key="key-lineage",
        )
    )
    assert first.task.status == "failed"
    assert second.task.status == "failed"
    assert second.task.task_id == first.task.task_id
    assert "task-1" in server._tasks
    assert "task-2" not in server._tasks
    assert len(calls) == 2


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
