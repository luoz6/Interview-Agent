from uuid import uuid4

import pytest

from app.services.decision_store import DecisionStoreConflict
from app.services.postgres_decision_store import PostgresDecisionStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from tests.postgres_support import require_postgres_dsn


pytestmark = pytest.mark.pg_runtime


def plan():
    return InterviewPlan(
        title="Decision test",
        questions=[
            InterviewQuestion(id="q1", kind="technical", prompt="Explain queues.", focus="queues")
        ],
    )


def test_postgres_decision_unique_prepare_lease_fencing_and_retry():
    dsn = require_postgres_dsn()
    prefix = "test_decision_" + uuid4().hex[:10]
    sessions = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    session = sessions.start(plan(), job_description="role", resume_text="resume", job_tags=[])
    store = PostgresDecisionStore(dsn=dsn, table_prefix=prefix, max_attempts=2)
    record = store.prepare(session_id=session.session_id, source_command_id="cmd-1", input_sha256="a" * 64)
    assert store.prepare(session_id=session.session_id, source_command_id="cmd-1", input_sha256="a" * 64).decision_id == record.decision_id
    first = store.claim(record.decision_id, worker_id="w1")
    assert store.heartbeat(first.attempt_id, worker_id="w1", lease_token=first.lease_token)
    store.fail(first.attempt_id, worker_id="w1", lease_token=first.lease_token, error_code="timeout")
    second = store.claim(record.decision_id, worker_id="w2")
    from app.services.decision_store import DecisionContract

    final = DecisionContract(
        action="next_question",
        answer_state="complete",
        gap_type="none",
        gap_summary="",
        reason_code="complete",
        decision_confidence="high",
        policy_version="fixed_v1",
    )
    with pytest.raises(DecisionStoreConflict):
        store.complete(first.attempt_id, worker_id="w1", lease_token=first.lease_token, decision=final)
    completed = store.complete(second.attempt_id, worker_id="w2", lease_token=second.lease_token, decision=final)
    assert completed.status == "completed"
    assert store.get(record.decision_id).final_decision.action == "next_question"
