import os
from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    KnowledgeQuerySnapshot,
    PrepContext,
    PrepQuestionHint,
    RoleProfile,
)
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)
from app.services.session_errors import SessionVersionConflict
from tests.test_knowledge_binding_resolver import (
    make_repository as make_binding_repository,
    make_v2_plan as make_bound_v2_plan,
)


pytestmark = pytest.mark.pg_runtime


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_runtime tests")
    return dsn


def make_table_prefix():
    return "test_runtime_" + uuid4().hex[:12]


def test_schema_initializes_runtime_tables():
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )

    tables = store.list_runtime_tables()

    assert set(tables) == {
        store.sessions_table,
        store.messages_table,
        store.reports_table,
        store.question_evaluations_table,
    }


def make_plan():
    return InterviewPlan(
        title="Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="Project depth",
            )
        ],
    )


def make_v2_plan():
    content_hash = "a" * 64
    manifest_hash = "b" * 64
    return InterviewPlan(
        title="Grounded Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis consistency.",
                focus="Redis consistency",
            )
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Retrieved one grounded topic.",
            knowledge_status="completed",
            role_profile=RoleProfile(
                role_title="Backend Engineer",
                canonical_tags=["redis"],
                technologies=["Redis"],
            ),
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="redis-consistency",
                    title="Redis consistency",
                    domain="redis",
                    source_type="theory",
                    score=0.91,
                    content_sha256=content_hash,
                    corpus_manifest_sha256=manifest_hash,
                    candidate_summary="Grounding for cache consistency trade-offs.",
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["redis-consistency"],
                )
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-postgres-round-trip",
                corpus_manifest_sha256=manifest_hash,
                status="completed",
                queries=[
                    KnowledgeQuerySnapshot(
                        query_id="query-redis",
                        topic_id="topic-redis",
                        filters={"tags": ["redis"]},
                        top_k=3,
                        hit_ids=["redis-consistency"],
                        hit_content_sha256={"redis-consistency": content_hash},
                    )
                ],
            ),
        ),
    )


def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=80,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid interview.",
        highlights=["Explained project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI API.",
                score=80,
                dimension_scores=make_dimension_scores(),
                rationale="The answer covered the project shape.",
                critique="Needs more failure-mode detail.",
                better_answer="Explain traffic, storage, caching, and failure handling.",
                references=[],
            )
        ],
    )


def make_feedback(
    *,
    question_id: str = "q1",
    score: int = 78,
    answer_state: str = "answered",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"Describe work for {question_id}.",
        user_answer=f"I answered {question_id}.",
        answer_state=answer_state,
        score=score,
        dimension_scores=make_dimension_scores(score),
        rationale="The answer gave implementation context.",
        critique="Business impact was thin.",
        better_answer="Tie the API work to latency and reliability outcomes.",
        references=[],
    )


def test_started_session_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)

    assert state["session_id"] == turn.session_id
    assert state["plan"].title == "Backend Interview"
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["content"] == "Describe your backend project."
    assert state["job_tags"] == ["python", "fastapi"]


def test_v2_knowledge_binding_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_v2_plan(),
        job_description="Redis backend role",
        resume_text="Built cache services",
        job_tags=["redis"],
    )

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix).get(
        turn.session_id
    )
    context = recovered["plan"].prep_context

    assert context.schema_version == "v2"
    assert context.evidence_refs[0].content_sha256 == "a" * 64
    assert context.evidence_refs[0].corpus_manifest_sha256 == "b" * 64
    assert context.binding_snapshot.prep_run_id == "prep-postgres-round-trip"
    assert context.binding_snapshot.queries[0].hit_content_sha256 == {
        "redis-consistency": "a" * 64
    }


def test_v2_examiner_uses_same_bound_ids_after_postgres_store_reinstantiation():
    class CapturingLLM:
        def __init__(self):
            self.context = None

        def generate_followup(self, context):
            self.context = context
            return "How do you handle the concurrent read race?"

    dsn = require_dsn()
    table_prefix = make_table_prefix()
    repository = make_binding_repository()
    store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        knowledge_repository=repository,
    )
    turn = store.start(
        make_bound_v2_plan(),
        job_description="Redis and Kafka role",
        resume_text="Built Redis and Kafka services",
        job_tags=["redis", "kafka"],
    )
    llm = CapturingLLM()

    recovered = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=llm,
        knowledge_repository=repository,
    )
    answered = recovered.submit_answer(
        turn.session_id,
        "I update the database and delete the cache.",
    )

    assert answered.follow_up == "How do you handle the concurrent read race?"
    assert repository.get_by_ids_calls == [
        {"ids": ["redis_consistency"], "expected_hashes": {"redis_consistency": "a" * 64}}
    ]
    assert repository.search_calls == 0
    assert any(item["role"] == "knowledge_evidence" for item in llm.context)
    assert "Kafka internal delivery evidence" not in str(llm.context)


def test_snapshot_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["session_id"] == turn.session_id
    assert snapshot["status"] == "active"
    assert snapshot["job_tags"] == ["python", "fastapi"]
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["messages"][0]["content"] == "Describe your backend project."


def test_skip_persists_next_question_snapshot():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        InterviewPlan(
            title="Two question interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe a backend project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis consistency.",
                    focus="redis",
                ),
            ],
        ),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "redis"],
    )

    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["status"] == "active"
    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["questions"][1]["state"] == "current"
    assert snapshot["messages"][-1]["content"] == "Explain Redis consistency."


def test_skip_metadata_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built Redis APIs.",
        job_tags=["python", "redis"],
    )
    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert state["skipped_question_ids"] == ["q1"]
    assert state["started_at"]
    assert state["finished_at"] is not None
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["skipped_questions"] == 1


def test_submit_answer_persists_candidate_and_followup_messages():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    answered = store.submit_answer(turn.session_id, "I built a FastAPI API.")

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    assert answered.follow_up is not None
    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][1]["content"] == "I built a FastAPI API."
    assert state["messages"][2]["content"] == answered.follow_up


def test_submit_answer_passes_current_command_id_to_orchestrator(monkeypatch):
    dsn = require_dsn()
    store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=make_table_prefix(),
    )
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    captured_commands = []
    apply_command = store._orchestrator.apply_command

    def capture_command(state, command):
        captured_commands.append(command.copy())
        return apply_command(state, command)

    monkeypatch.setattr(store._orchestrator, "apply_command", capture_command)

    store.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-current",
    )

    assert captured_commands == [
        {
            "kind": "answer",
            "answer": "I built a FastAPI API.",
            "command_id": "cmd-current",
        }
    ]


def test_streaming_prepare_and_complete_are_persisted_once():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    prepared = store.prepare_streaming_answer(turn.session_id, "I built APIs.")
    assert prepared.stream_follow_up is True
    assert store._runtime_control.list_outbox(
        session_id=turn.session_id
    ) == []

    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )
    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )

    recovered = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get(turn.session_id)

    assert [message["role"] for message in recovered["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert recovered["messages"][-1]["content"] == "Which failure mode did you handle?"
    assert store._runtime_control.list_outbox(
        session_id=turn.session_id
    ) == []


def test_closed_round_commits_one_outbox_event():
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    store.skip(
        turn.session_id,
        expected_version=1,
        command_id="cmd-skip",
    )

    events = store._runtime_control.list_outbox(
        session_id=turn.session_id
    )
    assert len(events) == 1
    assert events[0]["payload"]["causation_id"] == "cmd-skip"
    assert events[0]["payload"]["state_version"] == 2
    assert events[0]["payload"]["question_id"] == "q1"


def test_outbox_failure_rolls_back_state_and_messages(monkeypatch):
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    original_messages = store.list_messages(turn.session_id)
    monkeypatch.setattr(
        store._runtime_control,
        "enqueue_event",
        lambda cursor, event: (_ for _ in ()).throw(
            RuntimeError("insert failed")
        ),
    )

    with pytest.raises(RuntimeError, match="insert failed"):
        store.skip(
            turn.session_id,
            expected_version=1,
            command_id="cmd-skip",
        )

    snapshot = store.snapshot(turn.session_id)
    assert snapshot["state_version"] == 1
    assert snapshot["status"] == "active"
    assert store.list_messages(turn.session_id) == original_messages
    assert store._runtime_control.list_outbox(
        session_id=turn.session_id
    ) == []


def test_streaming_round_closes_only_during_complete():
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    first = store.prepare_streaming_answer(
        turn.session_id,
        "I built APIs.",
        expected_version=1,
        command_id="cmd-stream-first",
    )
    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
        expected_version=first.state["state_version"],
        command_id="cmd-stream-first",
    )

    prepared = store.prepare_streaming_answer(
        turn.session_id,
        "I added retries and idempotency.",
        expected_version=3,
        command_id="cmd-stream-close",
    )

    assert prepared.state["decision"]["action"] == "finish"
    assert prepared.state["current_index"] == 0
    assert prepared.state["status"] == "active"
    assert store._runtime_control.list_outbox(
        session_id=turn.session_id
    ) == []

    finalized = store.complete_streaming_answer(
        turn.session_id,
        expected_version=prepared.state["state_version"],
        command_id="cmd-stream-close",
    )
    events = store._runtime_control.list_outbox(
        session_id=turn.session_id
    )

    assert finalized["status"] == "finished"
    assert len(events) == 1
    assert events[0]["payload"]["question_id"] == "q1"
    assert events[0]["payload"]["causation_id"] == "cmd-stream-close"


def test_complete_streaming_answer_advances_version_after_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    store.prepare_streaming_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-stream",
    )
    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    finalized = recovered.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Please describe the API boundaries.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate = recovered.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Please describe the API boundaries.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = recovered.snapshot(turn.session_id)

    assert duplicate == finalized
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert len(
        [
            message
            for message in snapshot["messages"]
            if message["role"] == "interviewer"
            and message["content"] == "Please describe the API boundaries."
        ]
    ) == 1


def test_duplicate_command_id_is_idempotent_after_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    first = store.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    duplicate = recovered.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )
    snapshot = recovered.snapshot(turn.session_id)

    assert duplicate.follow_up == first.follow_up
    assert snapshot["state_version"] == 2
    assert snapshot["checkpoint_version"] == 2
    assert snapshot["last_command_id"] == "cmd-1"
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1


def test_replace_state_rejects_stale_previous_version():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    stale_state = store.get(turn.session_id)
    store.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )
    stale_state["messages"].append(
        {
            "role": "candidate",
            "content": "This stale write must not win.",
            "question_id": "q1",
        }
    )
    stale_state["state_version"] = 2
    stale_state["checkpoint_version"] = 2

    with pytest.raises(SessionVersionConflict) as exc:
        store._replace_state(stale_state, expected_previous_version=1)

    assert exc.value.expected_version == 1
    assert exc.value.actual_version == 2


def finish_session(store, session_id):
    store.submit_answer(session_id, "First answer.")
    store.submit_answer(session_id, "Second answer.")


def test_report_lifecycle_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, turn.session_id)

    assert store.mark_report_processing(turn.session_id) is True
    store.update_report_progress(
        turn.session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing answers.",
            current_question_id="q1",
        ),
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    record = recovered_store.get_report_record(turn.session_id)

    assert record is not None
    assert record.status == "processing"
    assert record.progress is not None
    assert record.progress.percent == 60

    recovered_store.fail_report(turn.session_id, "retrieval unavailable")
    failed = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get_report_record(turn.session_id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "retrieval unavailable"


def test_phase_metadata_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    store.finish(turn.session_id, expected_version=1, command_id="cmd-finish")
    store.mark_report_processing(turn.session_id)

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "active"
    assert snapshot["review_status"] == "processing"
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-finish"


def test_postgres_save_report_updates_review_phase_completed():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    store.finish(turn.session_id, expected_version=1, command_id="cmd-finish")
    store.mark_report_processing(turn.session_id)
    store.save_report(turn.session_id, make_report(turn.session_id))

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "completed"
    assert snapshot["review_status"] == "completed"
    assert snapshot["last_command_id"] == "cmd-finish"


def test_postgres_fail_report_updates_review_phase_failed():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    store.finish(turn.session_id, expected_version=1, command_id="cmd-finish")
    store.mark_report_processing(turn.session_id)
    store.fail_report(turn.session_id, "retrieval unavailable")

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "failed"
    assert snapshot["review_status"] == "failed"
    assert snapshot["last_command_id"] == "cmd-finish"


def test_list_reports_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    completed = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    processing = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, completed.session_id)
    finish_session(store, processing.session_id)

    store.mark_report_processing(completed.session_id)
    store.save_report(completed.session_id, make_report(completed.session_id))
    store.mark_report_processing(processing.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    reports = recovered_store.list_reports(limit=10)
    completed_reports = recovered_store.list_reports(status="completed", limit=10)

    assert [item["session_id"] for item in reports] == [
        processing.session_id,
        completed.session_id,
    ]
    assert [item["record"].status for item in reports] == [
        "processing",
        "completed",
    ]
    assert len(completed_reports) == 1
    assert completed_reports[0]["session_id"] == completed.session_id
    assert completed_reports[0]["record"].report.summary == "Solid interview."


def test_postgres_store_upserts_single_question_evaluation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    first = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(score=78),
    )
    replacement = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(score=91),
    )

    store.upsert_question_evaluation(turn.session_id, first)
    initial_created_at = store.list_question_evaluations(turn.session_id)[0].created_at
    store.upsert_question_evaluation(turn.session_id, replacement)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    saved = recovered_store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"
    assert saved[0].answer_state == "answered"
    assert saved[0].feedback.score == 91
    assert saved[0].created_at == initial_created_at


def test_postgres_store_bulk_save_merges_existing_question_evaluations():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    q1_initial = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q1", score=71),
    )
    q2_initial = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q2", score=64),
    )
    q1_final = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q1", score=90),
    )

    store.upsert_question_evaluation(turn.session_id, q1_initial)
    store.upsert_question_evaluation(turn.session_id, q2_initial)
    q1_created_at = {
        record.question_id: record.created_at
        for record in store.list_question_evaluations(turn.session_id)
    }["q1"]
    store.save_question_evaluations(turn.session_id, [q1_final])

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    saved = {
        record.question_id: record
        for record in recovered_store.list_question_evaluations(turn.session_id)
    }
    assert set(saved) == {"q1", "q2"}
    assert saved["q1"].feedback.score == 90
    assert saved["q2"].feedback.score == 64
    assert saved["q1"].created_at == q1_created_at


def test_submit_answer_appends_new_messages_without_rewriting_existing_rows():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    first_snapshot = store.list_messages(turn.session_id)
    assert len(first_snapshot) == 1
    first_id = first_snapshot[0]["id"]

    store.submit_answer(turn.session_id, "I built a FastAPI API.")

    second_snapshot = store.list_messages(turn.session_id)

    assert len(second_snapshot) == 3
    assert second_snapshot[0]["id"] == first_id
    assert second_snapshot[0]["sequence_no"] == 1
    assert second_snapshot[1]["sequence_no"] == 2
    assert second_snapshot[2]["sequence_no"] == 3
