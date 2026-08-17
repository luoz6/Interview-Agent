from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.knowledge.evidence import (
    BaseEvidenceBundle,
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceDecision,
    EvidenceRef,
    EvidenceSufficiency,
    QuestionEvidenceBinding,
)
from app.domain.knowledge.source_scope import SelectedUserDocumentRevision
from app.application.interview.session_snapshot import SessionSnapshotProjector
from app.adapters.postgres.row_mappers import (
    MessageRowMapper,
    QuestionEvaluationRowMapper,
    ReportRowMapper,
    SessionRowMapper,
    UnsupportedRowSchemaVersionError,
)
from app.graphs.interview_state import build_initial_state
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
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
    v2_plan_to_legacy,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.session_plan_binding import session_plan_binding_from_revision
from tests.unit.test_interview_plan_revision import plan as revision_plan, source


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
    plan = InterviewPlan(
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
                resume_signals=["Built cache-aside services"],
            ),
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="redis-consistency",
                    title="Redis consistency",
                    domain="redis",
                    source_type="theory",
                    score=0.91,
                    content_sha256="a" * 64,
                    corpus_manifest_sha256="b" * 64,
                    candidate_summary="用于验证缓存一致性取舍。",
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["redis-consistency"],
                )
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-1",
                corpus_manifest_sha256="b" * 64,
                status="completed",
                queries=[
                    KnowledgeQuerySnapshot(
                        query_id="query-redis",
                        topic_id="topic-redis",
                        filters={"tags": ["redis"]},
                        top_k=3,
                        hit_ids=["redis-consistency"],
                        hit_content_sha256={"redis-consistency": "a" * 64},
                    )
                ],
            ),
        ),
    )
    context = plan.prep_context
    bundle = BaseEvidenceBundle(
        bundle_id="bundle-redis",
        retrieval_request_id="retrieval-redis",
        prep_run_id="prep-1",
        query_sha256="c" * 64,
        structured_query_snapshot={
            "queries": [
                {
                    "query_id": "query-redis",
                    "query_sha256": "d" * 64,
                    "filter_keys": ["tags"],
                }
            ]
        },
        candidate_evidence_refs=(
            EvidenceRef(
                evidence_id="redis-consistency",
                title="Redis consistency",
                safe_excerpt="Safe Redis consistency summary.",
                domain="redis",
                source_type="theory",
                content_sha256="a" * 64,
                corpus_manifest_sha256="b" * 64,
            ),
        ),
        retrieval_engine_version="legacy-v1",
        profile_version="prep-v1",
        corpus_manifest_sha256="b" * 64,
    )
    binding = QuestionEvidenceBinding(
        binding_id="question-binding-redis",
        bundle_id=bundle.bundle_id,
        question_id="q1",
        selected_evidence_ids=("redis-consistency",),
        selection_version="question-evidence-selection-v1",
        decision=EvidenceDecision(
            availability=EvidenceAvailability.AVAILABLE,
            sufficiency=EvidenceSufficiency.NOT_EVALUATED,
            evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
            gate_version="retrieval-gate-v1",
        ),
    )
    snapshot = context.binding_snapshot.model_copy(
        update={
            "base_evidence_bundle": bundle,
            "question_evidence_bindings": [binding],
        }
    )
    return plan.model_copy(
        update={"prep_context": context.model_copy(update={"binding_snapshot": snapshot})}
    )


def make_state():
    return build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )


def make_report_record():
    report = InterviewReport(
        session_id="s1",
        overall_score=80,
        overall_dimension_scores=DimensionScores(
            breadth=80,
            depth=78,
            architecture=75,
            engineering=82,
            communication=84,
        ),
        summary="Solid backend project explanation.",
        highlights=["Clear project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI service.",
                score=80,
                dimension_scores=DimensionScores(
                    breadth=80,
                    depth=78,
                    architecture=75,
                    engineering=82,
                    communication=84,
                ),
                rationale="The answer covered project context and implementation.",
                critique="Failure modes need more detail.",
                better_answer="Explain traffic, storage, cache, failure handling, and tradeoffs.",
                references=[
                    FeedbackReference(
                        chunk_id="fastapi_backend",
                        title="FastAPI Backend",
                        source_type="expert_benchmark",
                        excerpt="High quality answers include API boundaries and failure handling.",
                    )
                ],
            )
        ],
    )
    return ReportRecord(status="completed", report=report)


def test_state_round_trips_from_session_and_message_rows():
    state = make_state()
    session_row = SessionRowMapper.to_row(state)
    message_rows = [
        MessageRowMapper.to_row("s1", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]

    restored = SessionRowMapper.from_rows(session_row, message_rows)

    assert restored["session_id"] == "s1"
    assert restored["plan"].questions[0].prompt == "Describe your backend project."
    assert restored["messages"] == state["messages"]
    assert restored["job_tags"] == ["python", "fastapi"]
    assert restored["workflow_engine"] == "legacy"
    assert restored["graph_schema_version"] is None
    assert restored["memory_policy_version"] == "deterministic-v1"
    assert restored["plan_origin"] == "legacy_session_snapshot"
    assert restored["plan_revision_id"] is None
    assert restored["plan_snapshot"] == state["plan_snapshot"]


def test_revision_plan_binding_round_trips_without_reading_latest_revision():
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=revision_plan(),
        retention_policy="test-v1",
        generator_version="plan-generator-v2-test",
    )
    state = build_initial_state(
        session_id="revision-session",
        plan=v2_plan_to_legacy(initial.plan),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=["backend"],
        plan_binding=session_plan_binding_from_revision(initial),
    )
    row = SessionRowMapper.to_row(state)
    messages = [
        MessageRowMapper.to_row("revision-session", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]
    revision_store.create_next_revision(
        plan_family_id=initial.plan_family_id,
        expected_revision=1,
        plan=initial.plan.model_copy(update={"title": "Later revision"}),
        source_kind="edited",
        created_reason="batch_edit",
        generator_version=initial.generator_version,
    )

    restored = SessionRowMapper.from_rows(row, messages)

    assert restored["plan_origin"] == "plan_revision"
    assert restored["plan_revision_id"] == initial.plan_revision_id
    assert restored["revision"] == 1
    assert restored["plan_sha256"] == initial.plan_sha256
    assert restored["plan_snapshot"] == initial.plan.model_dump(mode="json")


def test_scoped_session_binding_round_trips_internal_owner_and_safe_public_scope():
    owner = "principal-a"
    scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=False,
        selected_documents=(
            SelectedUserDocumentRevision(
                document_id="00000000-0000-0000-0000-000000000001",
                document_revision_id="00000000-0000-0000-0000-000000000011",
                content_sha256="a" * 64,
                allowed_usages=("question", "feedback"),
            ),
        ),
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    scoped_plan = revision_plan().model_copy(update={"knowledge_scope": scope})
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=scoped_plan,
        retention_policy="test-v1",
        generator_version="plan-generator-v2-test",
    )
    state = build_initial_state(
        session_id="scoped-revision-session",
        plan=v2_plan_to_legacy(initial.plan),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=["backend"],
        plan_binding=session_plan_binding_from_revision(
            initial,
            owner_principal_id=owner,
        ),
    )
    row = SessionRowMapper.to_row(state)
    messages = [
        MessageRowMapper.to_row("scoped-revision-session", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]

    restored = SessionRowMapper.from_rows(row, messages)
    public = SessionSnapshotProjector().project(restored)

    assert restored["owner_principal_id"] == owner
    assert restored["plan_snapshot"]["knowledge_scope"] == scope.model_dump(
        mode="json"
    )
    assert public["plan_snapshot"]["knowledge_scope"] == {
        "schema_version": "interview-knowledge-scope-v1",
        "include_system_knowledge": False,
        "selected_documents": [
            {"document_id": "00000000-0000-0000-0000-000000000001"}
        ],
    }
    assert "owner_principal_id" not in str(public)
    assert "document_revision_id" not in str(public["plan_snapshot"])
    assert "content_sha256" not in str(public["plan_snapshot"]["knowledge_scope"])
    assert "selection_sha256" not in str(public["plan_snapshot"])
    assert "allowed_usages" not in str(public["plan_snapshot"])
    assert "created_at" not in str(public["plan_snapshot"])


def test_memory_policy_round_trip_and_old_v2_backfill_are_stable():
    state = build_initial_state(
        session_id="s-v2",
        plan=make_plan(),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=[],
        memory_policy_version="question-memory-v1",
    )
    state["workflow_engine"] = "langgraph-v2"
    state["graph_schema_version"] = "langgraph-v2"
    row = SessionRowMapper.to_row(state)

    assert SessionRowMapper.from_rows(row, [])["memory_policy_version"] == "question-memory-v1"

    row.pop("memory_policy_version")
    assert SessionRowMapper.from_rows(row, [])["memory_policy_version"] == (
        "question-conversation-v1"
    )


def test_unsupported_stored_memory_policy_fails_closed():
    row = SessionRowMapper.to_row(make_state())
    row["memory_policy_version"] = "question-memory-v99"

    with pytest.raises(ValueError, match="unsupported stored"):
        SessionRowMapper.from_rows(row, [])


def test_legacy_plan_defaults_to_v1_prep_contract():
    plan = InterviewPlan.model_validate(make_plan().model_dump(mode="json"))

    assert plan.prep_context is None


def test_v2_plan_round_trip_preserves_evidence_hashes_and_binding_snapshot():
    state = build_initial_state(
        session_id="s-v2",
        plan=make_v2_plan(),
        job_description="Redis backend role",
        resume_text="Built cache services",
        job_tags=["redis"],
    )

    row = SessionRowMapper.to_row(state)
    restored = SessionRowMapper.from_rows(row, [])
    context = restored["plan"].prep_context

    assert row["plan_json"]["prep_context"]["schema_version"] == "v2"
    assert context.evidence_refs[0].content_sha256 == "a" * 64
    assert context.binding_snapshot.queries[0].hit_content_sha256 == {
        "redis-consistency": "a" * 64
    }
    assert context.binding_snapshot.base_evidence_bundle.bundle_id == "bundle-redis"
    assert (
        context.binding_snapshot.question_evidence_bindings[0].binding_id
        == "question-binding-redis"
    )
    assert row["plan_json"]["prep_context"]["binding_snapshot"][
        "base_evidence_bundle"
    ]["structured_query_snapshot"]["queries"][0]["query_sha256"] == "d" * 64


def test_session_serialization_preserves_skip_and_timing_metadata():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=["python"],
    )
    state["skipped_question_ids"] = ["q1"]
    state["finished_at"] = "2026-07-04T10:00:00Z"

    row = SessionRowMapper.to_row(state)
    restored = SessionRowMapper.from_rows(row, [])

    assert row["skipped_question_ids"] == ["q1"]
    assert row["started_at"] == state["started_at"]
    assert row["finished_at"] == "2026-07-04T10:00:00Z"
    assert restored["skipped_question_ids"] == ["q1"]
    assert restored["started_at"] == state["started_at"]
    assert restored["finished_at"] == "2026-07-04T10:00:00Z"


def test_session_serialization_round_trips_orchestration_metadata():
    state = make_state()
    state["phase"] = "review"
    state["phase_status"] = "completed"
    state["review_status"] = "completed"
    state["state_version"] = 6
    state["checkpoint_version"] = 6
    state["last_checkpoint_at"] = "2026-07-08T10:00:00Z"
    state["last_command_id"] = "cmd-2"

    row = SessionRowMapper.to_row(state)
    restored = SessionRowMapper.from_rows(row, [])

    assert row["phase"] == "review"
    assert row["phase_status"] == "completed"
    assert row["review_status"] == "completed"
    assert row["state_version"] == 6
    assert row["checkpoint_version"] == 6
    assert row["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert row["last_command_id"] == "cmd-2"
    assert restored["phase"] == "review"
    assert restored["phase_status"] == "completed"
    assert restored["review_status"] == "completed"
    assert restored["state_version"] == 6
    assert restored["checkpoint_version"] == 6
    assert restored["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert restored["last_command_id"] == "cmd-2"


def test_report_record_round_trips_from_row():
    record = make_report_record()
    row = ReportRowMapper.to_row(record)

    restored = ReportRowMapper.from_row(row)

    assert restored.status == "completed"
    assert restored.report is not None
    assert restored.report.overall_score == 80
    assert restored.report.feedbacks[0].references[0].chunk_id == "fastapi_backend"


def test_report_record_round_trips_lifecycle_timestamps():
    report = make_report_record()
    record = ReportRecord(
        status="completed",
        report=report.report,
        created_at="2026-07-04T10:00:00Z",
        finished_at="2026-07-04T10:02:00Z",
    )

    row = ReportRowMapper.to_row(record)
    restored = ReportRowMapper.from_row(row)

    assert row["created_at"] == "2026-07-04T10:00:00Z"
    assert row["finished_at"] == "2026-07-04T10:02:00Z"
    assert restored.created_at == "2026-07-04T10:00:00Z"
    assert restored.finished_at == "2026-07-04T10:02:00Z"


def test_processing_report_record_round_trips_from_row():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(
            stage="retrieving",
            percent=20,
            message="Retrieving references.",
        ),
    )
    row = ReportRowMapper.to_row(record)

    restored = ReportRowMapper.from_row(row)

    assert restored.status == "processing"
    assert restored.progress is not None
    assert restored.progress.percent == 20


def test_question_feedback_serializes_rule_scoring_metadata():
    from app.services.question_evaluations import question_evaluation_from_feedback
    from app.services.report import (
        DimensionScores,
        FeedbackReference,
        InterviewFeedback,
    )
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="如何设计高并发秒杀系统？",
        user_answer="我会做库存预扣、MQ 补偿和降级。",
        score=80,
        dimension_scores=DimensionScores(
            breadth=0,
            depth=75,
            architecture=85,
            engineering=80,
            communication=80,
        ),
        applicable_dimensions=[
            "architecture",
            "engineering",
            "depth",
            "communication",
        ],
        dimension_evidence=[
            {
                "dimension": "architecture",
                "observed": ["说明了库存预扣和服务边界。"],
                "missing": ["容量估算不足。"],
                "quality_signals": ["concrete_steps", "tradeoff", "risk"],
            }
        ],
        rationale="回答覆盖了系统设计主路径，但容量估算不足。",
        critique="缺少容量估算。",
        better_answer="补充容量、限流和降级策略。",
        references=[
            FeedbackReference(
                chunk_id="system-1",
                title="System design benchmark",
                source_type="theory",
                excerpt="高并发系统需要容量估算、限流和降级。",
            )
        ],
    )

    record = question_evaluation_from_feedback(
        session_id="s1",
        feedback=feedback,
        retrieval_path="bound_evidence_ids",
        evidence_content_sha256={"system-1": "a" * 64},
    )
    row = QuestionEvaluationRowMapper.to_row(record)
    restored = QuestionEvaluationRowMapper.from_row(row)

    assert restored.feedback.applicable_dimensions == [
        "architecture",
        "engineering",
        "depth",
        "communication",
    ]
    assert restored.feedback.dimension_evidence[0]["dimension"] == "architecture"
    assert restored.retrieval_path == "bound_evidence_ids"
    assert restored.evidence_content_sha256 == {"system-1": "a" * 64}


@pytest.mark.parametrize(
    ("mapper", "row"),
    [
        (SessionRowMapper, lambda: SessionRowMapper.to_row(make_state())),
        (
            MessageRowMapper,
            lambda: MessageRowMapper.to_row(
                "s1",
                1,
                {"role": "interviewer", "content": "hello", "question_id": "q1"},
            ),
        ),
        (ReportRowMapper, lambda: ReportRowMapper.to_row(make_report_record())),
    ],
)
def test_explicit_unknown_row_schema_versions_fail_closed(mapper, row):
    stored = row()
    stored["row_schema_version"] = "future-row-v99"

    with pytest.raises(UnsupportedRowSchemaVersionError, match="unsupported"):
        if mapper is SessionRowMapper:
            mapper.from_rows(stored, [])
        else:
            mapper.from_row(stored)


def test_missing_row_schema_version_uses_documented_legacy_v1_backfill():
    session_row = SessionRowMapper.to_row(make_state())
    message_row = MessageRowMapper.to_row(
        "s1",
        1,
        {"role": "interviewer", "content": "hello", "question_id": "q1"},
    )
    session_row.pop("row_schema_version")
    message_row.pop("row_schema_version")

    restored = SessionRowMapper.from_rows(session_row, [message_row])

    assert restored["messages"][0]["content"] == "hello"
