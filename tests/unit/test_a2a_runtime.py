from __future__ import annotations

import pytest

from app.a2a.client import InProcessA2AClient
from app.a2a.comparison import DeterministicArtifactComparator
from app.a2a.contracts import A2AAgentError, FollowupArtifactPayload
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.server import LocalA2AServer


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
