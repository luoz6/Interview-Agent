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
from app.services.session_serialization import (
    message_to_row,
    report_record_from_row,
    report_record_to_row,
    session_row_from_state,
    state_from_rows,
)


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
    session_row = session_row_from_state(state)
    message_rows = [
        message_to_row("s1", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]

    restored = state_from_rows(session_row, message_rows)

    assert restored["session_id"] == "s1"
    assert restored["plan"].questions[0].prompt == "Describe your backend project."
    assert restored["messages"] == state["messages"]
    assert restored["job_tags"] == ["python", "fastapi"]


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

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])
    context = restored["plan"].prep_context

    assert row["plan_json"]["prep_context"]["schema_version"] == "v2"
    assert context.evidence_refs[0].content_sha256 == "a" * 64
    assert context.binding_snapshot.queries[0].hit_content_sha256 == {
        "redis-consistency": "a" * 64
    }


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

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

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

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

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
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

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

    row = report_record_to_row(record)
    restored = report_record_from_row(row)

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
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

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
    from app.services.session_serialization import (
        question_evaluation_record_from_row,
        question_evaluation_record_to_row,
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
    row = question_evaluation_record_to_row(record)
    restored = question_evaluation_record_from_row(row)

    assert restored.feedback.applicable_dimensions == [
        "architecture",
        "engineering",
        "depth",
        "communication",
    ]
    assert restored.feedback.dimension_evidence[0]["dimension"] == "architecture"
    assert restored.retrieval_path == "bound_evidence_ids"
    assert restored.evidence_content_sha256 == {"system-1": "a" * 64}
