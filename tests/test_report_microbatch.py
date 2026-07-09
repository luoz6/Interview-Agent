import pytest

from app.graphs.interview_state import build_initial_state
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport
from app.services.report_microbatch import (
    MicrobatchReportUnavailable,
    build_report_coach_items_from_question_evaluations,
    ensure_completed_question_evaluations_for_report,
    finalize_report_with_microbatch_feedback,
    generate_microbatch_report,
)
from tests.report_microbatch_fixtures import (
    completed_record,
    make_dimension_scores,
    make_feedback,
    make_plan,
)


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a FastAPI Redis service.",
        job_tags=["python", "redis"],
    )
    state["messages"].extend(
        [
            {
                "role": "candidate",
                "content": "I built a FastAPI cache service.",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "I delete Redis cache after database writes.",
                "question_id": "q2",
            },
        ]
    )
    state["current_index"] = 2
    state["status"] = "finished"
    return state


class FakeStore:
    def __init__(self, state, records=None):
        self.state = state
        self.records = list(records or [])
        self.upserted = []
        self.get_calls = 0

    def get(self, session_id: str):
        assert session_id == self.state["session_id"]
        self.get_calls += 1
        return self.state

    def list_question_evaluations(self, session_id: str):
        assert session_id == self.state["session_id"]
        return list(self.records)

    def upsert_question_evaluation(self, session_id: str, record):
        assert session_id == self.state["session_id"]
        self.upserted.append(record)
        self.records = [
            existing
            for existing in self.records
            if existing.question_id != record.question_id
        ] + [record]


class FakeReviewer:
    calls = []

    def __init__(self, *, llm, vector_store):
        self.llm = llm
        self.vector_store = vector_store

    def evaluate(self, state, on_progress=None):
        question = state["plan"].questions[0]
        self.__class__.calls.append(question.id)
        return InterviewReport(
            session_id=state["session_id"],
            overall_score=83,
            overall_dimension_scores=make_dimension_scores(83),
            summary="single question report",
            highlights=["single question highlight"],
            feedbacks=[make_feedback(question_id=question.id, score=83)],
        )


class CapturingReportLLM:
    def __init__(self):
        self.evaluation_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("not used")

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        self.evaluation_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=82,
            overall_dimension_scores=make_dimension_scores(82),
            summary="Aggregated from microbatch feedback.",
            highlights=["Reused question-level reviews"],
            feedbacks=[
                make_feedback(
                    question_id=item["question_id"],
                    score=item["microbatch_score"],
                )
                for item in evaluation_items
            ],
        )


def test_ensure_completed_question_evaluations_reuses_existing_completed_records():
    state = make_state()
    records = [
        completed_record("s1", "q2", 82),
        completed_record("s1", "q1", 78),
    ]
    store = FakeStore(state, records)
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert [record.feedback.score for record in result] == [78, 82]
    assert FakeReviewer.calls == []
    assert store.upserted == []


def test_ensure_completed_question_evaluations_reviews_missing_records():
    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert FakeReviewer.calls == ["q2"]
    assert store.upserted[0].question_id == "q2"
    assert store.upserted[0].status == "completed"
    assert store.get_calls == 0


def test_ensure_completed_question_evaluations_reruns_failed_records():
    state = make_state()
    failed = QuestionEvaluationRecord(
        session_id="s1",
        question_id="q2",
        answer_state="answered",
        status="failed",
        error="review model unavailable",
    )
    store = FakeStore(state, [completed_record("s1", "q1", 78), failed])
    FakeReviewer.calls = []

    result = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=object(),
        vector_store=object(),
        reviewer_factory=FakeReviewer,
    )

    assert [record.question_id for record in result] == ["q1", "q2"]
    assert FakeReviewer.calls == ["q2"]
    assert result[1].status == "completed"


def test_ensure_completed_question_evaluations_raises_when_rerun_still_fails():
    class FailingReviewer:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("round review still unavailable")

    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])

    with pytest.raises(MicrobatchReportUnavailable, match="q2"):
        ensure_completed_question_evaluations_for_report(
            state,
            store=store,
            llm=object(),
            vector_store=object(),
            reviewer_factory=FailingReviewer,
        )


def test_build_report_coach_items_from_question_evaluations_preserves_microbatch_feedback():
    records = [
        completed_record("s1", "q1", 78),
        completed_record("s1", "q2", 82),
    ]

    items = build_report_coach_items_from_question_evaluations(records)

    assert [item["question_id"] for item in items] == ["q1", "q2"]
    assert items[0]["microbatch_score"] == 78
    assert items[0]["dimension_scores"]["breadth"] == 78
    assert items[0]["rationale"] == (
        "q1 \u7684\u56de\u7b54\u8986\u76d6\u4e86\u6838\u5fc3\u94fe\u8def\u548c\u4e3b\u8981\u53d6\u820d\u3002"
    )
    assert items[0]["critique"] == (
        "q1 \u7684\u56de\u7b54\u8fd8\u9700\u8981\u8865\u5145\u8fb9\u754c\u6761\u4ef6\u548c\u91cf\u5316\u7ed3\u679c\u3002"
    )
    assert items[0]["better_answer"] == (
        "q1 \u53ef\u4ee5\u8865\u5145\u6545\u969c\u515c\u5e95\u3001\u76d1\u63a7\u6307\u6807\u548c\u6027\u80fd\u6570\u636e\u3002"
    )
    assert items[0]["scoring_references"] == []
    assert items[0]["answer_references"] == []


def test_finalize_report_with_microbatch_feedback_locks_scores_to_shadow_reviewer_records():
    records = [
        completed_record("s1", "q1", 78),
        completed_record("s1", "q2", 82),
    ]
    coach_report = InterviewReport(
        session_id="s1",
        overall_score=99,
        overall_dimension_scores=make_dimension_scores(99),
        summary="Report Coach \u603b\u7ed3\u5e94\u8be5\u88ab\u4fdd\u7559\u3002",
        highlights=["Report Coach \u4eae\u70b9\u5e94\u8be5\u88ab\u4fdd\u7559"],
        feedbacks=[
            make_feedback(question_id="q1", score=11),
            make_feedback(question_id="q2", score=22),
        ],
    )

    report = finalize_report_with_microbatch_feedback(coach_report, records)

    assert report.summary == "Report Coach \u603b\u7ed3\u5e94\u8be5\u88ab\u4fdd\u7559\u3002"
    assert report.highlights == ["Report Coach \u4eae\u70b9\u5e94\u8be5\u88ab\u4fdd\u7559"]
    assert report.overall_score == 80
    assert report.overall_dimension_scores == make_dimension_scores(80)
    assert [feedback.score for feedback in report.feedbacks] == [78, 82]
    assert report.feedbacks[0].rationale == (
        "q1 \u7684\u56de\u7b54\u8986\u76d6\u4e86\u6838\u5fc3\u94fe\u8def\u548c\u4e3b\u8981\u53d6\u820d\u3002"
    )


def test_ensure_completed_question_evaluations_logs_unknown_answer_state(caplog):
    state = make_state()
    state["skipped_question_ids"] = []
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    FakeReviewer.calls = []

    import app.services.report_microbatch as report_microbatch

    original_build_chunks = report_microbatch.build_evaluation_chunks

    def fake_build_chunks(state):
        chunks = original_build_chunks(state)
        return [
            chunk.model_copy(update={"answer_state": "partial"})
            if chunk.question_id == "q2"
            else chunk
            for chunk in chunks
        ]

    report_microbatch.build_evaluation_chunks = fake_build_chunks
    try:
        with caplog.at_level("WARNING"):
            ensure_completed_question_evaluations_for_report(
                state,
                store=store,
                llm=object(),
                vector_store=object(),
                reviewer_factory=FakeReviewer,
            )
    finally:
        report_microbatch.build_evaluation_chunks = original_build_chunks

    assert "unknown answer_state for question review microbatch" in caplog.text


def test_generate_microbatch_report_calls_report_coach_with_microbatch_items():
    state = make_state()
    store = FakeStore(
        state,
        [completed_record("s1", "q1", 78), completed_record("s1", "q2", 82)],
    )
    llm = CapturingReportLLM()
    progress_updates = []

    report = generate_microbatch_report(
        state,
        store=store,
        llm=llm,
        vector_store=object(),
        on_progress=progress_updates.append,
        reviewer_factory=FakeReviewer,
    )

    assert report.session_id == "s1"
    assert report.overall_score == 80
    assert [item["question_id"] for item in llm.evaluation_items] == ["q1", "q2"]
    assert llm.evaluation_items[0]["source"] == "question_evaluation_record"
    assert [progress.stage for progress in progress_updates] == [
        "retrieving",
        "analyzing",
        "aggregating",
        "completed",
    ]


def test_generate_microbatch_report_reports_reuse_stats():
    state = make_state()
    store = FakeStore(
        state,
        [completed_record("s1", "q1", 78)],
    )
    llm = CapturingReportLLM()
    captured_stats = []
    FakeReviewer.calls = []

    report = generate_microbatch_report(
        state,
        store=store,
        llm=llm,
        vector_store=object(),
        on_progress=lambda progress: None,
        on_microbatch_stats=captured_stats.append,
        reviewer_factory=FakeReviewer,
    )

    assert report.overall_score == 80
    assert FakeReviewer.calls == ["q2"]
    assert len(captured_stats) == 1
    stats = captured_stats[0]
    assert stats.total_questions == 2
    assert stats.reused_questions == 1
    assert stats.rerun_questions == 1
    assert stats.failed_questions == 0
    assert stats.question_ids == ["q1", "q2"]
    assert stats.rerun_question_ids == ["q2"]
    assert stats.to_metadata()["report_path"] == "microbatch"
    assert stats.to_metadata()["microbatch_rerun_questions"] == 1


def test_generate_microbatch_report_reports_failed_stats_before_raising():
    class FailingReviewer:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("round review still unavailable")

    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    captured_stats = []

    with pytest.raises(MicrobatchReportUnavailable, match="q2"):
        generate_microbatch_report(
            state,
            store=store,
            llm=CapturingReportLLM(),
            vector_store=object(),
            on_microbatch_stats=captured_stats.append,
            reviewer_factory=FailingReviewer,
        )

    assert len(captured_stats) == 1
    stats = captured_stats[0]
    assert stats.total_questions == 2
    assert stats.reused_questions == 1
    assert stats.rerun_questions == 1
    assert stats.failed_questions == 1
    assert stats.rerun_question_ids == ["q2"]
    assert stats.failed_question_ids == ["q2"]
