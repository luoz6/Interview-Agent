from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_reliability import ReportReliabilityProjector


def _scores(value: int = 72) -> DimensionScores:
    return DimensionScores(
        breadth=value,
        depth=value,
        architecture=value,
        engineering=value,
        communication=value,
    )


def _state():
    state = build_initial_state(
        session_id="session-reliability",
        plan=InterviewPlan(
            title="可靠性工程师",
            questions=[
                InterviewQuestion(
                    id=f"q{index}",
                    kind="technical",
                    prompt=f"题目 {index}",
                    focus="可靠性",
                )
                for index in range(1, 5)
            ],
        ),
        job_description="可靠性工程师",
        resume_text="负责过生产系统",
        job_tags=["reliability"],
    )
    state["messages"].extend(
        [
            {"role": "candidate", "content": "回答一", "question_id": "q1"},
            {"role": "candidate", "content": "回答二", "question_id": "q2"},
            {"role": "candidate", "content": "回答三", "question_id": "q3"},
        ]
    )
    state["skipped_question_ids"] = ["q4"]
    return state


def _feedback(question_id: str, *, references: bool = True) -> InterviewFeedback:
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"题目 {question_id}",
        user_answer="候选人回答",
        answer_state="answered",
        score=72,
        dimension_scores=_scores(),
        rationale="评分依据",
        critique="改进点",
        better_answer="改进回答",
        references=(
            [
                FeedbackReference(
                    chunk_id=f"e-{question_id}",
                    title="证据",
                    source_type="knowledge",
                    excerpt="摘要",
                )
            ]
            if references
            else []
        ),
    )


def _report(*, fallback: bool = False) -> InterviewReport:
    return InterviewReport(
        session_id="session-reliability",
        overall_score=72,
        overall_dimension_scores=_scores(),
        summary="本轮总结",
        highlights=["能够说明关键取舍"],
        feedbacks=[
            _feedback("q1"),
            _feedback("q2"),
            _feedback("q3", references=False),
        ],
        is_fallback=fallback,
    )


def test_reliability_uses_authoritative_answer_state_and_safe_reason_codes():
    records = [
        QuestionEvaluationRecord(
            session_id="session-reliability",
            question_id="q1",
            status="completed",
            feedback=_feedback("q1"),
        ),
        QuestionEvaluationRecord(
            session_id="session-reliability",
            question_id="q2",
            status="completed",
            feedback=_feedback("q2"),
            retrieval_path="degraded",
            degraded_reason="provider detail must not be public",
        ),
        QuestionEvaluationRecord(
            session_id="session-reliability",
            question_id="q3",
            status="failed",
            error="private provider error",
        ),
    ]
    reliability = ReportReliabilityProjector().project(
        _state(), _report(), records, report_path="full_session_fallback"
    )
    assert reliability.model_dump() == {
        "planned_question_count": 4,
        "answered_question_count": 3,
        "skipped_question_count": 1,
        "unanswered_question_count": 0,
        "reviewed_answer_count": 2,
        "review_failed_answer_count": 1,
        "evidence_bound_question_count": 2,
        "degraded_question_count": 2,
        "generation_path": "mixed",
        "degraded_reasons": [
            "QUESTION_REVIEW_FALLBACK",
            "QUESTION_REVIEW_UNAVAILABLE",
            "KNOWLEDGE_RETRIEVAL_DEGRADED",
        ],
        "score_applicability": "limited",
    }


def test_reliability_marks_structured_complete_reviews_normal():
    records = [
        QuestionEvaluationRecord(
            session_id="session-reliability",
            question_id=question_id,
            status="completed",
            feedback=_feedback(question_id),
        )
        for question_id in ("q1", "q2", "q3")
    ]
    reliability = ReportReliabilityProjector().project(
        _state(), _report(), records, report_path="microbatch"
    )
    assert reliability.generation_path == "structured"
    assert reliability.score_applicability == "normal"
    assert reliability.degraded_reasons == []


def test_reliability_marks_fallback_score_insufficient():
    reliability = ReportReliabilityProjector().project(
        _state(), _report(fallback=True), [], report_path="full_session"
    )
    assert reliability.generation_path == "fallback"
    assert reliability.score_applicability == "insufficient"
    assert reliability.degraded_question_count == 3
    assert reliability.degraded_reasons == [
        "REPORT_FALLBACK",
    ]
